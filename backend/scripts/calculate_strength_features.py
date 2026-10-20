#!/usr/bin/env python3
"""
Calculate EvoAlpha market and industry strength tables.

This script is intentionally data-source agnostic: it consumes already-collected
SQLite tables and writes the L0/L1 result tables required by BLUEPRINTnew.md.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
CORE_INDEX_CODES = ("000001", "000300", "399006", "000852")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate market/industry strength tables")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    parser.add_argument(
        "--mode",
        choices=("init", "daily"),
        default="daily",
        help="init=all available dates, daily=latest trade dates",
    )
    parser.add_argument("--days", type=int, default=3, help="daily mode output trade-date count")
    return parser


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def get_output_dates(conn: sqlite3.Connection, mode: str, days: int) -> list[str]:
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
    return [row[0] for row in rows]


def get_load_start(output_dates: list[str], lookback_days: int = 420) -> str | None:
    if not output_dates:
        return None
    first_date = pd.to_datetime(output_dates[0]).date()
    return (first_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def read_sql(conn: sqlite3.Connection, query: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(query, conn, params=params)


def normalize_date(df: pd.DataFrame, column: str = "trade_date") -> pd.DataFrame:
    if not df.empty and column in df.columns:
        df[column] = pd.to_datetime(df[column], format="mixed").dt.strftime("%Y-%m-%d")
    return df


def classify_market_state(score: float) -> str:
    if score < 25:
        return "M0"
    if score < 45:
        return "M1"
    if score < 60:
        return "M2"
    if score < 75:
        return "M3"
    return "M4"


def classify_industry_state(score: float) -> str:
    if score < 25:
        return "I0"
    if score < 45:
        return "I1"
    if score < 60:
        return "I2"
    if score < 75:
        return "I3"
    return "I4"


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_strength_daily (
            trade_date TEXT NOT NULL,
            index_code TEXT NOT NULL,
            index_name TEXT,
            close REAL,
            ma_20 REAL,
            ma_50 REAL,
            ma_120 REAL,
            above_ma20 INTEGER,
            above_ma50 INTEGER,
            up_count INTEGER,
            down_count INTEGER,
            new_high_20_count INTEGER,
            new_high_60_count INTEGER,
            new_high_120_count INTEGER,
            limit_up_count INTEGER,
            limit_down_count INTEGER,
            total_amount REAL,
            amount_ratio_20 REAL,
            rps90_count INTEGER,
            market_strength_score REAL,
            market_state TEXT,
            created_at TEXT,
            PRIMARY KEY (trade_date, index_code)
        );

        CREATE INDEX IF NOT EXISTS idx_market_strength_date
            ON market_strength_daily (trade_date);

        CREATE TABLE IF NOT EXISTS industry_strength_daily (
            trade_date TEXT NOT NULL,
            sector_code TEXT,
            sector_name TEXT NOT NULL,
            sector_type TEXT,
            return_5d REAL,
            return_20d REAL,
            return_50d REAL,
            return_120d REAL,
            sector_rps_20 REAL,
            sector_rps_50 REAL,
            sector_rps_120 REAL,
            amount REAL,
            amount_ratio_20 REAL,
            rps90_stock_count INTEGER,
            rps90_stock_ratio REAL,
            new_high_stock_count INTEGER,
            new_high_stock_ratio REAL,
            leader_symbol TEXT,
            leader_strength_score REAL,
            industry_strength_score REAL,
            industry_state TEXT,
            created_at TEXT,
            PRIMARY KEY (trade_date, sector_name)
        );

        CREATE INDEX IF NOT EXISTS idx_industry_strength_date
            ON industry_strength_daily (trade_date);
        """
    )
    conn.commit()


