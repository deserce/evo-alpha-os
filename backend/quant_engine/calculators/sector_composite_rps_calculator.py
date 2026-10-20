"""RPS calculated from the canonical EvoAlpha sector composite series.

This calculator deliberately does not read ``sector_daily_prices``.  That table
contains historical mixed-source rows and is kept read-only for rollback and
diagnostics.  The canonical input is ``sector_composite_daily.net_value``.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import text

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../.."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core.database import get_engine


logger = logging.getLogger(__name__)

COMPOSITE_METHOD_VERSION = "ew_qfq_v1"
RPS_PERIODS = (5, 10, 20, 50, 120, 250)
VALID_QUALITY_FLAGS = ("PASS", "OK", "LEGACY_OK", "ok", "pass", "legacy_ok")


def compute_cross_sectional_rps(
    frame: pd.DataFrame,
    *,
    periods: Sequence[int] = RPS_PERIODS,
    entity_column: str = "sector_name",
    value_column: str = "net_value",
) -> pd.DataFrame:
    """Compute period returns and cross-sectional percentile RPS.

    Missing values are never forward-filled.  ``rank(pct=True)`` keeps the
    existing 0-100 interpretation while ``method='average'`` makes ties
    deterministic and explicit.
    """

    required = {entity_column, "trade_date", value_column}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing composite columns: {sorted(missing)}")
    if frame.empty:
        return pd.DataFrame(columns=[entity_column, "trade_date"])

    source = frame[[entity_column, "trade_date", value_column]].copy()
    source[entity_column] = source[entity_column].astype(str).str.strip()
    source["trade_date"] = pd.to_datetime(source["trade_date"], errors="coerce").dt.normalize()
    source[value_column] = pd.to_numeric(source[value_column], errors="coerce")
    source.loc[source[value_column] <= 0, value_column] = np.nan
    source = source.dropna(subset=[entity_column, "trade_date"])
    source = source[source[entity_column] != ""]
    source = source.drop_duplicates([entity_column, "trade_date"], keep="last")
    if source.empty:
        return pd.DataFrame(columns=[entity_column, "trade_date"])

    pivot = source.pivot(index="trade_date", columns=entity_column, values=value_column).sort_index()
    outputs: list[pd.DataFrame] = []
    for period in sorted({int(value) for value in periods if int(value) > 0}):
        change = pivot.divide(pivot.shift(period)).subtract(1.0)
        change = change.where(pivot.notna() & pivot.shift(period).notna() & pivot.shift(period).ne(0))
        ranked = change.rank(axis=1, pct=True, method="average").mul(100.0)

        change_long = (
            change.rename_axis(index="trade_date", columns=entity_column)
            .reset_index()
            .melt(id_vars="trade_date", var_name=entity_column, value_name=f"chg_{period}")
        )
        rps_long = (
            ranked.rename_axis(index="trade_date", columns=entity_column)
            .reset_index()
            .melt(id_vars="trade_date", var_name=entity_column, value_name=f"rps_{period}")
        )
        if outputs:
            outputs[0] = outputs[0].merge(
                change_long,
                on=["trade_date", entity_column],
                how="outer",
            ).merge(
                rps_long,
                on=["trade_date", entity_column],
                how="outer",
            )
        else:
            outputs.append(change_long.merge(rps_long, on=["trade_date", entity_column], how="outer"))

    if not outputs:
        return pd.DataFrame(columns=[entity_column, "trade_date"])
    result = outputs[0]
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.normalize()
    value_columns = [
        column
        for period in sorted({int(value) for value in periods if int(value) > 0})
        for column in (f"chg_{period}", f"rps_{period}")
    ]
    result = result[[entity_column, "trade_date", *value_columns]]
    for column in value_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
        result[column] = result[column].round(2 if column.startswith("rps_") else 6)
    return result.sort_values(["trade_date", entity_column]).reset_index(drop=True)


class SectorCompositeRPSCalculator:
    """Incremental calculator compatible with ``FeatureRunner``."""

    source_table = "sector_composite_daily"
    target_table = "quant_feature_sector_rps_v2"
    entity_column = "sector_name"
    periods = RPS_PERIODS

    def __init__(self) -> None:
        self.engine = get_engine()

    def _table_exists(self, table_name: str) -> bool:
        with self.engine.begin() as conn:
            return bool(
                conn.execute(
                    text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                    {"name": table_name},
                ).scalar()
            )

    def _init_table(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.target_table} (
                        sector_name TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        chg_5 REAL, rps_5 REAL,
                        chg_10 REAL, rps_10 REAL,
                        chg_20 REAL, rps_20 REAL,
                        chg_50 REAL, rps_50 REAL,
                        chg_120 REAL, rps_120 REAL,
                        chg_250 REAL, rps_250 REAL,
                        method_version TEXT NOT NULL DEFAULT '{COMPOSITE_METHOD_VERSION}',
                        quality_flag TEXT NOT NULL DEFAULT 'PASS',
                        PRIMARY KEY (sector_name, trade_date)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS idx_{self.target_table}_date "
                    f"ON {self.target_table} (trade_date)"
                )
            )

    def load_data(self, start_date: str | date | None = None, end_date: str | date | None = None) -> pd.DataFrame:
        if not self._table_exists(self.source_table):
            return pd.DataFrame(columns=[self.entity_column, "trade_date", "net_value"])
        clauses = [
            "method_version = :method_version",
        ]
        params: dict[str, object] = {"method_version": COMPOSITE_METHOD_VERSION}
        if start_date is not None:
            clauses.append("trade_date >= :start_date")
            params["start_date"] = str(start_date)[:10]
        if end_date is not None:
            clauses.append("trade_date < :end_date_exclusive")
            params["end_date_exclusive"] = (
                pd.to_datetime(end_date).date() + timedelta(days=1)
            ).isoformat()
        query = text(
            f"""
            SELECT sector_name,
                   trade_date,
                   UPPER(COALESCE(quality_flag, 'PASS')) AS quality_flag,
                   CASE WHEN UPPER(COALESCE(quality_flag, 'PASS'))
                             IN ('PASS', 'OK', 'LEGACY_OK')
                        AND net_value > 0
                        THEN net_value ELSE NULL END AS net_value
            FROM {self.source_table}
            WHERE {' AND '.join(clauses)}
            ORDER BY trade_date, sector_name
            """
        )
        return pd.read_sql(query, self.engine, params=params)

    @staticmethod
    def _attach_quality_flags(result: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
        """Carry source row quality into the derived RPS table.

        The numerical calculation intentionally drops bad net values from the
        cross-section.  Keeping the original flag beside the resulting NaNs
        makes the exclusion visible to API diagnostics instead of silently
        relabelling every derived row as PASS.
        """
        if result.empty or "quality_flag" not in source.columns:
            return result
        flags = source[["sector_name", "trade_date", "quality_flag"]].copy()
        flags["trade_date"] = pd.to_datetime(flags["trade_date"], errors="coerce").dt.normalize()
        flags = flags.drop_duplicates(["sector_name", "trade_date"], keep="last")
        return result.merge(flags, on=["sector_name", "trade_date"], how="left")

    def _source_dates(self, limit: int | None = None) -> list[date]:
        if not self._table_exists(self.source_table):
            return []
        limit_sql = f"LIMIT {max(1, int(limit))}" if limit else ""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT trade_date
                    FROM {self.source_table}
                    WHERE method_version = :method_version
                      AND UPPER(COALESCE(quality_flag, 'PASS')) IN ('PASS', 'OK', 'LEGACY_OK')
                    ORDER BY trade_date DESC
                    {limit_sql}
                    """
                ),
                {"method_version": COMPOSITE_METHOD_VERSION},
            ).fetchall()
        return sorted(pd.to_datetime(row[0]).date() for row in rows if row[0])

    def _existing_dates(self, dates: Iterable[date]) -> set[date]:
        values = sorted({value.isoformat() for value in dates})
        if not values or not self._table_exists(self.target_table):
            return set()
        placeholders = ", ".join(f":date_{index}" for index in range(len(values)))
        params = {f"date_{index}": value for index, value in enumerate(values)}
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"SELECT DISTINCT trade_date FROM {self.target_table} "
                    f"WHERE trade_date IN ({placeholders})"
                ),
                params,
            ).fetchall()
        return {pd.to_datetime(row[0]).date() for row in rows if row[0]}

    def _window_start(self, target_dates: Sequence[date]) -> date | None:
        if not target_dates:
            return None
        earliest = min(target_dates).isoformat()
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT trade_date
                    FROM {self.source_table}
                    WHERE trade_date <= :earliest
                      AND method_version = :method_version
                    ORDER BY trade_date DESC
                    LIMIT :limit
                    """
                ),
                {
                    "earliest": earliest,
                    "method_version": COMPOSITE_METHOD_VERSION,
                    "limit": max(self.periods) + 1,
                },
            ).fetchall()
        dates = [pd.to_datetime(row[0]).date() for row in rows if row[0]]
        return min(dates) if dates else None

    def _save(self, result: pd.DataFrame, *, replace: bool = False) -> int:
        if result.empty:
            return 0
        self._init_table()
        frame = result.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
        frame["method_version"] = COMPOSITE_METHOD_VERSION
        if "quality_flag" not in frame.columns:
            frame["quality_flag"] = "PASS"
        frame["quality_flag"] = frame["quality_flag"].fillna("PASS").astype(str).str.upper()
        columns = [
            self.entity_column,
            "trade_date",
            *[column for period in self.periods for column in (f"chg_{period}", f"rps_{period}")],
            "method_version",
            "quality_flag",
        ]
        for column in columns:
            if column not in frame:
                frame[column] = np.nan
        frame = frame[columns].drop_duplicates([self.entity_column, "trade_date"], keep="last")
        dates = sorted(frame["trade_date"].unique())
        with self.engine.begin() as conn:
            if replace:
                conn.execute(text(f"DELETE FROM {self.target_table}"))
            else:
                for value in dates:
                    conn.execute(
                        text(f"DELETE FROM {self.target_table} WHERE trade_date=:trade_date"),
                        {"trade_date": value},
                    )
            frame.to_sql(self.target_table, conn, if_exists="append", index=False, method="multi", chunksize=1000)
        return len(frame)

    def run_init(self, days: int | None = None) -> dict[str, object]:
        started = time.perf_counter()
        self._init_table()
        source_dates = self._source_dates()
        if days is not None and source_dates:
            cutoff = max(source_dates) - timedelta(days=max(1, int(days)))
        else:
            cutoff = None
        frame = self.load_data(start_date=cutoff)
        result = compute_cross_sectional_rps(frame, periods=self.periods)
        result = self._attach_quality_flags(result, frame)
        records = self._save(result, replace=True)
        return {"success": True, "records": records, "elapsed": time.perf_counter() - started}

    def run_daily(
        self,
        target_dates: Sequence[str | date] | None = None,
        missing_only: bool = False,
        **_: object,
    ) -> dict[str, object]:
        started = time.perf_counter()
        self._init_table()
        if target_dates:
            requested = sorted({pd.to_datetime(value).date() for value in target_dates})
        else:
            requested = self._source_dates(limit=7)
        if missing_only:
            existing = self._existing_dates(requested)
            requested = [value for value in requested if value not in existing]
        if not requested:
            return {"success": True, "records": 0, "elapsed": time.perf_counter() - started}
        window_start = self._window_start(requested)
        frame = self.load_data(start_date=window_start, end_date=max(requested))
        result = compute_cross_sectional_rps(frame, periods=self.periods)
        result = self._attach_quality_flags(result, frame)
        result = result[result["trade_date"].dt.date.isin(requested)]
        records = self._save(result)
        return {"success": True, "records": records, "elapsed": time.perf_counter() - started}

    def run(self, mode: str = "daily") -> dict[str, object]:
        return self.run_init() if mode == "init" else self.run_daily()
