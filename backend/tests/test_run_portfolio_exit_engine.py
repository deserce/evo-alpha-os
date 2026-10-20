import json
import sqlite3

import pytest

from portfolio.accounting import PortfolioAccountingService
from portfolio.exit_engine import (
    ExitAccountRunResult,
    ExitRunResult,
    PortfolioExitError,
)
from portfolio.schema import ensure_portfolio_schema
from scripts import run_portfolio_exit_engine


TRADE_DATE = "2026-07-24"


def _database(tmp_path):
    db_path = tmp_path / "portfolio-exit-cli.db"
    ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL NOT NULL,
                adjustment_mode TEXT NOT NULL DEFAULT 'qfq',
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE stock_daily_prices_raw (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close,
                source TEXT,
                quality_flag TEXT,
                adjustment_mode TEXT,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO stock_daily_prices (
                symbol, trade_date, close, adjustment_mode
            ) VALUES ('000001', ?, 10, 'qfq')
            """,
            (TRADE_DATE,),
        )
        conn.commit()
    return db_path


def _payload(capsys):
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def _exit_counts(db_path):
    with sqlite3.connect(db_path) as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            for table_name in (
                "portfolio_exit_plans",
                "portfolio_exit_states",
                "portfolio_exit_events",
            )
        )


def test_zero_active_accounts_succeeds_without_exit_writes(tmp_path, capsys):
    db_path = _database(tmp_path)

    exit_code = run_portfolio_exit_engine.main(
        ["--trade-date", TRADE_DATE, "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 0
    assert payload == {
        "accounts": [],
        "active_account_count": 0,
        "blocked_position_count": 0,
        "created_event_count": 0,
        "evaluated_position_count": 0,
        "ok": True,
        "trade_date": TRADE_DATE,
    }
    assert _exit_counts(db_path) == (0, 0, 0)


def test_accounts_are_invoked_sequentially_in_id_order(
    tmp_path,
    capsys,
    monkeypatch,
):
    db_path = _database(tmp_path)
    accounting = PortfolioAccountingService(db_path)
    first = accounting.create_account(name="A")
    second = accounting.create_account(name="B")
    calls = []

    class RecordingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def run_daily(self, trade_date, *, account_id=None):
            calls.append((trade_date, account_id))
            account_result = ExitAccountRunResult(
                account_id=account_id,
                evaluated_positions=account_id,
                blocked_positions=0,
                created_event_ids=(account_id,),
            )
            return ExitRunResult(
                trade_date=trade_date,
                accounts=(account_result,),
            )

    monkeypatch.setattr(
        run_portfolio_exit_engine,
        "PortfolioExitEngine",
        RecordingEngine,
    )

    exit_code = run_portfolio_exit_engine.main(
        ["--trade-date", TRADE_DATE, "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 0
    assert calls == [(TRADE_DATE, first.id), (TRADE_DATE, second.id)]
    assert [row["account_id"] for row in payload["accounts"]] == [
        first.id,
        second.id,
    ]
    assert payload["created_event_count"] == 2


def test_non_trading_day_fails_before_any_write(tmp_path, capsys):
    db_path = _database(tmp_path)
    PortfolioAccountingService(db_path).create_account(name="主账户")
    with sqlite3.connect(db_path) as conn:
        migrations_before = conn.execute(
            "SELECT version, name, applied_at FROM portfolio_schema_migrations"
        ).fetchall()

    exit_code = run_portfolio_exit_engine.main(
        ["--trade-date", "2026-07-25", "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 1
    assert payload["error"] == {"code": "NOT_TRADING_DAY"}
    assert _exit_counts(db_path) == (0, 0, 0)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version, name, applied_at FROM portfolio_schema_migrations"
        ).fetchall() == migrations_before


def test_missing_market_calendar_is_sanitized_and_does_not_write(
    tmp_path,
    capsys,
):
    db_path = tmp_path / "missing-calendar.db"
    ensure_portfolio_schema(db_path)

    exit_code = run_portfolio_exit_engine.main(
        ["--trade-date", TRADE_DATE, "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 1
    assert payload["error"] == {"code": "MARKET_CALENDAR_UNAVAILABLE"}
    assert _exit_counts(db_path) == (0, 0, 0)


def test_engine_failure_is_sanitized_and_reports_only_account_id(
    tmp_path,
    capsys,
    monkeypatch,
):
    db_path = _database(tmp_path)
    account = PortfolioAccountingService(db_path).create_account(
        name="敏感账户名"
    )

    class FailingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def run_daily(self, trade_date, *, account_id=None):
            raise PortfolioExitError(
                f"sensitive failure account=敏感账户名 db={db_path}"
            )

    monkeypatch.setattr(
        run_portfolio_exit_engine,
        "PortfolioExitEngine",
        FailingEngine,
    )

    exit_code = run_portfolio_exit_engine.main(
        [
            "--trade-date",
            TRADE_DATE,
            "--db",
            str(db_path),
            "--busy-timeout-ms",
            "0",
        ]
    )

    payload, stderr = _payload(capsys)
    assert exit_code == 1
    assert payload["error"] == {
        "account_id": account.id,
        "code": "EXIT_ENGINE_ERROR",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert str(db_path) not in serialized
    assert "敏感账户名" not in serialized
    assert "sensitive failure" not in stderr
    assert _exit_counts(db_path) == (0, 0, 0)


def test_main_rejects_invalid_date_and_timeout(capsys):
    with pytest.raises(SystemExit) as date_error:
        run_portfolio_exit_engine.main(
            ["--trade-date", "2026/07/24", "--db", "ignored.db"]
        )
    assert date_error.value.code == 2

    with pytest.raises(SystemExit) as timeout_error:
        run_portfolio_exit_engine.main(
            [
                "--trade-date",
                TRADE_DATE,
                "--db",
                "ignored.db",
                "--busy-timeout-ms",
                "-1",
            ]
        )
    assert timeout_error.value.code == 2
    assert capsys.readouterr().out == ""
