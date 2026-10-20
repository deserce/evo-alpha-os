import sqlite3

import pytest

from portfolio.accounting import PortfolioAccountingService
from portfolio.performance import (
    InvalidSnapshotDateError,
    PeriodPerformanceSummary,
    PortfolioPerformanceService,
    SnapshotAccountError,
    SnapshotConsistencyError,
)
from portfolio.schema import configure_portfolio_connection, ensure_portfolio_schema


def _setup(tmp_path, *, opening_cash_cents=1_000_000, raw_table=True):
    db_path = tmp_path / "portfolio-performance.db"
    ensure_portfolio_schema(db_path)
    accounting = PortfolioAccountingService(db_path)
    account = accounting.create_account(
        name="主账户",
        opening_cash_cents=opening_cash_cents,
    )
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, close REAL,
                adjustment_mode TEXT, quality_flag TEXT,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        if raw_table:
            conn.execute(
                """
                CREATE TABLE stock_daily_prices_raw (
                    symbol TEXT, trade_date TEXT, close,
                    source TEXT, quality_flag TEXT, run_id TEXT,
                    updated_at TEXT, adjustment_mode TEXT, market TEXT,
                    PRIMARY KEY (symbol, trade_date)
                )
                """
            )
        conn.commit()
    return db_path, accounting, account, PortfolioPerformanceService(db_path)


def _calendar(conn, *dates):
    conn.executemany(
        """
        INSERT INTO stock_daily_prices (
            symbol, trade_date, close, adjustment_mode, quality_flag
        ) VALUES ('000001', ?, 1, 'qfq', 'ok')
        """,
        [(item,) for item in dates],
    )


def _raw(
    conn,
    symbol,
    trade_date,
    close,
    *,
    quality="ok_unadjusted",
    mode="none",
    source="tdx_mootdx_raw",
):
    conn.execute(
        """
        INSERT INTO stock_daily_prices_raw (
            symbol, trade_date, close, source, quality_flag,
            adjustment_mode, market
        ) VALUES (?, ?, ?, ?, ?, ?, 'SH')
        """,
        (symbol, trade_date, close, source, quality, mode),
    )


def _buy(conn, account_id, symbol="600519", trade_date="2026-07-21"):
    return conn.execute(
        """
        INSERT INTO portfolio_transactions (
            account_id, client_request_id, business_type, status,
            trade_date, occurred_at, symbol, quantity, price_micros,
            gross_amount_cents, cash_delta_cents
        ) VALUES (?, ?, 'BUY', 'POSTED', ?, ?, ?, 100, 10000000, 100000, -100000)
        """,
        (
            account_id,
            f"buy-{account_id}-{symbol}-{trade_date}",
            trade_date,
            f"{trade_date}T10:00:00+08:00",
            symbol,
        ),
    ).lastrowid


def _flow(
    conn,
    account_id,
    business_type,
    amount,
    *,
    trade_date="2026-07-22",
    occurred_at="2026-07-22T12:00:00+08:00",
    counterparty_account_id=None,
    request_id=None,
):
    cash_delta = amount if business_type == "DEPOSIT" else -amount
    if business_type == "TRANSFER":
        cash_delta = 0
    return conn.execute(
        """
        INSERT INTO portfolio_transactions (
            account_id, counterparty_account_id, client_request_id,
            business_type, status, trade_date, occurred_at,
            gross_amount_cents, cash_delta_cents
        ) VALUES (?, ?, ?, ?, 'POSTED', ?, ?, ?, ?)
        """,
        (
            account_id,
            counterparty_account_id,
            request_id or f"{business_type}-{account_id}-{trade_date}",
            business_type,
            trade_date,
            occurred_at,
            amount,
            cash_delta,
        ),
    ).lastrowid


