"""
EvoAlpha OS - 四类买点信号计算器

产出 timing_signals_daily：
- pocket_pivot
- breakout
- supply_exhaustion
- earnings_gap
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from quant_engine.common import setup_logger


DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
logger = setup_logger(__name__)


class TimingSignalCalculator:
    """四类买点信号计算器。"""

    target_table = "timing_signals_daily"

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def _create_table(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS timing_signals_daily (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_score REAL,
                entry_price REAL,
                stop_loss_price REAL,
                stop_loss_pct REAL,
                suggested_position_pct REAL,
                risk_amount_pct REAL,
                is_chase_risk INTEGER DEFAULT 0,
                is_invalidated INTEGER DEFAULT 0,
                invalidation_reason TEXT,
                cooling_until TEXT,
                detail TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_timing_signal_date
                ON timing_signals_daily (trade_date);
            CREATE INDEX IF NOT EXISTS idx_timing_signal_symbol
                ON timing_signals_daily (symbol);
            """
        )
        conn.commit()

    def _get_output_dates(self, conn: sqlite3.Connection, mode: str, days: int) -> list[str]:
        if mode == "init":
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM stock_daily_prices ORDER BY trade_date"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM stock_daily_prices
                    ORDER BY trade_date DESC
                    LIMIT ?
                )
                ORDER BY trade_date
                """,
                (days,),
            ).fetchall()
        return [str(row[0])[:10] for row in rows]

    def _load_source(self, conn: sqlite3.Connection, dates: list[str], load_start: str) -> pd.DataFrame:
        date_values = ",".join("?" for _ in dates)
        lifecycle = pd.read_sql_query(
            f"""
            SELECT trade_date, symbol, name, sector_name, lifecycle_state, state_rank,
                   market_state, industry_state, rps_20, rps_50, rps_120, rps_250,
                   structure_score
            FROM stock_lifecycle_state
            WHERE trade_date IN ({date_values})
              AND state_rank >= 3
            """,
            conn,
            params=dates,
        )
        if lifecycle.empty:
            return lifecycle

        symbols = lifecycle["symbol"].drop_duplicates().tolist()
        symbol_values = ",".join("?" for _ in symbols)
        kline = pd.read_sql_query(
            f"""
            SELECT symbol, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate
            FROM stock_daily_prices
            WHERE trade_date >= ?
              AND symbol IN ({symbol_values})
            ORDER BY symbol, trade_date
            """,
            conn,
            params=[load_start, *symbols],
        )
        finance = pd.read_sql_query(
            f"""
            SELECT code AS symbol, report_date, net_profit_up, revenue_up, roe
            FROM stock_finance_summary
            WHERE code IN ({symbol_values})
            """,
            conn,
            params=symbols,
        )
        for df in (lifecycle, kline, finance):
            if not df.empty:
                date_column = "trade_date" if "trade_date" in df.columns else "report_date"
                df[date_column] = pd.to_datetime(df[date_column], format="mixed").dt.strftime("%Y-%m-%d")

        for column in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]:
            if column in kline.columns:
                kline[column] = pd.to_numeric(kline[column], errors="coerce")

        kline = kline.sort_values(["symbol", "trade_date"])
        grouped = kline.groupby("symbol", group_keys=False)
        for period in (10, 20, 50):
            kline[f"ma_{period}"] = grouped["close"].rolling(period, min_periods=max(5, period // 2)).mean().reset_index(level=0, drop=True)
        kline["volume_ma20"] = grouped["volume"].rolling(20, min_periods=5).mean().reset_index(level=0, drop=True)
        kline["high_10_prev"] = grouped["high"].rolling(10, min_periods=8).max().shift(1).reset_index(level=0, drop=True)
        kline["high_20_prev"] = grouped["high"].rolling(20, min_periods=10).max().shift(1).reset_index(level=0, drop=True)
        kline["prev_high"] = grouped["high"].shift(1)
        kline["low_20"] = grouped["low"].rolling(20, min_periods=10).min().reset_index(level=0, drop=True)
        kline["high_60"] = grouped["high"].rolling(60, min_periods=20).max().reset_index(level=0, drop=True)
        prev_close = grouped["close"].shift(1)
        tr = pd.concat(
            [
                kline["high"] - kline["low"],
                (kline["high"] - prev_close).abs(),
                (kline["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        kline["true_range"] = tr
        kline["atr_20"] = grouped["true_range"].rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
        kline["close_position"] = (
            (kline["close"] - kline["low"]) / (kline["high"] - kline["low"]).replace(0, np.nan)
        )
        kline["down_volume"] = kline["volume"].where(kline["close"] < kline["open"])
        kline["max_down_volume_10"] = grouped["down_volume"].rolling(10, min_periods=3).max().reset_index(level=0, drop=True)
        kline["volume_down_3"] = grouped["volume"].transform(lambda s: s.diff().lt(0).rolling(3, min_periods=3).sum() == 3)
        kline["gap_pct"] = (kline["open"] - prev_close) / prev_close.replace(0, np.nan)
        kline["gap_up_5"] = kline["gap_pct"] >= 0.05
        kline["gap_low"] = prev_close
        kline["gap_recent"] = grouped["gap_up_5"].rolling(10, min_periods=1).max().reset_index(level=0, drop=True).fillna(0).astype(bool)
        kline["gap_floor"] = kline["gap_low"].where(kline["gap_up_5"])
        kline["gap_floor"] = grouped["gap_floor"].ffill()
        kline["low_5"] = grouped["low"].rolling(5, min_periods=1).min().reset_index(level=0, drop=True)
        kline["gap_not_filled"] = kline["low_5"] > kline["gap_floor"]

        latest_finance = (
            finance.sort_values(["symbol", "report_date"])
            .drop_duplicates("symbol", keep="last")
            if not finance.empty else pd.DataFrame(columns=["symbol", "net_profit_up", "revenue_up", "roe"])
        )

        daily = kline[kline["trade_date"].isin(dates)].merge(
            lifecycle, on=["trade_date", "symbol"], how="inner"
        )
        daily = daily.merge(latest_finance, on="symbol", how="left")
        return daily

    def _position_pct(self, stop_loss_pct: float, score: float) -> float:
        if not stop_loss_pct or stop_loss_pct <= 0:
            return 0.0
        raw = 1.0 / (stop_loss_pct * 100.0) * 100.0
        cap = 15.0 if score >= 85 else 10.0
        return round(max(5.0, min(raw, cap)), 2)

    def _make_signal(self, row: pd.Series, signal_type: str, score: float, reason: str) -> dict | None:
        entry = float(row["close"])
        structural_stop = float(row["low_20"]) * 0.99 if pd.notna(row.get("low_20")) else entry * 0.92
        atr_stop = entry - 2 * float(row["atr_20"]) if pd.notna(row.get("atr_20")) else entry * 0.92
        max_loss_stop = entry * 0.90
        stop = max(structural_stop, atr_stop, max_loss_stop)
        stop_loss_pct = max(0.0, (entry - stop) / entry)
        if stop_loss_pct <= 0 or stop_loss_pct > 0.10:
            return None
        trade_date = str(row["trade_date"])
        symbol = str(row["symbol"])
        return {
            "signal_id": f"{trade_date}_{symbol}_{signal_type}",
            "symbol": symbol,
            "trade_date": trade_date,
            "signal_type": signal_type,
            "signal_score": round(float(score), 2),
            "entry_price": round(entry, 3),
            "stop_loss_price": round(stop, 3),
            "stop_loss_pct": round(stop_loss_pct, 4),
            "suggested_position_pct": self._position_pct(stop_loss_pct, score),
            "risk_amount_pct": 1.0,
            "is_chase_risk": 0,
            "is_invalidated": 0,
            "invalidation_reason": None,
            "cooling_until": (
                pd.to_datetime(trade_date).date() + timedelta(days=7)
            ).strftime("%Y-%m-%d"),
            "detail": json.dumps(
                {
                    "reason": reason,
                    "market_state": row.get("market_state"),
                    "industry_state": row.get("industry_state"),
                    "lifecycle_state": row.get("lifecycle_state"),
                    "rps_20": row.get("rps_20"),
                    "rps_50": row.get("rps_50"),
                    "structure_score": row.get("structure_score"),
                },
                ensure_ascii=False,
            ),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _load_trade_calendar(
        self,
        conn: sqlite3.Connection,
        start_date: str,
        end_date: str,
    ) -> list[str]:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM stock_daily_prices
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            (start_date, end_date),
        ).fetchall()
        return [str(row[0])[:10] for row in rows]

    def _load_cooldown_history(
        self,
        conn: sqlite3.Connection,
        start_date: str,
        first_output_date: str,
    ) -> pd.DataFrame:
        """Load prior signals so the 7-trading-day cooldown spans daily windows."""
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (self.target_table,),
        ).fetchone():
            return pd.DataFrame(columns=["symbol", "signal_type", "trade_date"])

        return pd.read_sql_query(
            """
            SELECT symbol, signal_type, trade_date
            FROM timing_signals_daily
            WHERE trade_date >= ?
              AND trade_date < ?
              AND COALESCE(is_invalidated, 0) = 0
            ORDER BY symbol, signal_type, trade_date
            """,
            conn,
            params=(start_date, first_output_date),
        )

    def _compute_signals(
        self,
        df: pd.DataFrame,
        cooldown_history: pd.DataFrame | None = None,
        trade_calendar: list[str] | None = None,
    ) -> pd.DataFrame:
        signals = []
        market_m2 = df["market_state"].isin(["M2", "M3", "M4"])
        market_m3 = df["market_state"].isin(["M3", "M4"])
        industry_i3 = df["industry_state"].isin(["I3", "I4"])
        close_green = df["close"] > df["open"]
        near_high_close = df["close_position"].fillna(0) >= 0.7
        structure_ok = df["structure_score"].fillna(0) >= 70

        pocket = (
            market_m2
            & industry_i3
            & df["lifecycle_state"].isin(["S3", "S4"])
            & close_green
            & near_high_close
            & (df["volume"] > df["max_down_volume_10"].fillna(0))
            & ((df["close"] >= df["ma_10"]) | (df["close"] >= df["ma_20"]))
            & (((df["close"] - df["ma_20"]).abs() / df["close"].replace(0, np.nan)) <= 0.10)
            & structure_ok
        )
        breakout = (
            market_m3
            & industry_i3
            & (df["lifecycle_state"] == "S4")
            & (df["rps_20"].fillna(0) >= 90)
            & (df["rps_50"].fillna(0) >= 85)
            & structure_ok
            & (df["close"] > df["high_20_prev"])
            & (df["volume"] > df["volume_ma20"].fillna(0) * 1.5)
            & near_high_close
        )
        pullback_from_high = (df["high_60"] - df["close"]) / df["high_60"].replace(0, np.nan)
        supply = (
            market_m3
            & industry_i3
            & (pullback_from_high >= 0.05)
            & (pullback_from_high <= 0.15)
            & ((df["close"] >= df["ma_20"]) | (df["close"] >= df["ma_50"]))
            & df["volume_down_3"].fillna(False)
            & (df["rps_20"].fillna(0) >= 80)
            & (df["rps_50"].fillna(0) >= 85)
            & ((df["close"] > df["prev_high"]) | ((df["close"] > df["ma_10"]) & close_green))
        )
        earnings = (
            market_m2
            & industry_i3
            & df["gap_recent"].fillna(False)
            & df["gap_not_filled"].fillna(False)
            & (df["close"] > df["high_10_prev"])
            & (df["volume"] > df["volume_ma20"].fillna(0) * 1.3)
            & ((df["net_profit_up"].fillna(0) >= 30) | (df["revenue_up"].fillna(0) >= 20))
            & (df["rps_20"].fillna(0) >= 80)
        )

        rules = [
            ("pocket_pivot", pocket, 78, "口袋买点：放量转强并贴近均线"),
            ("breakout", breakout, 88, "强市场突破：放量突破平台高点"),
            ("supply_exhaustion", supply, 82, "抛压衰竭：缩量回调后重新转强"),
            ("earnings_gap", earnings, 85, "业绩跳空后二次突破"),
        ]
        for signal_type, mask, base_score, reason in rules:
            for _, row in df[mask].iterrows():
                score = base_score + min(10, max(0, float(row.get("structure_score") or 0) - 70) * 0.3)
                signal = self._make_signal(row, signal_type, score, reason)
                if signal:
                    signals.append(signal)

        if not signals:
            return pd.DataFrame()
        result = pd.DataFrame(signals)
        result = result.sort_values(["symbol", "signal_type", "trade_date"])
        last_seen: dict[tuple[str, str], pd.Timestamp] = {}
        rank_map = {trade_date: idx for idx, trade_date in enumerate(trade_calendar or [])}

        if cooldown_history is not None and not cooldown_history.empty:
            history = cooldown_history.sort_values(["symbol", "signal_type", "trade_date"])
            for _, row in history.iterrows():
                key = (str(row["symbol"]), str(row["signal_type"]))
                last_seen[key] = pd.to_datetime(row["trade_date"])

        keep = []
        for _, row in result.iterrows():
            key = (row["symbol"], row["signal_type"])
            trade_dt = pd.to_datetime(row["trade_date"])
            prev = last_seen.get(key)
            if prev is not None:
                trade_rank = rank_map.get(str(row["trade_date"]))
                prev_rank = rank_map.get(prev.strftime("%Y-%m-%d"))
                if trade_rank is not None and prev_rank is not None:
                    within_cooldown = (trade_rank - prev_rank) < 7
                else:
                    within_cooldown = (trade_dt - prev).days < 7
                if within_cooldown:
                    keep.append(False)
                    continue
            last_seen[key] = trade_dt
            keep.append(True)
        return result[keep].copy()

    def _save(self, conn: sqlite3.Connection, df: pd.DataFrame, dates: list[str]) -> None:
        with conn:
            for trade_date in dates:
                conn.execute("DELETE FROM timing_signals_daily WHERE trade_date = ?", (trade_date,))
        if df.empty:
            logger.info("timing_signals_daily: no rows to save")
            return
        df.to_sql(self.target_table, conn, if_exists="append", index=False, method="multi", chunksize=1000)
        logger.info(
            "timing_signals_daily: saved %s rows, range %s..%s",
            f"{len(df):,}",
            df["trade_date"].min(),
            df["trade_date"].max(),
        )

    def run_daily(self, days: int = 3) -> dict:
        return self.run(mode="daily", days=days)

    def run_init(self, days: int = 3) -> dict:
        return self.run(mode="init", days=days)

    def run(self, mode: str = "daily", days: int = 3) -> dict:
        start = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                self._create_table(conn)
                dates = self._get_output_dates(conn, mode, days)
                if not dates:
                    return {"success": False, "error": "无交易日期", "records": 0}
                load_start = (pd.to_datetime(dates[0]).date() - timedelta(days=90)).strftime("%Y-%m-%d")
                cooldown_start = (pd.to_datetime(dates[0]).date() - timedelta(days=30)).strftime("%Y-%m-%d")
                logger.info("🚀 [TimingSignalCalculator] mode=%s, dates=%s..%s", mode, dates[0], dates[-1])
                source = self._load_source(conn, dates, load_start)
                cooldown_history = self._load_cooldown_history(conn, cooldown_start, dates[0])
                trade_calendar = self._load_trade_calendar(conn, cooldown_start, dates[-1])
                result = (
                    self._compute_signals(source, cooldown_history, trade_calendar)
                    if not source.empty
                    else pd.DataFrame()
                )
                self._save(conn, result, dates)
            return {"success": True, "records": len(result), "elapsed": time.time() - start}
        except Exception as e:
            logger.error(f"❌ 买点信号计算失败: {e}")
            return {"success": False, "error": str(e), "records": 0, "elapsed": time.time() - start}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha 四类买点信号计算器")
    parser.add_argument("--mode", choices=("daily", "init"), default="daily")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    result = TimingSignalCalculator().run(mode=args.mode, days=args.days)
    raise SystemExit(0 if result.get("success") else 1)
