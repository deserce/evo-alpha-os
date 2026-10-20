"""Watchlist/分组股票的独立历史机会遍历。"""

from __future__ import annotations

import fcntl
import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from quant_engine.watch.engine import DB_PATH, WatchEngine, WatchMember
from quant_engine.watch.rules import LowBuyRules, load_active_rules


MAX_SCAN_DATES = 160
MAX_SCAN_SYMBOLS = 200
VALID_SCOPES = {"all_groups", "monitored_groups", "pools"}
VALID_SCAN_ORIGINS = {"MANUAL", "AUTO_MEMBER"}
_SCAN_LOCK = threading.Lock()


class ScanValidationError(ValueError):
    """扫描请求超出范围或参数无效。"""


class ScanBusyError(RuntimeError):
    """已有扫描持有本地单写者资源。"""


class WatchHistoricalScanner:
    """按交易日从旧到新遍历，不写实时快照、事件或投递表。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.engine = WatchEngine(self.db_path)

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        self.engine.ensure_schema(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watch_scan_runs (
                scan_id TEXT PRIMARY KEY,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                scope TEXT NOT NULL,
                pool_ids TEXT NOT NULL,
                rule_version INTEGER NOT NULL,
                rule_config_json TEXT NOT NULL,
                trade_dates_json TEXT NOT NULL,
                universe_json TEXT NOT NULL,
                status TEXT NOT NULL,
                total_symbols INTEGER NOT NULL,
                total_dates INTEGER NOT NULL,
                total_cells INTEGER NOT NULL,
                processed_cells INTEGER NOT NULL DEFAULT 0,
                opportunity_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                origin TEXT NOT NULL DEFAULT 'MANUAL'
            );
            CREATE TABLE IF NOT EXISTS watch_scan_results (
                scan_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                pool_ids TEXT NOT NULL,
                watch_state TEXT NOT NULL,
                previous_state TEXT,
                setup_id TEXT,
                rule_version INTEGER NOT NULL,
                current_price REAL,
                zone_low REAL,
                zone_high REAL,
                trigger_price REAL,
                invalidation_price REAL,
                distance_to_trigger_pct REAL,
                reason TEXT,
                factors TEXT,
                data_status TEXT NOT NULL,
                data_reason TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scan_id, trade_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_watch_scan_status
                ON watch_scan_runs (status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_scan_result_filter
                ON watch_scan_results (scan_id, trade_date, watch_state, symbol);
            """
        )
        run_columns = self.engine._columns(conn, "watch_scan_runs")
        if "origin" not in run_columns:
            conn.execute(
                "ALTER TABLE watch_scan_runs "
                "ADD COLUMN origin TEXT NOT NULL DEFAULT 'MANUAL'"
            )
        conn.commit()

    @staticmethod
    def _normalize_date(value: str, label: str) -> str:
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError as exc:
            raise ScanValidationError(f"{label}必须是 YYYY-MM-DD") from exc

    def _trade_dates(
        self, conn: sqlite3.Connection, start_date: str, end_date: str
    ) -> list[str]:
        if not self.engine._table_exists(conn, "stock_daily_prices"):
            return []
        rows = conn.execute(
            """
            SELECT DISTINCT substr(trade_date, 1, 10)
            FROM stock_daily_prices
            WHERE substr(trade_date, 1, 10) BETWEEN ? AND ?
            ORDER BY substr(trade_date, 1, 10)
            """,
            (start_date, end_date),
        ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def _members(
        self,
        conn: sqlite3.Connection,
        scope: str,
        requested_pool_ids: list[str],
        requested_symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.engine._table_exists(conn, "stock_pool_members") or not self.engine._table_exists(
            conn, "stock_pools"
        ):
            return []
        pool_columns = self.engine._columns(conn, "stock_pools")
        clauses = ["m.symbol IS NOT NULL", "TRIM(m.symbol) <> ''"]
        params: list[Any] = []
        if scope == "monitored_groups":
            if "monitor_enabled" in pool_columns:
                clauses.append("COALESCE(p.monitor_enabled, 0)=1")
            elif "is_default" in pool_columns:
                clauses.append("COALESCE(p.is_default, 0)=1")
            else:
                return []
        elif scope == "pools":
            if not requested_pool_ids:
                raise ScanValidationError("指定分组范围时至少选择一个分组")
            placeholders = ", ".join("?" for _ in requested_pool_ids)
            clauses.append(f"m.pool_id IN ({placeholders})")
            params.extend(requested_pool_ids)
        pool_name = "p.name" if "name" in pool_columns else "p.pool_id"
        rows = conn.execute(
            f"""
            SELECT m.symbol, m.pool_id, {pool_name} AS pool_name
            FROM stock_pool_members m
            JOIN stock_pools p ON p.pool_id=m.pool_id
            WHERE {' AND '.join(clauses)}
            ORDER BY m.symbol, m.pool_id
            """,
            params,
        ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = str(row[0]).strip().upper()
            member = grouped.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "pool_id": str(row[1]),
                    "pool_name": str(row[2] or row[1]),
                    "pool_ids": [],
                },
            )
            member["pool_ids"].append(str(row[1]))
        members = list(grouped.values())
        if requested_symbols is not None:
            requested = set(requested_symbols)
            members = [member for member in members if member["symbol"] in requested]
        return members

    @staticmethod
    def _rules(
        conn: sqlite3.Connection, requested_version: int | None
    ) -> LowBuyRules:
        if requested_version is None:
            return load_active_rules(conn)
        row = conn.execute(
            "SELECT config_json FROM watch_rule_configs WHERE version=?",
            (requested_version,),
        ).fetchone()
        if row is None:
            raise ScanValidationError(f"规则版本 {requested_version} 不存在")
        try:
            values = json.loads(str(row[0]))
        except json.JSONDecodeError as exc:
            raise ScanValidationError(f"规则版本 {requested_version} 配置损坏") from exc
        return LowBuyRules.from_mapping(values, version=requested_version)

    def _preload_lifecycle(
        self,
        conn: sqlite3.Connection,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        if not symbols or not self.engine._table_exists(conn, "stock_lifecycle_state"):
            return {}
        columns = self.engine._columns(conn, "stock_lifecycle_state")
        if not {"symbol", "trade_date"} <= columns:
            return {}
        wanted = [
            column
            for column in (
                "name",
                "sector_name",
                "market_state",
                "industry_state",
                "rps_20",
                "rps_50",
                "rps_120",
                "structure_score",
                "data_status",
                "universe_source",
            )
            if column in columns
        ]
        placeholders = ", ".join("?" for _ in symbols)
        selected = ", ".join(wanted)
        suffix = f", {selected}" if selected else ""
        rows = conn.execute(
            f"""
            SELECT symbol, substr(trade_date, 1, 10){suffix}
            FROM stock_lifecycle_state
            WHERE symbol IN ({placeholders})
              AND substr(trade_date, 1, 10) BETWEEN ? AND ?
            """,
            [*symbols, start_date, end_date],
        ).fetchall()
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            symbol = str(row[0]).strip().upper()
            trade_date = str(row[1])[:10]
            result.setdefault(symbol, {})[trade_date] = dict(zip(wanted, row[2:]))
        self._attach_context_metrics(conn, result, symbols, start_date, end_date)
        return result

    def _attach_context_metrics(
        self,
        conn: sqlite3.Connection,
        lifecycle_rows: dict[str, dict[str, dict[str, Any]]],
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> None:
        """批量附加与实时引擎同口径的因果上下文，避免扫描内层查库。"""

        def query_start(table_name: str) -> str:
            row = conn.execute(
                f"SELECT MAX(substr(trade_date, 1, 10)) FROM {table_name} "
                "WHERE substr(trade_date, 1, 10)<?",
                (start_date,),
            ).fetchone()
            return str(row[0])[:10] if row and row[0] else start_date

        market_by_date: dict[str, tuple[float | None, float | None]] = {}
        market_columns = self.engine._columns(conn, "market_strength_daily")
        if {"trade_date", "index_code", "market_strength_score"} <= market_columns:
            context_start = query_start("market_strength_daily")
            market_rows = conn.execute(
                """
                SELECT substr(trade_date, 1, 10), market_strength_score
                FROM market_strength_daily
                WHERE index_code='000001'
                  AND substr(trade_date, 1, 10) BETWEEN ? AND ?
                ORDER BY substr(trade_date, 1, 10)
                """,
                (context_start, end_date),
            ).fetchall()
            previous_value: float | None = None
            for row in market_rows:
                value = self.engine._number(row[1])
                delta = (
                    value - previous_value
                    if value is not None and previous_value is not None
                    else None
                )
                market_by_date[str(row[0])] = (value, delta)
                if value is not None:
                    previous_value = value

        sector_names = sorted(
            {
                str(values.get("sector_name") or "").strip()
                for dates in lifecycle_rows.values()
                for values in dates.values()
                if str(values.get("sector_name") or "").strip()
            }
        )
        industry_by_key: dict[
            tuple[str, str], tuple[float | None, float | None, float | None, float | None]
        ] = {}
        industry_columns = self.engine._columns(conn, "industry_strength_daily")
        if sector_names and {
            "trade_date",
            "sector_name",
            "industry_strength_score",
            "sector_rps_20",
        } <= industry_columns:
            context_start = query_start("industry_strength_daily")
            placeholders = ", ".join("?" for _ in sector_names)
            rows = conn.execute(
                f"""
                SELECT sector_name, substr(trade_date, 1, 10),
                       industry_strength_score, sector_rps_20
                FROM industry_strength_daily
                WHERE sector_name IN ({placeholders})
                  AND substr(trade_date, 1, 10) BETWEEN ? AND ?
                ORDER BY sector_name, substr(trade_date, 1, 10)
                """,
                [*sector_names, context_start, end_date],
            ).fetchall()
            previous_by_sector: dict[str, tuple[float | None, float | None]] = {}
            for row in rows:
                sector = str(row[0])
                strength = self.engine._number(row[2])
                sector_rps = self.engine._number(row[3])
                previous_strength, previous_rps = previous_by_sector.get(
                    sector, (None, None)
                )
                industry_by_key[(sector, str(row[1]))] = (
                    strength,
                    strength - previous_strength
                    if strength is not None and previous_strength is not None
                    else None,
                    sector_rps,
                    sector_rps - previous_rps
                    if sector_rps is not None and previous_rps is not None
                    else None,
                )
                previous_by_sector[sector] = (strength, sector_rps)

        rps_by_key: dict[tuple[str, str], tuple[float | None, float | None]] = {}
        rps_columns = self.engine._columns(conn, "quant_feature_rps")
        if symbols and {"symbol", "trade_date", "rps_20"} <= rps_columns:
            context_start = query_start("quant_feature_rps")
            placeholders = ", ".join("?" for _ in symbols)
            rows = conn.execute(
                f"""
                SELECT symbol, substr(trade_date, 1, 10), rps_20
                FROM quant_feature_rps
                WHERE symbol IN ({placeholders})
                  AND substr(trade_date, 1, 10) BETWEEN ? AND ?
                ORDER BY symbol, substr(trade_date, 1, 10)
                """,
                [*symbols, context_start, end_date],
            ).fetchall()
            previous_by_symbol: dict[str, float | None] = {}
            for row in rows:
                symbol = str(row[0]).strip().upper()
                value = self.engine._number(row[2])
                previous_value = previous_by_symbol.get(symbol)
                rps_by_key[(symbol, str(row[1]))] = (
                    value,
                    value - previous_value
                    if value is not None and previous_value is not None
                    else None,
                )
                previous_by_symbol[symbol] = value

        for symbol, dates in lifecycle_rows.items():
            for trade_date, values in dates.items():
                market = market_by_date.get(trade_date)
                if market:
                    values["market_strength"], values["market_strength_delta"] = market
                sector_name = str(values.get("sector_name") or "").strip()
                industry = industry_by_key.get((sector_name, trade_date))
                if industry:
                    (
                        values["industry_strength"],
                        values["industry_strength_delta"],
                        values["sector_rps_20"],
                        values["sector_rps_20_delta"],
                    ) = industry
                rps = rps_by_key.get((symbol, trade_date))
                if rps:
                    _, values["rps_20_delta"] = rps

    def _preload_prices(
        self,
        conn: sqlite3.Connection,
        symbols: list[str],
        end_date: str,
    ) -> dict[str, list[dict[str, Any]]]:
        if not symbols or not self.engine._table_exists(conn, "stock_daily_prices"):
            return {}
        columns = self.engine._columns(conn, "stock_daily_prices")
        required = {"symbol", "trade_date", "open", "high", "low", "close", "volume"}
        if not required <= columns:
            return {}
        placeholders = ", ".join("?" for _ in symbols)
        rows = conn.execute(
            f"""
            SELECT symbol, substr(trade_date, 1, 10), open, high, low, close, volume
            FROM stock_daily_prices
            WHERE symbol IN ({placeholders}) AND substr(trade_date, 1, 10)<=?
            ORDER BY symbol, substr(trade_date, 1, 10)
            """,
            [*symbols, end_date],
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            symbol = str(row[0]).strip().upper()
            result.setdefault(symbol, []).append(
                {
                    "trade_date": str(row[1])[:10],
                    "open": self.engine._number(row[2]),
                    "high": self.engine._number(row[3]),
                    "low": self.engine._number(row[4]),
                    "close": self.engine._number(row[5]),
                    "volume": self.engine._number(row[6]),
                }
            )
        return result

    def create_scan(
        self,
        *,
        start_date: str,
        end_date: str,
        scope: str,
        pool_ids: list[str] | None = None,
        rule_version: int | None = None,
        symbols: list[str] | None = None,
        origin: str = "MANUAL",
    ) -> dict[str, Any]:
        normalized_start = self._normalize_date(start_date, "开始日期")
        normalized_end = self._normalize_date(end_date, "结束日期")
        if normalized_start > normalized_end:
            raise ScanValidationError("开始日期不能晚于结束日期")
        if scope not in VALID_SCOPES:
            raise ScanValidationError("扫描范围无效")
        normalized_origin = str(origin or "MANUAL").strip().upper()
        if normalized_origin not in VALID_SCAN_ORIGINS:
            raise ScanValidationError("扫描来源无效")
        normalized_pools = list(
            dict.fromkeys(
                str(pool_id).strip()
                for pool_id in (pool_ids or [])
                if str(pool_id).strip()
            )
        )
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
                raise ScanValidationError("定向扫描股票不能为空")
        with sqlite3.connect(self.db_path, timeout=1) as conn:
            self.ensure_schema(conn)
            dates = self._trade_dates(conn, normalized_start, normalized_end)
            if not dates:
                raise ScanValidationError("所选区间没有可用交易日")
            if len(dates) > MAX_SCAN_DATES:
                raise ScanValidationError(f"单次最多遍历 {MAX_SCAN_DATES} 个交易日")
            members = self._members(
                conn,
                scope,
                normalized_pools,
                requested_symbols=normalized_symbols,
            )
            if not members:
                raise ScanValidationError("所选范围没有股票")
            if len(members) > MAX_SCAN_SYMBOLS:
                raise ScanValidationError(f"单次最多遍历 {MAX_SCAN_SYMBOLS} 只股票")
            rules = self._rules(conn, rule_version)
            scan_id = uuid4().hex
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            total_cells = len(dates) * len(members)
            conn.execute(
                """
                INSERT INTO watch_scan_runs (
                    scan_id, start_date, end_date, scope, pool_ids, rule_version,
                    rule_config_json, trade_dates_json, universe_json, status,
                    total_symbols, total_dates, total_cells, processed_cells,
                    opportunity_count, created_at, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    scan_id,
                    normalized_start,
                    normalized_end,
                    scope,
                    json.dumps(normalized_pools, ensure_ascii=False),
                    rules.version,
                    json.dumps(rules.to_dict(), ensure_ascii=False),
                    json.dumps(dates, ensure_ascii=False),
                    json.dumps(members, ensure_ascii=False),
                    len(members),
                    len(dates),
                    total_cells,
                    now,
                    normalized_origin,
                ),
            )
            conn.commit()
        return self.get_scan(scan_id)

    def get_scan(self, scan_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self.ensure_schema(conn)
            if scan_id == "latest":
                row = conn.execute(
                    "SELECT * FROM watch_scan_runs "
                    "WHERE COALESCE(origin, 'MANUAL')='MANUAL' "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM watch_scan_runs WHERE scan_id=?", (scan_id,)
                ).fetchone()
            if row is None:
                raise KeyError(scan_id)
            result = dict(row)
        for field in ("pool_ids", "rule_config_json", "trade_dates_json", "universe_json"):
            result[field] = json.loads(result[field] or "[]")
        result["progress_pct"] = round(
            result["processed_cells"] / result["total_cells"] * 100, 1
        ) if result["total_cells"] else 100.0
        return result

    def run_scan(self, scan_id: str) -> dict[str, Any]:
        if not _SCAN_LOCK.acquire(blocking=False):
            raise ScanBusyError("已有历史遍历正在执行")
        lock_file = None
        try:
            lock_path = self.db_path.with_suffix(
                f"{self.db_path.suffix}.watch-scan.lock"
            )
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = lock_path.open("a+")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ScanBusyError("已有历史遍历正在执行") from exc
            return self._run_scan_locked(scan_id)
        finally:
            if lock_file is not None:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_file.close()
            _SCAN_LOCK.release()

    def _run_scan_locked(self, scan_id: str) -> dict[str, Any]:
        try:
            with sqlite3.connect(self.db_path, timeout=1) as conn:
                conn.row_factory = sqlite3.Row
                self.ensure_schema(conn)
                run = conn.execute(
                    "SELECT * FROM watch_scan_runs WHERE scan_id=?", (scan_id,)
                ).fetchone()
                if run is None:
                    raise KeyError(scan_id)
                if run["status"] == "COMPLETED":
                    return self.get_scan(scan_id)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "UPDATE watch_scan_runs SET status='RUNNING', started_at=?, error=NULL "
                    "WHERE scan_id=?",
                    (now, scan_id),
                )
                conn.execute("DELETE FROM watch_scan_results WHERE scan_id=?", (scan_id,))
                conn.commit()

                dates: list[str] = json.loads(run["trade_dates_json"])
                members: list[dict[str, Any]] = json.loads(run["universe_json"])
                rules = LowBuyRules.from_mapping(
                    json.loads(run["rule_config_json"]), version=int(run["rule_version"])
                )
                date_positions = {
                    trade_date: index for index, trade_date in enumerate(dates)
                }
                symbols = [str(member["symbol"]) for member in members]
                lifecycle_rows = self._preload_lifecycle(
                    conn, symbols, dates[0], dates[-1]
                )
                price_rows = self._preload_prices(conn, symbols, dates[-1])
                processed = 0
                opportunities = 0
                for member_data in members:
                    member = WatchMember(
                        symbol=member_data["symbol"],
                        pool_id=member_data["pool_id"],
                        pool_name=member_data["pool_name"],
                        watched_since=None,
                    )
                    previous: dict[str, Any] | None = None
                    previous_valid: dict[str, Any] | None = None
                    triggered_index: int | None = None
                    result_rows: list[tuple[Any, ...]] = []
                    symbol_prices = price_rows.get(member.symbol, [])
                    price_position = 0
                    price_history: list[dict[str, Any]] = []
                    for index, trade_date in enumerate(dates):
                        while (
                            price_position < len(symbol_prices)
                            and symbol_prices[price_position]["trade_date"] <= trade_date
                        ):
                            price_history.append(symbol_prices[price_position])
                            price_position += 1
                        lifecycle = lifecycle_rows.get(member.symbol, {}).get(trade_date)
                        prices = price_history[-121:]
                        errors = self.engine._data_errors(lifecycle, prices, trade_date)
                        if errors:
                            snapshot = self.engine._unknown_snapshot(
                                member,
                                trade_date,
                                lifecycle,
                                prices,
                                previous,
                                errors,
                                rules.version,
                            )
                        else:
                            evaluation_previous = previous_valid or previous
                            setup_age_days: int | None = None
                            if (evaluation_previous or {}).get("watch_state") == "IN_ZONE":
                                setup_date = str(
                                    (evaluation_previous or {}).get("setup_id") or ""
                                ).rsplit(":", 1)[-1]
                                setup_index = date_positions.get(setup_date)
                                setup_age_days = (
                                    index - setup_index if setup_index is not None else 999
                                )
                            cooldown_days = (
                                index - triggered_index
                                if triggered_index is not None
                                else 999
                            )
                            snapshot = self.engine._evaluate(
                                conn,
                                member,
                                trade_date,
                                lifecycle or {},
                                prices,
                                evaluation_previous,
                                rules,
                                cooldown_days_override=cooldown_days,
                                setup_age_days_override=setup_age_days,
                            )
                            if snapshot["watch_state"] == "TRIGGERED" and (
                                evaluation_previous or {}
                            ).get("watch_state") != "TRIGGERED":
                                triggered_index = index
                            elif snapshot["watch_state"] != "TRIGGERED" and (
                                evaluation_previous or {}
                            ).get("watch_state") == "TRIGGERED":
                                triggered_index = None
                            previous_valid = snapshot
                        if snapshot["watch_state"] in {"IN_ZONE", "TRIGGERED"}:
                            opportunities += 1
                        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        result_rows.append(
                            (
                                scan_id,
                                trade_date,
                                member.symbol,
                                snapshot.get("name"),
                                json.dumps(member_data["pool_ids"], ensure_ascii=False),
                                snapshot["watch_state"],
                                snapshot.get("previous_state"),
                                snapshot.get("setup_id"),
                                rules.version,
                                snapshot.get("current_price"),
                                snapshot.get("zone_low"),
                                snapshot.get("zone_high"),
                                snapshot.get("trigger_price"),
                                snapshot.get("invalidation_price"),
                                snapshot.get("distance_to_trigger_pct"),
                                snapshot.get("reason"),
                                snapshot.get("factors") or "{}",
                                snapshot["data_status"],
                                snapshot.get("data_reason"),
                                created_at,
                            )
                        )
                        previous = snapshot
                    conn.executemany(
                        """
                        INSERT INTO watch_scan_results (
                            scan_id, trade_date, symbol, name, pool_ids, watch_state,
                            previous_state, setup_id, rule_version, current_price,
                            zone_low, zone_high, trigger_price, invalidation_price,
                            distance_to_trigger_pct, reason, factors, data_status,
                            data_reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        result_rows,
                    )
                    processed += len(dates)
                    conn.execute(
                        "UPDATE watch_scan_runs SET processed_cells=?, opportunity_count=? "
                        "WHERE scan_id=?",
                        (processed, opportunities, scan_id),
                    )
                    conn.commit()
                completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "UPDATE watch_scan_runs SET status='COMPLETED', completed_at=? "
                    "WHERE scan_id=?",
                    (completed_at, scan_id),
                )
                conn.commit()
            return self.get_scan(scan_id)
        except Exception as exc:
            try:
                with sqlite3.connect(self.db_path, timeout=1) as conn:
                    conn.execute(
                        "UPDATE watch_scan_runs SET status='FAILED', error=?, completed_at=? "
                        "WHERE scan_id=?",
                        (
                            str(exc),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            scan_id,
                        ),
                    )
                    conn.commit()
            except sqlite3.Error:
                pass
            raise
