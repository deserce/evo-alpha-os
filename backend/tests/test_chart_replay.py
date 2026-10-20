"""Independent fixtures for causal, read-only multi-period replay."""
import sqlite3
from datetime import date, timedelta
import pytest
from quant_engine.watch.chart_replay import read_rules, replay_ab
from quant_engine.watch.rules import LowBuyRules


def fixture():
    rows, sessions, contexts = [], [], {}
    for index in range(65):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        sessions.append(day)
        contexts[day] = {"market_state": "M3", "industry_state": "I4", "rps_50": 95,
                         "structure_score": 90, "data_status": "OK"}
        rows.append({"trade_date": day + " 10:00", "open": 100., "close": 100.,
                     "high": 101., "low": 99., "volume": 100.})
    return rows, sessions, contexts


def test_replay_readonly_and_timestamp():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA query_only=ON")
        rows, sessions, contexts = fixture()
        result = replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())
        assert result["latest"] is not None
        assert result["latest"]["trade_date"].endswith(" 10:00")
        assert result["latest"]["context_date"] == sessions[-2]
        assert conn.total_changes == 0
        assert read_rules(conn)[1] == "code_defaults"


def test_no_future_context_or_price_leakage():
    with sqlite3.connect(":memory:") as conn:
        rows, sessions, contexts = fixture()
        baseline = replay_ab(conn, "600001", "120min", rows, contexts, sessions, LowBuyRules())
        contexts[sessions[-1]]["rps_50"] = 0
        rows[-1]["high"] = 200.
        changed = replay_ab(conn, "600001", "120min", rows, contexts, sessions, LowBuyRules())
        assert baseline["rows"] == changed["rows"]


def test_missing_context_blocks_and_resets():
    with sqlite3.connect(":memory:") as conn:
        rows, sessions, contexts = fixture()
        del contexts[sessions[-2]]
        result = replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())
        assert result["blocked"][-1]["reason"] == "daily_context_missing"
        assert result["latest"] is None


@pytest.mark.parametrize("mutation", ["nan", "order", "bounds"])
def test_invalid_prices_fail(mutation):
    with sqlite3.connect(":memory:") as conn:
        rows, sessions, contexts = fixture()
        if mutation == "nan": rows[-1]["close"] = float("nan")
        if mutation == "order": rows[-1]["trade_date"] = rows[-2]["trade_date"]
        if mutation == "bounds": rows[-1]["low"] = 102.
        with pytest.raises(ValueError):
            replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())


def test_repeated_states_only_emit_first_transition(monkeypatch):
    from quant_engine.watch.engine import WatchEngine
    def evaluate(self, conn, member, day, lifecycle, prices, previous, rules, **ages):
        return {"trade_date": day, "watch_state": "TRIGGERED", "setup_id": "fixed",
                "factors": "{}", "reason": "cooldown"}
    monkeypatch.setattr(WatchEngine, "_evaluate", evaluate)
    with sqlite3.connect(":memory:") as conn:
        rows, sessions, contexts = fixture()
        result = replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())
        assert len(result["rows"]) == 1
        assert result["rows"][0]["trade_date"] == rows[59]["trade_date"]


@pytest.mark.parametrize("field,value", [("close", True), ("trade_date", "bad")])
def test_bool_and_bad_timestamp_rejected(field, value):
    with sqlite3.connect(":memory:") as conn:
        rows, sessions, contexts = fixture()
        rows[-1][field] = value
        with pytest.raises(ValueError):
            replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())


def test_causal_context_and_bar_age_overrides(monkeypatch):
    from quant_engine.watch.engine import WatchEngine
    seen = []
    def evaluate(self, conn, member, day, lifecycle, prices, previous, rules, **ages):
        seen.append((day, lifecycle["rps_50"], prices[-1]["close"], ages.copy()))
        return {"watch_state": "IN_ZONE", "setup_id": (previous or {}).get("setup_id", "new"),
                "factors": "{}", "reason": "setup"}
    monkeypatch.setattr(WatchEngine, "_evaluate", evaluate)
    with sqlite3.connect(":memory:") as conn:
        rows, sessions, contexts = fixture()
        for index, day in enumerate(sessions): contexts[day]["rps_50"] = index
        replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())
        baseline = seen.copy()
        assert [item[1] for item in baseline] == list(range(58, 64))
        assert [item[3]["setup_age_days_override"] for item in baseline] == list(range(6))
        seen.clear()
        contexts[sessions[-1]]["rps_50"] = 999
        rows[-1]["close"] = 101.
        replay_ab(conn, "600001", "30min", rows, contexts, sessions, LowBuyRules())
        assert baseline[:-1] == seen[:-1]


def test_batch_context_exact_session_and_causal_delta():
    from quant_engine.watch.chart_replay import load_context
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE stock_lifecycle_state (symbol TEXT,trade_date TEXT,market_state TEXT,industry_state TEXT,rps_50 REAL,structure_score REAL)")
        conn.execute("CREATE TABLE market_strength_daily(index_code TEXT,trade_date TEXT,market_strength_score REAL)")
        conn.executemany("INSERT INTO stock_lifecycle_state VALUES (?,?,?,?,?,?)", [
            ("600001", "2026-09-28", "M3", "I4", 95, 90),
            ("600001", "2026-09-30", "M3", "I4", 99, 90),
        ])
        conn.executemany("INSERT INTO market_strength_daily VALUES (?,?,?)", [
            ("000001", "2026-09-25", 50), ("000001", "2026-09-28", 60),
            ("000001", "2026-09-30", 999),
        ])
        conn.execute("PRAGMA query_only=ON")
        contexts, sessions = load_context(conn, "600001", [{"trade_date": "2026-09-30 10:00"}])
        assert "2026-09-29" in sessions
        assert "2026-09-29" not in contexts  # no stale 28th background substitution
        assert contexts["2026-09-28"]["market_strength_delta"] == 10
