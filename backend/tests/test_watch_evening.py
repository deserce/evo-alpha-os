import sqlite3


def test_evening_digest_uses_latest_completed_watch_date(tmp_path):
    from quant_engine.watch.evening import run_evening

    db_path = tmp_path / "evening.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE watch_daily_snapshot (trade_date TEXT, symbol TEXT)"
        )
        conn.executemany(
            "INSERT INTO watch_daily_snapshot VALUES (?, '688001')",
            [("2026-07-16",), ("2026-07-17",)],
        )
    calls = []

    class FakeDispatcher:
        def dispatch_immediate(self, trade_date):
            calls.append(("immediate", trade_date))
            return {"sent": 0, "failed": 0, "skipped": 0}

        def dispatch_digest(self, trade_date, *, require_trusted=False):
            calls.append(("digest", trade_date, require_trusted))
            return {"sent": 1, "failed": 0, "skipped": 0}

    result = run_evening(db_path, dispatcher=FakeDispatcher())

    assert result["success"] is True
    assert result["trade_date"] == "2026-07-17"
    assert calls == [
        ("immediate", "2026-07-17"),
        ("digest", "2026-07-17", True),
    ]


def test_evening_digest_skips_safely_without_watch_snapshot(tmp_path):
    from quant_engine.watch.evening import run_evening

    db_path = tmp_path / "evening_empty.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stock_daily_prices (trade_date TEXT)")

    result = run_evening(db_path)

    assert result["success"] is True
    assert result["status"] == "NO_SNAPSHOT"


def test_report_followup_uses_bounded_deterministic_separate_cli(tmp_path, monkeypatch):
    from quant_engine.watch import evening
    calls = []
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0})()
    monkeypatch.setattr(evening.subprocess, "run", fake_run)
    result = evening.run_report_followup(tmp_path / "test.db", mode="evening")
    assert result["success"] is True
    assert [entry[0][-1] for entry in calls] == ["prepare", "--send"]
    assert calls[0][1]["timeout"] == 180
    assert calls[1][1]["env"]["EVOALPHA_DISABLE_LLM"] == "1"


def test_report_followup_stops_if_prepare_fails(tmp_path, monkeypatch):
    from quant_engine.watch import evening
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 1})()
    monkeypatch.setattr(evening.subprocess, "run", fake_run)
    assert evening.run_report_followup(tmp_path / "test.db", mode="evening")["success"] is False
    assert len(calls) == 1


def test_report_followup_timeout_is_report_failure_only(tmp_path, monkeypatch):
    from quant_engine.watch import evening
    def timeout(command, **kwargs):
        raise evening.subprocess.TimeoutExpired(command, 180)
    monkeypatch.setattr(evening.subprocess, "run", timeout)
    assert evening.run_report_followup(tmp_path / "test.db", mode="retry")["status"] == "FAILED"
