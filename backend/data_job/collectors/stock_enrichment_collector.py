"""Stock enrichment cache collector.

Heavy stock materials are cached to SQLite and never generated in the frontend.
Daily workflow should run this collector only for the core pool; users can
refresh an individual stock from the stock workspace when needed.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import text

from data_job.common import setup_backend_path, setup_logger
from data_job.core.base_collector import BaseCollector
from data_job.core.direct_market_sources import (
    CNInfoDirectClient,
    EastmoneyDirectClient,
    eastmoney_secid,
    fetch_tencent_quotes,
    normalize_symbol,
    safe_float,
    sina_financial_report,
)


setup_backend_path()
logger = setup_logger(__name__)


F10_CATEGORIES = (
    "最新提示",
    "公司概况",
    "财务分析",
    "股东研究",
    "股本结构",
    "资本运作",
    "业内点评",
    "行业分析",
    "公司大事",
)

DEFAULT_REFRESH_MODULES = (
    "profile",
    "finance_snapshot",
    "financial_reports",
    "f10",
    "announcements",
    "fund_flow",
    "concepts",
    "research",
    "dragon_tiger",
    "margin",
    "block_trades",
    "holder_num",
    "dividends",
    "lockups",
    "northbound_flow",
)

OPTIONAL_REFRESH_MODULES = (
    "finance_summary",
)

ALL_REFRESH_MODULES = DEFAULT_REFRESH_MODULES + OPTIONAL_REFRESH_MODULES


ENRICHMENT_TABLE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS stock_finance_snapshot_tdx (
        symbol TEXT NOT NULL,
        report_date TEXT NOT NULL,
        fetched_at TEXT,
        source TEXT,
        liutongguben REAL,
        zongguben REAL,
        eps REAL,
        bvps REAL,
        roe REAL,
        profit REAL,
        income REAL,
        raw_json TEXT,
        PRIMARY KEY (symbol, report_date, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_f10_sections (
        symbol TEXT NOT NULL,
        category TEXT NOT NULL,
        fetched_at TEXT,
        source TEXT,
        content TEXT,
        content_digest TEXT,
        raw_json TEXT,
        PRIMARY KEY (symbol, category, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_financial_reports (
        symbol TEXT NOT NULL,
        report_type TEXT NOT NULL,
        report_date TEXT NOT NULL,
        fetched_at TEXT,
        source TEXT,
        payload_json TEXT,
        PRIMARY KEY (symbol, report_type, report_date, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_research_reports (
        symbol TEXT NOT NULL,
        report_id TEXT NOT NULL,
        title TEXT,
        org_name TEXT,
        author TEXT,
        publish_date TEXT,
        rating TEXT,
        pdf_url TEXT,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, report_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS industry_research_reports (
        industry_code TEXT,
        industry_name TEXT,
        report_id TEXT NOT NULL,
        title TEXT,
        org_name TEXT,
        author TEXT,
        publish_date TEXT,
        pdf_url TEXT,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (industry_code, report_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_announcements_cninfo (
        symbol TEXT NOT NULL,
        announcement_id TEXT NOT NULL,
        title TEXT,
        category TEXT,
        publish_date TEXT,
        url TEXT,
        source TEXT,
        fetched_at TEXT,
        raw_json TEXT,
        PRIMARY KEY (symbol, announcement_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_dragon_tiger_records (
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        reason TEXT NOT NULL,
        net_buy REAL,
        turnover REAL,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, trade_date, reason)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_dragon_tiger_seats (
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        side TEXT NOT NULL,
        seat_rank INTEGER NOT NULL,
        seat_name TEXT,
        buy_amt REAL,
        sell_amt REAL,
        net_amt REAL,
        is_institution INTEGER,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, trade_date, side, seat_rank)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_lockup_expiry (
        symbol TEXT NOT NULL,
        free_date TEXT NOT NULL,
        lockup_type TEXT NOT NULL,
        shares REAL,
        ratio REAL,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, free_date, lockup_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_margin_trading (
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        rzye REAL,
        rzmre REAL,
        rzche REAL,
        rqye REAL,
        rqmcl REAL,
        rqchl REAL,
        rzrqye REAL,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_block_trades (
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        price REAL,
        close REAL,
        premium_pct REAL,
        volume REAL,
        amount REAL,
        buyer TEXT,
        seller TEXT,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, trade_date, price, buyer, seller)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_holder_num (
        symbol TEXT NOT NULL,
        report_date TEXT NOT NULL,
        holder_num INTEGER,
        change_num INTEGER,
        change_ratio REAL,
        avg_shares REAL,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, report_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_dividend_history (
        symbol TEXT NOT NULL,
        ex_dividend_date TEXT NOT NULL,
        bonus_rmb REAL,
        transfer_ratio REAL,
        bonus_ratio REAL,
        plan TEXT,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, ex_dividend_date, plan)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_fund_flow_daily (
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        main_net REAL,
        small_net REAL,
        mid_net REAL,
        large_net REAL,
        super_net REAL,
        raw_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_concept_blocks (
        symbol TEXT NOT NULL,
        block_code TEXT NOT NULL,
        block_name TEXT,
        block_type TEXT,
        pct_chg REAL,
        leading_stock TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (symbol, block_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_hot_stocks (
        rank_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        name TEXT,
        rank INTEGER NOT NULL,
        heat REAL,
        pct_chg REAL,
        rank_change REAL,
        concepts_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (rank_date, source, rank, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_hot_concepts (
        rank_date TEXT NOT NULL,
        concept_name TEXT NOT NULL,
        rank INTEGER NOT NULL,
        heat REAL,
        related_symbols_json TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (rank_date, source, rank, concept_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS limit_break_trading (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        name TEXT,
        latest_price REAL,
        limit_price REAL,
        pct_chg REAL,
        turnover_rate REAL,
        first_limit_time TEXT,
        break_count INTEGER,
        amplitude REAL,
        speed REAL,
        industry TEXT,
        limit_stats TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (trade_date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS yesterday_limit_trading (
        trade_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        name TEXT,
        latest_price REAL,
        pct_chg REAL,
        turnover_rate REAL,
        amplitude REAL,
        speed REAL,
        y_first_limit_time TEXT,
        y_boards INTEGER,
        industry TEXT,
        limit_stats TEXT,
        source TEXT,
        fetched_at TEXT,
        PRIMARY KEY (trade_date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS northbound_flow_daily (
        trade_date TEXT NOT NULL,
        hgt_yi REAL,
        sgt_yi REAL,
        northbound_yi REAL,
        source TEXT,
        fetched_at TEXT,
        raw_json TEXT,
        PRIMARY KEY (trade_date, source)
    )
    """,
)

