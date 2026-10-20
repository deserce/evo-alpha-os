"""
EvoAlpha OS - 结构健康评分计算器

根据蓝图 Phase 6，基于 K 线、均线、波动收缩、回撤位置和 RPS 持续性
生成 structure_health_daily，作为 S4 准备池输入。
"""

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
from quant_engine.utils.calculation_dates import resolve_dense_output_dates


DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
logger = setup_logger(__name__)


def _normalize_date(df: pd.DataFrame, column: str = "trade_date") -> pd.DataFrame:
    if not df.empty and column in df.columns:
        df[column] = pd.to_datetime(df[column], format="mixed").dt.strftime("%Y-%m-%d")
    return df


class StructureHealthCalculator:
    """结构健康评分计算器。"""

    target_table = "structure_health_daily"

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def _create_table(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS structure_health_daily (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                close REAL,
                ma_10 REAL,
                ma_20 REAL,
                ma_50 REAL,
                ma_120 REAL,
                ma_250 REAL,
                high_60 REAL,
                high_120 REAL,
                high_250 REAL,
                pullback_from_high_60 REAL,
                pullback_from_high_120 REAL,
                atr_10 REAL,
                atr_50 REAL,
                atr_ratio_10_50 REAL,
                volume_ratio_20 REAL,
                rps20_avg_10 REAL,
                trend_score REAL,
                pullback_score REAL,
                volatility_score REAL,
                rps_score REAL,
                support_score REAL,
                structure_score REAL,
                structure_state TEXT,
                is_structure_healthy INTEGER,
                created_at TEXT,
                PRIMARY KEY (trade_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_structure_health_date
                ON structure_health_daily (trade_date);
            CREATE INDEX IF NOT EXISTS idx_structure_health_symbol
                ON structure_health_daily (symbol);
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

    def _load_data(self, conn: sqlite3.Connection, load_start: str) -> pd.DataFrame:
        kline = pd.read_sql_query(
            """
            SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover_rate
            FROM stock_daily_prices
            WHERE trade_date >= ?
            ORDER BY symbol, trade_date
            """,
            conn,
            params=(load_start,),
        )
        kline = _normalize_date(kline)
        if kline.empty:
            return kline

        rps = pd.read_sql_query(
            """
            SELECT symbol, trade_date, rps_20, rps_50, rps_120, rps_250
            FROM quant_feature_rps
            WHERE trade_date >= ?
            """,
            conn,
            params=(load_start,),
        )
        rps = _normalize_date(rps)
        if not rps.empty:
            kline = kline.merge(rps, on=["symbol", "trade_date"], how="left")
        else:
            for column in ("rps_20", "rps_50", "rps_120", "rps_250"):
                kline[column] = np.nan
        return kline

    def _compute(self, df: pd.DataFrame, output_dates: list[str]) -> pd.DataFrame:
        numeric_cols = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover_rate",
            "rps_20",
            "rps_50",
            "rps_120",
            "rps_250",
        ]
        for column in numeric_cols:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.sort_values(["symbol", "trade_date"])
        grouped = df.groupby("symbol", group_keys=False)
        for period in (10, 20, 50, 120, 250):
            df[f"ma_{period}"] = grouped["close"].rolling(period, min_periods=max(5, period // 3)).mean().reset_index(level=0, drop=True)
        for period in (60, 120, 250):
            df[f"high_{period}"] = grouped["high"].rolling(period, min_periods=max(20, period // 3)).max().reset_index(level=0, drop=True)

        prev_close = grouped["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["true_range"] = tr
        df["atr_10"] = grouped["true_range"].rolling(10, min_periods=5).mean().reset_index(level=0, drop=True)
        df["atr_50"] = grouped["true_range"].rolling(50, min_periods=20).mean().reset_index(level=0, drop=True)
        df["atr_ratio_10_50"] = df["atr_10"] / df["atr_50"].replace(0, np.nan)
        df["volume_ma20"] = grouped["volume"].rolling(20, min_periods=5).mean().reset_index(level=0, drop=True)
        df["volume_ratio_20"] = df["volume"] / df["volume_ma20"].replace(0, np.nan)
        df["rps20_avg_10"] = grouped["rps_20"].rolling(10, min_periods=3).mean().reset_index(level=0, drop=True)
        df["pullback_from_high_60"] = (df["high_60"] - df["close"]) / df["high_60"].replace(0, np.nan)
        df["pullback_from_high_120"] = (df["high_120"] - df["close"]) / df["high_120"].replace(0, np.nan)

        trend_conditions = [
            df["close"] > df["ma_20"],
            df["ma_20"] > df["ma_50"],
            df["ma_50"] > df["ma_120"],
            df["close"] > df["ma_50"],
        ]
        df["trend_score"] = sum(condition.fillna(False).astype(int) for condition in trend_conditions) * 7.5

        pullback = df["pullback_from_high_60"].fillna(1)
        df["pullback_score"] = np.select(
            [
                (pullback >= 0.03) & (pullback <= 0.18),
                (pullback > 0.18) & (pullback <= 0.30),
                pullback < 0.03,
            ],
            [25, 15, 10],
            default=0,
        )

        atr_ratio = df["atr_ratio_10_50"].replace([np.inf, -np.inf], np.nan)
        volume_ratio = df["volume_ratio_20"].replace([np.inf, -np.inf], np.nan)
        df["volatility_score"] = (
            ((atr_ratio <= 0.85).fillna(False).astype(int) * 10)
            + ((volume_ratio <= 1.15).fillna(False).astype(int) * 5)
        )

        df["rps_score"] = np.select(
            [
                df["rps20_avg_10"] >= 85,
                df["rps20_avg_10"] >= 80,
                df["rps20_avg_10"] >= 70,
            ],
            [15, 12, 8],
            default=0,
        )

        support_distance = (df["close"] - df["ma_50"]) / df["close"].replace(0, np.nan)
        df["support_score"] = np.select(
            [
                support_distance.abs() <= 0.05,
                (support_distance > 0.05) & (support_distance <= 0.12),
                (support_distance < -0.05) & (support_distance >= -0.10),
            ],
            [15, 10, 6],
            default=0,
        )

        score_cols = ["trend_score", "pullback_score", "volatility_score", "rps_score", "support_score"]
        df["structure_score"] = df[score_cols].sum(axis=1).clip(0, 100).round(2)
        df["structure_state"] = np.select(
            [
                df["structure_score"] >= 85,
                df["structure_score"] >= 70,
                df["structure_score"] >= 50,
            ],
            ["excellent", "healthy", "repairing"],
            default="weak",
        )
        df["is_structure_healthy"] = (df["structure_score"] >= 70).astype(int)
        df["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        columns = [
            "trade_date",
            "symbol",
            "close",
            "ma_10",
            "ma_20",
            "ma_50",
            "ma_120",
            "ma_250",
            "high_60",
            "high_120",
            "high_250",
            "pullback_from_high_60",
            "pullback_from_high_120",
            "atr_10",
            "atr_50",
            "atr_ratio_10_50",
            "volume_ratio_20",
            "rps20_avg_10",
            "trend_score",
            "pullback_score",
            "volatility_score",
            "rps_score",
            "support_score",
            "structure_score",
            "structure_state",
            "is_structure_healthy",
            "created_at",
        ]
        return df[df["trade_date"].isin(output_dates)][columns].copy()

    def _save(self, conn: sqlite3.Connection, df: pd.DataFrame) -> None:
        if df.empty:
            logger.warning("structure_health_daily: no rows to save")
            return
        dates = sorted(df["trade_date"].dropna().unique().tolist())
        with conn:
            for trade_date in dates:
                conn.execute(f"DELETE FROM {self.target_table} WHERE trade_date = ?", (trade_date,))
        df = df.drop_duplicates(["trade_date", "symbol"], keep="last")
        df.to_sql(self.target_table, conn, if_exists="append", index=False, method="multi", chunksize=1000)
        logger.info(
            "structure_health_daily: saved %s rows, range %s..%s",
            f"{len(df):,}",
            df["trade_date"].min(),
            df["trade_date"].max(),
        )

    def run_daily(
        self,
        days: int = 3,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
    ) -> dict:
        return self.run(
            mode="daily",
            days=days,
            target_dates=target_dates,
            missing_only=missing_only,
        )

    def run_init(self, days: int = 3) -> dict:
        return self.run(mode="init", days=days)

    def run(
        self,
        mode: str = "daily",
        days: int = 3,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
    ) -> dict:
        start_time = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                self._create_table(conn)
                output_dates = resolve_dense_output_dates(
                    conn,
                    target_dates=target_dates,
                    fallback=lambda: self._get_output_dates(conn, mode, days),
                    target_tables=(self.target_table,),
                    missing_only=missing_only,
                )
                if not output_dates and target_dates:
                    return {"success": True, "records": 0, "elapsed": time.time() - start_time}
                if not output_dates:
                    return {"success": False, "error": "stock_daily_prices 没有交易日期", "records": 0}
                load_start = (
                    pd.to_datetime(output_dates[0]).date() - timedelta(days=420)
                ).strftime("%Y-%m-%d")
                logger.info(
                    "🚀 [StructureHealthCalculator] mode=%s, dates=%s..%s",
                    mode,
                    output_dates[0],
                    output_dates[-1],
                )
                source = self._load_data(conn, load_start)
                result = self._compute(source, output_dates)
                self._save(conn, result)
            elapsed = time.time() - start_time
            return {"success": True, "records": len(result), "elapsed": elapsed}
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ 结构健康计算失败: {e}")
            return {"success": False, "error": str(e), "records": 0, "elapsed": elapsed}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha 结构健康评分计算器")
    parser.add_argument("--mode", choices=("daily", "init"), default="daily")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    result = StructureHealthCalculator().run(mode=args.mode, days=args.days)
    raise SystemExit(0 if result.get("success") else 1)
