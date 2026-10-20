"""新增持续跟踪成员的定向生命周期与历史低吸补算。"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from quant_engine.calculators.lifecycle_state_calculator import LifecycleStateCalculator
from quant_engine.watch.engine import DB_PATH, WatchEngine
from quant_engine.watch.scanner import WatchHistoricalScanner


logger = logging.getLogger(__name__)
MAX_HISTORY_TRADE_DAYS = 160
_REFRESH_LOCK = threading.Lock()


class WatchMemberRefreshService:
    """只补指定股票；失败时不继续生成 Watch 买点。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    @staticmethod
    def _normalize_symbols(symbols: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(symbol).strip().upper()
                for symbol in symbols
                if str(symbol).strip()
            )
        )

    def _market_trade_dates(self, limit: int) -> list[str]:
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='stock_daily_prices'"
            ).fetchone()
            if table is None:
                return []
            rows = conn.execute(
                """
                SELECT trade_date FROM (
                    SELECT DISTINCT substr(trade_date, 1, 10) AS trade_date
                    FROM stock_daily_prices
                    ORDER BY trade_date DESC
                    LIMIT ?
                ) ORDER BY trade_date
                """,
                (limit,),
            ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def _has_price_history(self, symbol: str) -> bool:
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            row = conn.execute(
                "SELECT 1 FROM stock_daily_prices "
                "WHERE UPPER(TRIM(symbol))=? LIMIT 1",
                (symbol,),
            ).fetchone()
        return row is not None

    def refresh(
        self,
        symbols: list[str],
        *,
        history_trade_days: int = MAX_HISTORY_TRADE_DAYS,
    ) -> dict[str, Any]:
        normalized = self._normalize_symbols(symbols)
        if not normalized:
            return {
                "success": False,
                "status": "INVALID",
                "error": "symbols 不能为空",
                "symbols": [],
            }
        history_limit = min(
            max(1, int(history_trade_days)),
            MAX_HISTORY_TRADE_DAYS,
        )

        with _REFRESH_LOCK:
            market_trade_dates = self._market_trade_dates(history_limit)
            available = (
                [symbol for symbol in normalized if self._has_price_history(symbol)]
                if market_trade_dates
                else []
            )
            if not market_trade_dates or not available:
                return {
                    "success": False,
                    "status": "NO_DATA",
                    "error": "股票没有可用日线，未生成低吸状态",
                    "symbols": normalized,
                }

            calculator = LifecycleStateCalculator(str(self.db_path))
            lifecycle = calculator.backfill_symbols(
                available,
                market_trade_dates,
            )
            lifecycle_records = int(lifecycle.get("records") or 0)
            if not lifecycle.get("success"):
                return {
                    "success": False,
                    "status": "LIFECYCLE_FAILED",
                    "error": lifecycle.get("error") or "生命周期回填失败",
                    "symbols": normalized,
                }

            latest_date = market_trade_dates[-1]
            watch = WatchEngine(self.db_path).run(
                latest_date,
                symbols=available,
            )
            if not watch.get("success"):
                return {
                    "success": False,
                    "status": "WATCH_FAILED",
                    "error": watch.get("error") or "Watch Engine 刷新失败",
                    "symbols": normalized,
                }

            # AUTO_MEMBER 是初始化链路，不是正式质量门。它只刷新本次成员，
            # 不消费全局即时事件，避免并发入池时发送瞬态 lifecycle_missing。
            notification = {
                "sent": 0,
                "failed": 0,
                "skipped": 0,
                "reason": "AUTO_MEMBER refresh does not dispatch immediate alerts",
            }

            scanner = WatchHistoricalScanner(self.db_path)
            try:
                scan = scanner.create_scan(
                    start_date=market_trade_dates[0],
                    end_date=market_trade_dates[-1],
                    scope="all_groups",
                    symbols=available,
                    origin="AUTO_MEMBER",
                )
                scans = [scanner.run_scan(scan["scan_id"])]
            except Exception as exc:
                logger.exception("新增成员自动历史扫描失败")
                return {
                    "success": False,
                    "status": "SCAN_FAILED",
                    "error": str(exc),
                    "symbols": normalized,
                    "latest_trade_date": latest_date,
                    "lifecycle_records": lifecycle_records,
                    "watch": watch,
                    "notification": notification,
                }

            return {
                "success": True,
                "status": "COMPLETED",
                "symbols": normalized,
                "latest_trade_date": latest_date,
                "lifecycle_records": lifecycle_records,
                "watch": watch,
                "notification": notification,
                "scans": scans,
            }


def main() -> int:
    parser = argparse.ArgumentParser(description="补算新增 Watchlist 成员")
    parser.add_argument("symbols", nargs="+", help="股票代码")
    parser.add_argument("--history-trade-days", type=int, default=160)
    parser.add_argument("--db-path")
    args = parser.parse_args()
    result = WatchMemberRefreshService(args.db_path).refresh(
        args.symbols,
        history_trade_days=args.history_trade_days,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
