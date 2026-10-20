"""Bounded, causal chart replay; never initializes or writes formal Watch data."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
import math
import sqlite3
from typing import Any

from data_core.market_calendar import is_a_share_session
from quant_engine.watch.engine import WatchEngine, WatchMember
from quant_engine.watch.rules import LowBuyRules

PERIODS = {"daily", "weekly", "monthly", "30min", "120min"}
MAX_BARS = 520


def read_rules(conn: sqlite3.Connection) -> tuple[LowBuyRules, str]:
    """Read the shared active rule version without creating defaults."""
    if not WatchEngine._table_exists(conn, "watch_rule_configs"):
        return LowBuyRules(), "code_defaults"
    row = conn.execute(
        "SELECT version, config_json FROM watch_rule_configs "
        "WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return LowBuyRules(), "code_defaults"
    return LowBuyRules.from_mapping(json.loads(row[1]), version=int(row[0])), "active_global"


def load_context(
    conn: sqlite3.Connection, symbol: str, rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Batch daily background and its increments, including a preceding session."""
    if not rows:
        return {}, []
    start, end = str(rows[0]["trade_date"])[:10], str(rows[-1]["trade_date"])[:10]
    first = date.fromisoformat(start) - timedelta(days=14)
    last = date.fromisoformat(end)
    sessions = []
    day = first
    while day <= last:
        if is_a_share_session(day):
            sessions.append(day.isoformat())
        day += timedelta(days=1)
    context_start = sessions[0] if sessions else start
    engine = WatchEngine()
    columns = engine._columns(conn, "stock_lifecycle_state")
    if not {"symbol", "trade_date"} <= columns:
        return {}, sessions
    loaded = {
        str(row["trade_date"])[:10]: dict(row) for row in conn.execute(
            "SELECT * FROM stock_lifecycle_state WHERE symbol=? "
            "AND trade_date>=? AND trade_date<? ORDER BY trade_date",
            (symbol, context_start, end + "z"),
        )
    }

    def attach(table: str, identity: str, entity: str, fields: tuple[tuple[str, str], ...]):
        available = engine._columns(conn, table)
        selected = [(column, key) for column, key in fields if column in available]
        if not selected or not {identity, "trade_date"} <= available:
            return
        sql_fields = ", ".join(column for column, _ in selected)
        # One preceding point for deltas; avoid global MAX/substr scans of factor tables.
        older = conn.execute(
            f"SELECT trade_date, {sql_fields} FROM {table} WHERE {identity}=? "
            "AND trade_date<? ORDER BY trade_date DESC LIMIT 1", (entity, context_start)
        ).fetchall()
        points = conn.execute(
            f"SELECT trade_date, {sql_fields} FROM {table} WHERE {identity}=? "
            "AND trade_date>=? AND trade_date<? ORDER BY trade_date",
            (entity, context_start, end + "z"),
        ).fetchall()
        previous = {}
        for point in [*older, *points]:
            day = str(point[0])[:10]
            target = loaded.get(day)
            for offset, (_, key) in enumerate(selected, 1):
                value = engine._number(point[offset])
                if target is not None and (identity != "sector_name" or target.get("sector_name") == entity):
                    target[key] = value
                    target[key + "_delta"] = value - previous[key] if value is not None and previous.get(key) is not None else None
                previous[key] = value

    attach("market_strength_daily", "index_code", "000001", (("market_strength_score", "market_strength"),))
    sectors = {str(row.get("sector_name")) for row in loaded.values() if row.get("sector_name")}
    for sector in sectors:
        attach("industry_strength_daily", "sector_name", sector, (
            ("industry_strength_score", "industry_strength"), ("sector_rps_20", "sector_rps_20")))
    attach("quant_feature_rps", "symbol", symbol, (("rps_20", "rps_20"),))
    return loaded, sessions