def _snapshot(
    conn,
    account_id,
    snapshot_date,
    *,
    status,
    pnl_cents=0,
    return_ppb=0,
    flow_cents=0,
    equity_cents=1_000_000,
    realized_pnl_cents=0,
    unrealized_pnl_cents=0,
):
    is_unavailable = status == "UNAVAILABLE"
    if status in {"GAP", "UNAVAILABLE"}:
        pnl_cents = None
        return_ppb = None
    conn.execute(
        """
        INSERT INTO portfolio_account_daily_snapshots (
            account_id, snapshot_date, ledger_revision, cash_cents,
            market_value_cents, total_equity_cents,
            net_external_flow_cents, weighted_external_flow_micros,
            pnl_cents, return_ppb, realized_pnl_cents,
            unrealized_pnl_cents, position_count,
            unpriced_position_count, price_status,
            expected_price_date, performance_status, return_base_date
        ) VALUES (?, ?, 0, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            snapshot_date,
            equity_cents if is_unavailable else equity_cents,
            None if is_unavailable else 0,
            None if is_unavailable else equity_cents,
            flow_cents,
            pnl_cents,
            return_ppb,
            realized_pnl_cents,
            None if is_unavailable else unrealized_pnl_cents,
            1 if is_unavailable else 0,
            1 if is_unavailable else 0,
            "PARTIAL" if is_unavailable else "COMPLETE",
            snapshot_date,
            status,
            (
                "1900-01-01"
                if status == "COMPLETE"
                else None
            ),
        ),
    )


def test_builds_initial_and_next_day_modified_dietz_snapshot(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-21", "2026-07-22")
        _buy(conn, account.id)
        _raw(conn, "600519", "2026-07-21", "12")
        _raw(conn, "600519", "2026-07-22", "13")
        conn.commit()

    first = service.build_account_snapshot(account.id, "2026-07-21")
    assert (first.account.price_status, first.account.performance_status) == (
        "COMPLETE",
        "INITIAL",
    )
    assert (first.account.cash_cents, first.account.market_value_cents) == (
        900_000,
        120_000,
    )
    assert first.account.total_equity_cents == 1_020_000
    assert first.account.realized_pnl_cents == 0
    assert first.account.unrealized_pnl_cents == 20_000
    assert first.positions[0].price_micros == 12_000_000

    second = service.build_account_snapshot(account.id, "2026-07-22")
    assert second.account.performance_status == "COMPLETE"
    assert second.account.pnl_cents == 10_000
    assert second.account.return_ppb == 9_803_922
    assert second.account.return_base_date == "2026-07-21"


def test_stale_price_is_complete_but_missing_raw_is_unavailable(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-21", "2026-07-22", "2026-07-23")
        _buy(conn, account.id)
        _raw(conn, "600519", "2026-07-21", "12")
        conn.commit()

    stale = service.build_account_snapshot(account.id, "2026-07-22")
    assert stale.account.price_status == "STALE"
    assert stale.positions[0].price_status == "STALE"

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM stock_daily_prices_raw")
        conn.execute(
            """
            INSERT OR REPLACE INTO stock_daily_prices (
                symbol, trade_date, close, adjustment_mode, quality_flag
            ) VALUES ('600519', '2026-07-23', 9999, 'qfq', 'ok')
            """
        )
        conn.commit()
    missing = service.build_account_snapshot(account.id, "2026-07-23")
    assert missing.account.price_status == "PARTIAL"
    assert missing.account.performance_status == "UNAVAILABLE"
    assert missing.account.market_value_cents is None
    assert missing.account.total_equity_cents is None
    assert missing.account.pnl_cents is None
    assert missing.positions[0].price_status == "MISSING"


@pytest.mark.parametrize(
    ("close", "quality", "mode"),
    [
        ("NaN", "ok_unadjusted", "none"),
        ("0", "ok_unadjusted", "none"),
        ("12", "suspect", "none"),
        ("12", "ok_unadjusted", "qfq"),
    ],
)
def test_raw_price_validation_fails_closed(tmp_path, close, quality, mode):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        _buy(conn, account.id)
        _raw(conn, "600519", "2026-07-21", close, quality=quality, mode=mode)
        conn.commit()
    result = service.build_account_snapshot(account.id, "2026-07-21")
    assert result.account.performance_status == "UNAVAILABLE"


def test_raw_price_with_untrusted_source_fails_closed(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        _buy(conn, account.id)
        _raw(
            conn,
            "600519",
            "2026-07-21",
            "12",
            source="vendor_claiming_raw",
        )
        conn.commit()
    result = service.build_account_snapshot(account.id, "2026-07-21")
    assert result.account.price_status == "PARTIAL"
    assert result.account.performance_status == "UNAVAILABLE"
    assert result.positions[0].price_status == "MISSING"


def test_missing_raw_table_does_not_affect_cash_only_account(tmp_path):
    db_path, _, account, service = _setup(tmp_path, raw_table=False)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        conn.commit()
    result = service.build_account_snapshot(account.id, "2026-07-21")
    assert result.account.price_status == "COMPLETE"
    assert result.account.total_equity_cents == 1_000_000
    assert result.positions == ()


def test_missing_raw_table_with_open_position_is_unavailable(tmp_path):
    db_path, _, account, service = _setup(tmp_path, raw_table=False)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        _buy(conn, account.id)
        conn.commit()
    result = service.build_account_snapshot(account.id, "2026-07-21")
    assert result.account.price_status == "PARTIAL"
    assert result.account.performance_status == "UNAVAILABLE"


def test_gap_and_non_trading_day_rules(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21", "2026-07-22", "2026-07-23")
        conn.commit()
    service.build_account_snapshot(account.id, "2026-07-21")
    gap = service.build_account_snapshot(account.id, "2026-07-23")
    assert gap.account.performance_status == "GAP"
    assert gap.account.pnl_cents is None
    with pytest.raises(InvalidSnapshotDateError):
        service.build_account_snapshot(account.id, "2026-07-20")


def test_rejects_out_of_order_snapshot_without_writing(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21", "2026-07-22", "2026-07-23")
        conn.commit()

    latest = service.build_account_snapshot(account.id, "2026-07-23")
    assert latest.account.performance_status == "INITIAL"
    assert service.build_account_snapshot(account.id, "2026-07-23") == latest

    with pytest.raises(SnapshotConsistencyError, match="later snapshot"):
        service.build_account_snapshot(account.id, "2026-07-22")
    with sqlite3.connect(db_path) as conn:
        account_dates = conn.execute(
            """
            SELECT snapshot_date
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ?
            ORDER BY snapshot_date
            """,
            (account.id,),
        ).fetchall()
        position_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM portfolio_position_daily_snapshots
            WHERE account_id = ? AND snapshot_date = '2026-07-22'
            """,
            (account.id,),
        ).fetchone()[0]
    assert account_dates == [("2026-07-23",)]
    assert position_count == 0


