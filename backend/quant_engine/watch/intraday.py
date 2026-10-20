"""手工重点池的盘中 READY 小名单监控。

只读取上一交易日 ``IN_ZONE`` 快照，每只股只发起一次最近 K 线请求。
旧 bar、断线或缺失 bar 只会产生 ``DATA_BLOCKED``，
不会使用快照价格触发买点。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, time as wall_time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd


CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from data_job.core.tdx_adapter import TdxAdapter, get_tdx_adapter
from data_core.market_calendar import is_a_share_session
from portfolio.exit_engine import PortfolioExitEngine


DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_READY_SYMBOLS = 50
BAR_PUBLISH_GRACE_SECONDS = 30
MISSING_BAR_ALERT_THRESHOLD = 2
logger = logging.getLogger(__name__)


class IntradayWatchMonitor:
    """用完成的 5 分钟 bar 确认低吸触发。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        adapter: TdxAdapter | None = None,
        now_provider: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        notifier: Callable[[str], Any] | None = None,
        portfolio_exit_engine: PortfolioExitEngine | None = None,
    ):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.adapter = adapter
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI))
        self.sleep = sleep
        self.notifier = notifier
        self.portfolio_exit_engine = portfolio_exit_engine
        self._missing_bar_counts: dict[str, int] = {}

    @staticmethod
    def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return set()
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}

    @staticmethod
    def _local_now(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=SHANGHAI)
        return value.astimezone(SHANGHAI)

    @staticmethod
    def _is_session(now: datetime) -> bool:
        current = now.time().replace(tzinfo=None)
        morning = wall_time(9, 35) <= current < wall_time(11, 31)
        afternoon = wall_time(13, 5) <= current <= wall_time(15, 5)
        return morning or afternoon

    @staticmethod
    def _expected_bar_time(now: datetime) -> datetime:
        """返回当前应已完成的 5 分钟 bar 收盘时间。"""
        minute = now.minute - now.minute % 5
        expected = now.replace(minute=minute, second=0, microsecond=0)
        if expected.time().replace(tzinfo=None) > wall_time(15, 0):
            expected = expected.replace(hour=15, minute=0)
        return expected

    def _is_open_day(self, conn: sqlite3.Connection, current_date: str) -> bool:
        if not is_a_share_session(date.fromisoformat(current_date)):
            return False
        columns = self._columns(conn, "trading_calendar")
        if {"trade_date", "is_open"} <= columns:
            row = conn.execute(
                "SELECT MAX(is_open) FROM trading_calendar WHERE substr(trade_date, 1, 10)=?",
                (current_date,),
            ).fetchone()
            if row and row[0] is not None:
                return bool(row[0])
        return True

    def _ready_rows(
        self, conn: sqlite3.Connection, current_date: str
    ) -> tuple[str | None, list[dict[str, Any]], int]:
        columns = self._columns(conn, "watch_daily_snapshot")
        required = {"trade_date", "symbol", "watch_state"}
        if not required <= columns:
            return None, [], 0
        if {"intraday_trigger_price", "trigger_price"} <= columns:
            trigger_expression = "COALESCE(intraday_trigger_price, trigger_price)"
        elif "intraday_trigger_price" in columns:
            trigger_expression = "intraday_trigger_price"
        elif "trigger_price" in columns:
            trigger_expression = "trigger_price"
        else:
            return None, [], 0
        calendar_columns = self._columns(conn, "trading_calendar")
        price_columns = self._columns(conn, "stock_daily_prices")
        if {"trade_date", "is_open"} <= calendar_columns:
            previous = conn.execute(
                """
                SELECT MAX(substr(trade_date, 1, 10)) FROM trading_calendar
                WHERE substr(trade_date, 1, 10) < ? AND is_open=1
                """,
                (current_date,),
            ).fetchone()
        elif "trade_date" in price_columns:
            previous = conn.execute(
                """
                SELECT MAX(substr(trade_date, 1, 10)) FROM stock_daily_prices
                WHERE substr(trade_date, 1, 10) < ?
                """,
                (current_date,),
            ).fetchone()
        else:
            previous = conn.execute(
                """
                SELECT MAX(substr(trade_date, 1, 10)) FROM watch_daily_snapshot
                WHERE substr(trade_date, 1, 10) < ?
                """,
                (current_date,),
            ).fetchone()
        previous_date = str(previous[0]) if previous and previous[0] else None
        if not previous_date:
            return None, [], 0

        wanted = [
            column
            for column in (
                "trade_date",
                "symbol",
                "name",
                "pool_name",
                "watch_state",
                "invalidation_price",
                "setup_id",
                "current_price",
                "universe_source",
            )
            if column in columns
        ]
        manual_clause = ""
        params: list[Any] = [previous_date]
        if "universe_source" in columns:
            manual_clause = "AND COALESCE(universe_source, 'manual') IN ('manual', 'both')"
        where_clause = f"""
            WHERE substr(trade_date, 1, 10)=? AND watch_state='IN_ZONE'
              AND {trigger_expression} IS NOT NULL
              {manual_clause}
        """
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM watch_daily_snapshot {where_clause}",
            params,
        ).fetchone()
        total = int(count_row[0]) if count_row else 0
        rows = conn.execute(
            f"""
            SELECT {', '.join(wanted)}, {trigger_expression} AS trigger_price
            FROM watch_daily_snapshot
            {where_clause}
            ORDER BY symbol
            LIMIT ?
            """,
            [*params, MAX_READY_SYMBOLS],
        ).fetchall()
        return previous_date, [dict(row) for row in rows], total

    def _portfolio_symbols(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[list[str], int]:
        """Return current enabled Portfolio targets without migrating old databases."""
        plan_columns = self._columns(conn, "portfolio_exit_plans")
        position_columns = self._columns(conn, "portfolio_positions")
        if not {
            "account_id",
            "symbol",
            "position_revision",
            "enabled",
            "intraday_enabled",
        } <= plan_columns:
            return [], 0
        if not {
            "account_id",
            "symbol",
            "position_revision",
            "quantity",
            "status",
        } <= position_columns:
            return [], 0
        from_clause = """
            SELECT DISTINCT plan.symbol
            FROM portfolio_exit_plans AS plan
            JOIN portfolio_positions AS position
              ON position.account_id = plan.account_id
             AND position.symbol = plan.symbol
             AND position.position_revision = plan.position_revision
            WHERE plan.enabled = 1
              AND plan.intraday_enabled = 1
              AND position.status = 'OPEN'
              AND position.quantity > 0
        """
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM ({from_clause})"
        ).fetchone()
        total = int(count_row[0]) if count_row else 0
        rows = conn.execute(
            f"""
            {from_clause}
            ORDER BY plan.symbol
            LIMIT ?
            """,
            (MAX_READY_SYMBOLS,),
        ).fetchall()
        return [str(row[0]).zfill(6) for row in rows], total

    @staticmethod
    def _price_micros(value: float) -> int | None:
        if not math.isfinite(value) or value <= 0:
            return None
        try:
            micros = int(
                (Decimal(str(value)) * Decimal("1000000")).quantize(
                    Decimal("1"),
                    rounding=ROUND_HALF_UP,
                )
            )
        except (InvalidOperation, ValueError, OverflowError):
            return None
        return micros if 0 < micros <= 10_000_000_000 else None

    def _ensure_events_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_events (
                event_id TEXT PRIMARY KEY,
                dedupe_key TEXT UNIQUE,
                trade_date TEXT NOT NULL,
                symbol TEXT,
                setup_id TEXT,
                event_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                severity TEXT,
                title TEXT,
                message TEXT,
                status TEXT DEFAULT 'UNREAD',
                payload TEXT,
                resolved_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        if "resolved_at" not in self._columns(conn, "watch_events"):
            conn.execute("ALTER TABLE watch_events ADD COLUMN resolved_at TEXT")

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        dedupe_key: str,
        trade_date: str,
        event_type: str,
        title: str,
        message: str,
        severity: str,
        symbol: str | None = None,
        setup_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """按实际表列插入，兼容早期 watch_events 表。"""
        self._ensure_events_table(conn)
        columns = self._columns(conn, "watch_events")
        if "dedupe_key" in columns:
            exists = conn.execute(
                "SELECT 1 FROM watch_events WHERE dedupe_key=? LIMIT 1", (dedupe_key,)
            ).fetchone()
        elif {"trade_date", "symbol", "setup_id", "event_type"} <= columns:
            exists = conn.execute(
                """
                SELECT 1 FROM watch_events
                WHERE trade_date=? AND symbol IS ? AND setup_id IS ? AND event_type=? LIMIT 1
                """,
                (trade_date, symbol, setup_id, event_type),
            ).fetchone()
        else:
            exists = None
        if exists:
            return False

        now = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
        values: dict[str, Any] = {
            "event_id": uuid4().hex,
            "dedupe_key": dedupe_key,
            "trade_date": trade_date,
            "symbol": symbol,
            "setup_id": setup_id,
            "event_type": event_type,
            "from_state": "IN_ZONE" if event_type == "TRIGGERED" else None,
            "to_state": "TRIGGERED" if event_type == "TRIGGERED" else "UNKNOWN",
            "severity": severity,
            "title": title,
            "message": message,
            "status": "UNREAD",
            "payload": json.dumps(payload or {}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }
        insert_columns = [column for column in values if column in columns]
        if not {"trade_date", "event_type"} <= set(insert_columns):
            logger.error("watch_events 表缺少必需列，无法写入盘中事件")
            return False
        try:
            cursor = conn.execute(
                f"INSERT INTO watch_events ({', '.join(insert_columns)}) "
                f"VALUES ({', '.join('?' for _ in insert_columns)})",
                [values[column] for column in insert_columns],
            )
            return bool(cursor.rowcount)
        except sqlite3.IntegrityError as exc:
            logger.warning("盘中事件写入被约束拒绝: %s", exc)
            return False

    def _data_blocked(
        self,
        conn: sqlite3.Connection,
        *,
        current_date: str,
        reason: str,
        expected_bar: datetime | None = None,
        symbol: str | None = None,
        setup_id: str | None = None,
        details: str | None = None,
    ) -> bool:
        bar_key = expected_bar.strftime("%H:%M") if expected_bar else "day"
        dedupe_key = f"intraday:data:{current_date}:{reason}"
        payload = {
            "reason": reason,
            "details": details,
            "expected_bar": bar_key,
            "sample_symbol": symbol,
            "sample_setup_id": setup_id,
        }
        if reason in {"no_new_completed_bar", "tdx_unavailable", "tdx_request_failed"} and symbol:
            payload["failed_symbols"] = [symbol]
            self._ensure_events_table(conn)
            existing = conn.execute(
                """
                SELECT event_id, payload, resolved_at
                FROM watch_events
                WHERE dedupe_key=? AND event_type='DATA_BLOCKED'
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
            if existing:
                if existing[2] is not None:
                    return False
                try:
                    current_payload = json.loads(existing[1] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    current_payload = {}
                failed_symbols = {
                    str(item).zfill(6)
                    for item in current_payload.get("failed_symbols", [])
                    if str(item).strip()
                }
                sample_symbol = current_payload.get("sample_symbol")
                if sample_symbol:
                    failed_symbols.add(str(sample_symbol).zfill(6))
                failed_symbols.add(symbol)
                current_payload["failed_symbols"] = sorted(failed_symbols)
                now = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "UPDATE watch_events SET payload=?, updated_at=? WHERE event_id=?",
                    (
                        json.dumps(current_payload, ensure_ascii=False),
                        now,
                        existing[0],
                    ),
                )
                return False
        return self._insert_event(
            conn,
            # Data outages are system-level events. One root cause per day is
            # enough; per-symbol events would turn one TDX outage into 50 emails.
            dedupe_key=dedupe_key,
            trade_date=current_date,
            symbol=None,
            setup_id=None,
            event_type="DATA_BLOCKED",
            severity="CRITICAL",
            title="盘中监控数据阻断",
            message=f"{symbol or '盘中监控'}: {reason}",
            payload=payload,
        )

    def _resolve_intraday_data_block(
        self,
        conn: sqlite3.Connection,
        *,
        current_date: str,
        reason: str,
        recovered_symbols: set[str],
    ) -> bool:
        """按真实失败股票恢复盘中阻断，不触碰盘后数据事件。"""
        self._ensure_events_table(conn)
        if "resolved_at" not in self._columns(conn, "watch_events"):
            return False
        dedupe_key = f"intraday:data:{current_date}:{reason}"
        row = conn.execute(
            """
            SELECT event_id, payload FROM watch_events
            WHERE dedupe_key=? AND event_type='DATA_BLOCKED' AND resolved_at IS NULL
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if not row:
            return False
        event_id = str(row[0])
        try:
            payload = json.loads(row[1] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        failed_symbols = {
            str(item).zfill(6)
            for item in payload.get("failed_symbols", [])
            if str(item).strip()
        }
        sample_symbol = payload.get("sample_symbol")
        if not failed_symbols and sample_symbol:
            failed_symbols.add(str(sample_symbol).zfill(6))
        if not failed_symbols:
            return False
        remaining = failed_symbols - recovered_symbols
        if remaining:
            payload["failed_symbols"] = sorted(remaining)
            now = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE watch_events SET payload=?, updated_at=? WHERE event_id=?",
                (json.dumps(payload, ensure_ascii=False), now, event_id),
            )
            return False
        now = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
        self._insert_event(
            conn,
            dedupe_key=f"intraday:recovered:{event_id}",
            trade_date=current_date,
            event_type="DATA_RECOVERED",
            severity="INFO",
            title="盘中监控数据恢复",
            message=f"盘中完成柱已恢复：{reason}",
            payload={
                "reason": reason,
                "blocked_event_id": event_id,
                "recovered_symbols": sorted(failed_symbols),
            },
        )
        conn.execute(
            "UPDATE watch_events SET resolved_at=?, updated_at=? WHERE event_id=?",
            (now, now, event_id),
        )
        return True

    @staticmethod
    def _latest_bar(frame: pd.DataFrame | None) -> dict[str, Any] | None:
        if frame is None or frame.empty or not {"bar_time", "close"} <= set(frame.columns):
            return None
        clean = frame.copy()
        clean["bar_time"] = pd.to_datetime(clean["bar_time"], errors="coerce")
        clean["close"] = pd.to_numeric(clean["close"], errors="coerce")
        clean = clean.dropna(subset=["bar_time", "close"]).sort_values("bar_time")
        return clean.iloc[-1].to_dict() if not clean.empty else None

    @classmethod
    def _completed_bar(
        cls,
        frame: pd.DataFrame | None,
        expected_bar: datetime,
    ) -> dict[str, Any] | None:
        """Return the expected closed bar even if TDX also returns a forming bar."""
        if frame is None or frame.empty or not {"bar_time", "close"} <= set(frame.columns):
            return None
        clean = frame.copy()
        clean["bar_time"] = pd.to_datetime(clean["bar_time"], errors="coerce")
        clean["close"] = pd.to_numeric(clean["close"], errors="coerce")
        clean = clean.dropna(subset=["bar_time", "close"]).sort_values("bar_time")
        for record in reversed(clean.to_dict("records")):
            if cls._bar_datetime(record.get("bar_time")) == expected_bar:
                return record
        return None

    @staticmethod
    def _bar_datetime(value: Any) -> datetime | None:
        try:
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp):
                return None
            parsed = timestamp.to_pydatetime()
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=SHANGHAI)
        return parsed.astimezone(SHANGHAI)

    @staticmethod
    def _observation(
        row: dict[str, Any],
        *,
        status: str,
        current_price: float | None = None,
        bar_time: datetime | None = None,
    ) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "").zfill(6)
        name = str(row.get("name") or symbol)
        pool_name = str(row.get("pool_name") or "重点观察")

        def finite_number(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        trigger_price = finite_number(row.get("trigger_price"))
        invalidation_price = finite_number(row.get("invalidation_price"))
        distance = None
        if current_price is not None and trigger_price and trigger_price > 0:
            distance = round((trigger_price - current_price) / trigger_price * 100, 2)
        return {
            "symbol": symbol,
            "name": name,
            "pool_name": pool_name,
            "bar_time": bar_time.isoformat() if bar_time else None,
            "current_price": round(current_price, 3) if current_price is not None else None,
            "trigger_price": trigger_price,
            "invalidation_price": invalidation_price,
            "distance_to_trigger_pct": distance,
            "status": status,
        }

    def _run_once(self, now: datetime | None = None) -> dict[str, Any]:
        """执行一次五分钟轮询；重复执行不会重复产生事件。"""
        current = self._local_now(now or self.now_provider())
        current_date = current.date().isoformat()
        if not self._is_session(current):
            return {
                "success": True,
                "status": "outside_session",
                "monitored": 0,
                "triggered": 0,
                "portfolio_triggered": 0,
                "blocked": 0,
                "ready_targets": 0,
                "portfolio_targets": 0,
                "ready_omitted": 0,
                "portfolio_omitted": 0,
            }

        expected_bar = self._expected_bar_time(current)
        triggered = 0
        blocked = 0
        portfolio_triggered = 0
        portfolio_error: str | None = None
        ready_target_count = 0
        portfolio_target_count = 0
        ready_omitted = 0
        portfolio_omitted = 0
        selected_symbols: list[str] = []
        portfolio_prices: dict[str, int] = {}
        observations: list[dict[str, Any]] = []
        pending_triggers: list[tuple[dict[str, Any], str, float, float, datetime]] = []
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if not self._is_open_day(conn, current_date):
                return {
                    "success": True,
                    "status": "market_closed",
                    "monitored": 0,
                    "triggered": 0,
                    "portfolio_triggered": 0,
                    "blocked": 0,
                    "ready_targets": 0,
                    "portfolio_targets": 0,
                    "ready_omitted": 0,
                    "portfolio_omitted": 0,
                }
            previous_date, ready, ready_total = self._ready_rows(
                conn,
                current_date,
            )
            ready_by_symbol = {
                str(row["symbol"]).zfill(6): row
                for row in ready
            }
            ready_symbols = set(ready_by_symbol)
            portfolio_rows, portfolio_total = self._portfolio_symbols(conn)
            portfolio_symbols = set(portfolio_rows)
            ready_target_count = ready_total
            portfolio_target_count = portfolio_total
            selected_symbols = sorted(ready_symbols | portfolio_symbols)[
                :MAX_READY_SYMBOLS
            ]
            selected_set = set(selected_symbols)
            ready_omitted = ready_total - len(ready_symbols & selected_set)
            portfolio_omitted = (
                portfolio_total - len(portfolio_symbols & selected_set)
            )
            if ready_omitted or portfolio_omitted:
                blocked += int(
                    self._data_blocked(
                        conn,
                        current_date=current_date,
                        reason="intraday_universe_over_limit",
                        details=(
                            f"selected={len(selected_symbols)}, "
                            f"limit={MAX_READY_SYMBOLS}, "
                            f"ready_omitted={ready_omitted}, "
                            f"portfolio_omitted={portfolio_omitted}"
                        ),
                    )
                )
            if not selected_symbols:
                conn.commit()
                return {
                    "success": True,
                    "status": "no_ready_symbols",
                    "previous_trade_date": previous_date,
                    "monitored": 0,
                    "triggered": 0,
                    "portfolio_triggered": 0,
                    "blocked": blocked,
                    "observations": [],
                    "ready_targets": ready_target_count,
                    "portfolio_targets": portfolio_target_count,
                    "ready_omitted": ready_omitted,
                    "portfolio_omitted": portfolio_omitted,
                }

            adapter = self.adapter or get_tdx_adapter()
            completed_symbols: set[str] = set()
            for symbol in selected_symbols:
                row = ready_by_symbol.get(symbol)
                setup_id = (
                    str(row.get("setup_id") or f"{symbol}:{previous_date}")
                    if row is not None
                    else None
                )
                if adapter is None:
                    if row is not None:
                        observations.append(
                            self._observation(row, status="DATA_BLOCKED")
                        )
                    blocked += int(
                        self._data_blocked(
                            conn,
                            current_date=current_date,
                            expected_bar=expected_bar,
                            symbol=symbol,
                            setup_id=setup_id,
                            reason="tdx_unavailable",
                        )
                    )
                    continue
                try:
                    frame = adapter.get_recent_stock_bars(symbol, period="5min", count=3)
                except Exception as exc:
                    if row is not None:
                        observations.append(
                            self._observation(row, status="DATA_BLOCKED")
                        )
                    blocked += int(
                        self._data_blocked(
                            conn,
                            current_date=current_date,
                            expected_bar=expected_bar,
                            symbol=symbol,
                            setup_id=setup_id,
                            reason="tdx_request_failed",
                            details=type(exc).__name__,
                        )
                    )
                    continue

                completed = self._completed_bar(frame, expected_bar)
                bar_time = self._bar_datetime((completed or {}).get("bar_time"))
                if completed is None or bar_time is None:
                    missing_count = self._missing_bar_counts.get(symbol, 0) + 1
                    self._missing_bar_counts[symbol] = missing_count
                    if row is not None:
                        observations.append(
                            self._observation(
                                row,
                                status=(
                                    "DATA_BLOCKED"
                                    if missing_count >= MISSING_BAR_ALERT_THRESHOLD
                                    else "WAITING_FOR_BAR"
                                ),
                            )
                        )
                    latest = self._latest_bar(frame)
                    latest_time = self._bar_datetime((latest or {}).get("bar_time"))
                    if missing_count >= MISSING_BAR_ALERT_THRESHOLD:
                        blocked += int(
                            self._data_blocked(
                                conn,
                                current_date=current_date,
                                expected_bar=expected_bar,
                                symbol=symbol,
                                setup_id=setup_id,
                                reason="no_new_completed_bar",
                                details=latest_time.isoformat() if latest_time else None,
                            )
                        )
                    continue

                try:
                    close = float(completed["close"])
                except (TypeError, ValueError):
                    if row is not None:
                        observations.append(
                            self._observation(
                                row,
                                status="DATA_BLOCKED",
                                bar_time=bar_time,
                            )
                        )
                    blocked += int(
                        self._data_blocked(
                            conn,
                            current_date=current_date,
                            expected_bar=expected_bar,
                            symbol=symbol,
                            setup_id=setup_id,
                            reason="invalid_price",
                        )
                    )
                    continue
                close_micros = self._price_micros(close)
                if close_micros is None:
                    if row is not None:
                        observations.append(
                            self._observation(
                                row,
                                status="DATA_BLOCKED",
                                bar_time=bar_time,
                            )
                        )
                    blocked += int(
                        self._data_blocked(
                            conn,
                            current_date=current_date,
                            expected_bar=expected_bar,
                            symbol=symbol,
                            setup_id=setup_id,
                            reason="invalid_price",
                        )
                    )
                    continue
                self._missing_bar_counts.pop(symbol, None)
                completed_symbols.add(symbol)
                if symbol in portfolio_symbols:
                    portfolio_prices[symbol] = close_micros
                if row is None:
                    continue
                try:
                    trigger_price = float(row["trigger_price"])
                except (TypeError, ValueError):
                    observations.append(
                        self._observation(
                            row,
                            status="DATA_BLOCKED",
                            bar_time=bar_time,
                        )
                    )
                    continue
                if not math.isfinite(trigger_price) or trigger_price <= 0:
                    observations.append(
                        self._observation(
                            row,
                            status="DATA_BLOCKED",
                            bar_time=bar_time,
                        )
                    )
                    continue
                if close > trigger_price * 1.03:
                    observation_status = "ABOVE_CHASE_LIMIT"
                elif close >= trigger_price:
                    observation_status = "TRIGGERED"
                else:
                    observation_status = "WAITING"
                observations.append(
                    self._observation(
                        row,
                        status=observation_status,
                        current_price=close,
                        bar_time=bar_time,
                    )
                )
                if trigger_price <= close <= trigger_price * 1.03:
                    pending_triggers.append((row, setup_id, close, trigger_price, bar_time))

            if completed_symbols:
                for reason in ("no_new_completed_bar", "tdx_unavailable", "tdx_request_failed"):
                    self._resolve_intraday_data_block(
                        conn,
                        current_date=current_date,
                        reason=reason,
                        recovered_symbols=completed_symbols,
                    )

            for row, setup_id, close, trigger_price, bar_time in pending_triggers:
                symbol = str(row["symbol"]).zfill(6)
                name = str(row.get("name") or symbol)
                triggered += int(
                    self._insert_event(
                        conn,
                        # Share the setup-level key with the EOD engine so a
                        # later daily close cannot emit a second trigger email.
                        dedupe_key=f"state:{symbol}:{setup_id}:TRIGGERED",
                        trade_date=current_date,
                        symbol=symbol,
                        setup_id=setup_id,
                        event_type="TRIGGERED",
                        severity="HIGH",
                        title="盘中低吸确认触发",
                        message=(
                            f"{name} {symbol} 5分钟收盘价 {close:.2f} "
                            f"突破触发价 {trigger_price:.2f}，"
                            "且未超过 3% 追高线。"
                        ),
                        payload={
                            "source": "intraday_5min",
                            "name": name,
                            "pool_name": str(row.get("pool_name") or "重点观察"),
                            "bar_time": bar_time.isoformat(),
                            "current_price": close,
                            "close": close,
                            "trigger_price": trigger_price,
                            "invalidation_price": row.get("invalidation_price"),
                            "observations": observations,
                        },
                    )
                )
            conn.commit()

        if portfolio_prices:
            try:
                exit_engine = self.portfolio_exit_engine
                if exit_engine is None:
                    exit_engine = PortfolioExitEngine(self.db_path)
                    self.portfolio_exit_engine = exit_engine
                exit_result = exit_engine.run_intraday_bars(
                    expected_bar.isoformat(),
                    portfolio_prices,
                    observed_at=current.isoformat(),
                )
                portfolio_triggered = exit_result.created_event_count
            except Exception as exc:
                portfolio_error = type(exc).__name__
                logger.error(
                    "Portfolio 盘中卖点评估异常 (%s)",
                    portfolio_error,
                )
        return {
            "success": True,
            "status": "completed",
            "previous_trade_date": previous_date,
            "bar_time": expected_bar.isoformat(),
            "monitored": len(selected_symbols),
            "triggered": triggered,
            "portfolio_triggered": portfolio_triggered,
            "blocked": blocked,
            "observations": observations,
            "ready_targets": ready_target_count,
            "portfolio_targets": portfolio_target_count,
            "ready_omitted": ready_omitted,
            "portfolio_omitted": portfolio_omitted,
            **(
                {"portfolio_error": portfolio_error}
                if portfolio_error is not None
                else {}
            ),
        }

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        """Run one poll and dispatch newly persisted urgent events immediately."""
        result = self._run_once(now)
        if self.notifier and (
            result.get("triggered")
            or result.get("portfolio_triggered")
            or result.get("blocked")
        ):
            current = self._local_now(now or self.now_provider())
            try:
                self.notifier(current.date().isoformat())
            except Exception as exc:
                logger.error("盘中提醒投递器异常 (%s)", type(exc).__name__)
        return result

    @staticmethod
    def _next_poll(now: datetime) -> datetime | None:
        local = IntradayWatchMonitor._local_now(now)
        current_time = local.time().replace(tzinfo=None)
        morning_ready = wall_time(9, 35, BAR_PUBLISH_GRACE_SECONDS)
        morning_close_ready = wall_time(11, 30, BAR_PUBLISH_GRACE_SECONDS)
        afternoon_ready = wall_time(13, 5, BAR_PUBLISH_GRACE_SECONDS)
        if current_time < morning_ready:
            return local.replace(
                hour=9,
                minute=35,
                second=BAR_PUBLISH_GRACE_SECONDS,
                microsecond=0,
            )
        if morning_close_ready < current_time < afternoon_ready:
            return local.replace(
                hour=13,
                minute=5,
                second=BAR_PUBLISH_GRACE_SECONDS,
                microsecond=0,
            )
        if current_time >= wall_time(15, 5):
            return None
        expected = IntradayWatchMonitor._expected_bar_time(local)
        ready_at = expected + timedelta(seconds=BAR_PUBLISH_GRACE_SECONDS)
        candidate = (
            ready_at
            if local < ready_at
            else expected
            + timedelta(minutes=5, seconds=BAR_PUBLISH_GRACE_SECONDS)
        )
        candidate_time = candidate.time().replace(tzinfo=None)
        if morning_close_ready < candidate_time < afternoon_ready:
            candidate = candidate.replace(
                hour=13,
                minute=5,
                second=BAR_PUBLISH_GRACE_SECONDS,
            )
        if candidate.time().replace(tzinfo=None) >= wall_time(15, 5):
            return None
        return candidate

    def run_until_close(self) -> dict[str, Any]:
        """09:35--15:05 运行，午休不请求行情，收盘后自动退出。"""
        runs = 0
        triggered = 0
        portfolio_triggered = 0
        blocked = 0
        ready_omitted = 0
        portfolio_omitted = 0
        last_polled_bar: datetime | None = None
        current = self._local_now(self.now_provider())
        session_date = current.date()
        with closing(sqlite3.connect(self.db_path)) as conn:
            if not self._is_open_day(conn, session_date.isoformat()):
                return {
                    "success": True,
                    "status": "market_closed",
                    "runs": 0,
                    "triggered": 0,
                    "blocked": 0,
                }
        while True:
            if current.date() != session_date:
                logger.warning("盘中监控跨交易日存活，安全退出等待 launchd 重启")
                break
            next_poll = self._next_poll(current)
            if next_poll is None:
                break
            expected_bar = self._expected_bar_time(current)
            ready_at = expected_bar + timedelta(seconds=BAR_PUBLISH_GRACE_SECONDS)
            if (
                self._is_session(current)
                and current >= ready_at
                and expected_bar != last_polled_bar
            ):
                result = self.run_once(current)
                last_polled_bar = expected_bar
                runs += 1
                triggered += int(result.get("triggered", 0))
                portfolio_triggered += int(
                    result.get("portfolio_triggered", 0)
                )
                blocked += int(result.get("blocked", 0))
                ready_omitted = max(
                    ready_omitted,
                    int(result.get("ready_omitted", 0)),
                )
                portfolio_omitted = max(
                    portfolio_omitted,
                    int(result.get("portfolio_omitted", 0)),
                )
                current = self._local_now(self.now_provider())
                next_poll = self._next_poll(current)
                if next_poll is None:
                    break
            sleep_seconds = max((next_poll - current).total_seconds(), 1.0)
            self.sleep(sleep_seconds)
            current = self._local_now(self.now_provider())
        if self.adapter is not None:
            try:
                self.adapter.disconnect()
            except Exception:
                logger.debug("盘中监控断开 TDX 连接失败", exc_info=True)
        return {
            "success": True,
            "status": "completed",
            "runs": runs,
            "triggered": triggered,
            "portfolio_triggered": portfolio_triggered,
            "blocked": blocked,
            "ready_omitted": ready_omitted,
            "portfolio_omitted": portfolio_omitted,
        }


def dispatch_intraday_notifications(dispatcher: Any, trade_date: str) -> None:
    """Dispatch Watch and Portfolio emails independently after DB commits."""
    for method_name in (
        "dispatch_immediate",
        "dispatch_portfolio_exit_immediate",
    ):
        try:
            getattr(dispatcher, method_name)(trade_date)
        except Exception as exc:
            logger.error(
                "盘中提醒通道 %s 异常 (%s)",
                method_name,
                type(exc).__name__,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="EvoAlpha 盘中低吸 READY 小名单监控")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="只执行当前五分钟轮询")
    mode.add_argument("--run-until-close", action="store_true", help="运行至 15:05")
    parser.add_argument("--db-path", help="可选 SQLite 路径")
    args = parser.parse_args()
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    dispatcher = WatchNotificationDispatcher(args.db_path)
    monitor = IntradayWatchMonitor(
        db_path=args.db_path,
        notifier=lambda trade_date: dispatch_intraday_notifications(
            dispatcher,
            trade_date,
        ),
    )
    result = monitor.run_until_close() if args.run_until_close else monitor.run_once()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