# The *_symbol_date keys for tables whose PRIMARY KEY has the same order are
# intentionally omitted; keeping a second copy only grows SQLite.
ENRICHMENT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_enrich_f10_symbol ON stock_f10_sections(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_enrich_reports_symbol ON stock_financial_reports(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_enrich_ann_symbol ON stock_announcements_cninfo(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_enrich_concept_symbol ON stock_concept_blocks(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_enrich_block_symbol_date ON stock_block_trades(symbol, trade_date)",
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalize_date_text(value: Any, fallback: str | None = None) -> str | None:
    """Normalize source date values to YYYY-MM-DD."""
    if value is None or value == "":
        return fallback
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text_value = str(value).strip()
    if not text_value or text_value in {"-", "--", "None", "nan", "NaN"}:
        return fallback

    if re.fullmatch(r"\d{13,}", text_value):
        try:
            return datetime.fromtimestamp(int(text_value[:13]) / 1000).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            pass
    if re.fullmatch(r"\d{10}", text_value):
        try:
            return datetime.fromtimestamp(int(text_value)).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            pass
    if re.fullmatch(r"\d{8}", text_value):
        return f"{text_value[:4]}-{text_value[4:6]}-{text_value[6:8]}"

    match = re.search(r"(20\d{2}|19\d{2})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text_value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return text_value[:10] if len(text_value) >= 10 else fallback


def _pick_date(row: dict[str, Any], fallback: str) -> str:
    for key in (
        "REPORT_DATE",
        "REPORTDATE",
        "报告期",
        "截止日期",
        "END_DATE",
        "DATE",
        "date",
        "trade_date",
        "TRADE_DATE",
        "NOTICE_DATE",
        "ENDDATE",
        "HOLDER_DATE",
        "EX_DIVIDEND_DATE",
        "FREE_DATE",
        "解除限售日期",
    ):
        value = row.get(key)
        if value:
            return _normalize_date_text(value, fallback) or fallback
    return fallback


def _first_value(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _first_text(row: dict[str, Any], keys: Iterable[str], default: str | None = None) -> str | None:
    value = _first_value(row, keys)
    if value is None:
        return default
    text_value = str(value).strip()
    return text_value or default


def _first_float(row: dict[str, Any], keys: Iterable[str]) -> float | None:
    return safe_float(_first_value(row, keys))


def _first_int(row: dict[str, Any], keys: Iterable[str]) -> int | None:
    value = safe_float(_first_value(row, keys))
    return int(value) if value is not None else None


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "-", "--", "None", "nan", "NaN"}
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class StockEnrichmentCollector(BaseCollector):
    """Best-effort collector for stock material caches."""

    def __init__(self, *, em_client: EastmoneyDirectClient | None = None):
        super().__init__(
            collector_name="stock_enrichment",
            request_timeout=30,
            request_delay=1.0,
            max_retries=2,
            enable_anti_ban=False,
        )
        self.em = em_client or EastmoneyDirectClient(min_interval=0.35, timeout=5, retries=0)
        self.cninfo = CNInfoDirectClient(timeout=5)

    def ensure_tables(self) -> None:
        with self.engine.begin() as conn:
            for ddl in ENRICHMENT_TABLE_SQL:
                conn.execute(text(ddl))
            for ddl in ENRICHMENT_INDEX_SQL:
                conn.execute(text(ddl))
            if self._table_exists_in_conn(conn, "stock_valuation_daily"):
                for column, ddl_type in (
                    ("price", "FLOAT"),
                    ("pct_chg", "FLOAT"),
                    ("turnover", "FLOAT"),
                    ("volume_ratio", "FLOAT"),
                    ("limit_up_price", "FLOAT"),
                    ("limit_down_price", "FLOAT"),
                    ("source", "VARCHAR(50)"),
                ):
                    try:
                        conn.execute(text(f"ALTER TABLE stock_valuation_daily ADD COLUMN {column} {ddl_type}"))
                    except Exception:
                        pass
            self._cleanup_announcement_dates(conn)

    def core_symbols(self, *, trade_date: str | None = None, limit: int | None = None) -> list[str]:
        """Return symbols from watchlist + Alpha/MRGC/S4/S5 + same-day signals."""
        self.ensure_tables()
        params = {"trade_date": trade_date} if trade_date else {}
        queries: list[str] = []
        if self._table_exists("stock_pool_members"):
            queries.append("SELECT symbol FROM stock_pool_members")
        if self._table_exists("stock_lifecycle_state"):
            lifecycle_filter = (
                "AND DATE(trade_date) = DATE(:trade_date)"
                if trade_date
                else "AND DATE(trade_date) = (SELECT DATE(MAX(trade_date)) FROM stock_lifecycle_state)"
            )
            queries.append(
                f"""
                SELECT symbol
                FROM stock_lifecycle_state
                WHERE lifecycle_state IN ('S4', 'S5') {lifecycle_filter}
                """
            )
        if self._table_exists("quant_preselect_results"):
            date_column = "trade_date" if self._has_column("quant_preselect_results", "trade_date") else "date"
            date_filter = (
                f"WHERE DATE({date_column}) = DATE(:trade_date)"
                if trade_date
                else f"WHERE DATE({date_column}) = (SELECT DATE(MAX({date_column})) FROM quant_preselect_results)"
            )
            queries.append(f"SELECT symbol FROM quant_preselect_results {date_filter}")
        if self._table_exists("timing_signals_daily"):
            signal_filter = (
                "WHERE DATE(trade_date) = DATE(:trade_date)"
                if trade_date
                else "WHERE DATE(trade_date) = (SELECT DATE(MAX(trade_date)) FROM timing_signals_daily)"
            )
            queries.append(f"SELECT symbol FROM timing_signals_daily {signal_filter}")

        if not queries:
            return []

        sql = " UNION ".join(queries)
        if limit:
            sql = f"SELECT symbol FROM ({sql}) LIMIT :limit"
            params = {**params, "limit": limit}

        with self.engine.begin() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [normalize_symbol(row[0]) for row in rows if normalize_symbol(row[0])]

    def refresh_symbol(
        self,
        symbol: str,
        *,
        modules: Iterable[str] | None = None,
        trade_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Refresh one symbol and return per-module status."""
        code = normalize_symbol(symbol)
        self.ensure_tables()
        selected = self._normalize_modules(modules)
        status: dict[str, dict[str, Any]] = {}

        for module, handler in (
            ("profile", self._refresh_profile),
            ("finance_summary", self._refresh_finance_summary),
            ("finance_snapshot", self._refresh_finance_snapshot),
            ("financial_reports", self._refresh_financial_reports),
            ("f10", self._refresh_f10),
            ("announcements", self._refresh_announcements),
            ("fund_flow", self._refresh_fund_flow),
            ("concepts", self._refresh_concepts),
            ("research", self._refresh_research),
            ("dragon_tiger", self._refresh_dragon_tiger),
            ("margin", self._refresh_margin),
            ("block_trades", self._refresh_block_trades),
            ("holder_num", self._refresh_holder_num),
            ("dividends", self._refresh_dividends),
            ("lockups", self._refresh_lockups),
            ("northbound_flow", self._refresh_northbound_flow),
        ):
            if module not in selected:
                continue
            try:
                count = handler(code, trade_date=trade_date)
                status[module] = {"status": "ok", "rows": count}
            except Exception as exc:  # noqa: BLE001 - source failures are surfaced to UI.
                logger.warning("stock enrichment %s failed for %s: %s", module, code, exc)
                status[module] = {"status": "failed", "rows": 0, "error": str(exc)}

        return status

    def _normalize_modules(self, modules: Iterable[str] | None) -> set[str]:
        selected = set(modules or DEFAULT_REFRESH_MODULES)
        if "all" in selected:
            return set(ALL_REFRESH_MODULES)
        group_map = {
            "finance": {"finance_snapshot", "financial_reports"},
            "f10_sections": {"f10"},
            "funds": {"fund_flow", "dragon_tiger", "margin", "block_trades", "northbound_flow"},
            "risk": {"holder_num", "dividends", "lockups"},
            "materials": {"profile", "concepts", "research", "f10", "announcements"},
        }
        expanded = set(selected)
        for group_name, group_modules in group_map.items():
            if group_name in selected:
                expanded.update(group_modules)
        return expanded & set(ALL_REFRESH_MODULES)

    def run(
        self,
        *,
        mode: str = "core",
        symbols: Iterable[str] | None = None,
        limit: int | None = None,
        trade_date: str | None = None,
        modules: Iterable[str] | None = None,
    ) -> bool:
        self.log_collection_start()
        self.ensure_tables()
        target_symbols = [normalize_symbol(symbol) for symbol in (symbols or []) if normalize_symbol(symbol)]
        if mode == "core" and not target_symbols:
            target_symbols = self.core_symbols(trade_date=trade_date, limit=limit)
        if limit:
            target_symbols = target_symbols[:limit]

        if not target_symbols:
            message = "核心池为空，未刷新逐股重资料"
            logger.info(message)
            self.log_collection_end(True, message)
            return True

        ok = 0
        for index, symbol in enumerate(target_symbols, start=1):
            logger.info("刷新个股资料 [%s/%s] %s", index, len(target_symbols), symbol)
            result = self.refresh_symbol(symbol, modules=modules, trade_date=trade_date)
            if any(item.get("status") == "ok" for item in result.values()):
                ok += 1

        self.log_collection_end(True, f"刷新 {ok}/{len(target_symbols)} 只股票资料")
        return True

    def _table_exists(self, table_name: str) -> bool:
        with self.engine.begin() as conn:
            return self._table_exists_in_conn(conn, table_name)

    @staticmethod
    def _table_exists_in_conn(conn, table_name: str) -> bool:
        return bool(
            conn.execute(
                text(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name=:table_name
                    LIMIT 1
                    """
                ),
                {"table_name": table_name},
            ).fetchone()
        )

    def _has_column(self, table_name: str, column_name: str) -> bool:
        with self.engine.begin() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        return column_name in {row[1] for row in rows}

    def _columns(self, table_name: str) -> set[str]:
        with self.engine.begin() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        return {row[1] for row in rows}

    def _cleanup_announcement_dates(self, conn) -> None:
        if not self._table_exists_in_conn(conn, "stock_announcements_cninfo"):
            return
        rows = conn.execute(
            text(
                """
                SELECT symbol, announcement_id, publish_date
                FROM stock_announcements_cninfo
                WHERE publish_date GLOB '[0-9]*'
                  AND LENGTH(publish_date) IN (10, 13)
                LIMIT 10000
                """
            )
        ).fetchall()
        for symbol, announcement_id, publish_date in rows:
            normalized = _normalize_date_text(publish_date)
            if normalized and normalized != publish_date:
                conn.execute(
                    text(
                        """
                        UPDATE stock_announcements_cninfo
                        SET publish_date=:publish_date
                        WHERE symbol=:symbol AND announcement_id=:announcement_id
                        """
                    ),
                    {
                        "symbol": symbol,
                        "announcement_id": announcement_id,
                        "publish_date": normalized,
                    },
                )

    def _refresh_profile(self, symbol: str, *, trade_date: str | None = None) -> int:
        cached = self._latest_valuation(symbol)
        if cached and any(cached.get(key) is not None for key in ("pe_ttm", "pb", "total_mv", "circ_mv")):
            return 0
        df = fetch_tencent_quotes([symbol], timeout=6)
        em_quote = self._eastmoney_quote(symbol)
        if df.empty and not em_quote:
            return 0
        latest = df.iloc[0].to_dict() if not df.empty else {"code": symbol}
        for key, value in em_quote.items():
            if _is_blank(latest.get(key)) and not _is_blank(value):
                latest[key] = value
        with self.engine.begin() as conn:
            for column, ddl_type in (
                ("price", "FLOAT"),
                ("pct_chg", "FLOAT"),
                ("turnover", "FLOAT"),
                ("volume_ratio", "FLOAT"),
                ("limit_up_price", "FLOAT"),
                ("limit_down_price", "FLOAT"),
                ("source", "VARCHAR(50)"),
            ):
                try:
                    conn.execute(text(f"ALTER TABLE stock_valuation_daily ADD COLUMN {column} {ddl_type}"))
                except Exception:
                    pass
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO stock_valuation_daily (
                        code, name, trade_date, price, pe_ttm, pb, total_mv, circ_mv,
                        pct_chg, turnover, volume_ratio, limit_up_price,
                        limit_down_price, source
                    )
                    VALUES (
                        :code, :name, :trade_date, :price, :pe_ttm, :pb, :total_mv, :circ_mv,
                        :pct_chg, :turnover, :volume_ratio, :limit_up_price,
                        :limit_down_price, :source
                    )
                    """
                ),
                {
                    **latest,
                    "code": symbol,
                    "source": latest.get("_source") or "tencent_direct",
                    "trade_date": trade_date or latest.get("trade_date") or _today_text(),
                },
            )
        return 1

    def _eastmoney_quote(self, symbol: str) -> dict[str, Any]:
        try:
            data = self.em.push2(
                secid=eastmoney_secid(symbol),
                fields="f57,f58,f43,f50,f51,f52,f116,f117,f162,f167,f168,f170",
            ) or {}
        except Exception as exc:  # noqa: BLE001 - Tencent remains primary.
            logger.debug("eastmoney quote fallback failed for %s: %s", symbol, exc)
            return {}

        def price_value(key: str) -> float | None:
            value = safe_float(data.get(key))
            if value is None:
                return None
            return value / 100 if abs(value) > 10000 else value

        return {
            "code": symbol,
            "name": data.get("f58") or symbol,
            "price": price_value("f43"),
            "pct_chg": safe_float(data.get("f170")),
            "turnover": safe_float(data.get("f168")),
            "volume_ratio": safe_float(data.get("f50")),
            "pe_ttm": safe_float(data.get("f162")),
            "pb": safe_float(data.get("f167")),
            "total_mv": safe_float(data.get("f116")),
            "circ_mv": safe_float(data.get("f117")),
            "limit_up_price": price_value("f51"),
            "limit_down_price": price_value("f52"),
            "_source": "eastmoney_direct",
        }

    def _refresh_finance_summary(
        self, symbol: str, *, trade_date: str | None = None, years: int = 6
    ) -> int:
        # finance_summary is an optional, explicitly requested module. Recheck
        # the bounded per-symbol F10 source even when legacy cumulative rows
        # exist, because those rows do not prove that current/single-quarter
        # and deduct-profit fields are complete.
        from data_job.collectors.finance_summary_collector import FinanceSummaryCollector

        collector = FinanceSummaryCollector()
        collector.engine = self.engine
        try:
            result = collector.backfill_symbol(symbol, years=years)
            failures = result.get("failed") or []
            if failures:
                first_failure = failures[0] if isinstance(failures[0], dict) else {}
                error = str(first_failure.get("error") or "unknown source error")[:300]
                raise RuntimeError(f"finance_summary source failed: {error}")
            return int(result.get("updated") or 0)
        finally:
            close = getattr(getattr(collector, "f10_client", None), "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - cleanup is best-effort.
                    logger.debug("finance_summary client close failed: %s", exc)

    def _refresh_finance_snapshot(self, symbol: str, *, trade_date: str | None = None) -> int:
        summary_snapshot = self._finance_summary_snapshot(symbol)
        if summary_snapshot:
            return self._save_finance_snapshot(
                symbol,
                summary_snapshot,
                "finance_summary_fallback",
                trade_date=trade_date,
            )

        try:
            snapshot = self._mootdx_finance(symbol)
            source = "mootdx"
        except RuntimeError as exc:
            logger.warning("mootdx finance failed for %s, fallback to finance_summary: %s", symbol, exc)
            snapshot = self._finance_summary_snapshot(symbol)
            source = "finance_summary_fallback"
        if not snapshot:
            return 0
        return self._save_finance_snapshot(symbol, snapshot, source, trade_date=trade_date)

    def _save_finance_snapshot(
        self,
        symbol: str,
        snapshot: dict[str, Any],
        source: str,
        *,
        trade_date: str | None = None,
    ) -> int:
        report_date = str(snapshot.get("report_date") or snapshot.get("report") or trade_date or "latest")[:10]
        report_date = _normalize_date_text(report_date, trade_date or "latest") or "latest"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO stock_finance_snapshot_tdx (
                        symbol, report_date, fetched_at, source, liutongguben,
                        zongguben, eps, bvps, roe, profit, income, raw_json
                    )
                    VALUES (
                        :symbol, :report_date, :fetched_at, :source,
                        :liutongguben, :zongguben, :eps, :bvps,
                        :roe, :profit, :income, :raw_json
                    )
                    """
                ),
                {
                    "symbol": symbol,
                    "report_date": report_date,
                    "fetched_at": now,
                    "source": source,
                    "liutongguben": safe_float(snapshot.get("liutongguben")),
                    "zongguben": safe_float(snapshot.get("zongguben")),
                    "eps": safe_float(snapshot.get("eps")),
                    "bvps": safe_float(snapshot.get("bvps")),
                    "roe": safe_float(snapshot.get("roe")),
                    "profit": safe_float(snapshot.get("profit")),
                    "income": safe_float(snapshot.get("income")),
                    "raw_json": _json_dumps(snapshot),
                },
            )
        return 1

    def _finance_summary_snapshot(self, symbol: str) -> dict[str, Any] | None:
        if not self._table_exists("stock_finance_summary"):
            return None
        columns = self._columns("stock_finance_summary")
        optional_columns = [
            column
            for column in ("eps", "roe", "total_revenue", "net_profit")
            if column in columns
        ]
        if not optional_columns:
            return None
        sql = f"""
            SELECT report_date, {", ".join(optional_columns)}
            FROM stock_finance_summary
            WHERE code=:symbol
            ORDER BY DATE(report_date) DESC
            LIMIT 1
        """
        with self.engine.begin() as conn:
            row = conn.execute(text(sql), {"symbol": symbol}).mappings().first()
        if not row:
            return None
        row_dict = dict(row)
        return {
            "report_date": row_dict.get("report_date"),
            "eps": row_dict.get("eps"),
            "roe": row_dict.get("roe"),
            "profit": row_dict.get("net_profit"),
            "income": row_dict.get("total_revenue"),
        }

    def _stock_info(self, symbol: str) -> dict[str, Any] | None:
        if not self._table_exists("stock_info"):
            return None
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM stock_info WHERE symbol=:symbol LIMIT 1"),
                {"symbol": symbol},
            ).mappings().first()
        return dict(row) if row else None

    def _latest_valuation(self, symbol: str) -> dict[str, Any] | None:
        if not self._table_exists("stock_valuation_daily"):
            return None
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT *
                    FROM stock_valuation_daily
                    WHERE code=:symbol
                    ORDER BY DATE(trade_date) DESC
                    LIMIT 1
                    """
                ),
                {"symbol": symbol},
            ).mappings().first()
        return dict(row) if row else None

    def _refresh_f10(
        self, symbol: str, *, trade_date: str | None = None, remote_first: bool = False
    ) -> int:
        local_sections = self._local_f10_sections(symbol)
        if local_sections and not remote_first:
            return self._save_f10_sections(symbol, local_sections, "local_cache")

        source = "mootdx"
        try:
            sections = self._mootdx_f10(symbol)
            if not sections:
                raise RuntimeError("empty remote F10 response")
        except RuntimeError as exc:
            logger.warning("mootdx f10 failed for %s, fallback to eastmoney: %s", symbol, exc)
            sections = self._eastmoney_f10(symbol)
            source = "eastmoney_direct"
            if not sections and not remote_first:
                sections = self._local_f10_sections(symbol)
                source = "local_cache"

        if not sections:
            return 0
        return self._save_f10_sections(symbol, sections, source)

    def _save_f10_sections(self, symbol: str, sections: dict[str, str], source: str) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for category, content in sections.items():
                if not content:
                    continue
                digest = str(content).replace("\r", "\n").strip()[:800]
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_f10_sections (
                            symbol, category, fetched_at, source, content,
                            content_digest, raw_json
                        )
                        VALUES (
                            :symbol, :category, :fetched_at, :source,
                            :content, :content_digest, :raw_json
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "category": category,
                        "fetched_at": now,
                        "source": source,
                        "content": str(content),
                        "content_digest": digest,
                        "raw_json": _json_dumps({"category": category, "content": content, "source": source}),
                    },
                )
                count += 1
        return count

    def _local_f10_sections(self, symbol: str) -> dict[str, str]:
        info = self._stock_info(symbol)
        valuation = self._latest_valuation(symbol)
        finance = self._finance_summary_snapshot(symbol)
        if not any((info, valuation, finance)):
            return {}

        def present(label: str, value: Any) -> str | None:
            if value is None or value == "":
                return None
            return f"{label}: {value}"

        sections: dict[str, str] = {}
        latest_lines = [
            present("股票简称", (info or {}).get("name")),
            present("行业", (info or {}).get("industry")),
            present("上市日期", (info or {}).get("list_date")),
            present("收盘价", (valuation or {}).get("price")),
            present("PE(TTM)", (valuation or {}).get("pe_ttm")),
            present("PB", (valuation or {}).get("pb")),
            present("总市值", (valuation or {}).get("total_mv")),
            present("流通市值", (valuation or {}).get("circ_mv")),
        ]
        if any(latest_lines):
            sections["最新提示"] = "\n".join(line for line in latest_lines if line)

        profile_lines = [
            present("股票代码", symbol),
            present("股票简称", (info or {}).get("name")),
            present("所属行业", (info or {}).get("industry")),
            present("上市日期", (info or {}).get("list_date")),
        ]
        if any(profile_lines):
            sections["公司概况"] = "\n".join(line for line in profile_lines if line)

        finance_lines = [
            present("报告期", (finance or {}).get("report_date")),
            present("每股收益", (finance or {}).get("eps")),
            present("净资产收益率", (finance or {}).get("roe")),
            present("营业总收入", (finance or {}).get("income")),
            present("净利润", (finance or {}).get("profit")),
        ]
        if any(finance_lines):
            sections["财务分析"] = "\n".join(line for line in finance_lines if line)
        return sections

    def _refresh_financial_reports(
        self, symbol: str, *, trade_date: str | None = None, local_only: bool = False
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            count += self._insert_finance_summary_reports(conn, symbol, now)
            if count >= 4 or local_only:
                return count
            for report_type in ("lrb", "fzb", "llb"):
                try:
                    rows = sina_financial_report(symbol, report_type, num=8, timeout=8)
                except Exception as exc:  # noqa: BLE001 - finance summary remains fallback.
                    logger.warning("sina %s report failed for %s: %s", report_type, symbol, exc)
                    rows = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    report_date = _pick_date(row, trade_date or "latest")
                    conn.execute(
                        text(
                            """
                            INSERT OR REPLACE INTO stock_financial_reports (
                                symbol, report_type, report_date, fetched_at,
                                source, payload_json
                            )
                            VALUES (
                                :symbol, :report_type, :report_date, :fetched_at,
                                'sina_direct', :payload_json
                            )
                            """
                        ),
                        {
                            "symbol": symbol,
                            "report_type": report_type,
                            "report_date": report_date,
                            "fetched_at": now,
                            "payload_json": _json_dumps(row),
                        },
                    )
                    count += 1
            if count == 0:
                count += self._insert_finance_summary_reports(conn, symbol, now)
        return count

    def _insert_finance_summary_reports(self, conn, symbol: str, fetched_at: str, limit: int = 16) -> int:
        if not self._table_exists_in_conn(conn, "stock_finance_summary"):
            return 0
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(stock_finance_summary)")).fetchall()}
        select_columns = [
            column
            for column in (
                "code",
                "name",
                "report_date",
                "eps",
                "net_profit_up",
                "revenue_up",
                "roe",
                "total_revenue",
                "net_profit",
                "net_margin",
            )
            if column in columns
        ]
        if "report_date" not in select_columns:
            return 0
        rows = conn.execute(
            text(
                f"""
                SELECT {", ".join(select_columns)}
                FROM stock_finance_summary
                WHERE code=:symbol
                ORDER BY DATE(report_date) DESC
                LIMIT :limit
                """
            ),
            {"symbol": symbol, "limit": limit},
        ).mappings().all()
        count = 0
        for row in rows:
            row_dict = dict(row)
            report_date = _normalize_date_text(row_dict.get("report_date"), "latest") or "latest"
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO stock_financial_reports (
                        symbol, report_type, report_date, fetched_at,
                        source, payload_json
                    )
                    VALUES (
                        :symbol, 'summary', :report_date, :fetched_at,
                        'finance_summary', :payload_json
                    )
                    """
                ),
                {
                    "symbol": symbol,
                    "report_date": report_date,
                    "fetched_at": fetched_at,
                    "payload_json": _json_dumps(row_dict),
                },
            )
            count += 1
        return count

    def _refresh_announcements(self, symbol: str, *, trade_date: str | None = None) -> int:
        announcements: list[dict[str, Any]] = []
        for page_num in range(1, 4):
            page_rows = self.cninfo.announcements(symbol, page_num=page_num, page_size=50)
            if not page_rows:
                break
            announcements.extend(page_rows)
            if len(page_rows) < 50:
                break
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for item in announcements:
                announcement_id = str(
                    item.get("announcementId")
                    or item.get("announcement_id")
                    or item.get("adjunctUrl")
                    or f"{symbol}-{count}"
                )
                adjunct_url = item.get("adjunctUrl") or ""
                url = (
                    f"https://static.cninfo.com.cn/{adjunct_url}"
                    if adjunct_url and not str(adjunct_url).startswith("http")
                    else adjunct_url
                )
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_announcements_cninfo (
                            symbol, announcement_id, title, category,
                            publish_date, url, source, fetched_at, raw_json
                        )
                        VALUES (
                            :symbol, :announcement_id, :title, :category,
                            :publish_date, :url, 'cninfo_direct', :fetched_at, :raw_json
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "announcement_id": announcement_id,
                        "title": item.get("announcementTitle") or item.get("title"),
                        "category": item.get("categoryName") or item.get("category"),
                        "publish_date": (
                            _normalize_date_text(
                                item.get("announcementTime")
                                or item.get("publish_date")
                                or item.get("publishDate")
                                or item.get("adjunctUrl"),
                                trade_date or _today_text(),
                            )
                            or trade_date
                            or _today_text()
                        ),
                        "url": url,
                        "fetched_at": now,
                        "raw_json": _json_dumps(item),
                    },
                )
                count += 1
        return count

    def _refresh_fund_flow(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self.em.stock_fund_flow_120d(symbol)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.engine.begin() as conn:
            for row in rows:
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_fund_flow_daily (
                            symbol, trade_date, main_net, small_net, mid_net,
                            large_net, super_net, raw_json, source, fetched_at
                        )
                        VALUES (
                            :symbol, :trade_date, :main_net, :small_net, :mid_net,
                            :large_net, :super_net, :raw_json, 'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        **row,
                        "raw_json": _json_dumps(row.get("raw")),
                        "fetched_at": now,
                    },
                )
        return len(rows)

    def _refresh_concepts(self, symbol: str, *, trade_date: str | None = None) -> int:
        local_items = self._local_concept_items(symbol)
        if local_items:
            return self._save_concept_items(local_items)

        rows: list[dict[str, Any]] = []
        try:
            rows = self._em_symbol_report_rows(
                ["RPT_F10_CORETHEME_BOARDTYPE", "RPT_STOCK_RELATECONCEPT"],
                symbol,
                page_size=80,
                max_pages=2,
            )
        except Exception as exc:  # noqa: BLE001 - local industry fallback below.
            logger.warning("eastmoney concept failed for %s: %s", symbol, exc)

        items: list[dict[str, Any]] = []
        for row in rows:
            block_name = _first_text(
                row,
                (
                    "BOARD_NAME",
                    "CONCEPT_NAME",
                    "CONCEPT_BOARD_NAME",
                    "BK_NAME",
                    "DERIVE_BOARD_NAME",
                    "NEW_BOARD_NAME",
                    "THEME_NAME",
                ),
            )
            if not block_name:
                continue
            items.append(
                {
                    "symbol": symbol,
                    "block_code": _first_text(
                        row,
                        ("BOARD_CODE", "CONCEPT_CODE", "BK_CODE", "DERIVE_BOARD_CODE", "THEME_CODE"),
                        f"concept:{block_name}",
                    ),
                    "block_name": block_name,
                    "block_type": _first_text(row, ("BOARD_TYPE", "TYPE", "THEME_TYPE"), "concept"),
                    "pct_chg": _first_float(row, ("CHANGE_RATE", "涨跌幅", "PCT_CHG")),
                    "leading_stock": _first_text(row, ("LEADING_STOCK", "LEADER_SECURITY_NAME", "领涨股")),
                    "source": "eastmoney_direct",
                }
            )

        if not items:
            items.extend(self._local_concept_items(symbol))

        return self._save_concept_items(items)

    def _local_concept_items(self, symbol: str) -> list[dict[str, Any]]:
        info = self._stock_info(symbol)
        industry = (info or {}).get("industry")
        if not industry:
            return []
        return [
            {
                "symbol": symbol,
                "block_code": f"industry:{industry}",
                "block_name": industry,
                "block_type": "industry_local",
                "pct_chg": None,
                "leading_stock": None,
                "source": "local_stock_info",
            }
        ]

    def _save_concept_items(self, items: list[dict[str, Any]]) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.engine.begin() as conn:
            for item in items:
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_concept_blocks (
                            symbol, block_code, block_name, block_type,
                            pct_chg, leading_stock, source, fetched_at
                        )
                        VALUES (
                            :symbol, :block_code, :block_name, :block_type,
                            :pct_chg, :leading_stock, :source, :fetched_at
                        )
                        """
                    ),
                    {**item, "fetched_at": now},
                )
        return len(items)

    def _refresh_research(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPT_RESEARCH_REPORT", "RPT_ORGOP_ALL"],
            symbol,
            page_size=50,
            max_pages=2,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                title = _first_text(row, ("TITLE", "REPORT_TITLE", "REPORT_NAME", "研报标题"))
                report_id = _first_text(
                    row,
                    ("REPORT_ID", "INFO_CODE", "ART_CODE", "ID"),
                    f"{symbol}-{_pick_date(row, trade_date or _today_text())}-{count}",
                )
                if not title:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_research_reports (
                            symbol, report_id, title, org_name, author,
                            publish_date, rating, pdf_url, raw_json,
                            source, fetched_at
                        )
                        VALUES (
                            :symbol, :report_id, :title, :org_name, :author,
                            :publish_date, :rating, :pdf_url, :raw_json,
                            'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "report_id": report_id,
                        "title": title,
                        "org_name": _first_text(row, ("ORG_NAME", "ORG_SNAME", "机构名称")),
                        "author": _first_text(row, ("AUTHOR", "ANALYST_NAME", "作者")),
                        "publish_date": _pick_date(row, trade_date or _today_text()),
                        "rating": _first_text(row, ("RATING", "EM_RATING_NAME", "评级")),
                        "pdf_url": _first_text(row, ("ATTACH_URL", "PDF_URL", "INFO_URL")),
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_dragon_tiger(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPT_DAILYBILLBOARD_DETAILS"],
            symbol,
            page_size=80,
            max_pages=3,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                row_date = _pick_date(row, trade_date or _today_text())
                reason = _first_text(row, ("EXPLANATION", "REASON", "BILLBOARD_TYPE", "上榜原因"), "龙虎榜")
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_dragon_tiger_records (
                            symbol, trade_date, reason, net_buy, turnover,
                            raw_json, source, fetched_at
                        )
                        VALUES (
                            :symbol, :trade_date, :reason, :net_buy, :turnover,
                            :raw_json, 'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "trade_date": row_date,
                        "reason": reason,
                        "net_buy": _first_float(row, ("BILLBOARD_NET_AMT", "NET_BUY_AMT", "NET_AMT")),
                        "turnover": _first_float(row, ("BILLBOARD_DEAL_AMT", "DEAL_AMT", "TURNOVER")),
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_margin(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPTA_WEB_RZRQ_GGMX", "RPT_MARGIN_TRADING_DETAIL"],
            symbol,
            page_size=120,
            max_pages=3,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                row_date = _pick_date(row, trade_date or _today_text())
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_margin_trading (
                            symbol, trade_date, rzye, rzmre, rzche,
                            rqye, rqmcl, rqchl, rzrqye,
                            raw_json, source, fetched_at
                        )
                        VALUES (
                            :symbol, :trade_date, :rzye, :rzmre, :rzche,
                            :rqye, :rqmcl, :rqchl, :rzrqye,
                            :raw_json, 'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "trade_date": row_date,
                        "rzye": _first_float(row, ("RZYE", "FIN_BALANCE")),
                        "rzmre": _first_float(row, ("RZMRE", "FIN_BUY_AMT")),
                        "rzche": _first_float(row, ("RZCHE", "FIN_REPAY_AMT")),
                        "rqye": _first_float(row, ("RQYE", "SEC_BALANCE")),
                        "rqmcl": _first_float(row, ("RQMCL", "SEC_SELL_VOLUME")),
                        "rqchl": _first_float(row, ("RQCHL", "SEC_REPAY_VOLUME")),
                        "rzrqye": _first_float(row, ("RZRQYE", "MARGIN_BALANCE")),
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_block_trades(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPT_BLOCKTRADE_DEAL"],
            symbol,
            page_size=80,
            max_pages=3,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                row_date = _pick_date(row, trade_date or _today_text())
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_block_trades (
                            symbol, trade_date, price, close, premium_pct,
                            volume, amount, buyer, seller, raw_json,
                            source, fetched_at
                        )
                        VALUES (
                            :symbol, :trade_date, :price, :close, :premium_pct,
                            :volume, :amount, :buyer, :seller, :raw_json,
                            'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "trade_date": row_date,
                        "price": _first_float(row, ("DEAL_PRICE", "PRICE", "成交价")),
                        "close": _first_float(row, ("CLOSE_PRICE", "收盘价")),
                        "premium_pct": _first_float(row, ("PREMIUM_RATIO", "溢价率")),
                        "volume": _first_float(row, ("DEAL_VOLUME", "VOLUME", "成交量")),
                        "amount": _first_float(row, ("DEAL_AMT", "AMOUNT", "成交额")),
                        "buyer": _first_text(row, ("BUYER_NAME", "BUYER", "买方营业部")),
                        "seller": _first_text(row, ("SELLER_NAME", "SELLER", "卖方营业部")),
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_holder_num(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPT_HOLDERNUM_DET"],
            symbol,
            page_size=80,
            max_pages=3,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                report_date = _pick_date(row, trade_date or _today_text())
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_holder_num (
                            symbol, report_date, holder_num, change_num,
                            change_ratio, avg_shares, raw_json, source, fetched_at
                        )
                        VALUES (
                            :symbol, :report_date, :holder_num, :change_num,
                            :change_ratio, :avg_shares, :raw_json,
                            'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "report_date": report_date,
                        "holder_num": _first_int(row, ("HOLDER_TOTAL_NUM", "HOLDER_NUM", "TOTAL_NUM")),
                        "change_num": _first_int(row, ("HOLDER_NUM_CHANGE", "CHANGE_NUM")),
                        "change_ratio": _first_float(row, ("HOLDER_NUM_RATIO", "CHANGE_RATIO", "TOTAL_NUM_RATIO")),
                        "avg_shares": _first_float(row, ("AVG_FREE_SHARES", "AVG_HOLD_NUM")),
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_dividends(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPT_SHAREBONUS_DET"],
            symbol,
            page_size=80,
            max_pages=3,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                ex_date = _pick_date(row, trade_date or _today_text())
                plan = _first_text(
                    row,
                    ("IMPL_PLAN_PROFILE", "ASSIGN_PROGRESS", "PLAN_EXPLAIN", "分红方案"),
                    "dividend",
                )
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_dividend_history (
                            symbol, ex_dividend_date, bonus_rmb,
                            transfer_ratio, bonus_ratio, plan,
                            raw_json, source, fetched_at
                        )
                        VALUES (
                            :symbol, :ex_dividend_date, :bonus_rmb,
                            :transfer_ratio, :bonus_ratio, :plan,
                            :raw_json, 'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "ex_dividend_date": ex_date,
                        "bonus_rmb": _first_float(row, ("BONUS_RMB", "CASH_DIVIDEND_RATIO", "派息")),
                        "transfer_ratio": _first_float(row, ("TRANSFER_RATIO", "转增比例")),
                        "bonus_ratio": _first_float(row, ("BONUS_RATIO", "送股比例")),
                        "plan": plan,
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_lockups(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self._em_symbol_report_rows(
            ["RPT_LIFT_STAGE", "RPT_LIFT_DETAIL"],
            symbol,
            page_size=80,
            max_pages=3,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                free_date = _pick_date(row, trade_date or _today_text())
                lockup_type = _first_text(row, ("LIFT_TYPE", "FREE_TYPE", "解除限售类型"), "lockup")
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO stock_lockup_expiry (
                            symbol, free_date, lockup_type, shares,
                            ratio, raw_json, source, fetched_at
                        )
                        VALUES (
                            :symbol, :free_date, :lockup_type, :shares,
                            :ratio, :raw_json, 'eastmoney_direct', :fetched_at
                        )
                        """
                    ),
                    {
                        "symbol": symbol,
                        "free_date": free_date,
                        "lockup_type": lockup_type,
                        "shares": _first_float(row, ("LIFT_NUM", "FREE_SHARES", "SHARES")),
                        "ratio": _first_float(row, ("LIFT_RATIO", "FREE_RATIO", "RATIO")),
                        "raw_json": _json_dumps(row),
                        "fetched_at": now,
                    },
                )
                count += 1
        return count

    def _refresh_northbound_flow(self, symbol: str, *, trade_date: str | None = None) -> int:
        rows = self.em.datacenter_pages(
            report_name="RPT_MUTUAL_MARKET_STA",
            page_size=80,
            max_pages=2,
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self.engine.begin() as conn:
            for row in rows:
                row_date = _pick_date(row, trade_date or _today_text())
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO northbound_flow_daily (
                            trade_date, hgt_yi, sgt_yi, northbound_yi,
                            source, fetched_at, raw_json
                        )
                        VALUES (
                            :trade_date, :hgt_yi, :sgt_yi, :northbound_yi,
                            'eastmoney_direct', :fetched_at, :raw_json
                        )
                        """
                    ),
                    {
                        "trade_date": row_date,
                        "hgt_yi": self._amount_to_yi(_first_value(row, ("HGT_NET_BUY_AMT", "HGT_NET_AMT"))),
                        "sgt_yi": self._amount_to_yi(_first_value(row, ("SGT_NET_BUY_AMT", "SGT_NET_AMT"))),
                        "northbound_yi": self._amount_to_yi(
                            _first_value(row, ("NET_BUY_AMT", "NORTH_NET_BUY_AMT", "NORTHBOUND_NET_AMT"))
                        ),
                        "fetched_at": now,
                        "raw_json": _json_dumps(row),
                    },
                )
                count += 1
        return count

    def _em_symbol_report_rows(
        self,
        report_names: Iterable[str],
        symbol: str,
        *,
        page_size: int = 80,
        max_pages: int = 2,
    ) -> list[dict[str, Any]]:
        code = normalize_symbol(symbol)
        market = "SH" if eastmoney_secid(code).startswith("1.") else "SZ"
        filters = [
            f'(SECURITY_CODE="{code}")',
            f'(SECUCODE="{code}.{market}")',
            f'(SECURITY_CODE="{code}")(SECUCODE="{code}.{market}")',
        ]
        last_error: Exception | None = None
        for report_name in report_names:
            for filter_value in filters:
                try:
                    rows = self.em.datacenter_pages(
                        report_name=report_name,
                        filter_value=filter_value,
                        page_size=page_size,
                        max_pages=max_pages,
                    )
                except Exception as exc:  # noqa: BLE001 - try alternate reports/filters.
                    last_error = exc
                    continue
                if rows:
                    return rows
        if last_error:
            raise RuntimeError(str(last_error)) from last_error
        return []

    @staticmethod
    def _amount_to_yi(value: Any) -> float | None:
        amount = safe_float(value)
        if amount is None:
            return None
        return amount / 100000000 if abs(amount) > 10000 else amount

    def _mootdx_finance(self, symbol: str) -> dict[str, Any] | None:
        try:
            from mootdx.quotes import Quotes

            with self._timeout_context(8):
                client = Quotes.factory(market="std")
                data = client.finance(symbol=symbol)
            if isinstance(data, pd.DataFrame):
                if data.empty:
                    return None
                return data.iloc[0].dropna().to_dict()
            return dict(data) if data else None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"mootdx finance unavailable: {exc}") from exc

    def _mootdx_f10(self, symbol: str) -> dict[str, str]:
        client = None
        try:
            from mootdx.quotes import Quotes

            with self._timeout_context(10):
                client = Quotes.factory(market="std")
                sections: dict[str, str] = {}
                for category in F10_CATEGORIES:
                    try:
                        data = client.f10(symbol=symbol, name=category)
                    except TypeError:
                        data = client.f10(symbol=symbol, category=category)
                    if isinstance(data, pd.DataFrame):
                        content = "\n".join(str(value) for value in data.to_numpy().flatten() if str(value).strip())
                    else:
                        content = str(data or "")
                    if content.strip():
                        sections[category] = content.strip()
            return sections
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"mootdx f10 unavailable: {exc}") from exc
        finally:
            close = getattr(client, "close", None) or getattr(client, "disconnect", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.warning("F10 client cleanup failed", exc_info=True)

    def _eastmoney_f10(self, symbol: str) -> dict[str, str]:
        code = normalize_symbol(symbol)
        secid = eastmoney_secid(code)
        market = "SH" if secid.startswith("1.") else "SZ"
        rows = self.em.datacenter(
            report_name="RPT_F10_ORG_BASICINFO",
            filter_value=f'(SECUCODE="{code}.{market}")',
            page_size=1,
        )
        if not rows:
            return {}
        row = rows[0]

        def line(label: str, key: str) -> str | None:
            value = row.get(key)
            if value is None or value == "":
                return None
            return f"{label}: {value}"

        section_fields = {
            "最新提示": [
                ("股票简称", "SECURITY_NAME_ABBR"),
                ("上市日期", "LISTING_DATE"),
                ("交易市场", "TRADE_MARKET"),
                ("发行价", "ISSUE_PRICE"),
                ("所属板块", "BOARD_NAME_1LEVEL"),
                ("二级行业", "BOARD_NAME_2LEVEL"),
                ("三级行业", "BOARD_NAME_3LEVEL"),
            ],
            "公司概况": [
                ("公司名称", "ORG_NAME"),
                ("英文名称", "ORG_NAME_EN"),
                ("成立日期", "FOUND_DATE"),
                ("注册资本", "REG_CAPITAL"),
                ("注册地址", "REG_ADDRESS"),
                ("办公地址", "ADDRESS"),
                ("公司网址", "ORG_WEB"),
                ("联系电话", "ORG_TEL"),
                ("公司简介", "ORG_PROFIE"),
                ("简介摘要", "ORG_PROFILE"),
            ],
            "主营业务": [
                ("主营业务", "MAIN_BUSINESS"),
                ("经营范围", "BUSINESS_SCOPE"),
                ("主要产品", "PRODUCT_NAME"),
                ("收入结构", "INCOME_STRU_NAMENEW"),
                ("结构占比", "INCOME_STRU_RATIONEW"),
                ("最高利润产品", "MAXPROFIT_PRODUCT"),
                ("毛利贡献", "GROSS_PROFIT_RATIO"),
            ],
            "概念题材": [
                ("概念板块", "BLGAINIAN"),
                ("地域板块", "AREA_BOARD_NAME"),
                ("证监会行业", "CSRC_INDUSTRY_NAME"),
                ("公司形态", "ORG_FORM"),
            ],
            "股东研究": [
                ("控股股东", "CONTROL_HOLDER"),
                ("直接持股", "CONTROL_DIRECT_RATIO"),
                ("间接持股", "CONTROL_INDIRECT_RATIO"),
                ("实际控制人", "REAL_CONTROLER"),
                ("实控人间接持股", "REAL_INDIRECT_RATIO"),
            ],
            "公司治理": [
                ("董事长", "CHAIRMAN"),
                ("法人代表", "LEGAL_PERSON"),
                ("总经理", "PRESIDENT"),
                ("董秘", "SECRETARY"),
                ("证券代表", "SECPRESENT"),
                ("独立董事", "INDEDIRECTORS"),
                ("会计师事务所", "ACCOUNT_FIRM"),
                ("法律顾问", "LEGAL_ADVISER"),
            ],
        }
        sections: dict[str, str] = {}
        for category, fields in section_fields.items():
            lines = [item for label, key in fields if (item := line(label, key))]
            if lines:
                sections[category] = "\n".join(lines)
        return sections


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh stock enrichment caches")
    parser.add_argument("--mode", choices=["core", "symbols"], default="core")
    parser.add_argument("--symbols", help="comma separated symbols for --mode symbols")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--trade-date", help="target trade date")
    parser.add_argument("--modules", help="comma separated modules")
    args = parser.parse_args()

    modules = [item.strip() for item in args.modules.split(",") if item.strip()] if args.modules else None
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
    collector = StockEnrichmentCollector()
    from data_job.auto_f10 import enqueue_current_universe

    enqueue_current_universe(collector.engine)
    return 0 if collector.run(
        mode=args.mode,
        symbols=symbols,
        limit=args.limit,
        trade_date=args.trade_date,
        modules=modules,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