def test_prior_snapshot_revision_mismatch_after_reversal_forces_gap(tmp_path):
    db_path, accounting, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21", "2026-07-22")
        deposit_id = _flow(
            conn,
            account.id,
            "DEPOSIT",
            10_000,
            trade_date="2026-07-21",
            occurred_at="2026-07-21T12:00:00+08:00",
        )
        conn.commit()

    first = service.build_account_snapshot(account.id, "2026-07-21")
    before_reversal = service.build_account_snapshot(account.id, "2026-07-22")
    assert before_reversal.account.performance_status == "COMPLETE"
    assert first.account.ledger_revision == deposit_id

    reversal = accounting.reverse_transaction(
        account_id=account.id,
        transaction_id=deposit_id,
        client_request_id="reverse-prior-deposit",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T13:00:00+08:00",
    )
    rebuilt = service.build_account_snapshot(account.id, "2026-07-22")
    assert rebuilt.account.ledger_revision == reversal.id
    assert rebuilt.account.performance_status == "GAP"
    assert rebuilt.account.pnl_cents is None
    assert rebuilt.account.return_ppb is None
    assert rebuilt.account.return_base_date is None


def test_rebuild_account_snapshots_repairs_missing_and_stale_range(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-21", "2026-07-22", "2026-07-23")
        buy_id = _buy(conn, account.id)
        _raw(conn, "600519", "2026-07-21", "12")
        _raw(conn, "600519", "2026-07-22", "13")
        _raw(conn, "600519", "2026-07-23", "14")
        conn.commit()

    first = service.build_account_snapshot(account.id, "2026-07-21")
    stale_gap = service.build_account_snapshot(account.id, "2026-07-23")
    assert first.account.ledger_revision == buy_id
    assert stale_gap.account.performance_status == "GAP"

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        latest_revision = _flow(
            conn,
            account.id,
            "DEPOSIT",
            10_000,
            trade_date="2026-07-21",
            occurred_at="2026-07-21T12:00:00+08:00",
        )
        conn.commit()

    rebuilt = service.rebuild_account_snapshots(
        account.id,
        "2026-07-21",
        "2026-07-23",
    )

    assert [item.account.snapshot_date for item in rebuilt] == [
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
    ]
    assert [item.account.performance_status for item in rebuilt] == [
        "INITIAL",
        "COMPLETE",
        "COMPLETE",
    ]
    assert [item.account.price_status for item in rebuilt] == [
        "COMPLETE",
        "COMPLETE",
        "COMPLETE",
    ]
    assert [item.account.ledger_revision for item in rebuilt] == [
        latest_revision,
        latest_revision,
        latest_revision,
    ]
    assert [item.account.total_equity_cents for item in rebuilt] == [
        1_030_000,
        1_040_000,
        1_050_000,
    ]
    assert [item.account.pnl_cents for item in rebuilt] == [0, 10_000, 10_000]
    assert [item.account.return_base_date for item in rebuilt] == [
        None,
        "2026-07-21",
        "2026-07-22",
    ]
    assert all(
        result.positions[0].price_date == result.account.snapshot_date
        and result.positions[0].price_status == "CURRENT"
        for result in rebuilt
    )


def test_rebuild_account_snapshots_missing_exact_raw_price_rolls_back(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-21", "2026-07-22")
        _buy(conn, account.id)
        _raw(conn, "600519", "2026-07-21", "12")
        _raw(conn, "600519", "2026-07-22", "13")
        conn.commit()

    service.build_account_snapshot(account.id, "2026-07-21")
    service.build_account_snapshot(account.id, "2026-07-22")
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _flow(
            conn,
            account.id,
            "DEPOSIT",
            10_000,
            trade_date="2026-07-21",
            occurred_at="2026-07-21T12:00:00+08:00",
        )
        conn.execute(
            """
            DELETE FROM stock_daily_prices_raw
            WHERE symbol = '600519' AND trade_date = '2026-07-22'
            """
        )
        old_accounts = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT * FROM portfolio_account_daily_snapshots
                WHERE account_id = ? ORDER BY snapshot_date
                """,
                (account.id,),
            ).fetchall()
        ]
        old_positions = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT * FROM portfolio_position_daily_snapshots
                WHERE account_id = ? ORDER BY snapshot_date, symbol
                """,
                (account.id,),
            ).fetchall()
        ]
        conn.commit()

    with pytest.raises(SnapshotConsistencyError, match="raw close"):
        service.rebuild_account_snapshots(
            account.id,
            "2026-07-21",
            "2026-07-22",
        )

    with sqlite3.connect(db_path) as conn:
        assert [
            tuple(row)
            for row in conn.execute(
                """
                SELECT * FROM portfolio_account_daily_snapshots
                WHERE account_id = ? ORDER BY snapshot_date
                """,
                (account.id,),
            ).fetchall()
        ] == old_accounts
        assert [
            tuple(row)
            for row in conn.execute(
                """
                SELECT * FROM portfolio_position_daily_snapshots
                WHERE account_id = ? ORDER BY snapshot_date, symbol
                """,
                (account.id,),
            ).fetchall()
        ] == old_positions


