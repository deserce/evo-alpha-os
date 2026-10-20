"""
EvoAlpha OS - 生命周期状态机计算器

将 S0 核心股票池、M0-M4 市场状态、I0-I4 行业状态、RPS、结构健康和
预选信号串成统一的 stock_lifecycle_state。
"""

import sqlite3
import sys
import time
from datetime import datetime
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


class LifecycleStateCalculator:
    """S0-S7 生命周期状态机计算器。"""

    target_table = "stock_lifecycle_state"

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        if not self._table_exists(conn, table_name):
            return set()
        return {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _create_table(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_lifecycle_state (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                sector_name TEXT,
                universe_source TEXT NOT NULL DEFAULT 'core',
                lifecycle_state TEXT NOT NULL,
                state_rank INTEGER NOT NULL,
                data_status TEXT NOT NULL DEFAULT 'OK',
                market_state TEXT,
                industry_state TEXT,
                rps_20 REAL,
                rps_50 REAL,
                rps_120 REAL,
                rps_250 REAL,
                structure_score REAL,
                structure_state TEXT,
                signal_type TEXT,
                entry_reason TEXT,
                exit_reason TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (trade_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_lifecycle_date
                ON stock_lifecycle_state (trade_date);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_state
                ON stock_lifecycle_state (lifecycle_state);
            """
        )
        columns = self._table_columns(conn, self.target_table)
        if "universe_source" not in columns:
            conn.execute(
                "ALTER TABLE stock_lifecycle_state "
                "ADD COLUMN universe_source TEXT NOT NULL DEFAULT 'core'"
            )
        if "data_status" not in columns:
            conn.execute(
                "ALTER TABLE stock_lifecycle_state "
                "ADD COLUMN data_status TEXT NOT NULL DEFAULT 'OK'"
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

    def _ensure_monitor_column(self, conn: sqlite3.Connection) -> None:
        """Safely migrate pools created before continuous monitoring existed."""
        if not self._table_exists(conn, "stock_pools"):
            return
        if "monitor_enabled" in self._table_columns(conn, "stock_pools"):
            return
        conn.execute(
            "ALTER TABLE stock_pools "
            "ADD COLUMN monitor_enabled INTEGER NOT NULL DEFAULT 0"
        )
        if "pool_id" in self._table_columns(conn, "stock_pools"):
            conn.execute(
                "UPDATE stock_pools SET monitor_enabled=1 WHERE pool_id='watchlist'"
            )
        conn.commit()

    @staticmethod
    def _empty_frame(columns: list[str]) -> pd.DataFrame:
        return pd.DataFrame(columns=columns)

    def _read_pool(self, conn: sqlite3.Connection) -> pd.DataFrame:
        core = self._empty_frame(["symbol", "name", "source"])
        if self._table_exists(conn, "quant_stock_pool"):
            core = pd.read_sql_query(
                """
                SELECT symbol, name, 'core' AS source
                FROM quant_stock_pool
                WHERE pool_name = 'core_pool'
                  AND COALESCE(is_active, 1) = 1
                """,
                conn,
            )

        manual = self._empty_frame(["symbol", "name", "source"])
        if self._table_exists(conn, "stock_pool_members"):
            if self._table_exists(conn, "stock_info"):
                manual_sql = """
                    SELECT m.symbol, s.name, 'manual' AS source
                    FROM stock_pool_members m
                    LEFT JOIN stock_info s ON s.symbol = m.symbol
                    WHERE m.symbol IS NOT NULL AND TRIM(m.symbol) <> ''
                """
            else:
                manual_sql = """
                    SELECT m.symbol, NULL AS name, 'manual' AS source
                    FROM stock_pool_members m
                    WHERE m.symbol IS NOT NULL AND TRIM(m.symbol) <> ''
                """
            manual = pd.read_sql_query(manual_sql, conn)

        combined = pd.concat([core, manual], ignore_index=True)
        if combined.empty:
            return self._empty_frame(["symbol", "name", "universe_source"])

        combined["symbol"] = combined["symbol"].astype(str).str.strip().str.upper()
        combined = combined[combined["symbol"] != ""]
        rows = []
        for symbol, group in combined.groupby("symbol", sort=False):
            sources = set(group["source"].dropna().astype(str))
            names = group["name"].dropna().astype(str)
            rows.append(
                {
                    "symbol": symbol,
                    "name": names.iloc[0] if not names.empty else symbol,
                    "universe_source": (
                        "both" if sources == {"core", "manual"}
                        else "manual" if "manual" in sources
                        else "core"
                    ),
                }
            )
        return pd.DataFrame(rows)

    def _compute_for_dates(
        self,
        conn: sqlite3.Connection,
        output_dates: list[str],
        symbols: list[str] | None = None,
    ) -> pd.DataFrame:
        pool = self._read_pool(conn)
        if symbols is not None:
            pool = pool[pool["symbol"].isin(symbols)].copy()
        if pool.empty:
            logger.warning("核心池与手工分组均为空，生命周期无数据可算")
            return pd.DataFrame()

        date_values = ",".join("?" for _ in output_dates)
        symbol_values = ",".join("?" for _ in symbols or ())
        symbol_filter = (
            f" AND UPPER(TRIM(symbol)) IN ({symbol_values})"
            if symbols is not None
            else ""
        )
        date_symbol_params = [*output_dates, *(symbols or [])]
        daily = pd.read_sql_query(
            f"""
            SELECT DISTINCT symbol, substr(trade_date, 1, 10) AS trade_date,
                   1 AS has_daily_price
            FROM stock_daily_prices
            WHERE substr(trade_date, 1, 10) IN ({date_values})
            {symbol_filter}
            """,
            conn,
            params=date_symbol_params,
        )
        if self._table_exists(conn, "quant_feature_rps"):
            rps = pd.read_sql_query(
                f"""
                SELECT symbol, substr(trade_date, 1, 10) AS trade_date,
                       rps_20, rps_50, rps_120, rps_250
                FROM quant_feature_rps
                WHERE substr(trade_date, 1, 10) IN ({date_values})
                {symbol_filter}
                """,
                conn,
                params=date_symbol_params,
            )
        else:
            rps = self._empty_frame(
                ["symbol", "trade_date", "rps_20", "rps_50", "rps_120", "rps_250"]
            )
        if self._table_exists(conn, "structure_health_daily"):
            structure = pd.read_sql_query(
                f"""
                SELECT symbol, substr(trade_date, 1, 10) AS trade_date,
                       structure_score, structure_state
                FROM structure_health_daily
                WHERE substr(trade_date, 1, 10) IN ({date_values})
                {symbol_filter}
                """,
                conn,
                params=date_symbol_params,
            )
        else:
            structure = self._empty_frame(
                ["symbol", "trade_date", "structure_score", "structure_state"]
            )
        if self._table_exists(conn, "stock_sector_map"):
            sector_map = pd.read_sql_query(
                f"""
                SELECT symbol, sector_name
                FROM stock_sector_map
                {f"WHERE UPPER(TRIM(symbol)) IN ({symbol_values})" if symbols is not None else ""}
                GROUP BY symbol
                """,
                conn,
                params=symbols or None,
            )
        else:
            sector_map = self._empty_frame(["symbol", "sector_name"])
        if self._table_exists(conn, "industry_strength_daily"):
            industry = pd.read_sql_query(
                f"""
                SELECT substr(trade_date, 1, 10) AS trade_date,
                       sector_name, industry_state
                FROM industry_strength_daily
                WHERE substr(trade_date, 1, 10) IN ({date_values})
                """,
                conn,
                params=output_dates,
            )
        else:
            industry = self._empty_frame(["trade_date", "sector_name", "industry_state"])
        if self._table_exists(conn, "market_strength_daily"):
            market = pd.read_sql_query(
                f"""
                SELECT substr(trade_date, 1, 10) AS trade_date,
                       market_state, market_strength_score
                FROM market_strength_daily
                WHERE substr(trade_date, 1, 10) IN ({date_values})
                """,
                conn,
                params=output_dates,
            )
        else:
            market = self._empty_frame(
                ["trade_date", "market_state", "market_strength_score"]
            )
        if not market.empty:
            market = (
                market.sort_values(["trade_date", "market_strength_score"], ascending=[True, False])
                .drop_duplicates("trade_date")
                [["trade_date", "market_state"]]
            )

        signal_frames = []
        if self._table_exists(conn, "timing_signals_daily"):
            signal_frames.append(
                pd.read_sql_query(
                    f"""
                    SELECT trade_date, symbol, signal_type,
                           signal_type AS entry_reason
                    FROM timing_signals_daily
                    WHERE trade_date IN ({date_values})
                      AND COALESCE(is_invalidated, 0) = 0
                      {symbol_filter}
                    """,
                    conn,
                    params=date_symbol_params,
                )
            )
        if self._table_exists(conn, "quant_preselect_results"):
            signal_frames.append(
                pd.read_sql_query(
                    f"""
                    SELECT substr(trade_date, 1, 10) AS trade_date, symbol,
                           signal_type, strategy_name AS entry_reason
                    FROM quant_preselect_results
                    WHERE substr(trade_date, 1, 10) IN ({date_values})
                    {symbol_filter}
                    """,
                    conn,
                    params=date_symbol_params,
                )
            )
        signals = (
            pd.concat(signal_frames, ignore_index=True).drop_duplicates(["trade_date", "symbol"])
            if signal_frames else
            pd.DataFrame(columns=["trade_date", "symbol", "signal_type", "entry_reason"])
        )

        frames = []
        for trade_date in output_dates:
            day = pool.copy()
            day["trade_date"] = trade_date
            frames.append(day)
        result = pd.concat(frames, ignore_index=True)
        result = result.merge(daily, on=["trade_date", "symbol"], how="left")
        result = result.merge(rps, on=["trade_date", "symbol"], how="left")
        result = result.merge(structure, on=["trade_date", "symbol"], how="left")
        result = result.merge(sector_map, on="symbol", how="left")
        result = result.merge(industry, on=["trade_date", "sector_name"], how="left")
        result = result.merge(market, on="trade_date", how="left")
        result = result.merge(signals, on=["trade_date", "symbol"], how="left")

        for column in ("rps_20", "rps_50", "rps_120", "rps_250", "structure_score"):
            result[column] = pd.to_numeric(result[column], errors="coerce")

        market_ok = result["market_state"].isin(["M2", "M3", "M4"])
        industry_ok = result["industry_state"].isin(["I2", "I3", "I4"])
        rps_ok = (result["rps_20"] >= 80) & (result["rps_50"] >= 70)
        structure_ok = (result["structure_score"] >= 70) & (result["rps_20"] >= 80)
        signal_ok = result["signal_type"].notna()

        def get_data_status(row: pd.Series) -> str:
            missing = []
            if pd.isna(row.get("has_daily_price")):
                missing.append("MISSING_STOCK_DAILY")
            if pd.isna(row.get("rps_20")) or pd.isna(row.get("rps_50")):
                missing.append("MISSING_RPS")
            if pd.isna(row.get("structure_score")) or pd.isna(row.get("structure_state")):
                missing.append("MISSING_STRUCTURE")
            if pd.isna(row.get("market_state")):
                missing.append("MISSING_MARKET")
            if pd.isna(row.get("sector_name")):
                missing.append("MISSING_SECTOR")
            if pd.isna(row.get("industry_state")):
                missing.append("MISSING_INDUSTRY")
            return "|".join(missing) if missing else "OK"

        result["data_status"] = result.apply(get_data_status, axis=1)
        data_missing = result["data_status"] != "OK"

        result["state_rank"] = 0
        result["lifecycle_state"] = "S0"
        result.loc[market_ok, ["lifecycle_state", "state_rank"]] = ["S1", 1]
        result.loc[market_ok & industry_ok, ["lifecycle_state", "state_rank"]] = ["S2", 2]
        result.loc[market_ok & industry_ok & rps_ok, ["lifecycle_state", "state_rank"]] = ["S3", 3]
        result.loc[
            market_ok & industry_ok & rps_ok & structure_ok,
            ["lifecycle_state", "state_rank"],
        ] = ["S4", 4]
        result.loc[
            market_ok & industry_ok & rps_ok & structure_ok & signal_ok,
            ["lifecycle_state", "state_rank"],
        ] = ["S5", 5]
        result.loc[data_missing, ["lifecycle_state", "state_rank"]] = ["UNKNOWN", -1]
        result.loc[data_missing, ["signal_type", "entry_reason"]] = None

        result["exit_reason"] = np.where(result["rps_20"] < 60, "RPS20跌破60", None)
        result["is_active"] = 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["created_at"] = now
        result["updated_at"] = now

        columns = [
            "trade_date",
            "symbol",
            "name",
            "sector_name",
            "universe_source",
            "lifecycle_state",
            "state_rank",
            "data_status",
            "market_state",
            "industry_state",
            "rps_20",
            "rps_50",
            "rps_120",
            "rps_250",
            "structure_score",
            "structure_state",
            "signal_type",
            "entry_reason",
            "exit_reason",
            "is_active",
            "created_at",
            "updated_at",
        ]
        return result[columns].drop_duplicates(["trade_date", "symbol"], keep="last")

    def _save(
        self,
        conn: sqlite3.Connection,
        df: pd.DataFrame,
        symbols: list[str] | None = None,
    ) -> None:
        if df.empty:
            logger.warning("stock_lifecycle_state: no rows to save")
            return
        dates = sorted(df["trade_date"].dropna().unique().tolist())
        with conn:
            for trade_date in dates:
                if symbols is None:
                    conn.execute(
                        f"DELETE FROM {self.target_table} WHERE trade_date = ?",
                        (trade_date,),
                    )
                else:
                    placeholders = ",".join("?" for _ in symbols)
                    conn.execute(
                        f"DELETE FROM {self.target_table} "
                        f"WHERE trade_date = ? AND symbol IN ({placeholders})",
                        (trade_date, *symbols),
                    )
        df.to_sql(
            self.target_table,
            conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000,
        )
        logger.info(
            "stock_lifecycle_state: saved %s rows, range %s..%s",
            f"{len(df):,}",
            df["trade_date"].min(),
            df["trade_date"].max(),
        )

    def run_daily(
        self,
        days: int = 3,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
        symbols: list[str] | None = None,
    ) -> dict:
        return self.run(
            mode="daily",
            days=days,
            target_dates=target_dates,
            missing_only=missing_only,
            symbols=symbols,
        )

    def run_init(self, days: int = 3) -> dict:
        return self.run(mode="init", days=days)

    def run(
        self,
        mode: str = "daily",
        days: int = 3,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
        symbols: list[str] | None = None,
    ) -> dict:
        start_time = time.time()
        try:
            normalized_symbols = None
            if symbols is not None:
                normalized_symbols = list(
                    dict.fromkeys(
                        str(symbol).strip().upper()
                        for symbol in symbols
                        if str(symbol).strip()
                    )
                )
                if not normalized_symbols:
                    return {
                        "success": False,
                        "error": "symbols 不能为空",
                        "records": 0,
                        "elapsed": time.time() - start_time,
                    }
            with sqlite3.connect(self.db_path) as conn:
                self._create_table(conn)
                output_dates = resolve_dense_output_dates(
                    conn,
                    target_dates=target_dates,
                    fallback=lambda: self._get_output_dates(conn, mode, days),
                    target_tables=(self.target_table,),
                    missing_only=missing_only,
                    limit=(
                        max(7, len(target_dates or []))
                        if normalized_symbols is not None
                        else 7
                    ),
                )
                if not output_dates and target_dates:
                    return {"success": True, "records": 0, "elapsed": time.time() - start_time}
                if not output_dates:
                    return {"success": False, "error": "stock_daily_prices 没有交易日期", "records": 0}
                logger.info(
                    "🚀 [LifecycleStateCalculator] mode=%s, dates=%s..%s",
                    mode,
                    output_dates[0],
                    output_dates[-1],
                )
                result = self._compute_for_dates(
                    conn,
                    output_dates,
                    symbols=normalized_symbols,
                )
                self._save(conn, result, symbols=normalized_symbols)
            elapsed = time.time() - start_time
            return {"success": True, "records": len(result), "elapsed": elapsed}
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ 生命周期状态机计算失败: {e}")
            return {"success": False, "error": str(e), "records": 0, "elapsed": elapsed}

    def backfill_symbols(
        self,
        symbols: list[str],
        trade_dates: list[str],
    ) -> dict:
        """定向回填股票，分批绕开日常计算最多 7 个目标日的保护。"""
        normalized_symbols = list(
            dict.fromkeys(
                str(symbol).strip().upper()
                for symbol in symbols
                if str(symbol).strip()
            )
        )
        normalized_dates = sorted(
            {
                str(trade_date)[:10]
                for trade_date in trade_dates
                if str(trade_date).strip()
            }
        )
        if not normalized_symbols:
            return {"success": False, "error": "symbols 不能为空", "records": 0}
        if not normalized_dates:
            return {"success": False, "error": "trade_dates 不能为空", "records": 0}

        result = self.run_daily(
            target_dates=normalized_dates,
            symbols=normalized_symbols,
        )
        total_records = int(result.get("records") or 0)
        total_elapsed = float(result.get("elapsed") or 0)
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error") or "生命周期定向回填失败",
                "records": total_records,
                "elapsed": total_elapsed,
            }
        return {
            "success": True,
            "records": total_records,
            "elapsed": total_elapsed,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha 生命周期状态机计算器")
    parser.add_argument("--mode", choices=("daily", "init"), default="daily")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    result = LifecycleStateCalculator().run(mode=args.mode, days=args.days)
    raise SystemExit(0 if result.get("success") else 1)