def calculate_stock_market_stats(conn: sqlite3.Connection, load_start: str) -> pd.DataFrame:
    stock_df = read_sql(
        conn,
        """
        SELECT symbol, trade_date, close, high, amount, pct_chg
        FROM stock_daily_prices
        WHERE trade_date >= ?
        ORDER BY symbol, trade_date
        """,
        (load_start,),
    )
    stock_df = normalize_date(stock_df)
    if stock_df.empty:
        return pd.DataFrame()

    stock_df["pct_chg"] = pd.to_numeric(stock_df["pct_chg"], errors="coerce")
    stock_df["amount"] = pd.to_numeric(stock_df["amount"], errors="coerce").fillna(0)
    stock_df["high"] = pd.to_numeric(stock_df["high"], errors="coerce")
    stock_df["close"] = pd.to_numeric(stock_df["close"], errors="coerce")

    stock_df = stock_df.sort_values(["symbol", "trade_date"])
    grouped = stock_df.groupby("symbol", group_keys=False)
    stock_df["high_20"] = grouped["high"].rolling(20, min_periods=20).max().reset_index(level=0, drop=True)
    stock_df["high_60"] = grouped["high"].rolling(60, min_periods=60).max().reset_index(level=0, drop=True)
    stock_df["high_120"] = grouped["high"].rolling(120, min_periods=120).max().reset_index(level=0, drop=True)
    stock_df["new_high_20"] = stock_df["close"] >= stock_df["high_20"]
    stock_df["new_high_60"] = stock_df["close"] >= stock_df["high_60"]
    stock_df["new_high_120"] = stock_df["close"] >= stock_df["high_120"]

    stats = stock_df.groupby("trade_date").agg(
        up_count=("pct_chg", lambda s: int((s > 0).sum())),
        down_count=("pct_chg", lambda s: int((s < 0).sum())),
        new_high_20_count=("new_high_20", "sum"),
        new_high_60_count=("new_high_60", "sum"),
        new_high_120_count=("new_high_120", "sum"),
        total_amount=("amount", "sum"),
    )
    stats["amount_ratio_20"] = stats["total_amount"] / stats["total_amount"].rolling(
        20, min_periods=5
    ).mean()
    return stats.reset_index()


def calculate_limit_stats(conn: sqlite3.Connection) -> pd.DataFrame:
    frames = []

    if table_exists(conn, "limit_board_trading"):
        up_df = read_sql(
            conn,
            """
            SELECT trade_date, COUNT(DISTINCT symbol) AS limit_up_count
            FROM limit_board_trading
            GROUP BY trade_date
            """,
        )
        frames.append(normalize_date(up_df))

    if table_exists(conn, "limit_down_trading"):
        down_df = read_sql(
            conn,
            """
            SELECT trade_date, COUNT(DISTINCT symbol) AS limit_down_count
            FROM limit_down_trading
            GROUP BY trade_date
            """,
        )
        frames.append(normalize_date(down_df))

    if not frames:
        return pd.DataFrame(columns=["trade_date", "limit_up_count", "limit_down_count"])

    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="trade_date", how="outer")
    for column in ("limit_up_count", "limit_down_count"):
        if column not in result.columns:
            result[column] = 0
        result[column] = result[column].fillna(0).astype(int)
    return result


def calculate_rps90_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    if not table_exists(conn, "quant_feature_rps"):
        return pd.DataFrame(columns=["trade_date", "rps90_count"])

    columns = {row[1] for row in conn.execute("PRAGMA table_info(quant_feature_rps)").fetchall()}
    available_periods = [col for col in ("rps_20", "rps_50", "rps_120") if col in columns]
    if not available_periods:
        return pd.DataFrame(columns=["trade_date", "rps90_count"])

    predicate = " OR ".join(f"COALESCE({col}, 0) >= 90" for col in available_periods)
    rps_df = read_sql(
        conn,
        f"""
        SELECT trade_date, COUNT(DISTINCT symbol) AS rps90_count
        FROM quant_feature_rps
        WHERE {predicate}
        GROUP BY trade_date
        """,
    )
    return normalize_date(rps_df)