def replay_ab(
    conn: sqlite3.Connection, symbol: str, period: str, prices: list[dict[str, Any]],
    contexts: dict[str, dict[str, Any]], sessions: list[str], rules: LowBuyRules,
) -> dict[str, Any]:
    """Evaluate completed bars with prior-session daily qualification, using bar ages."""
    if period not in PERIODS or len(prices) > MAX_BARS:
        raise ValueError("unsupported period or more than 520 bars")
    previous_time = ""
    for row in prices:
        timestamp = str(row.get("trade_date", ""))
        try:
            if period in {"30min", "120min"}:
                parsed = datetime.fromisoformat(timestamp)
                valid_time = (len(timestamp) in {16, 19} and timestamp[10] == " "
                              and parsed.tzinfo is None
                              and parsed.isoformat(sep=" ", timespec="minutes" if len(timestamp) == 16 else "seconds") == timestamp)
            else:
                valid_time = date.fromisoformat(timestamp).isoformat() == timestamp
        except ValueError:
            valid_time = False
        if not valid_time:
            raise ValueError("invalid bar timestamp")
        if timestamp <= previous_time:
            raise ValueError("bar timestamps must be strictly increasing")
        previous_time = timestamp
        values = [row.get(key) for key in ("open", "high", "low", "close", "volume")]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in values) or min(values[:4]) <= 0 or values[4] < 0:
            raise ValueError("invalid OHLCV")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            raise ValueError("invalid OHLC bounds")
    engine = WatchEngine()
    member = WatchMember(symbol, "chart", "图表回放", None)
    snapshots, blocked = [], []
    latest_context = None
    previous = None
    setup_index = trigger_index = None
    for index, row in enumerate(prices):
        timestamp, day = str(row["trade_date"]), str(row["trade_date"])[:10]
        position = bisect_left(sessions, day) - 1
        context_date = sessions[position] if position >= 0 else None
        lifecycle = contexts.get(context_date, {}) if context_date else {}
        required = ("market_state", "industry_state", "rps_50", "structure_score")
        if index < 59 or any(lifecycle.get(key) is None for key in required) or lifecycle.get("data_status") not in (None, "OK"):
            blocked.append({"trade_date": timestamp, "context_date": context_date,
                            "reason": "warmup" if index < 59 else "daily_context_missing"})
            previous = None
            setup_index = trigger_index = None
            continue
        repair_missing = rules.repair_path_enabled and any(
            lifecycle.get(key) is None for key in (
                "market_strength", "market_strength_delta", "industry_strength",
                "industry_strength_delta", "sector_rps_20", "rps_20", "rps_120",
            )
        )
        effective_rules = replace(rules, repair_path_enabled=False) if repair_missing else rules
        snapshot = engine._evaluate(
            conn, member, day, lifecycle, prices[max(0, index - 119): index + 1],
            previous, effective_rules,
            cooldown_days_override=index - trigger_index if trigger_index is not None else 0,
            setup_age_days_override=index - setup_index if setup_index is not None else 0,
        )
        snapshot["trade_date"] = timestamp
        snapshot["context_date"] = context_date
        snapshot["source"] = "chart_replay"
        snapshot["period"] = period
        snapshot["path_status"] = {"A": "OK", "B": "BLOCKED" if repair_missing else "OK"}
        snapshot["factors"] = json.loads(snapshot["factors"])
        snapshot["reason"] = (f"{period}回放；日线背景{context_date}；" +
                              snapshot["reason"].replace("个交易日", "根K线"))
        if repair_missing:
            snapshot["factors"]["repair_blocked_by"] = ["daily_repair_context_missing"]
        if snapshot["watch_state"] == "IN_ZONE" and snapshot["setup_id"] != (previous or {}).get("setup_id"):
            setup_index = index
            snapshot["setup_id"] = f"{symbol}:{timestamp}"
        elif snapshot["watch_state"] == "TRIGGERED" and (previous or {}).get("watch_state") != "TRIGGERED":
            trigger_index = index
        latest_context = context_date
        if snapshot["watch_state"] in {"IN_ZONE", "TRIGGERED", "INVALIDATED"} and (
            snapshot["watch_state"] != (previous or {}).get("watch_state")
            or snapshot["setup_id"] != (previous or {}).get("setup_id")
        ):
            snapshot["marker_type"] = snapshot["watch_state"]
            snapshots.append(snapshot)
        if snapshot["setup_id"] is None:
            setup_index = trigger_index = None
        previous = snapshot
    return {"rows": snapshots, "blocked": blocked, "latest": previous,
            "context_date": latest_context}
