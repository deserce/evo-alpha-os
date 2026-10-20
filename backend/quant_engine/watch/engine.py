"""手工重点池的盘后低吸状态机。

该模块只生成可审计的每日快照和状态变化事件；通知投递由独立模块消费
``watch_events``，避免数据计算与外部网络调用耦合。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from quant_engine.watch.rules import LowBuyRules, ensure_rule_schema, load_active_rules

DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
logger = logging.getLogger(__name__)

VALID_DATA_STATUSES = {"OK", "VALID", "COMPLETE", "READY"}
VALID_WATCH_STATES = {"WATCHING", "IN_ZONE", "TRIGGERED", "INVALIDATED", "UNKNOWN"}


@dataclass(frozen=True)
class WatchMember:
    symbol: str
    pool_id: str
    pool_name: str
    watched_since: str | None


class WatchEngine:
    """计算启用持续跟踪的股票池成员的盘后状态。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone() is not None

    @staticmethod
    def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        if not WatchEngine._table_exists(conn, table_name):
            return set()
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}

    @staticmethod
    def _ensure_columns(
        conn: sqlite3.Connection,
        table_name: str,
        definitions: dict[str, str],
    ) -> None:
        columns = WatchEngine._columns(conn, table_name)
        for name, definition in definitions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        """创建表，并为早期本地表安全补列。"""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watch_daily_snapshot (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                pool_id TEXT,
                pool_name TEXT,
                watch_state TEXT NOT NULL,
                previous_state TEXT,
                setup_id TEXT,
                rule_version INTEGER,
                universe_source TEXT,
                watched_since TEXT,
                tracking_days INTEGER DEFAULT 0,
                current_price REAL,
                zone_low REAL,
                zone_high REAL,
                trigger_price REAL,
                intraday_trigger_price REAL,
                invalidation_price REAL,
                distance_to_trigger_pct REAL,
                reason TEXT,
                factors TEXT,
                data_status TEXT NOT NULL,
                data_reason TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (trade_date, symbol)
            );
            CREATE TABLE IF NOT EXISTS watch_events (
                event_id TEXT PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                trade_date TEXT NOT NULL,
                symbol TEXT,
                setup_id TEXT,
                event_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'UNREAD',
                snoozed_until TEXT,
                payload TEXT,
                acknowledged_at TEXT,
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                delivery_id TEXT PRIMARY KEY,
                event_id TEXT,
                channel TEXT NOT NULL DEFAULT 'email',
                delivery_type TEXT NOT NULL DEFAULT 'event',
                dedupe_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                next_retry_at TEXT,
                sent_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._ensure_columns(
            conn,
            "watch_daily_snapshot",
            {
                "name": "TEXT",
                "pool_id": "TEXT",
                "pool_name": "TEXT",
                "watch_state": "TEXT",
                "previous_state": "TEXT",
                "setup_id": "TEXT",
                "rule_version": "INTEGER",
                "universe_source": "TEXT",
                "watched_since": "TEXT",
                "tracking_days": "INTEGER DEFAULT 0",
                "current_price": "REAL",
                "zone_low": "REAL",
                "zone_high": "REAL",
                "trigger_price": "REAL",
                "intraday_trigger_price": "REAL",
                "invalidation_price": "REAL",
                "distance_to_trigger_pct": "REAL",
                "reason": "TEXT",
                "factors": "TEXT",
                "data_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                "data_reason": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "watch_events",
            {
                "dedupe_key": "TEXT",
                "trade_date": "TEXT",
                "symbol": "TEXT",
                "setup_id": "TEXT",
                "event_type": "TEXT",
                "from_state": "TEXT",
                "to_state": "TEXT",
                "severity": "TEXT NOT NULL DEFAULT 'INFO'",
                "title": "TEXT",
                "message": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'UNREAD'",
                "snoozed_until": "TEXT",
                "payload": "TEXT",
                "acknowledged_at": "TEXT",
                "resolved_at": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "notification_deliveries",
            {
                "event_id": "TEXT",
                "channel": "TEXT NOT NULL DEFAULT 'email'",
                "delivery_type": "TEXT NOT NULL DEFAULT 'event'",
                "dedupe_key": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'PENDING'",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "last_error": "TEXT",
                "next_retry_at": "TEXT",
                "sent_at": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        snapshot_columns = self._columns(conn, "watch_daily_snapshot")
        if "state" in snapshot_columns:
            conn.execute(
                "UPDATE watch_daily_snapshot SET watch_state=state "
                "WHERE watch_state IS NULL OR watch_state=''"
            )
        conn.execute(
            "UPDATE watch_events SET dedupe_key='legacy:' || event_id "
            "WHERE dedupe_key IS NULL OR dedupe_key=''"
        )
        conn.execute(
            "UPDATE notification_deliveries SET dedupe_key='legacy:' || delivery_id "
            "WHERE dedupe_key IS NULL OR dedupe_key=''"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_events_dedupe "
            "ON watch_events (dedupe_key)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_delivery_dedupe "
            "ON notification_deliveries (dedupe_key)"
        )
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_watch_snapshot_state
                ON watch_daily_snapshot (trade_date, watch_state);
            CREATE INDEX IF NOT EXISTS idx_watch_snapshot_symbol
                ON watch_daily_snapshot (symbol, trade_date);
            CREATE INDEX IF NOT EXISTS idx_watch_events_status
                ON watch_events (status, trade_date);
            CREATE INDEX IF NOT EXISTS idx_watch_events_symbol
                ON watch_events (symbol, trade_date);
            CREATE INDEX IF NOT EXISTS idx_notification_delivery_status
                ON notification_deliveries (status, next_retry_at);
            """
        )
        if {"symbol", "trade_date"} <= self._columns(conn, "stock_lifecycle_state"):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lifecycle_symbol_date "
                "ON stock_lifecycle_state (symbol, trade_date)"
            )
        if {"sector_name", "trade_date"} <= self._columns(
            conn, "industry_strength_daily"
        ):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_industry_sector_date "
                "ON industry_strength_daily (sector_name, trade_date)"
            )
        ensure_rule_schema(conn)
        conn.commit()

    def _output_date(self, conn: sqlite3.Connection, requested: str | None) -> str | None:
        if requested:
            try:
                return date.fromisoformat(str(requested)[:10]).isoformat()
            except ValueError as exc:
                raise ValueError(f"无效交易日期: {requested}") from exc
        if not self._table_exists(conn, "stock_daily_prices"):
            return None
        row = conn.execute("SELECT MAX(trade_date) FROM stock_daily_prices").fetchone()
        return str(row[0])[:10] if row and row[0] else None

    @staticmethod
    def _day_bounds(value: str) -> tuple[str, str]:
        """返回可直接使用 trade_date 索引的当日半开区间。"""
        current = date.fromisoformat(str(value)[:10])
        return current.isoformat(), (current + timedelta(days=1)).isoformat()

    def _monitored_members(self, conn: sqlite3.Connection) -> list[WatchMember]:
        if not self._table_exists(conn, "stock_pools") or not self._table_exists(
            conn, "stock_pool_members"
        ):
            return []
        pool_columns = self._columns(conn, "stock_pools")
        member_columns = self._columns(conn, "stock_pool_members")
        if "monitor_enabled" in pool_columns:
            enabled_clause = "COALESCE(p.monitor_enabled, 0) = 1"
        elif "is_default" in pool_columns:
            # 兼容迁移尚未执行的旧库。
            # 仅默认自选池进入监控，避免扩大范围。
            enabled_clause = "COALESCE(p.is_default, 0) = 1"
        else:
            return []
        added_at = "m.added_at" if "added_at" in member_columns else "NULL"
        pool_name = "p.name" if "name" in pool_columns else "p.pool_id"
        rows = conn.execute(
            f"""
            SELECT m.symbol, p.pool_id, {pool_name} AS pool_name, {added_at} AS watched_since
            FROM stock_pool_members m
            JOIN stock_pools p ON p.pool_id = m.pool_id
            WHERE {enabled_clause}
              AND m.symbol IS NOT NULL AND TRIM(m.symbol) <> ''
            ORDER BY COALESCE(p.is_default, 0) DESC, p.pool_id
            """
            if "is_default" in pool_columns
            else f"""
            SELECT m.symbol, p.pool_id, {pool_name} AS pool_name, {added_at} AS watched_since
            FROM stock_pool_members m
            JOIN stock_pools p ON p.pool_id = m.pool_id
            WHERE {enabled_clause}
              AND m.symbol IS NOT NULL AND TRIM(m.symbol) <> ''
            ORDER BY p.pool_id
            """
        ).fetchall()
        members: dict[str, WatchMember] = {}
        for row in rows:
            symbol = str(row[0]).strip().upper()
            watched_since = str(row[3])[:10] if row[3] else None
            members.setdefault(
                symbol,
                WatchMember(symbol, str(row[1]), str(row[2] or row[1]), watched_since),
            )
        return list(members.values())

    def _lifecycle(
        self, conn: sqlite3.Connection, symbol: str, trade_date: str
    ) -> dict[str, Any] | None:
        if not self._table_exists(conn, "stock_lifecycle_state"):
            return None
        columns = self._columns(conn, "stock_lifecycle_state")
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
        if not {"symbol", "trade_date"} <= columns:
            return None
        select = ", ".join(wanted) or "symbol"
        day_start, day_end = self._day_bounds(trade_date)
        row = conn.execute(
            f"SELECT {select} FROM stock_lifecycle_state "
            "WHERE symbol=? AND trade_date>=? AND trade_date<? LIMIT 1",
            (symbol, day_start, day_end),
        ).fetchone()
        if row is None:
            return None
        result = dict(zip(wanted or ["symbol"], row))
        result.update(
            self._context_metrics(
                conn,
                symbol=symbol,
                sector_name=str(result.get("sector_name") or "").strip(),
                trade_date=trade_date,
            )
        )
        return result

    def _context_metrics(
        self,
        conn: sqlite3.Connection,
        *,
        symbol: str,
        sector_name: str,
        trade_date: str,
    ) -> dict[str, float | None]:
        """读取只使用目标日及之前数据的市场、行业和 RPS 变化。"""
        result: dict[str, float | None] = {}

        def add_pair(prefix: str, rows: list[sqlite3.Row | tuple[Any, ...]]) -> None:
            values = [self._number(row[0]) for row in rows]
            values = [value for value in values if value is not None]
            if values:
                result[prefix] = values[0]
            if len(values) >= 2:
                result[f"{prefix}_delta"] = values[0] - values[1]

        _, day_end = self._day_bounds(trade_date)
        market_columns = self._columns(conn, "market_strength_daily")
        if {"trade_date", "index_code", "market_strength_score"} <= market_columns:
            rows = conn.execute(
                """
                SELECT market_strength_score
                FROM market_strength_daily
                WHERE index_code='000001' AND trade_date<?
                ORDER BY trade_date DESC
                LIMIT 2
                """,
                (day_end,),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT substr(trade_date, 1, 10) AS day,
                               market_strength_score,
                               ROW_NUMBER() OVER (
                                   PARTITION BY substr(trade_date, 1, 10)
                                   ORDER BY market_strength_score DESC
                               ) AS rank_in_day
                        FROM market_strength_daily
                        WHERE trade_date<?
                    )
                    SELECT market_strength_score
                    FROM ranked
                    WHERE rank_in_day=1
                    ORDER BY day DESC
                    LIMIT 2
                    """,
                    (day_end,),
                ).fetchall()
            add_pair("market_strength", rows)

        industry_columns = self._columns(conn, "industry_strength_daily")
        if sector_name and {
            "trade_date",
            "sector_name",
            "industry_strength_score",
            "sector_rps_20",
        } <= industry_columns:
            rows = conn.execute(
                """
                SELECT industry_strength_score, sector_rps_20
                FROM industry_strength_daily
                WHERE sector_name=? AND trade_date<?
                ORDER BY trade_date DESC
                LIMIT 2
                """,
                (sector_name, day_end),
            ).fetchall()
            add_pair("industry_strength", [(row[0],) for row in rows])
            add_pair("sector_rps_20", [(row[1],) for row in rows])

        rps_columns = self._columns(conn, "quant_feature_rps")
        if {"symbol", "trade_date", "rps_20"} <= rps_columns:
            rows = conn.execute(
                """
                SELECT rps_20
                FROM quant_feature_rps
                WHERE symbol=? AND trade_date<?
                ORDER BY trade_date DESC
                LIMIT 2
                """,
                (symbol, day_end),
            ).fetchall()
            add_pair("rps_20_context", rows)
            if "rps_20_context_delta" in result:
                result["rps_20_delta"] = result["rps_20_context_delta"]
        return result

    def _prices(
        self, conn: sqlite3.Connection, symbol: str, trade_date: str
    ) -> list[dict[str, float | str]]:
        if not self._table_exists(conn, "stock_daily_prices"):
            return []
        columns = self._columns(conn, "stock_daily_prices")
        required = {"symbol", "trade_date", "open", "high", "low", "close", "volume"}
        if not required <= columns:
            return []
        _, day_end = self._day_bounds(trade_date)
        rows = conn.execute(
            """
            SELECT substr(trade_date, 1, 10), open, high, low, close, volume
            FROM stock_daily_prices
            WHERE symbol=? AND trade_date<?
            ORDER BY trade_date DESC
            LIMIT 121
            """,
            (symbol, day_end),
        ).fetchall()
        result = []
        for row in reversed(rows):
            values = [self._number(value) for value in row[1:]]
            columns = ("trade_date", "open", "high", "low", "close", "volume")
            result.append(dict(zip(columns, (str(row[0]), *values))))
        return result

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _previous_snapshot(
        self, conn: sqlite3.Connection, symbol: str, trade_date: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT * FROM watch_daily_snapshot
            WHERE symbol=? AND trade_date<?
            ORDER BY trade_date DESC LIMIT 1
            """,
            (symbol, trade_date),
        ).fetchone()
        return dict(row) if row else None

    def _previous_valid_snapshot(
        self, conn: sqlite3.Connection, symbol: str, trade_date: str
    ) -> dict[str, Any] | None:
        """跳过 UNKNOWN 数据日，找到状态机最后一个可信状态。"""
        row = conn.execute(
            """
            SELECT * FROM watch_daily_snapshot
            WHERE symbol=? AND trade_date<?
              AND watch_state<>'UNKNOWN' AND data_status='OK'
            ORDER BY trade_date DESC LIMIT 1
            """,
            (symbol, trade_date),
        ).fetchone()
        return dict(row) if row else None

    def _trade_days_since_trigger(
        self, conn: sqlite3.Connection, symbol: str, setup_id: str | None, trade_date: str
    ) -> int:
        if not setup_id:
            return 999
        row = conn.execute(
            """
            SELECT trade_date FROM watch_events
            WHERE symbol=? AND setup_id=? AND event_type='TRIGGERED'
            ORDER BY trade_date DESC LIMIT 1
            """,
            (symbol, setup_id),
        ).fetchone()
        if not row or not self._table_exists(conn, "stock_daily_prices"):
            return 999
        _, range_start = self._day_bounds(str(row[0]))
        _, range_end = self._day_bounds(trade_date)
        count = conn.execute(
            """
            SELECT COUNT(DISTINCT substr(trade_date, 1, 10))
            FROM stock_daily_prices
            WHERE trade_date>=? AND trade_date<?
            """,
            (range_start, range_end),
        ).fetchone()
        return int(count[0] or 0)

    def _trade_days_since_setup(
        self, conn: sqlite3.Connection, setup_id: str | None, trade_date: str
    ) -> int:
        """计算 setup 建立后的市场交易日数，建立当日为 0。"""
        if not setup_id or not self._table_exists(conn, "stock_daily_prices"):
            return 999
        setup_date = str(setup_id).rsplit(":", 1)[-1]
        try:
            setup_date = date.fromisoformat(setup_date).isoformat()
        except ValueError:
            return 999
        _, range_start = self._day_bounds(setup_date)
        _, range_end = self._day_bounds(trade_date)
        count = conn.execute(
            """
            SELECT COUNT(DISTINCT substr(trade_date, 1, 10))
            FROM stock_daily_prices
            WHERE trade_date>=? AND trade_date<?
            """,
            (range_start, range_end),
        ).fetchone()
        return int(count[0] or 0)

    def _data_errors(
        self,
        lifecycle: dict[str, Any] | None,
        prices: list[dict[str, Any]],
        trade_date: str,
    ) -> list[str]:
        errors: list[str] = []
        if lifecycle is None:
            errors.append("lifecycle_missing")
        else:
            for column in ("market_state", "industry_state", "rps_50", "structure_score"):
                if lifecycle.get(column) is None or lifecycle.get(column) == "":
                    errors.append(f"{column}_missing")
            source_status = str(lifecycle.get("data_status") or "OK").upper()
            if source_status not in VALID_DATA_STATUSES:
                errors.append(f"lifecycle_data_status_{source_status.lower()}")
        if not prices or prices[-1].get("trade_date") != trade_date:
            errors.append("daily_price_missing")
        elif len(prices) < 60:
            errors.append("daily_price_history_lt_60")
        else:
            for column in ("open", "high", "low", "close", "volume"):
                if prices[-1].get(column) is None:
                    errors.append(f"daily_price_{column}_missing")
        return sorted(set(errors))

    def _factors(
        self,
        prices: list[dict[str, Any]],
        lifecycle: dict[str, Any],
        rules: LowBuyRules,
    ) -> dict[str, Any]:
        window = prices[-60:]
        current = window[-1]
        closes = [float(row["close"]) for row in window if row["close"] is not None]
        highs = [float(row["high"]) for row in window if row["high"] is not None]
        lows = [float(row["low"]) for row in window if row["low"] is not None]
        volumes = [float(row["volume"]) for row in window if row["volume"] is not None]
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50
        volume_ma20 = sum(volumes[-20:]) / 20
        high60 = max(highs)
        low20 = min(lows[-20:])
        true_ranges: list[float] = []
        for index, row in enumerate(window[-21:]):
            if index == 0:
                continue
            prev_close = float(window[-21:][index - 1]["close"])
            high = float(row["high"])
            low = float(row["low"])
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr20 = sum(true_ranges[-20:]) / len(true_ranges[-20:])
        close = float(current["close"])
        invalidation = max(low20 * 0.99, close - 2 * atr20, close * 0.92)

        macd_closes = [
            float(row["close"])
            for row in prices[-120:]
            if row.get("close") is not None
        ]

        def ema(values: list[float], span: int) -> list[float]:
            alpha = 2 / (span + 1)
            result = [values[0]]
            for value in values[1:]:
                result.append(alpha * value + (1 - alpha) * result[-1])
            return result

        ema12 = ema(macd_closes, 12)
        ema26 = ema(macd_closes, 26)
        dif_values = [fast - slow for fast, slow in zip(ema12, ema26)]
        dea_values = ema(dif_values, 9)
        macd_hist_values = [
            2 * (dif - dea) for dif, dea in zip(dif_values, dea_values)
        ]
        macd_hist = macd_hist_values[-1]
        macd_hist_previous = macd_hist_values[-2]
        macd_momentum_turn_up = macd_hist > macd_hist_previous
        macd_hist_improving = macd_hist < 0 and all(
            macd_hist_values[-offset] > macd_hist_values[-offset - 1]
            for offset in range(1, rules.macd_improve_days + 1)
        )
        macd_hist_crossed_zero = macd_hist_previous <= 0 < macd_hist

        structure_rows = prices[-(rules.structure_lookback_days + 2) :]
        structure_lows = [float(row["low"]) for row in structure_rows]
        swing_lows: list[tuple[int, float]] = []
        for index in range(1, len(structure_lows) - 1):
            previous_low = structure_lows[index - 1]
            current_low = structure_lows[index]
            next_low = structure_lows[index + 1]
            if (current_low <= previous_low and current_low < next_low) or (
                current_low < previous_low and current_low <= next_low
            ):
                swing_lows.append((index, current_low))
        first_swing_low: float | None = None
        second_swing_low: float | None = None
        swing_low_gap_days: int | None = None
        double_bottom_diff_pct: float | None = None
        double_bottom = False
        higher_swing_low = False
        if len(swing_lows) >= 2:
            (first_index, first_swing_low), (
                second_index,
                second_swing_low,
            ) = swing_lows[-2:]
            swing_low_gap_days = second_index - first_index
            second_low_age = len(structure_lows) - 1 - second_index
            pair_is_current = swing_low_gap_days >= 2 and second_low_age <= 3
            double_bottom_diff_pct = (
                abs(second_swing_low - first_swing_low) / first_swing_low * 100
            )
            double_bottom = pair_is_current and (
                double_bottom_diff_pct <= rules.double_bottom_tolerance_pct
            )
            higher_swing_low = pair_is_current and second_swing_low > first_swing_low

        current_open = float(current["open"])
        current_high = float(current["high"])
        current_low = float(current["low"])
        previous_close = float(prices[-2]["close"])
        candle_range = max(current_high - current_low, 1e-9)
        close_position = (close - current_low) / candle_range
        bullish_reversal = (
            close > current_open and close > previous_close and close_position >= 0.60
        )
        previous_five_lows = lows[-6:-1]
        low_rejection = (
            bullish_reversal
            and bool(previous_five_lows)
            and current_low <= min(previous_five_lows) * 1.01
        )
        previous_day_is_pivot = len(lows) >= 5 and lows[-2] <= min(lows[-5:-2])
        short_higher_low = (
            current_low > lows[-2] and previous_day_is_pivot
        ) or (len(lows) >= 3 and lows[-1] > lows[-2] > lows[-3])
        price_structure_confirmed = (
            low_rejection or short_higher_low or double_bottom or higher_swing_low
        )
        structure_macd_confirmed = (
            (double_bottom or higher_swing_low)
            and (macd_momentum_turn_up or macd_hist_crossed_zero)
        )

        rps_20_delta = self._number(lifecycle.get("rps_20_delta")) or 0.0
        sector_rps_20_delta = (
            self._number(lifecycle.get("sector_rps_20_delta")) or 0.0
        )
        relative_strength_turn_up = (
            rps_20_delta >= 3.0 or sector_rps_20_delta >= 5.0
        )
        repair_confirmation_count = sum(
            (
                bullish_reversal,
                price_structure_confirmed,
                macd_momentum_turn_up,
                relative_strength_turn_up,
            )
        )

        trigger_candidates: list[float] = []
        if "break_prev_high" in rules.confirmation_methods:
            trigger_candidates.append(float(prices[-2]["high"]))
        if "reclaim_ma10" in rules.confirmation_methods:
            trigger_candidates.append(ma10)
        trigger = min(trigger_candidates) if trigger_candidates else None
        intraday_trigger = min(float(prices[-2]["high"]), ma10)
        shrink_days = rules.volume_shrink_days
        volume_shrinking = all(
            earlier > later
            for earlier, later in zip(
                volumes[-shrink_days:], volumes[-shrink_days + 1 :]
            )
        )
        result = {
            "market_state": lifecycle.get("market_state"),
            "industry_state": lifecycle.get("industry_state"),
            "market_strength": self._number(lifecycle.get("market_strength")),
            "market_strength_delta": self._number(
                lifecycle.get("market_strength_delta")
            ),
            "industry_strength": self._number(lifecycle.get("industry_strength")),
            "industry_strength_delta": self._number(
                lifecycle.get("industry_strength_delta")
            ),
            "sector_rps_20": self._number(lifecycle.get("sector_rps_20")),
            "sector_rps_20_delta": self._number(
                lifecycle.get("sector_rps_20_delta")
            ),
            "rps_20": self._number(lifecycle.get("rps_20")),
            "rps_50": self._number(lifecycle.get("rps_50")),
            "rps_120": self._number(lifecycle.get("rps_120")),
            "rps_20_delta": self._number(lifecycle.get("rps_20_delta")),
            "structure_score": self._number(lifecycle.get("structure_score")),
            "open": current_open,
            "high": current_high,
            "low": current_low,
            "close": close,
            "prev_close": previous_close,
            "prev_high": self._number(prices[-2]["high"]),
            "ma_10": ma10,
            "ma_20": ma20,
            "ma_50": ma50,
            "volume": self._number(current["volume"]),
            "previous_volume": self._number(prices[-2]["volume"]),
            "volume_ma20": volume_ma20,
            "volume_ratio_pct": (
                float(current["volume"]) / volume_ma20 * 100 if volume_ma20 else None
            ),
            "volume_down_3": volumes[-3] > volumes[-2] > volumes[-1],
            "volume_shrinking": volume_shrinking,
            "high_60": high60,
            "pullback_pct": (high60 - close) / high60,
            "atr_20": atr20,
            "low_20": low20,
            "zone_low": high60 * (1 - rules.pullback_max_pct / 100),
            "zone_high": high60 * (1 - rules.pullback_min_pct / 100),
            "trigger_price": trigger,
            "intraday_trigger_price": intraday_trigger,
            "invalidation_price": invalidation,
            "stop_loss_pct": (close - invalidation) / close,
            "double_bottom": double_bottom,
            "higher_swing_low": higher_swing_low,
            "first_swing_low": first_swing_low,
            "second_swing_low": second_swing_low,
            "swing_low_gap_days": swing_low_gap_days,
            "double_bottom_diff_pct": double_bottom_diff_pct,
            "macd_dif": dif_values[-1],
            "macd_dea": dea_values[-1],
            "macd_hist": macd_hist,
            "macd_hist_previous": macd_hist_previous,
            "macd_hist_improving": macd_hist_improving,
            "macd_hist_crossed_zero": macd_hist_crossed_zero,
            "macd_momentum_turn_up": macd_momentum_turn_up,
            "close_position": close_position,
            "pct_chg": (close / previous_close - 1) * 100,
            "bullish_reversal": bullish_reversal,
            "low_rejection": low_rejection,
            "short_higher_low": short_higher_low,
            "price_structure_confirmed": price_structure_confirmed,
            "relative_strength_turn_up": relative_strength_turn_up,
            "repair_confirmation_count": repair_confirmation_count,
            "structure_macd_confirmed": structure_macd_confirmed,
        }
        result["constructive_consolidation"] = self._is_constructive_consolidation(
            result, rules
        )
        return result

    @staticmethod
    def _base_qualified(factors: dict[str, Any], rules: LowBuyRules) -> bool:
        return (
            factors["market_state"] in rules.market_states
            and factors["industry_state"] in rules.industry_states
            and (factors["rps_50"] or 0) >= rules.rps50_min
            and (factors["structure_score"] or 0) >= rules.structure_score_min
        )

    @staticmethod
    def _in_zone(factors: dict[str, Any], rules: LowBuyRules) -> bool:
        close = factors["close"]
        support_averages = [factors["ma_20"], factors["ma_50"]]
        if rules.trend_include_ma10_support:
            support_averages.append(factors["ma_10"])
        near_ma = (
            min(abs(close - moving_average) for moving_average in support_averages)
            / close
            <= rules.near_ma_pct / 100
        )
        volume_ok = factors["volume_shrinking"] or factors["volume"] <= (
            factors["volume_ma20"] * rules.volume_ratio_max_pct / 100
        )
        above_ma50 = not rules.require_above_ma50 or close >= factors["ma_50"]
        return (
            rules.pullback_min_pct / 100
            <= factors["pullback_pct"]
            <= rules.pullback_max_pct / 100
            and above_ma50
            and near_ma
            and volume_ok
            and 0 < factors["stop_loss_pct"] <= rules.max_stop_loss_pct / 100
        )

    @staticmethod
    def _repair_setup_score(
        factors: dict[str, Any], rules: LowBuyRules
    ) -> dict[str, Any]:
        """评估领先股在市场/行业尚未完全升级时的修复机会。

        得分用于排序，硬拦截用于防止将放量下跌或趋势破坏误判为低吸。
        """
        number = WatchEngine._number
        market_state = str(factors.get("market_state") or "")
        industry_state = str(factors.get("industry_state") or "")
        market_delta = number(factors.get("market_strength_delta")) or 0.0
        industry_delta = number(factors.get("industry_strength_delta")) or 0.0
        sector_rps20 = number(factors.get("sector_rps_20")) or 0.0

        market_points = {"M0": 0, "M1": 6, "M2": 12, "M3": 16, "M4": 20}.get(
            market_state, 0
        )
        if market_delta >= rules.market_rebound_delta_min:
            market_points += 5

        industry_points = {
            "I0": 0,
            "I1": 4,
            "I2": 8,
            "I3": 12,
            "I4": 15,
        }.get(industry_state, 0)
        if industry_delta >= rules.industry_rebound_delta_min:
            industry_points += 5
        if sector_rps20 >= 70:
            industry_points += 5

        rps_values = [
            number(factors.get(name)) or 0.0
            for name in ("rps_20", "rps_50", "rps_120")
        ]
        rps_anchor = max(rps_values)
        if rps_anchor >= 90:
            rps_points = 15
        elif rps_anchor >= 80:
            rps_points = 12
        elif rps_anchor >= 70:
            rps_points = 9
        elif rps_anchor >= 60:
            rps_points = 6
        else:
            rps_points = 0

        structure_score = number(factors.get("structure_score")) or 0.0
        if structure_score >= 70:
            structure_points = 12
        elif structure_score >= 50:
            structure_points = 8
        elif structure_score >= 40:
            structure_points = 4
        else:
            structure_points = 0
        if factors.get("double_bottom") or factors.get("higher_swing_low"):
            structure_points += 8

        pullback_pct = (number(factors.get("pullback_pct")) or 0.0) * 100
        close = number(factors.get("close")) or 0.0
        ma20 = number(factors.get("ma_20")) or 0.0
        ma50 = number(factors.get("ma_50")) or 0.0
        volume = number(factors.get("volume")) or 0.0
        volume_ma20 = number(factors.get("volume_ma20")) or 0.0
        stop_loss_pct = (number(factors.get("stop_loss_pct")) or 0.0) * 100
        volume_ratio_pct = (
            volume / volume_ma20 * 100 if volume_ma20 > 0 else float("inf")
        )
        bullish_volume_exception = bool(factors.get("bullish_reversal")) and (
            close > (number(factors.get("prev_close")) or close)
            and volume_ratio_pct <= rules.repair_reversal_volume_ratio_max_pct
        )
        volume_ok = bool(factors.get("volume_shrinking")) or (
            volume_ma20 > 0
            and volume_ratio_pct <= rules.repair_volume_ratio_max_pct
        ) or bullish_volume_exception
        ma50_below_pct = (
            max(ma50 - close, 0) / ma50 * 100 if ma50 > 0 and close > 0 else 100.0
        )
        near_ma_pct = (
            min(abs(close - ma20), abs(close - ma50)) / close * 100
            if close > 0 and ma20 > 0 and ma50 > 0
            else 100.0
        )
        location_points = 0
        if rules.pullback_min_pct <= pullback_pct <= rules.repair_pullback_max_pct:
            location_points += 6
        if near_ma_pct <= 5:
            location_points += 5
        elif ma50_below_pct <= rules.repair_ma50_below_max_pct:
            location_points += 3
        if volume_ok:
            location_points += 5
        if 0 < stop_loss_pct <= rules.max_stop_loss_pct:
            location_points += 4

        components = {
            "market": market_points,
            "industry": industry_points,
            "rps": rps_points,
            "structure": structure_points,
            "location_risk": location_points,
        }
        total = float(sum(components.values()))
        blocked_by: list[str] = []
        if not rules.repair_path_enabled:
            blocked_by.append("disabled")
        if market_state not in rules.repair_market_states:
            blocked_by.append("market")
        if industry_state not in rules.repair_industry_states:
            blocked_by.append("industry")
        if not rules.pullback_min_pct <= pullback_pct <= rules.repair_pullback_max_pct:
            blocked_by.append("pullback")
        if ma50_below_pct > rules.repair_ma50_below_max_pct:
            blocked_by.append("ma50")
        if not volume_ok:
            blocked_by.append("volume")
        if not 0 < stop_loss_pct <= rules.max_stop_loss_pct:
            blocked_by.append("stop")
        has_context_rebound = (
            market_delta >= rules.market_rebound_delta_min
            or industry_delta >= rules.industry_rebound_delta_min
        )
        if (
            rps_anchor < rules.repair_min_rps_anchor
            and structure_score < 50
            and not has_context_rebound
        ):
            blocked_by.append("anchor")
        if total < rules.repair_setup_score_min:
            blocked_by.append("score")

        confidence = "HIGH" if total >= 70 else "MEDIUM" if total >= 60 else "LOW"
        return {
            "total": round(total, 2),
            "components": components,
            "blocked_by": blocked_by,
            "qualified": not blocked_by,
            "confidence": confidence,
            "bullish_volume_exception": bullish_volume_exception,
        }

    @staticmethod
    def _is_constructive_consolidation(
        factors: dict[str, Any], rules: LowBuyRules
    ) -> bool:
        """路径 B 的受控小阴线确认：不以突破换取时效，严格约束回落质量。"""
        number = WatchEngine._number
        pct_chg = number(factors.get("pct_chg"))
        close = number(factors.get("close"))
        ma10 = number(factors.get("ma_10"))
        ma20 = number(factors.get("ma_20"))
        volume = number(factors.get("volume"))
        previous_volume = number(factors.get("previous_volume"))
        return bool(
            pct_chg is not None
            and -rules.constructive_max_decline_pct <= pct_chg <= 0
            and close is not None
            and ma10 is not None
            and ma20 is not None
            and close >= ma10
            and close >= ma20
            and volume is not None
            and previous_volume is not None
            and volume < previous_volume
            and factors.get("short_higher_low")
            and (
                factors.get("macd_hist_crossed_zero")
                or factors.get("macd_hist_improving")
            )
        )

    @staticmethod
    def _confirmed(
        factors: dict[str, Any],
        rules: LowBuyRules,
        setup_path: str | None = None,
    ) -> tuple[bool, bool, float, tuple[str, ...]]:
        close = factors["close"]
        green = close > factors["open"]
        crossed_ma10 = factors["prev_close"] <= factors["ma_10"] < close
        reversal_confirmed = bool(factors.get("bullish_reversal")) and int(
            factors.get("repair_confirmation_count") or 0
        ) >= rules.repair_confirmation_min_count
        confirmations: dict[str, tuple[bool, float, bool]] = {
            "break_prev_high": (
                close > factors["prev_high"],
                factors["prev_high"],
                True,
            ),
            "reclaim_ma10": (crossed_ma10, factors["ma_10"], True),
            "reversal_confluence": (reversal_confirmed, close, True),
            "structure_macd": (
                bool(factors.get("structure_macd_confirmed")),
                close,
                True,
            ),
            "constructive_consolidation": (
                setup_path == "REPAIR_REVERSAL"
                and bool(factors.get("constructive_consolidation")),
                close,
                False,
            ),
        }
        matched = tuple(
            method
            for method in rules.confirmation_methods
            if confirmations.get(method, (False, close, True))[0]
            and (
                green
                or not confirmations.get(method, (False, close, True))[2]
            )
        )
        if not matched:
            planned_trigger = factors["trigger_price"]
            return False, False, planned_trigger or close, ()
        # 同时满足多个确认方式时，以较强的实际突破位衡量是否追高，避免用
        # setup 建立时偏低的 MA10 错误过滤后续有效突破。
        planned_matches = [
            confirmations[method][1]
            for method in matched
            if method
            not in {
                "reversal_confluence",
                "structure_macd",
                "constructive_consolidation",
            }
        ]
        confirmation_trigger = max(planned_matches) if planned_matches else close
        chase = close > confirmation_trigger * (1 + rules.chase_limit_pct / 100)
        return True, chase, confirmation_trigger, matched

    @staticmethod
    def _confirmation_waiting_for(
        rules: LowBuyRules, setup_path: str | None
    ) -> list[str]:
        """只展示当前路径真正可用的确认方式。"""
        waiting_for: list[str] = []
        if "reversal_confluence" in rules.confirmation_methods:
            waiting_for.append(
                "反转 K 线与结构/动能"
                f"至少{rules.repair_confirmation_min_count}项共振"
            )
        if "structure_macd" in rules.confirmation_methods:
            waiting_for.append("价格结构与 MACD 动能共振")
        if (
            setup_path == "REPAIR_REVERSAL"
            and "constructive_consolidation" in rules.confirmation_methods
        ):
            waiting_for.append("缩量小阴、低点抬高且 MACD 转强")
        if "break_prev_high" in rules.confirmation_methods:
            waiting_for.append("收阳突破前高")
        if "reclaim_ma10" in rules.confirmation_methods:
            waiting_for.append("收阳重回 MA10")
        return waiting_for

    @staticmethod
    def _confirmation_reason(matched_methods: tuple[str, ...]) -> str:
        method_labels = {
            "break_prev_high": "突破前一日高点",
            "reclaim_ma10": "重新站上 MA10",
            "reversal_confluence": "反转 K 线与结构/动能共振",
            "structure_macd": "价格结构与 MACD 动能共振",
            "constructive_consolidation": "缩量小阴且低点抬高、MACD 转强",
        }
        matched_text = "、".join(
            method_labels.get(method, method) for method in matched_methods
        )
        prefix = "" if matched_methods == ("constructive_consolidation",) else "收阳并"
        return f"{prefix}{matched_text}，完成低吸确认"

    @staticmethod
    def _confirmation_quality_ok(
        setup_path: str | None,
        setup_quality_ok: bool,
        repair_score: dict[str, Any],
        rules: LowBuyRules,
    ) -> bool:
        if not setup_quality_ok:
            return False
        if setup_path != "REPAIR_REVERSAL":
            return True
        return float(repair_score.get("total") or 0) >= rules.repair_trigger_score_min

    def _evaluate(
        self,
        conn: sqlite3.Connection,
        member: WatchMember,
        trade_date: str,
        lifecycle: dict[str, Any],
        prices: list[dict[str, Any]],
        previous: dict[str, Any] | None,
        rules: LowBuyRules,
        cooldown_days_override: int | None = None,
        setup_age_days_override: int | None = None,
    ) -> dict[str, Any]:
        factors = self._factors(prices, lifecycle, rules)
        previous_state = str(previous.get("watch_state")) if previous else "WATCHING"
        if previous_state not in VALID_WATCH_STATES or previous_state == "UNKNOWN":
            previous_state = "WATCHING"
        setup_id = previous.get("setup_id") if previous else None
        previous_trigger = self._number(previous.get("trigger_price")) if previous else None
        previous_stop = self._number(previous.get("invalidation_price")) if previous else None
        trigger_price = (
            previous_trigger
            if previous_state == "TRIGGERED" and previous_trigger is not None
            else factors["trigger_price"]
        )
        base_ok = self._base_qualified(factors, rules)
        trend_quality_ok = (
            (factors["rps_50"] or 0) >= rules.rps50_min
            and (factors["structure_score"] or 0) >= rules.structure_score_min
        )
        trend_in_zone = base_ok and self._in_zone(factors, rules)
        repair_score = self._repair_setup_score(factors, rules)
        repair_in_zone = repair_score["qualified"]
        factors["trend_path_qualified"] = trend_in_zone
        factors["repair_path_qualified"] = repair_in_zone
        factors["repair_setup_score"] = repair_score["total"]
        factors["repair_score_components"] = repair_score["components"]
        factors["repair_blocked_by"] = repair_score["blocked_by"]
        factors["repair_bullish_volume_exception"] = repair_score[
            "bullish_volume_exception"
        ]
        factors["repair_confidence"] = repair_score["confidence"]
        in_zone = trend_in_zone or repair_in_zone

        previous_factors: dict[str, Any] = {}
        raw_previous_factors = previous.get("factors") if previous else None
        if isinstance(raw_previous_factors, dict):
            previous_factors = raw_previous_factors
        elif isinstance(raw_previous_factors, str) and raw_previous_factors:
            try:
                decoded = json.loads(raw_previous_factors)
                if isinstance(decoded, dict):
                    previous_factors = decoded
            except json.JSONDecodeError:
                previous_factors = {}
        previous_setup_path = str(previous_factors.get("setup_path") or "") or None
        setup_path = (
            previous_setup_path
            if previous_state in {"IN_ZONE", "TRIGGERED"}
            else None
        )

        repair_hold_blockers = {
            "disabled",
            "market",
            "industry",
            "ma50",
            "stop",
            "anchor",
        }
        repair_quality_ok = (
            rules.repair_path_enabled
            and not repair_hold_blockers.intersection(repair_score["blocked_by"])
            and repair_score["total"] >= max(rules.repair_setup_score_min - 15, 0)
        )
        if previous_setup_path == "REPAIR_REVERSAL":
            setup_quality_ok = repair_quality_ok
        else:
            # 旧快照没有 setup_path，继续按原强势回调门槛管理。
            setup_quality_ok = trend_quality_ok
        factors["setup_quality_ok"] = setup_quality_ok
        confirmation_quality_ok = self._confirmation_quality_ok(
            setup_path, setup_quality_ok, repair_score, rules
        )
        factors["confirmation_quality_ok"] = confirmation_quality_ok
        confirmed, chase, confirmation_trigger, matched_methods = self._confirmed(
            factors, rules, setup_path=setup_path
        )
        if confirmed and previous_state != "TRIGGERED":
            trigger_price = confirmation_trigger
        factors["confirmation_methods_matched"] = list(matched_methods)
        factors["confirmation_trigger_price"] = (
            confirmation_trigger if confirmed else None
        )
        setup_age_days = setup_age_days_override
        if setup_age_days is None and previous_state == "IN_ZONE":
            setup_age_days = self._trade_days_since_setup(
                conn, str(setup_id) if setup_id else None, trade_date
            )
        setup_active = (
            previous_state == "IN_ZONE"
            and setup_age_days is not None
            and setup_age_days <= rules.setup_valid_days
        )
        cooldown_days = cooldown_days_override
        if cooldown_days is None:
            cooldown_days = self._trade_days_since_trigger(
                conn, member.symbol, str(setup_id) if setup_id else None, trade_date
            )
        cooldown_complete = (
            previous_state == "TRIGGERED"
            and cooldown_days >= rules.cooldown_trade_days
        )
        state = "WATCHING"
        reason = (
            "等待强势回调或领先修复路径达到入区条件"
            if rules.repair_path_enabled
            else "等待满足市场、行业、RPS、结构与回调区条件"
        )
        stop_breached = (
            previous_stop is not None and factors["close"] <= previous_stop
        )

        if previous_state in {"IN_ZONE", "TRIGGERED"} and stop_breached:
            state = "INVALIDATED"
            reason = "跌破失效价"
        elif previous_state == "TRIGGERED" and not cooldown_complete:
            state = "TRIGGERED"
            reason = f"确认触发后的 {rules.cooldown_trade_days} 个交易日冷却期内"
            if not base_ok:
                reason += "；入区资格已转弱，继续按失效价管理"
        elif setup_active and confirmation_quality_ok and confirmed and not chase:
            state = "TRIGGERED"
            reason = self._confirmation_reason(matched_methods)
            if not base_ok:
                reason += "；市场/行业状态较 setup 建立时转弱"
        elif setup_active and confirmation_quality_ok and confirmed and chase:
            state = "WATCHING"
            setup_id = None
            setup_path = None
            reason = f"确认价已超过触发价 {rules.chase_limit_pct:g}%，按追高规则过滤"
        elif setup_active:
            state = "IN_ZONE"
            waiting_for = self._confirmation_waiting_for(rules, setup_path)
            reason = (
                f"回调 setup 确认窗口第 {setup_age_days}/{rules.setup_valid_days} 个交易日，"
                f"等待{' / '.join(waiting_for)}"
            )
            if not setup_quality_ok:
                reason += (
                    "；RPS/结构分尚未恢复到入区门槛"
                    if setup_path != "REPAIR_REVERSAL"
                    else "；当前综合质量尚未恢复到 setup 管理门槛"
                )
            elif confirmed and not confirmation_quality_ok:
                reason += (
                    f"；确认形态已出现，但综合分 {repair_score['total']:g} "
                    f"未达触发分 {rules.repair_trigger_score_min:g}"
                )
            elif not base_ok:
                reason += "；入区资格暂时转弱，仍按失效价观察"
        elif previous_state == "IN_ZONE":
            state = "WATCHING"
            setup_id = None
            setup_path = None
            reason = f"{rules.setup_valid_days} 个交易日确认窗口结束，恢复持续观察"
        elif in_zone:
            state = "IN_ZONE"
            if trend_in_zone:
                setup_path = "TREND_PULLBACK"
                ma50_text = "、守住 MA50" if rules.require_above_ma50 else ""
                reason = (
                    f"强势回调：回撤 {rules.pullback_min_pct:g}%-"
                    f"{rules.pullback_max_pct:g}%{ma50_text} 且缩量接近 MA20/MA50"
                )
            else:
                setup_path = "REPAIR_REVERSAL"
                reason = (
                    f"领先修复：综合分 {repair_score['total']:g}，"
                    f"置信度 {repair_score['confidence']}\uff0c等待反转共振确认"
                )
            if previous_state not in {"IN_ZONE", "TRIGGERED"} or cooldown_complete:
                setup_id = f"{member.symbol}:{trade_date}"
        elif previous_state == "INVALIDATED":
            setup_id = None
            setup_path = None
            reason = "上一 setup 已失效，重置为持续观察"
        elif cooldown_complete:
            setup_id = None
            setup_path = None
            reason = "确认触发冷却期结束，恢复持续观察"

        factors["setup_path"] = setup_path
        effective_invalidation_price = (
            previous_stop
            if state == "INVALIDATED" and previous_stop is not None
            else factors["invalidation_price"]
        )

        current = factors["close"]
        tracking_days = 0
        if member.watched_since:
            try:
                elapsed_days = (
                    date.fromisoformat(trade_date) - date.fromisoformat(member.watched_since)
                ).days
                tracking_days = max(elapsed_days + 1, 0)
            except ValueError:
                tracking_days = 0
        return {
            "trade_date": trade_date,
            "symbol": member.symbol,
            "name": lifecycle.get("name"),
            "pool_id": member.pool_id,
            "pool_name": member.pool_name,
            "watch_state": state,
            "previous_state": previous_state,
            "setup_id": setup_id,
            "rule_version": rules.version,
            "universe_source": lifecycle.get("universe_source") or "manual",
            "watched_since": member.watched_since,
            "tracking_days": tracking_days,
            "current_price": round(current, 3),
            "zone_low": round(
                factors["high_60"]
                * (
                    1 - rules.repair_pullback_max_pct / 100
                    if setup_path == "REPAIR_REVERSAL"
                    else 1 - rules.pullback_max_pct / 100
                ),
                3,
            ),
            "zone_high": round(factors["zone_high"], 3),
            "trigger_price": round(trigger_price, 3) if trigger_price is not None else None,
            "intraday_trigger_price": round(factors["intraday_trigger_price"], 3),
            "invalidation_price": round(effective_invalidation_price, 3),
            "distance_to_trigger_pct": (
                round((trigger_price - current) / current * 100, 2)
                if trigger_price is not None
                else None
            ),
            "reason": reason,
            "factors": json.dumps(factors, ensure_ascii=False),
            "data_status": "OK",
            "data_reason": None,
        }

    def _unknown_snapshot(
        self,
        member: WatchMember,
        trade_date: str,
        lifecycle: dict[str, Any] | None,
        prices: list[dict[str, Any]],
        previous: dict[str, Any] | None,
        errors: list[str],
        rule_version: int,
    ) -> dict[str, Any]:
        has_current_price = prices and prices[-1].get("trade_date") == trade_date
        current = prices[-1].get("close") if has_current_price else None
        tracking_days = 0
        if member.watched_since:
            try:
                elapsed_days = (
                    date.fromisoformat(trade_date) - date.fromisoformat(member.watched_since)
                ).days
                tracking_days = max(elapsed_days + 1, 0)
            except ValueError:
                pass
        return {
            "trade_date": trade_date,
            "symbol": member.symbol,
            "name": (lifecycle or {}).get("name") or (previous or {}).get("name"),
            "pool_id": member.pool_id,
            "pool_name": member.pool_name,
            "watch_state": "UNKNOWN",
            "previous_state": (previous or {}).get("watch_state"),
            "setup_id": (previous or {}).get("setup_id"),
            "rule_version": rule_version,
            "universe_source": (lifecycle or {}).get("universe_source") or "manual",
            "watched_since": member.watched_since,
            "tracking_days": tracking_days,
            "current_price": current,
            "zone_low": (previous or {}).get("zone_low"),
            "zone_high": (previous or {}).get("zone_high"),
            "trigger_price": (previous or {}).get("trigger_price"),
            "intraday_trigger_price": (previous or {}).get("intraday_trigger_price"),
            "invalidation_price": (previous or {}).get("invalidation_price"),
            "distance_to_trigger_pct": (previous or {}).get("distance_to_trigger_pct"),
            "reason": "关键数据不完整，保留上一有效参考且禁止生成买点",
            "factors": (previous or {}).get("factors") or "{}",
            "data_status": "BLOCKED",
            "data_reason": ",".join(errors),
        }

    def _save_snapshot(self, conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snapshot = {**snapshot, "created_at": now, "updated_at": now}
        columns = list(snapshot)
        conn.execute(
            "DELETE FROM watch_daily_snapshot WHERE trade_date=? AND symbol=?",
            (snapshot["trade_date"], snapshot["symbol"]),
        )
        conn.execute(
            f"INSERT INTO watch_daily_snapshot ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [snapshot[column] for column in columns],
        )

    def _event(
        self,
        conn: sqlite3.Connection,
        *,
        dedupe_key: str,
        trade_date: str,
        event_type: str,
        severity: str,
        title: str,
        message: str,
        symbol: str | None = None,
        setup_id: str | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, setup_id, event_type,
                from_state, to_state, severity, title, message, status, payload,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREAD', ?, ?, ?)
            """,
            (
                uuid4().hex,
                dedupe_key,
                trade_date,
                symbol,
                setup_id,
                event_type,
                from_state,
                to_state,
                severity,
                title,
                message,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        return bool(cursor.rowcount)

    def _state_event(
        self,
        conn: sqlite3.Connection,
        snapshot: dict[str, Any],
        previous: dict[str, Any] | None,
    ) -> int:
        state = snapshot["watch_state"]
        previous_state = (previous or {}).get("watch_state") or "WATCHING"
        if state == previous_state or state == "UNKNOWN":
            return 0
        event_map = {
            "IN_ZONE": ("INFO", "进入回调区"),
            "TRIGGERED": ("HIGH", "低吸确认触发"),
            "INVALIDATED": ("HIGH", "低吸 setup 已失效"),
            "WATCHING": ("INFO", "离开回调区"),
        }
        severity, title = event_map[state]
        event_type = "ZONE_EXITED" if state == "WATCHING" else state
        setup_key = (
            snapshot.get("setup_id")
            or (previous or {}).get("setup_id")
            or snapshot["trade_date"]
        )
        raw_factors = snapshot.get("factors")
        snapshot_factors: dict[str, Any] = {}
        if isinstance(raw_factors, dict):
            snapshot_factors = raw_factors
        elif isinstance(raw_factors, str):
            try:
                parsed_factors = json.loads(raw_factors)
                if isinstance(parsed_factors, dict):
                    snapshot_factors = parsed_factors
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot_factors = {}
        inserted = self._event(
            conn,
            dedupe_key=f"state:{snapshot['symbol']}:{setup_key}:{event_type}",
            trade_date=snapshot["trade_date"],
            symbol=snapshot["symbol"],
            setup_id=snapshot.get("setup_id") or (previous or {}).get("setup_id"),
            event_type=event_type,
            from_state=previous_state,
            to_state=state,
            severity=severity,
            title=title,
            message=str(snapshot["reason"]),
            payload={
                "name": snapshot.get("name"),
                "pool_id": snapshot.get("pool_id"),
                "pool_name": snapshot.get("pool_name"),
                "current_price": snapshot.get("current_price"),
                "zone_low": snapshot.get("zone_low"),
                "zone_high": snapshot.get("zone_high"),
                "trigger_price": snapshot.get("trigger_price"),
                "invalidation_price": snapshot.get("invalidation_price"),
                "setup_path": snapshot_factors.get("setup_path"),
            },
        )
        return int(inserted)

    def _sync_data_events(
        self, conn: sqlite3.Connection, trade_date: str, blocked_reasons: set[str]
    ) -> int:
        created = 0
        for reason in sorted(blocked_reasons):
            created += int(
                self._event(
                    conn,
                    dedupe_key=f"data:{trade_date}:{reason}",
                    trade_date=trade_date,
                    event_type="DATA_BLOCKED",
                    severity="CRITICAL",
                    title="Watch Engine 数据阻断",
                    message=f"关键数据不完整：{reason}；已禁止生成买点。",
                    payload={"reason": reason},
                )
            )
        if blocked_reasons:
            return created

        unresolved = conn.execute(
            """
            SELECT event_id, trade_date, dedupe_key, payload
            FROM watch_events
            WHERE event_type='DATA_BLOCKED' AND resolved_at IS NULL
              AND dedupe_key LIKE 'data:%'
            """
        ).fetchall()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in unresolved:
            payload = json.loads(row[3] or "{}")
            reason = str(payload.get("reason") or row[2])
            created += int(
                self._event(
                    conn,
                    dedupe_key=f"recovered:{row[0]}",
                    trade_date=trade_date,
                    event_type="DATA_RECOVERED",
                    severity="INFO",
                    title="Watch Engine 数据恢复",
                    message=f"关键数据已恢复：{reason}",
                    payload={"reason": reason, "blocked_event_id": row[0]},
                )
            )
            conn.execute(
                "UPDATE watch_events SET resolved_at=?, updated_at=? WHERE event_id=?",
                (now, now, row[0]),
            )
        return created

    def run(
        self,
        trade_date: str | None = None,
        *,
        symbols: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        """运行一个交易日；指定股票时只刷新成员且不改动全局数据事件。"""
        started = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                self.ensure_schema(conn)
                rules = load_active_rules(conn)
                output_date = self._output_date(conn, trade_date)
                if output_date is None:
                    return {
                        "success": False,
                        "error": "stock_daily_prices 没有交易日期",
                        "records": 0,
                        "events": 0,
                    }
                members = self._monitored_members(conn)
                is_member_refresh = symbols is not None
                if is_member_refresh:
                    symbol_scope = {
                        str(symbol).strip().upper()
                        for symbol in symbols
                        if str(symbol).strip()
                    }
                    members = [
                        member for member in members if member.symbol in symbol_scope
                    ]
                blocked_reasons: set[str] = set()
                event_count = 0
                for member in members:
                    lifecycle = self._lifecycle(conn, member.symbol, output_date)
                    prices = self._prices(conn, member.symbol, output_date)
                    previous = self._previous_snapshot(conn, member.symbol, output_date)
                    evaluation_previous = previous
                    if (previous or {}).get("watch_state") == "UNKNOWN":
                        evaluation_previous = self._previous_valid_snapshot(
                            conn, member.symbol, output_date
                        ) or previous
                    errors = self._data_errors(lifecycle, prices, output_date)
                    if errors:
                        snapshot = self._unknown_snapshot(
                            member,
                            output_date,
                            lifecycle,
                            prices,
                            previous,
                            errors,
                            rules.version,
                        )
                        blocked_reasons.update(errors)
                    else:
                        snapshot = self._evaluate(
                            conn,
                            member,
                            output_date,
                            lifecycle or {},
                            prices,
                            evaluation_previous,
                            rules,
                        )
                    self._save_snapshot(conn, snapshot)
                    if not errors:
                        event_count += self._state_event(conn, snapshot, previous)
                if not is_member_refresh:
                    event_count += self._sync_data_events(
                        conn,
                        output_date,
                        blocked_reasons,
                    )
                conn.commit()
            return {
                "success": True,
                "trade_date": output_date,
                "records": len(members),
                "events": event_count,
                "rule_version": rules.version,
                "blocked_reasons": sorted(blocked_reasons),
                "elapsed": time.time() - started,
            }
        except Exception as exc:
            logger.exception("Watch Engine 运行失败")
            return {
                "success": False,
                "error": str(exc),
                "records": 0,
                "events": 0,
                "elapsed": time.time() - started,
            }

    def run_daily(self, trade_date: str | None = None) -> dict[str, Any]:
        """供每日工作流调用的稳定入口。"""
        return self.run(trade_date)


def main() -> int:
    parser = argparse.ArgumentParser(description="EvoAlpha 低吸长期跟踪盘后状态机")
    parser.add_argument("--mode", choices=("daily",), default="daily")
    parser.add_argument(
        "--trade-date", help="指定交易日 YYYY-MM-DD；默认取最新日线日期"
    )
    parser.add_argument("--db-path", help="SQLite 数据库路径；默认使用本地量化库")
    args = parser.parse_args()
    result = WatchEngine(args.db_path).run_daily(args.trade_date)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