def calculate_market_strength(
    conn: sqlite3.Connection, output_dates: list[str], load_start: str
) -> pd.DataFrame:
    market_stats = calculate_stock_market_stats(conn, load_start)
    if market_stats.empty:
        return pd.DataFrame()

    index_df = read_sql(
        conn,
        """
        SELECT index_code, index_name, trade_date, close
        FROM index_daily
        WHERE trade_date >= ?
        ORDER BY index_code, trade_date
        """,
        (load_start,),
    )
    index_df = normalize_date(index_df)
    if index_df.empty:
        return pd.DataFrame()

    index_df["close"] = pd.to_numeric(index_df["close"], errors="coerce")
    index_df = index_df[index_df["index_code"].isin(CORE_INDEX_CODES)].copy()
    if index_df.empty:
        return pd.DataFrame()

    # AkShare 指数接口有时晚于个股 K 线 1-2 个交易日。市场强度不能因此整日缺失：
    # 指数 close/名称使用最近可得值前向填充，市场广度/成交/RPS/涨停仍使用当日个股数据。
    full_dates = sorted(market_stats["trade_date"].dropna().unique().tolist())
    completed = []
    for index_code, group in index_df.groupby("index_code"):
        group = group.drop_duplicates("trade_date", keep="last").set_index("trade_date")
        group = group.reindex(full_dates).ffill()
        group["index_code"] = index_code
        group["trade_date"] = group.index
        completed.append(group.reset_index(drop=True))
    index_df = pd.concat(completed, ignore_index=True)

    index_df = index_df.sort_values(["index_code", "trade_date"])
    grouped = index_df.groupby("index_code", group_keys=False)
    index_df["ma_20"] = grouped["close"].rolling(20, min_periods=5).mean().reset_index(level=0, drop=True)
    index_df["ma_50"] = grouped["close"].rolling(50, min_periods=10).mean().reset_index(level=0, drop=True)
    index_df["ma_120"] = grouped["close"].rolling(120, min_periods=20).mean().reset_index(level=0, drop=True)
    index_df["ma_20_prev"] = grouped["ma_20"].shift(5)
    index_df["ma_50_prev"] = grouped["ma_50"].shift(5)
    index_df["above_ma20"] = (index_df["close"] > index_df["ma_20"]).astype(int)
    index_df["above_ma50"] = (index_df["close"] > index_df["ma_50"]).astype(int)
    index_df["ma20_up"] = (index_df["ma_20"] > index_df["ma_20_prev"]).astype(int)
    index_df["ma50_up"] = (index_df["ma_50"] > index_df["ma_50_prev"]).astype(int)

    merged = index_df.merge(market_stats, on="trade_date", how="left")
    merged = merged.merge(calculate_limit_stats(conn), on="trade_date", how="left")
    merged = merged.merge(calculate_rps90_counts(conn), on="trade_date", how="left")
    merged["limit_up_count"] = merged["limit_up_count"].fillna(0).astype(int)
    merged["limit_down_count"] = merged["limit_down_count"].fillna(0).astype(int)
    merged["rps90_count"] = merged["rps90_count"].fillna(0).astype(int)

    total_count = merged["up_count"].fillna(0) + merged["down_count"].fillna(0)
    up_ratio = (merged["up_count"].fillna(0) / total_count.replace(0, np.nan)).fillna(0)
    amount_component = (merged["amount_ratio_20"].fillna(1).clip(0, 1.5) / 1.5) * 15
    index_component = (
        merged["above_ma20"] * 8
        + merged["above_ma50"] * 10
        + merged["ma20_up"] * 7
        + merged["ma50_up"] * 5
    )
    merged["market_strength_score"] = (
        index_component
        + up_ratio * 20
        + amount_component
        + (merged["limit_up_count"].clip(0, 100) / 100) * 15
        + (merged["rps90_count"].clip(0, 500) / 500) * 15
        + (merged["new_high_60_count"].fillna(0).clip(0, 300) / 300) * 10
    ).clip(0, 100).round(2)
    merged["market_state"] = merged["market_strength_score"].map(classify_market_state)
    merged["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    columns = [
        "trade_date",
        "index_code",
        "index_name",
        "close",
        "ma_20",
        "ma_50",
        "ma_120",
        "above_ma20",
        "above_ma50",
        "up_count",
        "down_count",
        "new_high_20_count",
        "new_high_60_count",
        "new_high_120_count",
        "limit_up_count",
        "limit_down_count",
        "total_amount",
        "amount_ratio_20",
        "rps90_count",
        "market_strength_score",
        "market_state",
        "created_at",
    ]
    result = merged[merged["trade_date"].isin(output_dates)].copy()
    return result[columns]


