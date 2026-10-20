import json
import sqlite3
from types import SimpleNamespace

import pytest

from portfolio.accounting import PortfolioAccountingService
from portfolio.performance import SnapshotConsistencyError
from portfolio.schema import configure_portfolio_connection, ensure_portfolio_schema
from scripts import build_portfolio_snapshots


TRADE_DATE = "2026-07-24"


def _database(tmp_path):
    db_path = tmp_path / "snapshot-cli.db"
    ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO stock_daily_prices (symbol, trade_date, close)
            VALUES ('000001', ?, 10)
            """,
            (TRADE_DATE,),
        )
        conn.commit()
    return db_path


def _payload(capsys):
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def _snapshot_count(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
        ).fetchone()[0]


def test_zero_active_accounts_succeeds_with_json_summary(tmp_path, capsys):
    db_path = _database(tmp_path)

    exit_code = build_portfolio_snapshots.main(
        ["--trade-date", TRADE_DATE, "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 0
    assert payload == {
        "accounts": [],
        "active_account_count": 0,
        "ok": True,
        "snapshot_count": 0,
        "status_counts": {},
        "trade_date": TRADE_DATE,
    }
    assert _snapshot_count(db_path) == 0


def test_two_accounts_are_built_sequentially_and_missing_quote_is_unavailable(
    tmp_path,
    capsys,
):
    db_path = _database(tmp_path)
    accounting = PortfolioAccountingService(db_path)
    empty_account = accounting.create_account(name="空账户")
    unpriced_account = accounting.create_account(name="缺行情账户")
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, price_micros,
                gross_amount_cents, cash_delta_cents
            ) VALUES (?, 'unpriced-buy', 'BUY', 'POSTED', ?, ?,
                      '600519', 100, 10000000, 100000, -100000)
            """,
            (
                unpriced_account.id,
                TRADE_DATE,
                f"{TRADE_DATE}T10:00:00+08:00",
            ),
        )
        conn.commit()

    exit_code = build_portfolio_snapshots.main(
        ["--trade-date", TRADE_DATE, "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 0
    assert payload["accounts"] == [
        {"account_id": empty_account.id, "status": "INITIAL"},
        {"account_id": unpriced_account.id, "status": "UNAVAILABLE"},
    ]
    assert payload["status_counts"] == {"INITIAL": 1, "UNAVAILABLE": 1}
    assert payload["snapshot_count"] == 2
    assert _snapshot_count(db_path) == 2


def test_non_trading_day_exits_nonzero_without_snapshot_writes(
    tmp_path,
    capsys,
):
    db_path = _database(tmp_path)
    PortfolioAccountingService(db_path).create_account(name="主账户")
    with sqlite3.connect(db_path) as conn:
        migrations_before = conn.execute(
            "SELECT version, name, applied_at FROM portfolio_schema_migrations"
        ).fetchall()

    exit_code = build_portfolio_snapshots.main(
        ["--trade-date", "2026-07-25", "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 1
    assert payload["error"] == {"code": "NOT_TRADING_DAY"}
    assert _snapshot_count(db_path) == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version, name, applied_at FROM portfolio_schema_migrations"
        ).fetchall() == migrations_before


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    (
        (sqlite3.OperationalError("database is locked"), "DATABASE_BUSY"),
        (SnapshotConsistencyError("projection mismatch"), "CONSISTENCY_ERROR"),
    ),
)
def test_account_failure_is_sanitized_and_returns_nonzero(
    tmp_path,
    capsys,
    monkeypatch,
    exception,
    expected_code,
):
    db_path = _database(tmp_path)
    account = PortfolioAccountingService(db_path).create_account(name="敏感名称")

    class FailingService:
        def __init__(self, *args, **kwargs):
            pass

        def build_account_snapshot(self, account_id, trade_date):
            raise exception

    monkeypatch.setattr(
        build_portfolio_snapshots,
        "PortfolioPerformanceService",
        FailingService,
    )

    exit_code = build_portfolio_snapshots.main(
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
    assert payload["ok"] is False
    assert payload["error"] == {
        "account_id": account.id,
        "code": expected_code,
    }
    assert str(db_path) not in json.dumps(payload, ensure_ascii=False)
    assert "敏感名称" not in json.dumps(payload, ensure_ascii=False)
    assert "projection mismatch" not in stderr
    assert _snapshot_count(db_path) == 0


def test_main_rejects_invalid_date_and_timeout(capsys):
    with pytest.raises(SystemExit) as date_error:
        build_portfolio_snapshots.main(
            ["--trade-date", "2026/07/24", "--db", "ignored.db"]
        )
    assert date_error.value.code == 2

    with pytest.raises(SystemExit) as timeout_error:
        build_portfolio_snapshots.main(
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


def test_start_date_rebuilds_explicit_range_and_reports_status(
    tmp_path,
    capsys,
    monkeypatch,
):
    start_date = "2026-07-21"
    db_path = _database(tmp_path)
    account = PortfolioAccountingService(db_path).create_account(name="主账户")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stock_daily_prices (symbol, trade_date, close)
            VALUES ('000001', ?, 10)
            """,
            (start_date,),
        )
        conn.commit()

    calls = []

    class RecordingService:
        def __init__(self, *args, **kwargs):
            pass

        def rebuild_account_snapshots(self, account_id, range_start, range_end):
            calls.append((account_id, range_start, range_end))
            return (
                SimpleNamespace(
                    account=SimpleNamespace(performance_status="INITIAL")
                ),
                SimpleNamespace(
                    account=SimpleNamespace(performance_status="COMPLETE")
                ),
            )

        def build_account_snapshot(self, *args, **kwargs):
            pytest.fail("range mode must not call the single-day builder")

    monkeypatch.setattr(
        build_portfolio_snapshots,
        "PortfolioPerformanceService",
        RecordingService,
    )

    exit_code = build_portfolio_snapshots.main(
        [
            "--start-date",
            start_date,
            "--trade-date",
            TRADE_DATE,
            "--db",
            str(db_path),
        ]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 0
    assert calls == [(account.id, start_date, TRADE_DATE)]
    assert payload["ok"] is True
    assert payload["start_date"] == start_date
    assert payload["trade_date"] == TRADE_DATE
    assert payload["rebuilt_snapshot_count"] == 2
    assert payload["accounts"] == [
        {
            "account_id": account.id,
            "rebuilt_snapshot_count": 2,
            "status": "COMPLETE",
        }
    ]


def test_daily_run_auto_repairs_an_earlier_missing_snapshot(
    tmp_path,
    capsys,
    monkeypatch,
):
    db_path = _database(tmp_path)
    accounting = PortfolioAccountingService(db_path)
    account = accounting.create_account(name="主账户")
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO stock_daily_prices (symbol, trade_date, close)
            VALUES ('000001', ?, 10)
            """,
            [("2026-07-21",), ("2026-07-22",), ("2026-07-23",)],
        )
        conn.commit()
    real_service = build_portfolio_snapshots.PortfolioPerformanceService(db_path)
    real_service.build_account_snapshot(account.id, "2026-07-21")
    gap = real_service.build_account_snapshot(account.id, "2026-07-23")
    assert gap.account.performance_status == "GAP"

    calls = []

    class RecordingService:
        def __init__(self, *args, **kwargs):
            pass

        def rebuild_account_snapshots(self, account_id, range_start, range_end):
            calls.append((account_id, range_start, range_end))
            return (
                SimpleNamespace(
                    account=SimpleNamespace(performance_status="COMPLETE")
                ),
                SimpleNamespace(
                    account=SimpleNamespace(performance_status="COMPLETE")
                ),
            )

        def build_account_snapshot(self, *args, **kwargs):
            pytest.fail("detected history gap must use atomic range rebuild")

    monkeypatch.setattr(
        build_portfolio_snapshots,
        "PortfolioPerformanceService",
        RecordingService,
    )

    exit_code = build_portfolio_snapshots.main(
        ["--trade-date", "2026-07-23", "--db", str(db_path)]
    )

    payload, _ = _payload(capsys)
    assert exit_code == 0
    assert calls == [(account.id, "2026-07-22", "2026-07-23")]
    assert payload["accounts"] == [
        {
            "account_id": account.id,
            "rebuilt_snapshot_count": 2,
            "status": "COMPLETE",
        }
    ]
    assert payload["rebuilt_snapshot_count"] == 2
