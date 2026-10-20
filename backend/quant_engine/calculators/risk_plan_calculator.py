"""
EvoAlpha OS - 风控交易计划计算器

基于 timing_signals_daily 生成 trade_plans，落地初始止损、仓位和执行约束。
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd


CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from quant_engine.common import setup_logger


DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
logger = setup_logger(__name__)


class RiskPlanCalculator:
    """交易计划与基础风控计算。"""

    target_table = "trade_plans"

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def _create_table(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trade_plans (
                plan_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                signal_trade_date TEXT,
                symbol TEXT NOT NULL,
                signal_type TEXT,
                entry_price REAL,
                initial_stop_price REAL,
                stop_loss_pct REAL,
                risk_amount_pct REAL,
                suggested_position_pct REAL,
                max_position_pct REAL,
                time_stop_10d_action TEXT,
                time_stop_20d_action TEXT,
                market_risk_action TEXT,
                industry_risk_action TEXT,
                current_stop_price REAL,
                days_since_signal INTEGER,
                max_high_since_entry REAL,
                latest_close REAL,
                r_multiple REAL,
                risk_status TEXT,
                recommended_action TEXT,
                add_allowed INTEGER DEFAULT 0,
                reduce_pct REAL DEFAULT 0,
                exit_reason TEXT,
                plan_status TEXT DEFAULT 'active',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trade_plans_date
                ON trade_plans (trade_date);
            CREATE INDEX IF NOT EXISTS idx_trade_plans_symbol
                ON trade_plans (symbol);
            """
        )
        self._ensure_columns(conn)
        conn.commit()

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trade_plans)").fetchall()}
        new_columns = {
            "signal_trade_date": "TEXT",
            "current_stop_price": "REAL",
            "days_since_signal": "INTEGER",
            "max_high_since_entry": "REAL",
            "latest_close": "REAL",
            "r_multiple": "REAL",
            "risk_status": "TEXT",
            "recommended_action": "TEXT",
            "add_allowed": "INTEGER DEFAULT 0",
            "reduce_pct": "REAL DEFAULT 0",
            "exit_reason": "TEXT",
        }
        for column_name, column_type in new_columns.items():
            if column_name not in columns:
                conn.execute(f"ALTER TABLE trade_plans ADD COLUMN {column_name} {column_type}")

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _latest_price_date(self, conn: sqlite3.Connection) -> str | None:
        if not self._table_exists(conn, "stock_daily_prices"):
            return None
        row = conn.execute("SELECT MAX(substr(trade_date, 1, 10)) FROM stock_daily_prices").fetchone()
        return str(row[0])[:10] if row and row[0] else None

    def _load_kline(self, conn: sqlite3.Connection, signals: pd.DataFrame, end_date: str) -> pd.DataFrame:
        if not self._table_exists(conn, "stock_daily_prices") or signals.empty:
            return pd.DataFrame()

        symbols = signals["symbol"].dropna().drop_duplicates().tolist()
        if not symbols:
            return pd.DataFrame()

        symbol_values = ",".join("?" for _ in symbols)
        start_date = str(signals["trade_date"].min())[:10]
        kline = pd.read_sql_query(
            f"""
            SELECT symbol, trade_date, high, low, close
            FROM stock_daily_prices
            WHERE symbol IN ({symbol_values})
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY symbol, trade_date
            """,
            conn,
            params=[*symbols, start_date, end_date],
        )
        if not kline.empty:
            kline["trade_date"] = pd.to_datetime(kline["trade_date"], format="mixed").dt.strftime("%Y-%m-%d")
            for column in ("high", "low", "close"):
                kline[column] = pd.to_numeric(kline[column], errors="coerce")
        return kline

    def _dynamic_fields(self, signal: pd.Series, kline_by_symbol: dict[str, pd.DataFrame]) -> dict[str, object]:
        def number(value: object, fallback: float = 0.0) -> float:
            parsed = pd.to_numeric(value, errors="coerce")
            return fallback if pd.isna(parsed) else float(parsed)

        symbol = str(signal["symbol"])
        signal_date = str(signal["trade_date"])[:10]
        entry = number(signal.get("entry_price"), 0.0)
        initial_stop = number(signal.get("stop_loss_price"), entry * 0.92)
        risk_per_share = max(entry - initial_stop, entry * 0.01, 0.01)

        rows = kline_by_symbol.get(symbol)
        if rows is None or rows.empty:
            latest_close = entry
            max_high = entry
            days_since_signal = 0
            low_5 = initial_stop
            low_10 = initial_stop
        else:
            window = rows[rows["trade_date"] >= signal_date].copy()
            if window.empty:
                latest_close = entry
                max_high = entry
                days_since_signal = 0
                low_5 = initial_stop
                low_10 = initial_stop
            else:
                latest = window.iloc[-1]
                latest_close = float(latest["close"]) if pd.notna(latest["close"]) else entry
                max_high = float(window["high"].max()) if pd.notna(window["high"].max()) else entry
                days_since_signal = max(int(window["trade_date"].nunique()) - 1, 0)
                low_5 = float(window.tail(5)["low"].min()) if pd.notna(window.tail(5)["low"].min()) else initial_stop
                low_10 = float(window.tail(10)["low"].min()) if pd.notna(window.tail(10)["low"].min()) else initial_stop

        r_multiple = (latest_close - entry) / risk_per_share
        current_stop = initial_stop
        if r_multiple >= 1:
            current_stop = max(current_stop, entry)
        if r_multiple >= 2:
            current_stop = max(current_stop, entry + risk_per_share)
        if r_multiple >= 3:
            current_stop = max(current_stop, low_10)
        if r_multiple >= 5:
            current_stop = max(current_stop, low_5)

        max_gain_pct = (max_high - entry) / entry if entry else 0
        market_state = signal.get("market_state")
        industry_state = signal.get("industry_state")
        add_allowed = int(r_multiple >= 1 and market_state != "M0" and industry_state not in {"I0", "I1"})
        reduce_pct = 0.0
        risk_status = "normal"
        recommended_action = "hold"
        exit_reason = None
        plan_status = "active"

        if latest_close <= current_stop:
            risk_status = "stop_triggered"
            recommended_action = "exit"
            reduce_pct = 100.0
            exit_reason = "收盘价触发动态止损"
            plan_status = "exit_pending"
        elif market_state == "M0":
            risk_status = "market_risk"
            recommended_action = "reduce_market"
            reduce_pct = 50.0
            exit_reason = "市场进入M0，触发总仓降风险"
            plan_status = "reduce"
        elif industry_state in {"I0", "I1"}:
            risk_status = "industry_risk"
            recommended_action = "reduce_industry"
            reduce_pct = 50.0
            exit_reason = "行业进入I0/I1，触发个股减仓"
            plan_status = "reduce"
        elif days_since_signal >= 20 and max_gain_pct < 0.05:
            risk_status = "time_stop_20d"
            recommended_action = "exit_time_stop"
            reduce_pct = 100.0
            exit_reason = "20个交易日未达到+5%"
            plan_status = "exit_pending"
        elif days_since_signal >= 10 and max_gain_pct < 0.05:
            risk_status = "time_stop_10d"
            recommended_action = "reduce_time_stop"
            reduce_pct = 50.0
            exit_reason = "10个交易日未达到+5%"
            plan_status = "reduce"
        elif add_allowed:
            risk_status = "profitable"
            recommended_action = "hold_or_add_on_new_signal"

        return {
            "current_stop_price": round(current_stop, 3),
            "days_since_signal": days_since_signal,
            "max_high_since_entry": round(max_high, 3),
            "latest_close": round(latest_close, 3),
            "r_multiple": round(r_multiple, 2),
            "risk_status": risk_status,
            "recommended_action": recommended_action,
            "add_allowed": add_allowed,
            "reduce_pct": reduce_pct,
            "exit_reason": exit_reason,
            "plan_status": plan_status,
        }

    def run_daily(self, days: int = 3) -> dict:
        return self.run(mode="daily", days=days)

    def run_init(self, days: int = 3) -> dict:
        return self.run(mode="init", days=days)

    def run(self, mode: str = "daily", days: int = 3) -> dict:
        start = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                self._create_table(conn)
                if not self._table_exists(conn, "timing_signals_daily"):
                    return {"success": True, "records": 0, "elapsed": time.time() - start}
                latest_date = self._latest_price_date(conn)
                if not latest_date:
                    return {"success": True, "records": 0, "elapsed": time.time() - start}
                existing_created_at = {
                    str(row[0]): row[1]
                    for row in conn.execute(
                        "SELECT plan_id, created_at FROM trade_plans WHERE plan_id IS NOT NULL"
                    ).fetchall()
                }
                signals = pd.read_sql_query(
                    """
                    SELECT s.*, l.market_state, l.industry_state, l.structure_score
                    FROM timing_signals_daily s
                    LEFT JOIN stock_lifecycle_state l
                      ON l.trade_date = ? AND s.symbol = l.symbol
                    WHERE COALESCE(s.is_invalidated, 0) = 0
                    """,
                    conn,
                    params=(latest_date,),
                )
                if signals.empty:
                    with conn:
                        conn.execute("DELETE FROM trade_plans")
                    return {"success": True, "records": 0, "elapsed": time.time() - start}

                for column in (
                    "entry_price",
                    "stop_loss_price",
                    "stop_loss_pct",
                    "risk_amount_pct",
                    "suggested_position_pct",
                    "structure_score",
                ):
                    signals[column] = pd.to_numeric(signals[column], errors="coerce")
                signals["trade_date"] = pd.to_datetime(
                    signals["trade_date"],
                    format="mixed",
                ).dt.strftime("%Y-%m-%d")

                kline = self._load_kline(conn, signals, latest_date)
                kline_by_symbol = {
                    symbol: group.sort_values("trade_date")
                    for symbol, group in kline.groupby("symbol")
                } if not kline.empty else {}
                dynamic_rows = signals.apply(
                    lambda row: self._dynamic_fields(row, kline_by_symbol),
                    axis=1,
                    result_type="expand",
                )

                max_position = signals["structure_score"].fillna(0).apply(lambda score: 15.0 if score >= 85 else 10.0)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                plan_ids = signals["signal_id"].map(lambda x: f"plan_{x}")
                plans = pd.DataFrame(
                    {
                        "plan_id": plan_ids,
                        "signal_id": signals["signal_id"],
                        "trade_date": latest_date,
                        "signal_trade_date": signals["trade_date"],
                        "symbol": signals["symbol"],
                        "signal_type": signals["signal_type"],
                        "entry_price": signals["entry_price"],
                        "initial_stop_price": signals["stop_loss_price"],
                        "stop_loss_pct": signals["stop_loss_pct"],
                        "risk_amount_pct": signals["risk_amount_pct"],
                        "suggested_position_pct": signals["suggested_position_pct"],
                        "max_position_pct": max_position,
                        "time_stop_10d_action": "10日未达到+5%则减仓50%",
                        "time_stop_20d_action": "20日未走出趋势则清仓",
                        "market_risk_action": signals["market_state"].map(
                            lambda s: "市场M0触发总仓减半" if s == "M0" else "正常"
                        ),
                        "industry_risk_action": signals["industry_state"].map(
                            lambda s: "行业I0/I1触发减仓50%" if s in {"I0", "I1"} else "正常"
                        ),
                        "current_stop_price": dynamic_rows["current_stop_price"],
                        "days_since_signal": dynamic_rows["days_since_signal"],
                        "max_high_since_entry": dynamic_rows["max_high_since_entry"],
                        "latest_close": dynamic_rows["latest_close"],
                        "r_multiple": dynamic_rows["r_multiple"],
                        "risk_status": dynamic_rows["risk_status"],
                        "recommended_action": dynamic_rows["recommended_action"],
                        "add_allowed": dynamic_rows["add_allowed"],
                        "reduce_pct": dynamic_rows["reduce_pct"],
                        "exit_reason": dynamic_rows["exit_reason"],
                        "plan_status": dynamic_rows["plan_status"],
                        "created_at": plan_ids.map(lambda value: existing_created_at.get(value) or now),
                        "updated_at": now,
                    }
                )
                with conn:
                    conn.execute("DELETE FROM trade_plans")
                plans.to_sql("trade_plans", conn, if_exists="append", index=False, method="multi", chunksize=1000)
            return {"success": True, "records": len(plans), "elapsed": time.time() - start}
        except Exception as e:
            logger.error(f"❌ 风控交易计划失败: {e}")
            return {"success": False, "error": str(e), "records": 0, "elapsed": time.time() - start}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha 风控交易计划计算器")
    parser.add_argument("--mode", choices=("daily", "init"), default="daily")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    result = RiskPlanCalculator().run(mode=args.mode, days=args.days)
    raise SystemExit(0 if result.get("success") else 1)
