import sqlite3
from datetime import date, timedelta


def _make_price_db(path, *, dates: int = 10) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?)",
            [(f"2026-07-{day:02d}",) for day in range(1, dates + 1)],
        )


def test_member_refresh_backfills_history_then_refreshes_live_watch_and_markers(
    tmp_path, monkeypatch
):
    from quant_engine.watch import member_refresh

    db_path = tmp_path / "member_refresh.db"
    _make_price_db(db_path)
    calls = []

    class FakeLifecycle:
        def __init__(self, _db_path):
            pass

        def backfill_symbols(self, symbols, trade_dates):
            calls.append(("lifecycle", symbols, trade_dates))
            return {"success": True, "records": len(trade_dates)}

    class FakeEngine:
        def __init__(self, _db_path):
            pass

        def run(self, trade_date, *, symbols=None):
            calls.append(("watch", trade_date, symbols))
            return {"success": True, "records": 1, "events": 0}

    class FakeScanner:
        def __init__(self, _db_path):
            pass

        def create_scan(self, **kwargs):
            calls.append(("create_scan", kwargs))
            return {"scan_id": "auto-scan"}

        def run_scan(self, scan_id):
            calls.append(("run_scan", scan_id))
            return {"scan_id": scan_id, "status": "COMPLETED"}

    monkeypatch.setattr(member_refresh, "LifecycleStateCalculator", FakeLifecycle)
    monkeypatch.setattr(member_refresh, "WatchEngine", FakeEngine)
    monkeypatch.setattr(member_refresh, "WatchHistoricalScanner", FakeScanner)

    result = member_refresh.WatchMemberRefreshService(db_path).refresh(
        ["688001", "688001"], history_trade_days=160
    )

    assert result["success"] is True
    assert result["symbols"] == ["688001"]
    assert calls[0][0] == "lifecycle"
    assert len(calls[0][2]) == 10
    assert calls[1] == ("watch", "2026-07-10", ["688001"])
    assert calls[2][1]["symbols"] == ["688001"]
    assert calls[2][1]["origin"] == "AUTO_MEMBER"
    assert calls[3] == ("run_scan", "auto-scan")
    assert result["notification"] == {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "reason": "AUTO_MEMBER refresh does not dispatch immediate alerts",
    }


def test_member_refresh_without_price_history_returns_no_data(tmp_path, monkeypatch):
    from quant_engine.watch import member_refresh

    db_path = tmp_path / "member_refresh_empty.db"
    _make_price_db(db_path, dates=0)

    class ForbiddenLifecycle:
        def __init__(self, _db_path):
            raise AssertionError("没有日线时不应启动生命周期计算")

    monkeypatch.setattr(member_refresh, "LifecycleStateCalculator", ForbiddenLifecycle)

    result = member_refresh.WatchMemberRefreshService(db_path).refresh(["688001"])

    assert result["success"] is False
    assert result["status"] == "NO_DATA"


def test_member_refresh_stops_when_lifecycle_backfill_fails(tmp_path, monkeypatch):
    from quant_engine.watch import member_refresh

    db_path = tmp_path / "member_refresh_failed.db"
    _make_price_db(db_path)

    class FailedLifecycle:
        def __init__(self, _db_path):
            pass

        def backfill_symbols(self, symbols, trade_dates):
            return {"success": False, "error": "structure data missing", "records": 0}

    class ForbiddenEngine:
        def __init__(self, _db_path):
            raise AssertionError("生命周期失败后不得生成 Watch 买点")

    monkeypatch.setattr(member_refresh, "LifecycleStateCalculator", FailedLifecycle)
    monkeypatch.setattr(member_refresh, "WatchEngine", ForbiddenEngine)

    result = member_refresh.WatchMemberRefreshService(db_path).refresh(["688001"])

    assert result["success"] is False
    assert result["status"] == "LIFECYCLE_FAILED"


def test_member_refresh_caps_suspended_stock_to_160_market_trade_dates(
    tmp_path, monkeypatch
):
    from quant_engine.watch import member_refresh

    db_path = tmp_path / "member_refresh_market_window.db"
    market_dates = [
        (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
        for offset in range(161)
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_daily_prices VALUES ('000001', ?)",
            [(trade_date,) for trade_date in market_dates],
        )
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?)",
            (market_dates[-1],),
        )
    calls = []

    class FakeLifecycle:
        def __init__(self, _db_path):
            pass

        def backfill_symbols(self, symbols, trade_dates):
            calls.append(("lifecycle", symbols, trade_dates))
            return {"success": True, "records": len(trade_dates)}

    class FakeEngine:
        def __init__(self, _db_path):
            pass

        def run(self, trade_date, *, symbols=None):
            assert symbols == ["688001"]
            return {"success": True, "trade_date": trade_date}

    class FakeScanner:
        def __init__(self, _db_path):
            pass

        def create_scan(self, **kwargs):
            calls.append(("scan", kwargs))
            return {"scan_id": "scan"}

        def run_scan(self, scan_id):
            return {"scan_id": scan_id, "status": "COMPLETED"}

    monkeypatch.setattr(member_refresh, "LifecycleStateCalculator", FakeLifecycle)
    monkeypatch.setattr(member_refresh, "WatchEngine", FakeEngine)
    monkeypatch.setattr(member_refresh, "WatchHistoricalScanner", FakeScanner)

    result = member_refresh.WatchMemberRefreshService(db_path).refresh(["688001"])

    assert result["success"] is True
    assert len(calls[0][2]) == 160
    assert calls[0][2][0] == market_dates[1]
    assert calls[1][1]["start_date"] == market_dates[1]
