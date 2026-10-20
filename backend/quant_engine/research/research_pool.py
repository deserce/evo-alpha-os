"""
EvoAlpha OS - 产业研究池 MVP

提供研究主题、产业链节点、手工观察标的和每日市场验证快照。
"""

from __future__ import annotations

import csv
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


class ResearchPoolManager:
    """研究池表初始化、CSV 导入与每日快照。"""

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def create_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_themes (
                theme_id TEXT PRIMARY KEY,
                theme_name TEXT NOT NULL UNIQUE,
                description TEXT,
                status TEXT DEFAULT 'watching',
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS research_theme_nodes (
                node_id TEXT PRIMARY KEY,
                theme_id TEXT NOT NULL,
                node_name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(theme_id, node_name)
            );

            CREATE TABLE IF NOT EXISTS research_watchlist (
                watch_id TEXT PRIMARY KEY,
                theme_id TEXT NOT NULL,
                node_id TEXT,
                symbol TEXT NOT NULL,
                stock_name TEXT,
                role_in_theme TEXT,
                research_reason TEXT,
                logic_summary TEXT,
                catalyst TEXT,
                risk_note TEXT,
                conviction_level TEXT DEFAULT 'medium',
                research_status TEXT DEFAULT 'watching',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(theme_id, symbol)
            );

            CREATE TABLE IF NOT EXISTS research_stock_daily_snapshot (
                trade_date TEXT NOT NULL,
                theme_name TEXT NOT NULL,
                node_name TEXT,
                symbol TEXT NOT NULL,
                stock_name TEXT,
                conviction_level TEXT,
                research_status TEXT,
                lifecycle_state TEXT,
                market_state TEXT,
                industry_state TEXT,
                rps_20 REAL,
                rps_50 REAL,
                structure_score REAL,
                timing_signal_count INTEGER DEFAULT 0,
                latest_signal_type TEXT,
                created_at TEXT,
                PRIMARY KEY (trade_date, theme_name, symbol)
            );

            CREATE INDEX IF NOT EXISTS idx_research_snapshot_date
                ON research_stock_daily_snapshot (trade_date);
            CREATE INDEX IF NOT EXISTS idx_research_watch_symbol
                ON research_watchlist (symbol);
            """
        )
        conn.commit()

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _id(self, *parts: object) -> str:
        return "_".join(str(part).strip().replace(" ", "_") for part in parts if part is not None)

    def import_csv(self, csv_path: str) -> dict:
        path = Path(csv_path)
        if not path.exists():
            return {"success": False, "error": f"CSV 不存在: {path}", "records": 0}

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        with sqlite3.connect(self.db_path) as conn:
            self.create_tables(conn)
            for row in rows:
                theme_name = (row.get("theme_name") or "").strip()
                node_name = (row.get("node_name") or "").strip()
                symbol = (row.get("symbol") or "").strip()
                if not theme_name or not symbol:
                    continue

                theme_id = self._id("theme", theme_name)
                node_id = self._id("node", theme_name, node_name) if node_name else None
                watch_id = self._id("watch", theme_name, symbol)

                conn.execute(
                    """
                    INSERT INTO research_themes (
                        theme_id, theme_name, status, created_at, updated_at
                    ) VALUES (?, ?, 'watching', ?, ?)
                    ON CONFLICT(theme_name) DO UPDATE SET updated_at = excluded.updated_at
                    """,
                    (theme_id, theme_name, now, now),
                )
                if node_id:
                    conn.execute(
                        """
                        INSERT INTO research_theme_nodes (
                            node_id, theme_id, node_name, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(theme_id, node_name) DO UPDATE SET updated_at = excluded.updated_at
                        """,
                        (node_id, theme_id, node_name, now, now),
                    )

                conn.execute(
                    """
                    INSERT INTO research_watchlist (
                        watch_id, theme_id, node_id, symbol, stock_name, role_in_theme,
                        research_reason, logic_summary, catalyst, risk_note,
                        conviction_level, research_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(theme_id, symbol) DO UPDATE SET
                        node_id = excluded.node_id,
                        stock_name = excluded.stock_name,
                        role_in_theme = excluded.role_in_theme,
                        research_reason = excluded.research_reason,
                        logic_summary = excluded.logic_summary,
                        catalyst = excluded.catalyst,
                        risk_note = excluded.risk_note,
                        conviction_level = excluded.conviction_level,
                        research_status = excluded.research_status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        watch_id,
                        theme_id,
                        node_id,
                        symbol,
                        row.get("stock_name"),
                        row.get("role_in_theme"),
                        row.get("research_reason"),
                        row.get("logic_summary"),
                        row.get("catalyst"),
                        row.get("risk_note"),
                        row.get("conviction_level") or "medium",
                        row.get("research_status") or "watching",
                        now,
                        now,
                    ),
                )
            conn.commit()

        return {"success": True, "records": len(rows)}

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

    def run_daily(self, days: int = 3) -> dict:
        return self.run(mode="daily", days=days)

    def run_init(self, days: int = 3) -> dict:
        return self.run(mode="init", days=days)

    def run(self, mode: str = "daily", days: int = 3) -> dict:
        start_time = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                self.create_tables(conn)
                dates = self._get_output_dates(conn, mode, days)
                if not dates:
                    return {"success": False, "error": "无交易日期", "records": 0}

                watch = pd.read_sql_query(
                    """
                    SELECT
                        t.theme_name,
                        n.node_name,
                        w.symbol,
                        w.stock_name,
                        w.conviction_level,
                        w.research_status
                    FROM research_watchlist w
                    JOIN research_themes t ON w.theme_id = t.theme_id
                    LEFT JOIN research_theme_nodes n ON w.node_id = n.node_id
                    WHERE COALESCE(w.research_status, 'watching') != 'archived'
                    """,
                    conn,
                )
                if watch.empty:
                    return {"success": True, "records": 0, "elapsed": time.time() - start_time}

                date_values = ",".join("?" for _ in dates)
                lifecycle = pd.read_sql_query(
                    f"""
                    SELECT trade_date, symbol, lifecycle_state, market_state, industry_state,
                           rps_20, rps_50, structure_score
                    FROM stock_lifecycle_state
                    WHERE trade_date IN ({date_values})
                    """,
                    conn,
                    params=dates,
                )
                if self._table_exists(conn, "timing_signals_daily"):
                    signals = pd.read_sql_query(
                        f"""
                        SELECT trade_date, symbol, COUNT(*) AS timing_signal_count,
                               MAX(signal_type) AS latest_signal_type
                        FROM timing_signals_daily
                        WHERE trade_date IN ({date_values})
                          AND COALESCE(is_invalidated, 0) = 0
                        GROUP BY trade_date, symbol
                        """,
                        conn,
                        params=dates,
                    )
                else:
                    signals = pd.DataFrame(
                        columns=["trade_date", "symbol", "timing_signal_count", "latest_signal_type"]
                    )

                frames = []
                for trade_date in dates:
                    frame = watch.copy()
                    frame["trade_date"] = trade_date
                    frames.append(frame)
                snapshot = pd.concat(frames, ignore_index=True)
                snapshot = snapshot.merge(lifecycle, on=["trade_date", "symbol"], how="left")
                snapshot = snapshot.merge(signals, on=["trade_date", "symbol"], how="left")
                snapshot["timing_signal_count"] = snapshot["timing_signal_count"].fillna(0).astype(int)
                snapshot["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                with conn:
                    for trade_date in dates:
                        conn.execute(
                            "DELETE FROM research_stock_daily_snapshot WHERE trade_date = ?",
                            (trade_date,),
                        )
                snapshot.to_sql(
                    "research_stock_daily_snapshot",
                    conn,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=1000,
                )

            return {"success": True, "records": len(snapshot), "elapsed": time.time() - start_time}
        except Exception as e:
            logger.error(f"❌ 研究池快照失败: {e}")
            return {"success": False, "error": str(e), "records": 0, "elapsed": time.time() - start_time}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha 研究池 MVP")
    parser.add_argument("--mode", choices=("daily", "init"), default="daily")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--import-csv", help="导入研究池 CSV")
    args = parser.parse_args()

    manager = ResearchPoolManager()
    if args.import_csv:
        result = manager.import_csv(args.import_csv)
    else:
        result = manager.run(mode=args.mode, days=args.days)
    raise SystemExit(0 if result.get("success") else 1)