def calculate_industry_strength(
    conn: sqlite3.Connection, output_dates: list[str], load_start: str
) -> pd.DataFrame:
    map_df = read_sql(
        conn,
        "SELECT DISTINCT symbol, sector_name, sector_type FROM stock_sector_map",
    )
    if map_df.empty:
        return pd.DataFrame()
    map_df["sector_name"] = map_df["sector_name"].astype(str).str.strip()

    industry_universe = set(map_df["sector_name"].dropna().unique().tolist())
    if table_exists(conn, "sector_list"):
        list_df = read_sql(
            conn,
            """
            SELECT DISTINCT sector_name
            FROM sector_list
            WHERE sector_type = 'Industry'
            """,
        )
        if not list_df.empty:
            list_df["sector_name"] = list_df["sector_name"].astype(str).str.strip()
            industry_universe = set(list_df["sector_name"].dropna().unique().tolist())
            map_df = map_df[map_df["sector_name"].isin(industry_universe)].copy()
            if map_df.empty:
                return pd.DataFrame()

    # Industry strength must consume the same canonical equal-weight net
    # value as sector RPS.  ``sector_daily_prices`` is retained below only as
    # a migration-compatibility read for old isolated callers; the production
    # daily workflow creates the composite table before this calculator runs.
    if table_exists(conn, "sector_composite_daily"):
        sector_df = read_sql(
            conn,
            """
            SELECT sector_name, trade_date, net_value AS close, amount
            FROM sector_composite_daily
            WHERE trade_date >= ?
              AND UPPER(COALESCE(quality_flag, 'PASS')) IN ('PASS', 'OK', 'LEGACY_OK')
            ORDER BY sector_name, trade_date
            """,
            (load_start,),
        )
    elif table_exists(conn, "sector_daily_prices"):
        # This branch is intentionally not a synthetic fallback.  It only
        # keeps pre-migration scripts/tests readable until the additive schema
        # migration has been run, and is never selected by the production
        # quality-gated workflow.
        sector_df = read_sql(
            conn,
            """
            SELECT sector_name, trade_date, close, amount
            FROM sector_daily_prices
            WHERE trade_date >= ?
            ORDER BY sector_name, trade_date
            """,
            (load_start,),
        )
    else:
        sector_df = pd.DataFrame()
    sector_df = normalize_date(sector_df)
    if not sector_df.empty:
        sector_df["sector_name"] = sector_df["sector_name"].astype(str).str.strip()
        if industry_universe:
            sector_df = sector_df[sector_df["sector_name"].isin(industry_universe)].copy()

    if not sector_df.empty:
        overlap_count = len(set(sector_df["sector_name"]).intersection(set(map_df["sector_name"])))
    else:
        overlap_count = 0

    if sector_df.empty or overlap_count == 0:
        # A missing/ambiguous canonical series is a hard data gap.  Inventing
        # a second series here would make the strength panel disagree with the
        # chart and with RPS, which was the original source of the misleading
        # sector K-line behaviour.
        return pd.DataFrame()

    sector_df["close"] = pd.to_numeric(sector_df["close"], errors="coerce")
    sector_df["amount"] = pd.to_numeric(sector_df["amount"], errors="coerce").fillna(0)
    sector_df = sector_df.sort_values(["sector_name", "trade_date"])
    grouped = sector_df.groupby("sector_name", group_keys=False)
    for period in (5, 20, 50, 120):
        sector_df[f"return_{period}d"] = grouped["close"].pct_change(period)
    sector_df["amount_ratio_20"] = (
        sector_df["amount"]
        / grouped["amount"].rolling(20, min_periods=5).mean().reset_index(level=0, drop=True)
    )

    sector_rps_table = (
        "quant_feature_sector_rps_v2"
        if table_exists(conn, "quant_feature_sector_rps_v2")
        else "quant_feature_sector_rps"
    )
    if table_exists(conn, sector_rps_table):
        sector_rps = read_sql(
            conn,
            f"""
            SELECT sector_name, trade_date, rps_20 AS sector_rps_20,
                   rps_50 AS sector_rps_50, rps_120 AS sector_rps_120
            FROM {sector_rps_table}
            WHERE trade_date >= ?
            """,
            (load_start,),
        )
        sector_rps = normalize_date(sector_rps)
        sector_df = sector_df.merge(sector_rps, on=["sector_name", "trade_date"], how="left")
    else:
        sector_df["sector_rps_20"] = np.nan
        sector_df["sector_rps_50"] = np.nan
        sector_df["sector_rps_120"] = np.nan

    stock_counts = map_df.groupby("sector_name")["symbol"].nunique().rename("stock_count")

    stock_metrics = pd.DataFrame()
    if table_exists(conn, "quant_feature_rps"):
        stock_metrics = read_sql(
            conn,
            """
            SELECT symbol, trade_date, rps_5, rps_20, rps_50, rps_120, rps_250
            FROM quant_feature_rps
            WHERE trade_date >= ?
            """,
            (load_start,),
        )
        stock_metrics = normalize_date(stock_metrics)

    if not stock_metrics.empty:
        weights = {"rps_5": 0.10, "rps_20": 0.20, "rps_50": 0.25, "rps_120": 0.25, "rps_250": 0.20}
        stock_metrics["stock_strength_score"] = sum(
            pd.to_numeric(stock_metrics[col], errors="coerce").fillna(0) * weight
            for col, weight in weights.items()
            if col in stock_metrics.columns
        )
        stock_metrics["is_rps90"] = (
            pd.to_numeric(stock_metrics.get("rps_50", 0), errors="coerce").fillna(0) >= 90
        ) | (
            pd.to_numeric(stock_metrics.get("rps_120", 0), errors="coerce").fillna(0) >= 90
        )
        stock_metrics = stock_metrics.merge(map_df[["symbol", "sector_name"]], on="symbol", how="inner")
        rps_agg = stock_metrics.groupby(["trade_date", "sector_name"]).agg(
            rps90_stock_count=("is_rps90", "sum"),
            leader_strength_score=("stock_strength_score", "max"),
        )
        leaders = stock_metrics.sort_values(
            ["trade_date", "sector_name", "stock_strength_score"],
            ascending=[True, True, False],
        ).drop_duplicates(["trade_date", "sector_name"])
        leaders = leaders.set_index(["trade_date", "sector_name"])["symbol"].rename("leader_symbol")
        stock_agg = rps_agg.join(leaders).reset_index()
    else:
        stock_agg = pd.DataFrame(
            columns=[
                "trade_date",
                "sector_name",
                "rps90_stock_count",
                "leader_strength_score",
                "leader_symbol",
            ]
        )

    stock_kline = read_sql(
        conn,
        """
        SELECT symbol, trade_date, close, high
        FROM stock_daily_prices
        WHERE trade_date >= ?
        ORDER BY symbol, trade_date
        """,
        (load_start,),
    )
    stock_kline = normalize_date(stock_kline)
    if not stock_kline.empty:
        stock_kline["close"] = pd.to_numeric(stock_kline["close"], errors="coerce")
        stock_kline["high"] = pd.to_numeric(stock_kline["high"], errors="coerce")
        stock_kline = stock_kline.sort_values(["symbol", "trade_date"])
        stock_kline["high_120"] = (
            stock_kline.groupby("symbol", group_keys=False)["high"]
            .rolling(120, min_periods=120)
            .max()
            .reset_index(level=0, drop=True)
        )
        stock_kline["is_new_high"] = stock_kline["close"] >= stock_kline["high_120"]
        stock_kline = stock_kline.merge(map_df[["symbol", "sector_name"]], on="symbol", how="inner")
        high_agg = stock_kline.groupby(["trade_date", "sector_name"]).agg(
            new_high_stock_count=("is_new_high", "sum")
        ).reset_index()
    else:
        high_agg = pd.DataFrame(columns=["trade_date", "sector_name", "new_high_stock_count"])

    result = sector_df.merge(stock_agg, on=["trade_date", "sector_name"], how="left")
    result = result.merge(high_agg, on=["trade_date", "sector_name"], how="left")
    result = result.merge(stock_counts, on="sector_name", how="left")
    sector_type = map_df.drop_duplicates("sector_name").set_index("sector_name")["sector_type"]
    result["sector_type"] = result["sector_name"].map(sector_type).fillna("Industry")
    result["sector_code"] = result["sector_name"]
    result["rps90_stock_count"] = result["rps90_stock_count"].fillna(0).astype(int)
    result["new_high_stock_count"] = result["new_high_stock_count"].fillna(0).astype(int)
    result["stock_count"] = result["stock_count"].replace(0, np.nan)
    result["rps90_stock_ratio"] = result["rps90_stock_count"] / result["stock_count"]
    result["new_high_stock_ratio"] = result["new_high_stock_count"] / result["stock_count"]
    result["leader_strength_score"] = result["leader_strength_score"].fillna(0)

    trade_groups = result.groupby("trade_date", group_keys=False)
    result["return20_rank"] = trade_groups["return_20d"].rank(pct=True).fillna(0) * 100
    result["return50_rank"] = trade_groups["return_50d"].rank(pct=True).fillna(0) * 100
    result["base_strength_score"] = (
        result["return20_rank"] * 0.25
        + result["return50_rank"] * 0.15
        + (result["amount_ratio_20"].fillna(1).clip(0, 1.5) / 1.5 * 100) * 0.15
        + (result["rps90_stock_ratio"].fillna(0).clip(0, 0.35) / 0.35 * 100) * 0.15
        + (result["new_high_stock_ratio"].fillna(0).clip(0, 0.25) / 0.25 * 100) * 0.10
        + result["leader_strength_score"].clip(0, 100) * 0.10
    ).clip(0, 90)

    result = result.sort_values(["sector_name", "trade_date"])
    result["strong_day"] = result["base_strength_score"] >= 60
    result["strong_streak"] = result.groupby("sector_name")["strong_day"].transform(
        lambda s: s.groupby((~s).cumsum()).cumcount() + 1
    )
    result.loc[~result["strong_day"], "strong_streak"] = 0
    result["industry_strength_score"] = (
        result["base_strength_score"] + result["strong_streak"].clip(0, 5) / 5 * 10
    ).clip(0, 100).round(2)
    result["industry_state"] = result["industry_strength_score"].map(classify_industry_state)
    result["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    columns = [
        "trade_date",
        "sector_code",
        "sector_name",
        "sector_type",
        "return_5d",
        "return_20d",
        "return_50d",
        "return_120d",
        "sector_rps_20",
        "sector_rps_50",
        "sector_rps_120",
        "amount",
        "amount_ratio_20",
        "rps90_stock_count",
        "rps90_stock_ratio",
        "new_high_stock_count",
        "new_high_stock_ratio",
        "leader_symbol",
        "leader_strength_score",
        "industry_strength_score",
        "industry_state",
        "created_at",
    ]
    result = result[result["trade_date"].isin(output_dates)].copy()
    return result[columns]