def test_intraday_flow_weight_transfer_and_reversal(tmp_path):
    db_path, accounting, source, source_service = _setup(tmp_path)
    target = accounting.create_account(name="目标账户", opening_cash_cents=0)
    target_service = PortfolioPerformanceService(db_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21", "2026-07-22")
        transfer_id = _flow(
            conn,
            source.id,
            "TRANSFER",
            20_000,
            counterparty_account_id=target.id,
        )
        conn.commit()
    source_service.build_account_snapshot(source.id, "2026-07-21")
    target_service.build_account_snapshot(target.id, "2026-07-21")
    source_day = source_service.build_account_snapshot(source.id, "2026-07-22")
    target_day = target_service.build_account_snapshot(target.id, "2026-07-22")
    assert source_day.account.net_external_flow_cents == -20_000
    assert target_day.account.net_external_flow_cents == 20_000
    assert source_day.account.weighted_external_flow_micros == -10_000_000_000
    assert target_day.account.weighted_external_flow_micros == 10_000_000_000

    accounting.reverse_transaction(
        account_id=source.id,
        transaction_id=transfer_id,
        client_request_id="reverse-transfer",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T13:00:00+08:00",
    )
    rebuilt = source_service.build_account_snapshot(source.id, "2026-07-22")
    assert rebuilt.account.net_external_flow_cents == 0
    assert rebuilt.account.weighted_external_flow_micros == 0


def test_deposit_uses_half_day_weight_and_idempotent_upsert(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21", "2026-07-22")
        _flow(conn, account.id, "DEPOSIT", 10_000)
        conn.commit()
    service.build_account_snapshot(account.id, "2026-07-21")
    first = service.build_account_snapshot(account.id, "2026-07-22")
    second = service.build_account_snapshot(account.id, "2026-07-22")
    assert first == second
    assert second.account.weighted_external_flow_micros == 5_000_000_000
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
        ).fetchone()[0] == 2


def test_price_loading_is_one_batch_and_atomic_rollback_restores_old_rows(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        first_id = _buy(conn, account.id, "600519")
        _buy(conn, account.id, "000002")
        _raw(conn, "600519", "2026-07-21", "12")
        _raw(conn, "000002", "2026-07-21", "11")
        conn.commit()
    original = service.build_account_snapshot(account.id, "2026-07-21")
    assert service.raw_price_batch_query_count == 1
    assert len(original.positions) == 2

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TRIGGER reject_snapshot_rebuild
            BEFORE INSERT ON portfolio_position_daily_snapshots
            WHEN NEW.last_transaction_id = {int(first_id)}
            BEGIN
                SELECT RAISE(ABORT, 'forced snapshot failure');
            END
            """
        )
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="forced snapshot failure"):
        service.build_account_snapshot(account.id, "2026-07-21")
    assert service.get_account_snapshots(account.id) == (original.account,)


def test_archived_account_and_busy_database_do_not_write_partial_rows(tmp_path):
    db_path, _, account, _ = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        conn.execute(
            """
            UPDATE portfolio_accounts
            SET status = 'ARCHIVED', archived_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account.id,),
        )
        conn.commit()
    service = PortfolioPerformanceService(db_path, busy_timeout_ms=1)
    with pytest.raises(SnapshotAccountError):
        service.build_account_snapshot(account.id, "2026-07-21")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE portfolio_accounts
            SET status = 'ACTIVE', archived_at = NULL
            WHERE id = ?
            """,
            (account.id,),
        )
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            service.build_account_snapshot(account.id, "2026-07-21")
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
        ).fetchone()[0] == 0


def test_uses_one_account_projection_before_opening_write_connection(tmp_path):
    db_path, accounting, account, _ = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        conn.commit()

    original_project = accounting.project_account_as_of
    calls = 0
    replay_finished = False

    def tracked_project(account_id, as_of_date):
        nonlocal calls, replay_finished
        calls += 1
        result = original_project(account_id, as_of_date)
        replay_finished = True
        return result

    accounting.project_account_as_of = tracked_project
    accounting.project_positions_as_of = lambda *_: pytest.fail(
        "legacy position projection must not be called"
    )
    accounting.cash_balance_as_of = lambda *_: pytest.fail(
        "legacy cash projection must not be called"
    )
    accounting.ledger_revision_as_of = lambda *_: pytest.fail(
        "legacy revision projection must not be called"
    )

    class OrderedPerformanceService(PortfolioPerformanceService):
        def _connect(self):
            assert replay_finished, "write connection opened before ledger replay"
            return super()._connect()

    service = OrderedPerformanceService(
        db_path,
        accounting_service=accounting,
    )
    result = service.build_account_snapshot(account.id, "2026-07-21")
    assert result.account.performance_status == "INITIAL"
    assert calls == 1


def test_revision_change_between_projection_and_write_is_zero_write(tmp_path):
    db_path, accounting, account, _ = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _calendar(conn, "2026-07-21")
        conn.commit()

    class RacingAccounting:
        def __init__(self):
            self.calls = 0

        def project_account_as_of(self, account_id, as_of_date):
            self.calls += 1
            projection = accounting.project_account_as_of(
                account_id,
                as_of_date,
            )
            with sqlite3.connect(db_path) as conn:
                configure_portfolio_connection(conn)
                _flow(
                    conn,
                    account_id,
                    "DEPOSIT",
                    10_000,
                    trade_date=as_of_date,
                    occurred_at=f"{as_of_date}T12:00:00+08:00",
                    request_id="race-deposit",
                )
                conn.commit()
            return projection

    racing = RacingAccounting()
    service = PortfolioPerformanceService(
        db_path,
        accounting_service=racing,
    )
    with pytest.raises(SnapshotConsistencyError, match="stale"):
        service.build_account_snapshot(account.id, "2026-07-21")
    assert racing.calls == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_position_daily_snapshots"
        ).fetchone()[0] == 0


def test_summarizes_day_week_month_and_year_with_compounded_returns(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    dates = (
        "2026-01-02",
        "2026-06-29",
        "2026-06-30",
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    )
    rows = (
        ("INITIAL", 0, 0, 10_000),
        ("COMPLETE", 1_000, 100_000_000, 2_000),
        ("COMPLETE", -500, -50_000_000, -500),
        ("COMPLETE", 200, 20_000_000, 0),
        ("COMPLETE", 300, 30_000_000, 100),
        ("COMPLETE", 400, 40_000_000, -50),
    )
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, *dates)
        for snapshot_date, values in zip(dates, rows, strict=True):
            _snapshot(
                conn,
                account.id,
                snapshot_date,
                status=values[0],
                pnl_cents=values[1],
                return_ppb=values[2],
                flow_cents=values[3],
                equity_cents=1_200_000,
                realized_pnl_cents=100,
                unrealized_pnl_cents=200,
            )
        conn.commit()

    summaries = service.summarize_account_periods(account.id)

    assert tuple(summaries) == ("DAY", "WEEK", "MONTH", "YEAR")
    assert all(
        isinstance(summary, PeriodPerformanceSummary)
        for summary in summaries.values()
    )
    assert summaries["DAY"] == PeriodPerformanceSummary(
        period="DAY",
        start_date="2026-07-03",
        end_date="2026-07-03",
        status="COMPLETE",
        trading_day_count=1,
        net_external_flow_cents=-50,
        pnl_cents=400,
        return_ppb=40_000_000,
        end_equity_cents=1_200_000,
        end_realized_pnl_cents=100,
        end_unrealized_pnl_cents=200,
    )
    assert (
        summaries["WEEK"].start_date,
        summaries["WEEK"].trading_day_count,
        summaries["WEEK"].net_external_flow_cents,
        summaries["WEEK"].pnl_cents,
        summaries["WEEK"].return_ppb,
    ) == ("2026-06-29", 5, 1_550, 1_400, 141_792_080)
    assert (
        summaries["MONTH"].start_date,
        summaries["MONTH"].trading_day_count,
        summaries["MONTH"].net_external_flow_cents,
        summaries["MONTH"].pnl_cents,
        summaries["MONTH"].return_ppb,
    ) == ("2026-07-01", 3, 50, 900, 92_624_000)
    assert (
        summaries["YEAR"].start_date,
        summaries["YEAR"].trading_day_count,
        summaries["YEAR"].net_external_flow_cents,
        summaries["YEAR"].pnl_cents,
        summaries["YEAR"].return_ppb,
    ) == ("2026-01-02", 6, 11_550, 1_400, 141_792_080)
    assert summaries["WEEK"].return_ppb != sum(
        value[2] for value in rows[1:]
    )


def test_midperiod_first_initial_snapshot_is_a_valid_baseline(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(
            conn,
            "2026-07-20",
            "2026-07-21",
            "2026-07-22",
            "2026-07-23",
            "2026-07-24",
        )
        _snapshot(conn, account.id, "2026-07-22", status="INITIAL")
        _snapshot(
            conn,
            account.id,
            "2026-07-23",
            status="COMPLETE",
            pnl_cents=100,
            return_ppb=100_000_000,
        )
        _snapshot(
            conn,
            account.id,
            "2026-07-24",
            status="COMPLETE",
            pnl_cents=50,
            return_ppb=-50_000_000,
        )
        conn.commit()

    week = service.summarize_account_periods(
        account.id,
        as_of_date="2026-07-24",
    )["WEEK"]

    assert (week.status, week.start_date, week.trading_day_count) == (
        "COMPLETE",
        "2026-07-22",
        3,
    )
    assert (week.pnl_cents, week.return_ppb) == (150, 45_000_000)


def test_midperiod_first_snapshot_must_be_initial(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-20", "2026-07-21", "2026-07-22")
        _snapshot(
            conn,
            account.id,
            "2026-07-21",
            status="COMPLETE",
            pnl_cents=100,
            return_ppb=10_000_000,
        )
        _snapshot(
            conn,
            account.id,
            "2026-07-22",
            status="COMPLETE",
            pnl_cents=200,
            return_ppb=20_000_000,
        )
        conn.commit()

    week = service.summarize_account_periods(account.id)["WEEK"]

    assert week.status == "GAP"
    assert (week.pnl_cents, week.return_ppb) == (None, None)


def test_period_gap_nulls_financial_outputs_but_day_reuses_selected_snapshot(
    tmp_path,
):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(
            conn,
            "2026-07-17",
            "2026-07-20",
            "2026-07-21",
            "2026-07-22",
        )
        _snapshot(conn, account.id, "2026-07-17", status="INITIAL")
        _snapshot(
            conn,
            account.id,
            "2026-07-20",
            status="COMPLETE",
            pnl_cents=100,
            return_ppb=10_000_000,
        )
        _snapshot(
            conn,
            account.id,
            "2026-07-22",
            status="COMPLETE",
            pnl_cents=200,
            return_ppb=20_000_000,
        )
        conn.commit()

    summaries = service.summarize_account_periods(account.id)

    assert summaries["DAY"].status == "COMPLETE"
    assert (summaries["DAY"].pnl_cents, summaries["DAY"].return_ppb) == (
        200,
        20_000_000,
    )
    week = summaries["WEEK"]
    assert (week.status, week.start_date, week.trading_day_count) == (
        "GAP",
        "2026-07-20",
        3,
    )
    assert (
        week.net_external_flow_cents,
        week.pnl_cents,
        week.return_ppb,
        week.end_equity_cents,
        week.end_realized_pnl_cents,
        week.end_unrealized_pnl_cents,
    ) == (None, None, None, None, None, None)


@pytest.mark.parametrize(
    ("bad_status", "expected"),
    (("GAP", "GAP"), ("UNAVAILABLE", "UNAVAILABLE")),
)
def test_gap_or_unavailable_daily_snapshot_invalidates_period(
    tmp_path,
    bad_status,
    expected,
):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-20", "2026-07-21")
        _snapshot(conn, account.id, "2026-07-20", status="INITIAL")
        _snapshot(conn, account.id, "2026-07-21", status=bad_status)
        conn.commit()

    summaries = service.summarize_account_periods(account.id)

    assert summaries["DAY"].status == expected
    assert summaries["WEEK"].status == expected
    assert summaries["DAY"].pnl_cents is None
    assert summaries["WEEK"].return_ppb is None


def test_weekend_as_of_uses_latest_snapshot_without_mutating_rows(tmp_path):
    db_path, _, account, service = _setup(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _calendar(conn, "2026-07-23", "2026-07-24")
        _snapshot(conn, account.id, "2026-07-23", status="INITIAL")
        _snapshot(
            conn,
            account.id,
            "2026-07-24",
            status="COMPLETE",
            pnl_cents=123,
            return_ppb=12_300_000,
        )
        conn.commit()
        before = conn.execute(
            """
            SELECT *
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ?
            ORDER BY snapshot_date
            """,
            (account.id,),
        ).fetchall()

    summary = service.summarize_account_periods(
        account.id,
        as_of_date="2026-07-26",
    )["DAY"]

    assert (summary.end_date, summary.pnl_cents) == ("2026-07-24", 123)
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            """
            SELECT *
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ?
            ORDER BY snapshot_date
            """,
            (account.id,),
        ).fetchall()
    assert after == before


def test_empty_account_and_invalid_as_of_date(tmp_path):
    _, accounting, _, service = _setup(tmp_path)
    empty = accounting.create_account(name="空账户")

    summaries = service.summarize_account_periods(empty.id)

    assert tuple(summaries) == ("DAY", "WEEK", "MONTH", "YEAR")
    assert all(summary.status == "EMPTY" for summary in summaries.values())
    assert all(summary.start_date is None for summary in summaries.values())
    assert all(summary.trading_day_count == 0 for summary in summaries.values())
    with pytest.raises(InvalidSnapshotDateError):
        service.summarize_account_periods(empty.id, as_of_date="2026-02-30")
    with pytest.raises(SnapshotAccountError):
        service.summarize_account_periods(999_999)