def save_frame(conn: sqlite3.Connection, table_name: str, df: pd.DataFrame, key_column: str) -> None:
    if df.empty:
        print(f"{table_name}: no rows to save")
        return

    dates = sorted(df["trade_date"].dropna().unique().tolist())
    with conn:
        for trade_date in dates:
            conn.execute(f"DELETE FROM {table_name} WHERE trade_date = ?", (trade_date,))
    df = df.drop_duplicates(subset=["trade_date", key_column], keep="last")
    df.to_sql(table_name, conn, if_exists="append", index=False, method="multi", chunksize=100)
    print(
        f"{table_name}: saved {len(df):,} rows, "
        f"range {df['trade_date'].min()}..{df['trade_date'].max()}"
    )


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2

    with sqlite3.connect(db_path) as conn:
        create_tables(conn)
        output_dates = get_output_dates(conn, args.mode, args.days)
        load_start = get_load_start(output_dates)
        if not output_dates or load_start is None:
            print("stock_daily_prices has no trade dates", file=sys.stderr)
            return 1

        print(
            f"Calculating strength features: mode={args.mode}, "
            f"output_dates={output_dates[0]}..{output_dates[-1]}, load_start={load_start}"
        )
        market_df = calculate_market_strength(conn, output_dates, load_start)
        industry_df = calculate_industry_strength(conn, output_dates, load_start)
        save_frame(conn, "market_strength_daily", market_df, "index_code")
        save_frame(conn, "industry_strength_daily", industry_df, "sector_name")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
