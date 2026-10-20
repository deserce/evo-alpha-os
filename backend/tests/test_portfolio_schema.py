import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import portfolio.schema as schema_module
from portfolio.schema import (
    BUSINESS_TYPES,
    SCHEMA_VERSION,
    TRANSACTION_STATUSES,
    configure_portfolio_connection,
    ensure_portfolio_schema,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = BACKEND_DIR / "scripts" / "migrate_portfolio_schema.py"
EXPECTED_TABLES = [
    "portfolio_schema_migrations",
    "portfolio_accounts",
    "portfolio_transactions",
    "portfolio_positions",
    "portfolio_account_daily_snapshots",
    "portfolio_position_daily_snapshots",
    "portfolio_exit_plans",
    "portfolio_exit_states",
    "portfolio_exit_events",
]


def _insert_account(
    conn: sqlite3.Connection,
    *,
    name: str = "主账户",
    is_default: int = 0,
    status: str = "ACTIVE",
    archived_at: str | None = None,
    currency: str = "CNY",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO portfolio_accounts (
            name, account_type, currency, opening_cash_cents,
            status, is_default, archived_at
        ) VALUES (?, 'CASH', ?, 10000000, ?, ?, ?)
        """,
        (name, currency, status, is_default, archived_at),
    )
    return int(cursor.lastrowid)


def _insert_buy(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    client_request_id: str = "buy-0001",
    symbol: str = "600519",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO portfolio_transactions (
            account_id, client_request_id, business_type, status,
            trade_date, occurred_at, symbol, quantity, price_micros,
            gross_amount_cents, cash_delta_cents, fee_cents, tax_cents,
            target_position_bps
        ) VALUES (
            ?, ?, 'BUY', 'POSTED',
            '2026-07-23', '2026-07-23T10:00:00+08:00',
            ?, 100, 1423500000, 14235000, -14235500, 500, 0, 2500
        )
        """,
        (account_id, client_request_id, symbol),
    )
    return int(cursor.lastrowid)


def _create_v1_schema(conn: sqlite3.Connection) -> None:
    """Reproduce the already-deployed v1 without relying on user_version."""
    conn.execute(schema_module._CREATE_ACCOUNTS_SQL)
    conn.execute(schema_module._CREATE_TRANSACTIONS_SQL)
    for statement in schema_module._CREATE_INDEX_SQL:
        conn.execute(statement)
    for statement in schema_module._CREATE_TRIGGER_SQL:
        conn.execute(statement)


def _create_v2_schema(conn: sqlite3.Connection) -> None:
    """Reproduce the deployed v2 with explicit migration history."""
    _create_v1_schema(conn)
    conn.execute(schema_module._CREATE_POSITIONS_SQL)
    for statement in schema_module._CREATE_POSITION_INDEX_SQL:
        conn.execute(statement)
    for statement in schema_module._CREATE_POSITION_TRIGGER_SQL:
        conn.execute(statement)
    conn.execute(schema_module._CREATE_MIGRATIONS_SQL)
    for statement in schema_module._CREATE_MIGRATION_TRIGGER_SQL:
        conn.execute(statement)
    conn.executemany(
        """
        INSERT INTO portfolio_schema_migrations (version, name)
        VALUES (?, ?)
        """,
        [
            (1, "create_accounts_and_transactions"),
            (2, "create_rebuildable_positions"),
        ],
    )


def _create_v3_schema(conn: sqlite3.Connection) -> None:
    """Reproduce the deployed v3 with explicit migration history."""
    _create_v2_schema(conn)
    schema_module._create_daily_snapshots(conn)
    conn.execute(
        """
        INSERT INTO portfolio_schema_migrations (version, name)
        VALUES (3, 'create_rebuildable_daily_snapshots')
        """
    )


def _insert_exit_plan(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    entry_transaction_id: int,
    symbol: str = "600519",
    position_revision: int = 1,
    enabled: int = 1,
    hard_stop_micros: int | None = 1_300_000_000,
    take_profit_1_micros: int | None = 1_500_000_000,
    take_profit_1_reduce_bps: int | None = 3000,
    take_profit_2_micros: int | None = 1_600_000_000,
    take_profit_2_reduce_bps: int | None = 3000,
    take_profit_3_micros: int | None = 1_700_000_000,
    take_profit_3_reduce_bps: int | None = 4000,
    trailing_activation_bps: int | None = 1000,
    trailing_distance_bps: int | None = 500,
    time_stop_trading_days: int | None = 20,
    time_stop_min_return_bps: int | None = 0,
    trend_exit_enabled: int = 1,
    intraday_enabled: int = 1,
    rule_version: str = "exit-v1",
) -> None:
    entry_row = conn.execute(
        """
        SELECT symbol
        FROM portfolio_transactions
        WHERE id = ? AND account_id = ?
        """,
        (entry_transaction_id, account_id),
    ).fetchone()
    has_position = conn.execute(
        """
        SELECT 1
        FROM portfolio_positions
        WHERE account_id = ? AND symbol = ?
        """,
        (account_id, symbol),
    ).fetchone()
    if (
        entry_row is not None
        and entry_row[0] == symbol
        and has_position is None
    ):
        _insert_position(
            conn,
            account_id=account_id,
            symbol=symbol,
            last_transaction_id=entry_transaction_id,
        )
    conn.execute(
        """
        INSERT INTO portfolio_exit_plans (
            account_id, symbol, position_revision, enabled,
            entry_transaction_id, hard_stop_micros,
            take_profit_1_micros, take_profit_1_reduce_bps,
            take_profit_2_micros, take_profit_2_reduce_bps,
            take_profit_3_micros, take_profit_3_reduce_bps,
            trailing_activation_bps, trailing_distance_bps,
            time_stop_trading_days, time_stop_min_return_bps,
            trend_exit_enabled, intraday_enabled, rule_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            symbol,
            position_revision,
            enabled,
            entry_transaction_id,
            hard_stop_micros,
            take_profit_1_micros,
            take_profit_1_reduce_bps,
            take_profit_2_micros,
            take_profit_2_reduce_bps,
            take_profit_3_micros,
            take_profit_3_reduce_bps,
            trailing_activation_bps,
            trailing_distance_bps,
            time_stop_trading_days,
            time_stop_min_return_bps,
            trend_exit_enabled,
            intraday_enabled,
            rule_version,
        ),
    )


def _insert_exit_state(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    last_transaction_id: int,
    symbol: str = "600519",
    position_revision: int = 1,
    state: str = "NORMAL",
    highest_price_micros: int | None = 1_500_000_000,
    trailing_stop_micros: int | None = 1_425_000_000,
    last_price_micros: int | None = 1_423_500_000,
    last_price_date: str | None = "2026-07-23",
    last_bar_time: str | None = "2026-07-23T14:55:00+08:00",
    data_status: str = "OK",
    rule_version: str = "exit-v1",
) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_exit_states (
            account_id, symbol, position_revision, state,
            highest_price_micros, trailing_stop_micros,
            last_price_micros, last_price_date, last_bar_time,
            data_status, last_transaction_id, rule_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            symbol,
            position_revision,
            state,
            highest_price_micros,
            trailing_stop_micros,
            last_price_micros,
            last_price_date,
            last_bar_time,
            data_status,
            last_transaction_id,
            rule_version,
        ),
    )


def _insert_exit_event(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    symbol: str = "600519",
    position_revision: int = 1,
    event_type: str = "TAKE_PROFIT_TRIGGERED",
    severity: str = "WARNING",
    event_status: str = "OPEN",
    trigger_date: str = "2026-07-23",
    triggered_at: str = "2026-07-23T14:55:30+08:00",
    bar_time: str | None = "2026-07-23T14:55:00+08:00",
    observed_price_micros: int | None = 1_500_000_000,
    available_quantity: int = 100,
    recommended_quantity: int = 30,
    executable: int = 1,
    tier: int | None = 1,
    dedupe_key: str = "acct1:600519:r1:tp1:202607231455",
    payload_json: str | None = '{"source":"portfolio_5min"}',
    rule_version: str = "exit-v1",
    acknowledged_at: str | None = None,
    resolved_at: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO portfolio_exit_events (
            account_id, symbol, position_revision, event_type,
            severity, event_status, trigger_date, triggered_at, bar_time,
            observed_price_micros, available_quantity,
            recommended_quantity, executable, tier, dedupe_key,
            payload_json, rule_version, acknowledged_at, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            symbol,
            position_revision,
            event_type,
            severity,
            event_status,
            trigger_date,
            triggered_at,
            bar_time,
            observed_price_micros,
            available_quantity,
            recommended_quantity,
            executable,
            tier,
            dedupe_key,
            payload_json,
            rule_version,
            acknowledged_at,
            resolved_at,
        ),
    )
    return int(cursor.lastrowid)


def _insert_position(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    symbol: str = "600519",
    quantity: int = 100,
    available_quantity: int = 100,
    cost_basis_cents: int = 14235500,
    avg_cost_micros: int = 1423550000,
    realized_pnl_cents: int = 0,
    status: str = "OPEN",
    last_transaction_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_positions (
            account_id, symbol, position_revision, quantity,
            available_quantity, cost_basis_cents, avg_cost_micros,
            realized_pnl_cents, first_buy_date, last_trade_date,
            status, last_transaction_id
        ) VALUES (
            ?, ?, 1, ?, ?, ?, ?, ?,
            '2026-07-23', '2026-07-23', ?, ?
        )
        """,
        (
            account_id,
            symbol,
            quantity,
            available_quantity,
            cost_basis_cents,
            avg_cost_micros,
            realized_pnl_cents,
            status,
            last_transaction_id,
        ),
    )


def _insert_account_snapshot(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    snapshot_date: str = "2026-07-23",
    ledger_revision: int = 1,
    cash_cents: int = 1_000_000,
    market_value_cents: int | None = 14_235_000,
    total_equity_cents: int | None = 15_235_000,
    net_external_flow_cents: int = 0,
    weighted_external_flow_micros: int = 0,
    pnl_cents: int | None = 100,
    return_ppb: int | None = 6_566,
    realized_pnl_cents: int = 0,
    unrealized_pnl_cents: int | None = 100,
    position_count: int = 1,
    unpriced_position_count: int = 0,
    price_status: str = "COMPLETE",
    expected_price_date: str | None = "2026-07-23",
    performance_status: str = "COMPLETE",
    return_base_date: str | None = "2026-07-22",
) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_account_daily_snapshots (
            account_id, snapshot_date, ledger_revision, cash_cents,
            market_value_cents, total_equity_cents,
            net_external_flow_cents, weighted_external_flow_micros,
            pnl_cents, return_ppb, realized_pnl_cents,
            unrealized_pnl_cents, position_count,
            unpriced_position_count, price_status, expected_price_date,
            performance_status, return_base_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            snapshot_date,
            ledger_revision,
            cash_cents,
            market_value_cents,
            total_equity_cents,
            net_external_flow_cents,
            weighted_external_flow_micros,
            pnl_cents,
            return_ppb,
            realized_pnl_cents,
            unrealized_pnl_cents,
            position_count,
            unpriced_position_count,
            price_status,
            expected_price_date,
            performance_status,
            return_base_date,
        ),
    )


def _insert_position_snapshot(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    last_transaction_id: int | None,
    snapshot_date: str = "2026-07-23",
    symbol: str = "600519",
    position_revision: int = 1,
    quantity: int = 100,
    available_quantity: int = 100,
    cost_basis_cents: int = 14_234_900,
    realized_pnl_cents: int = 0,
    price_micros: int | None = 1_423_500_000,
    price_date: str | None = "2026-07-23",
    market_value_cents: int | None = 14_235_000,
    unrealized_pnl_cents: int | None = 100,
    price_status: str = "CURRENT",
) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_position_daily_snapshots (
            account_id, snapshot_date, symbol, position_revision,
            quantity, available_quantity, cost_basis_cents,
            realized_pnl_cents, price_micros, price_date,
            market_value_cents, unrealized_pnl_cents,
            price_status, last_transaction_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            snapshot_date,
            symbol,
            position_revision,
            quantity,
            available_quantity,
            cost_basis_cents,
            realized_pnl_cents,
            price_micros,
            price_date,
            market_value_cents,
            unrealized_pnl_cents,
            price_status,
            last_transaction_id,
        ),
    )


def test_schema_is_idempotent_and_preserves_existing_rows(tmp_path):
    db_path = tmp_path / "portfolio.db"

    first = ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn, is_default=1)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_position(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        conn.commit()

    second = ensure_portfolio_schema(db_path)

    assert first["schema_version"] == SCHEMA_VERSION == 4
    assert first["tables"] == EXPECTED_TABLES
    assert second["tables"] == first["tables"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert conn.execute("SELECT COUNT(*) FROM portfolio_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 1
        assert conn.execute(
            "SELECT account_id FROM portfolio_transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()[0] == account_id


def test_existing_v1_is_inferred_and_upgraded_without_using_user_version(tmp_path):
    db_path = tmp_path / "existing-v1.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v1_schema(conn)
        account_id = _insert_account(conn, is_default=1)
        transaction_id = _insert_buy(conn, account_id=account_id)
        conn.execute("PRAGMA user_version = 73")
        conn.commit()

    result = ensure_portfolio_schema(db_path)

    assert result["schema_version"] == 4
    assert result["tables"] == EXPECTED_TABLES
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 73
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 0


def test_positions_accept_open_and_closed_projection_rows(tmp_path):
    db_path = tmp_path / "positions.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_position(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        _insert_position(
            conn,
            account_id=account_id,
            symbol="000001",
            quantity=0,
            available_quantity=0,
            cost_basis_cents=0,
            avg_cost_micros=0,
            realized_pnl_cents=-12345,
            status="CLOSED",
        )

        rows = conn.execute(
            """
            SELECT symbol, quantity, available_quantity, status,
                   typeof(realized_pnl_cents)
            FROM portfolio_positions
            ORDER BY symbol
            """
        ).fetchall()

    assert rows == [
        ("000001", 0, 0, "CLOSED", "integer"),
        ("600519", 100, 100, "OPEN", "integer"),
    ]


def test_position_checkpoint_requires_eligible_same_account_and_symbol_transaction(
    tmp_path,
):
    db_path = tmp_path / "position-checkpoint.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        other_account_id = _insert_account(conn, name="账户B")
        buy_id = _insert_buy(conn, account_id=account_id)
        other_symbol_buy_id = _insert_buy(
            conn,
            account_id=account_id,
            client_request_id="buy-other-symbol",
            symbol="000001",
        )
        other_account_buy_id = _insert_buy(
            conn,
            account_id=other_account_id,
            client_request_id="buy-other-account",
        )
        dividend_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, gross_amount_cents,
                cash_delta_cents
            ) VALUES (
                ?, 'dividend-checkpoint', 'DIVIDEND', 'POSTED',
                '2026-07-23', '2026-07-23T11:00:00+08:00',
                '600519', 10000, 10000
            )
            """,
            (account_id,),
        ).lastrowid
        sell_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, price_micros,
                gross_amount_cents, cash_delta_cents, fee_cents
            ) VALUES (
                ?, 'sell-checkpoint', 'SELL', 'POSTED',
                '2026-07-23', '2026-07-23T11:15:00+08:00',
                '600519', 100, 1423500000, 14235000, 14234500, 500
            )
            """,
            (account_id,),
        ).lastrowid
        corporate_action_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, cash_delta_cents
            ) VALUES (
                ?, 'corporate-action-checkpoint',
                'CORPORATE_ACTION', 'POSTED',
                '2026-07-23', '2026-07-23T11:30:00+08:00',
                '600519', 10, 0
            )
            """,
            (account_id,),
        ).lastrowid

        _insert_position(
            conn,
            account_id=account_id,
            last_transaction_id=buy_id,
        )
        conn.execute(
            """
            UPDATE portfolio_positions
            SET last_transaction_id = ?, position_revision = 2
            WHERE account_id = ? AND symbol = '600519'
            """,
            (sell_id, account_id),
        )
        conn.execute(
            """
            UPDATE portfolio_positions
            SET last_transaction_id = ?, position_revision = 3
            WHERE account_id = ? AND symbol = '600519'
            """,
            (corporate_action_id, account_id),
        )

        invalid_checkpoints = (
            (None, "OPEN"),
            (other_symbol_buy_id, "OPEN"),
            (other_account_buy_id, "OPEN"),
            (dividend_id, "OPEN"),
        )
        for checkpoint_id, status in invalid_checkpoints:
            with pytest.raises(sqlite3.IntegrityError, match="checkpoint"):
                conn.execute(
                    """
                    UPDATE portfolio_positions
                    SET last_transaction_id = ?,
                        status = ?,
                        position_revision = position_revision + 1
                    WHERE account_id = ? AND symbol = '600519'
                    """,
                    (checkpoint_id, status, account_id),
                )

        with pytest.raises(sqlite3.IntegrityError, match="checkpoint"):
            _insert_position(
                conn,
                account_id=account_id,
                symbol="000002",
                last_transaction_id=other_symbol_buy_id,
            )

        assert conn.execute(
            """
            SELECT last_transaction_id, position_revision
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account_id,),
        ).fetchone() == (corporate_action_id, 3)


@pytest.mark.parametrize(
    (
        "symbol",
        "position_revision",
        "quantity",
        "available_quantity",
        "cost_basis_cents",
        "avg_cost_micros",
        "status",
    ),
    [
        ("60051", 1, 100, 100, 1000, 100000, "OPEN"),
        ("600519", 0, 100, 100, 1000, 100000, "OPEN"),
        ("600519", 1, -1, 0, 0, 0, "CLOSED"),
        ("600519", 1, 100, 101, 1000, 100000, "OPEN"),
        ("600519", 1, 100, 100, -1, 100000, "OPEN"),
        ("600519", 1, 100, 100, 1000, -1, "OPEN"),
        ("600519", 1, 0, 0, 0, 0, "OPEN"),
        ("600519", 1, 1, 0, 0, 0, "CLOSED"),
        ("600519", 1, 0, 0, 1, 0, "CLOSED"),
        ("600519", 1, 0, 0, 0, 1, "CLOSED"),
        ("600519", 1, 0, 0, 0, 0, "WATCHING"),
    ],
)
def test_position_constraints_reject_invalid_projection_rows(
    tmp_path,
    symbol,
    position_revision,
    quantity,
    available_quantity,
    cost_basis_cents,
    avg_cost_micros,
    status,
):
    db_path = tmp_path / "invalid-position.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_positions (
                    account_id, symbol, position_revision, quantity,
                    available_quantity, cost_basis_cents, avg_cost_micros,
                    realized_pnl_cents, first_buy_date, last_trade_date,
                    status
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, 0,
                    '2026-07-23', '2026-07-23', ?
                )
                """,
                (
                    account_id,
                    symbol,
                    position_revision,
                    quantity,
                    available_quantity,
                    cost_basis_cents,
                    avg_cost_micros,
                    status,
                ),
            )


def test_position_foreign_keys_primary_key_and_indexes(tmp_path):
    db_path = tmp_path / "position-structure.db"
    result = ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        other_account_id = _insert_account(conn, name="账户B")
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_position(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_position(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_position(conn, account_id=999, symbol="000001")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_position(
                conn,
                account_id=other_account_id,
                symbol="000001",
                last_transaction_id=transaction_id,
            )

        foreign_keys = {
            (row[2], row[3], row[4])
            for row in conn.execute("PRAGMA foreign_key_list(portfolio_positions)")
        }
        indexes = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index' AND tbl_name = 'portfolio_positions'
                """
            )
        }

    assert {
        ("portfolio_accounts", "account_id", "id"),
        ("portfolio_transactions", "last_transaction_id", "id"),
    } <= foreign_keys
    assert {
        "idx_portfolio_positions_account_status",
        "idx_portfolio_positions_status_updated",
    } <= indexes
    assert {
        "idx_portfolio_positions_account_status",
        "idx_portfolio_positions_status_updated",
    } <= set(result["indexes"])


def test_v2_upgrade_is_atomic_when_existing_positions_table_is_incompatible(tmp_path):
    db_path = tmp_path / "broken-v2.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v1_schema(conn)
        account_id = _insert_account(conn)
        _insert_buy(conn, account_id=account_id)
        conn.execute("CREATE TABLE portfolio_positions (account_id INTEGER)")
        conn.commit()

    with pytest.raises(RuntimeError, match="portfolio_positions.*missing required columns"):
        ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'portfolio_schema_migrations'
            """
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM portfolio_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0] == 1


def test_connection_enables_foreign_keys_and_bounded_busy_timeout(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path, busy_timeout_ms=4321)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn, busy_timeout_ms=4321)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 4321

        with pytest.raises(sqlite3.IntegrityError):
            _insert_buy(conn, account_id=999)


def test_only_one_active_default_account_and_archive_state_is_consistent(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _insert_account(conn, name="账户A", is_default=1)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_account(conn, name="账户B", is_default=1)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_account(
                conn,
                name="错误归档账户",
                status="ARCHIVED",
                archived_at=None,
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_account(
                conn,
                name="归档默认账户",
                is_default=1,
                status="ARCHIVED",
                archived_at="2026-07-23T12:00:00+08:00",
            )

        archived_id = _insert_account(
            conn,
            name="已归档账户",
            status="ARCHIVED",
            archived_at="2026-07-23T12:00:00+08:00",
        )
        assert archived_id > 0

        with pytest.raises(sqlite3.IntegrityError):
            _insert_account(conn, name="坏状态", status="DELETED")


def test_transaction_checks_units_enums_idempotency_and_reversals(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    assert {"BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "TRANSFER", "REVERSAL"} <= (
        BUSINESS_TYPES
    )
    assert "TRANSFER_IN" not in BUSINESS_TYPES
    assert "TRANSFER_OUT" not in BUSINESS_TYPES
    assert TRANSACTION_STATUSES == {"POSTED"}

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        second_account_id = _insert_account(conn, name="反转隔离账户")
        original_id = _insert_buy(conn, account_id=account_id)

        stored = conn.execute(
            """
            SELECT typeof(price_micros), price_micros,
                   typeof(gross_amount_cents), gross_amount_cents
            FROM portfolio_transactions
            WHERE id = ?
            """,
            (original_id,),
        ).fetchone()
        assert stored == ("integer", 1423500000, "integer", 14235000)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_buy(
                conn,
                account_id=account_id,
                client_request_id="buy-0001",
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, symbol, quantity, price_micros,
                    gross_amount_cents, cash_delta_cents, fee_cents, tax_cents
                ) VALUES (
                    ?, 'pending-buy', 'BUY', 'PENDING',
                    '2026-07-23', '2026-07-23T10:00:00+08:00',
                    '600519', 100, 1423500000, 14235000, -14235500, 500, 0
                )
                """,
                (account_id,),
            )

        invalid_rows = (
            (
                account_id,
                "invalid-type",
                "GAMBLE",
                "POSTED",
                "2026-07-23",
                "2026-07-23T10:00:00+08:00",
            ),
            (
                account_id,
                "invalid-status",
                "BUY",
                "PENDING",
                "2026-07-23",
                "2026-07-23T10:00:00+08:00",
            ),
        )
        for row in invalid_rows:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO portfolio_transactions (
                        account_id, client_request_id, business_type, status,
                        trade_date, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, symbol, quantity, price_micros,
                    gross_amount_cents
                ) VALUES (
                    ?, 'fractional-cents', 'BUY', 'POSTED',
                    '2026-07-23', '2026-07-23T10:00:00+08:00',
                    '600519', 100, 1423500000, 14235000.5
                )
                """,
                (account_id,),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, symbol, quantity,
                    gross_amount_cents
                ) VALUES (
                    ?, 'buy-without-price', 'BUY', 'POSTED',
                    '2026-07-23', '2026-07-23T10:00:00+08:00',
                    '600519', 100, 14235000
                )
                """,
                (account_id,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="reversal"):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, cash_delta_cents, reversal_of
                ) VALUES (
                    ?, 'reverse-wrong-cash', 'REVERSAL', 'POSTED',
                    '2026-07-23', '2026-07-23T10:30:00+08:00',
                    14235499, ?
                )
                """,
                (account_id, original_id),
            )

        with pytest.raises(sqlite3.IntegrityError, match="reversal"):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, cash_delta_cents, reversal_of
                ) VALUES (
                    ?, 'reverse-cross-account', 'REVERSAL', 'POSTED',
                    '2026-07-23', '2026-07-23T10:45:00+08:00',
                    14235500, ?
                )
                """,
                (second_account_id, original_id),
            )

        with pytest.raises(sqlite3.IntegrityError, match="reversal"):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, cash_delta_cents, reversal_of
                ) VALUES (
                    ?, 'reverse-before-original', 'REVERSAL', 'POSTED',
                    '2026-07-22', '2026-07-22T10:30:00+08:00',
                    14235500, ?
                )
                """,
                (account_id, original_id),
            )

        reversal_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, cash_delta_cents, reversal_of
            ) VALUES (
                ?, 'reverse-buy-0001', 'REVERSAL', 'POSTED',
                '2026-07-23', '2026-07-23T11:00:00+08:00',
                14235500, ?
            )
            """,
            (account_id, original_id),
        ).lastrowid
        assert reversal_id > original_id

        with pytest.raises(sqlite3.IntegrityError, match="reversal"):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, cash_delta_cents, reversal_of
                ) VALUES (
                    ?, 'reverse-a-reversal', 'REVERSAL', 'POSTED',
                    '2026-07-23', '2026-07-23T11:00:01+08:00',
                    -14235500, ?
                )
                """,
                (account_id, reversal_id),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, cash_delta_cents, reversal_of
                ) VALUES (
                    ?, 'reverse-buy-again', 'REVERSAL', 'POSTED',
                    '2026-07-23', '2026-07-23T11:01:00+08:00',
                    14235500, ?
                )
                """,
                (account_id, original_id),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, cash_delta_cents
                ) VALUES (
                    ?, 'reversal-without-target', 'REVERSAL', 'POSTED',
                    '2026-07-23', '2026-07-23T11:02:00+08:00',
                    14235500
                )
                """,
                (account_id,),
            )


def test_ledger_defaults_and_cash_direction_checks(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        second_account_id = _insert_account(conn, name="账户B")
        account = conn.execute(
            """
            SELECT cost_method, version
            FROM portfolio_accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        assert account == ("AVERAGE", 1)

        buy_id = _insert_buy(conn, account_id=account_id)
        transaction = conn.execute(
            """
            SELECT cash_delta_cents, source, counterparty_account_id
            FROM portfolio_transactions
            WHERE id = ?
            """,
            (buy_id,),
        ).fetchone()
        assert transaction == (-14235500, "MANUAL", None)

        cash_rows = (("DEPOSIT", 10000, 10000), ("WITHDRAWAL", 10000, -10000))
        for index, (business_type, gross, cash_delta) in enumerate(cash_rows):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, gross_amount_cents, cash_delta_cents
                ) VALUES (
                    ?, ?, ?, 'POSTED', '2026-07-23',
                    '2026-07-23T12:00:00+08:00', ?, ?
                )
                """,
                (
                    account_id,
                    f"cash-{index}",
                    business_type,
                    gross,
                    cash_delta,
                ),
            )

        transfer_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, counterparty_account_id,
                client_request_id, business_type, status,
                trade_date, occurred_at, gross_amount_cents,
                cash_delta_cents
            ) VALUES (
                ?, ?, 'transfer-001', 'TRANSFER', 'POSTED',
                '2026-07-23', '2026-07-23T12:00:00+08:00',
                20000, 0
            )
            """,
            (account_id, second_account_id),
        ).lastrowid
        transfer = conn.execute(
            """
            SELECT account_id, counterparty_account_id,
                   gross_amount_cents, cash_delta_cents
            FROM portfolio_transactions
            WHERE id = ?
            """,
            (transfer_id,),
        ).fetchone()
        assert transfer == (account_id, second_account_id, 20000, 0)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, counterparty_account_id,
                    client_request_id, business_type, status,
                    trade_date, occurred_at, gross_amount_cents,
                    cash_delta_cents
                ) VALUES (
                    ?, ?, 'transfer-same-account', 'TRANSFER', 'POSTED',
                    '2026-07-23', '2026-07-23T12:00:03+08:00',
                    30000, 0
                )
                """,
                (account_id, account_id),
            )

        usd_account_id = _insert_account(
            conn,
            name="美元账户",
            currency="USD",
        )
        with pytest.raises(sqlite3.IntegrityError, match="same currency"):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, counterparty_account_id,
                    client_request_id, business_type, status,
                    trade_date, occurred_at, gross_amount_cents,
                    cash_delta_cents
                ) VALUES (
                    ?, ?, 'transfer-cross-currency', 'TRANSFER', 'POSTED',
                    '2026-07-23', '2026-07-23T12:00:05+08:00',
                    40000, 0
                )
                """,
                (account_id, usd_account_id),
            )

        reversal_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, cash_delta_cents, reversal_of
            ) VALUES (
                ?, 'reverse-transfer-001', 'REVERSAL', 'POSTED',
                '2026-07-23', '2026-07-23T12:01:00+08:00', 0, ?
            )
            """,
            (account_id, transfer_id),
        ).lastrowid
        assert reversal_id > transfer_id

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, gross_amount_cents,
                    cash_delta_cents
                ) VALUES (
                    ?, 'deposit-wrong-direction', 'DEPOSIT', 'POSTED',
                    '2026-07-23', '2026-07-23T12:01:00+08:00',
                    10000, -10000
                )
                """,
                (account_id,),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, gross_amount_cents,
                    cash_delta_cents
                ) VALUES (
                    ?, 'transfer-without-counterparty', 'TRANSFER', 'POSTED',
                    '2026-07-23', '2026-07-23T12:02:00+08:00',
                    10000, 0
                )
                """,
                (account_id,),
            )


def test_trade_gross_amount_uses_bounded_round_half_up(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)

        rounded_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, price_micros,
                gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, 'half-up-valid', 'BUY', 'POSTED',
                '2026-07-23', '2026-07-23T12:30:00+08:00',
                '000001', 1, 15000, 2, -2
            )
            """,
            (account_id,),
        ).lastrowid
        assert rounded_id > 0

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, symbol, quantity, price_micros,
                    gross_amount_cents, cash_delta_cents
                ) VALUES (
                    ?, 'half-up-wrong', 'BUY', 'POSTED',
                    '2026-07-23', '2026-07-23T12:30:01+08:00',
                    '000001', 1, 15000, 1, -1
                )
                """,
                (account_id,),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, symbol, quantity, price_micros,
                    gross_amount_cents, cash_delta_cents
                ) VALUES (
                    ?, 'quantity-overflow-bound', 'BUY', 'POSTED',
                    '2026-07-23', '2026-07-23T12:30:02+08:00',
                    '000001', 100000001, 10000, 100000001, -100000001
                )
                """,
                (account_id,),
            )


def test_datetime_json_and_non_trade_fee_tax_checks(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)

        invalid_rows = (
            (
                "bad-occurred-at",
                "not-a-datetime",
                0,
                0,
                '{"source":"test"}',
            ),
            (
                "bad-json",
                "2026-07-23T13:00:00+08:00",
                0,
                0,
                "{bad-json}",
            ),
            (
                "non-trade-fee",
                "2026-07-23T13:00:00+08:00",
                1,
                0,
                '{"source":"test"}',
            ),
        )
        for client_request_id, occurred_at, fee, tax, metadata in invalid_rows:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO portfolio_transactions (
                        account_id, client_request_id, business_type, status,
                        trade_date, occurred_at, gross_amount_cents,
                        cash_delta_cents, fee_cents, tax_cents, metadata_json
                    ) VALUES (
                        ?, ?, 'DEPOSIT', 'POSTED', '2026-07-23',
                        ?, 10000, 10000, ?, ?, ?
                    )
                    """,
                    (
                        account_id,
                        client_request_id,
                        occurred_at,
                        fee,
                        tax,
                        metadata,
                    ),
                )

        oversized_metadata = json.dumps({"payload": "x" * 65536})
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO portfolio_transactions (
                    account_id, client_request_id, business_type, status,
                    trade_date, occurred_at, gross_amount_cents,
                    cash_delta_cents, metadata_json
                ) VALUES (
                    ?, 'metadata-too-large', 'DEPOSIT', 'POSTED',
                    '2026-07-23', '2026-07-23T13:00:00+08:00',
                    10000, 10000, ?
                )
                """,
                (account_id, oversized_metadata),
            )


def test_transactions_are_append_only_and_triggers_are_reported(tmp_path):
    db_path = tmp_path / "portfolio.db"
    result = ensure_portfolio_schema(db_path)

    assert result["triggers"] == [
        "trg_portfolio_transactions_no_update",
        "trg_portfolio_transactions_no_delete",
        "trg_portfolio_transactions_validate_reversal",
        "trg_portfolio_transactions_validate_transfer",
        "trg_portfolio_accounts_no_delete",
        "trg_portfolio_accounts_protect_history",
        "trg_portfolio_positions_validate_checkpoint_insert",
        "trg_portfolio_positions_validate_checkpoint_update",
        "trg_portfolio_schema_migrations_no_update",
        "trg_portfolio_schema_migrations_no_delete",
        "trg_portfolio_exit_plans_validate_entry_insert",
        "trg_portfolio_exit_plans_validate_entry_update",
        "trg_portfolio_exit_plans_protect_identity",
        "trg_portfolio_exit_plans_protect_event_history",
        "trg_portfolio_exit_states_validate_checkpoint_insert",
        "trg_portfolio_exit_states_validate_checkpoint_update",
        "trg_portfolio_exit_states_protect_identity",
        "trg_portfolio_exit_events_validate_insert_lifecycle",
        "trg_portfolio_exit_events_require_enabled_plan",
        "trg_portfolio_exit_events_no_delete",
        "trg_portfolio_exit_events_protect_facts",
        "trg_portfolio_exit_events_validate_status_transition",
    ]
    assert ensure_portfolio_schema(db_path)["triggers"] == result["triggers"]
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)

        triggers = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name IN (
                          'portfolio_accounts', 'portfolio_transactions',
                          'portfolio_positions', 'portfolio_schema_migrations',
                          'portfolio_exit_plans', 'portfolio_exit_states',
                          'portfolio_exit_events'
                      )
                """
            )
        }
        assert set(result["triggers"]) <= triggers

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                """
                UPDATE portfolio_transactions
                SET gross_amount_cents = gross_amount_cents + 1
                WHERE id = ?
                """,
                (transaction_id,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM portfolio_transactions WHERE id = ?",
                (transaction_id,),
            )


def test_migration_history_names_are_validated_and_rows_are_immutable(tmp_path):
    db_path = tmp_path / "migration-history.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        assert conn.execute(
            """
            SELECT version, name
            FROM portfolio_schema_migrations
            ORDER BY version
            """
        ).fetchall() == [
            (1, "create_accounts_and_transactions"),
            (2, "create_rebuildable_positions"),
            (3, "create_rebuildable_daily_snapshots"),
            (4, "create_portfolio_exit_alerts"),
        ]

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                """
                UPDATE portfolio_schema_migrations
                SET name = 'tampered'
                WHERE version = 1
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM portfolio_schema_migrations WHERE version = 2"
            )

    broken_db_path = tmp_path / "migration-history-tampered.db"
    with sqlite3.connect(broken_db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v1_schema(conn)
        conn.execute(schema_module._CREATE_MIGRATIONS_SQL)
        conn.execute(
            """
            INSERT INTO portfolio_schema_migrations (version, name)
            VALUES (1, 'wrong_v1_name')
            """
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="migration v1 name mismatch"):
        ensure_portfolio_schema(broken_db_path)

    with sqlite3.connect(broken_db_path) as conn:
        assert conn.execute(
            "SELECT name FROM portfolio_schema_migrations WHERE version = 1"
        ).fetchone()[0] == "wrong_v1_name"
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'portfolio_positions'
            """
        ).fetchone()[0] == 0


def test_expected_query_indexes_exist(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                  AND tbl_name IN ('portfolio_accounts', 'portfolio_transactions')
                """
            )
        }

    assert {
        "uq_portfolio_accounts_single_default",
        "idx_portfolio_accounts_status",
        "idx_portfolio_transactions_account_date",
        "idx_portfolio_transactions_symbol_date",
        "idx_portfolio_transactions_status_date",
        "idx_portfolio_transactions_counterparty_date",
        "uq_portfolio_transactions_reversal_of",
    } <= indexes


def test_accounts_cannot_be_deleted_or_change_historical_terms(tmp_path):
    db_path = tmp_path / "portfolio.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)

        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM portfolio_accounts WHERE id = ?",
                (account_id,),
            )

        protected_updates = (
            ("account_type", "MARGIN"),
            ("currency", "USD"),
            ("opening_cash_cents", 20000000),
            ("cost_method", "FIFO"),
        )
        for column_name, value in protected_updates:
            with pytest.raises(sqlite3.IntegrityError, match="historical terms"):
                conn.execute(
                    f"UPDATE portfolio_accounts SET {column_name} = ? WHERE id = ?",
                    (value, account_id),
                )

        conn.execute(
            """
            UPDATE portfolio_accounts
            SET name = '长期账户',
                broker_name = '测试券商',
                is_default = 1,
                version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        assert conn.execute(
            """
            SELECT name, broker_name, is_default, version
            FROM portfolio_accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone() == ("长期账户", "测试券商", 1, 2)

        archived_id = _insert_account(conn, name="待归档账户")
        conn.execute(
            """
            UPDATE portfolio_accounts
            SET status = 'ARCHIVED',
                archived_at = '2026-07-23T14:00:00+08:00',
                version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (archived_id,),
        )
        assert conn.execute(
            "SELECT status, version FROM portfolio_accounts WHERE id = ?",
            (archived_id,),
        ).fetchone() == ("ARCHIVED", 2)


def test_incompatible_legacy_schema_rolls_back_without_partial_migration(tmp_path):
    db_path = tmp_path / "legacy-portfolio.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE portfolio_transactions (id INTEGER PRIMARY KEY)")
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="portfolio_transactions.*missing required columns",
    ):
        ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE name LIKE 'portfolio_%'
                   OR name LIKE '%portfolio_transactions%'
                """
            )
        }

    assert ("table", "portfolio_transactions") in objects
    assert ("table", "portfolio_accounts") not in objects
    assert not any(object_type in {"index", "trigger"} for object_type, _ in objects)


def test_migration_cli_only_writes_requested_database_and_is_idempotent(tmp_path):
    db_path = tmp_path / "cli-portfolio.db"

    commands = [
        sys.executable,
        str(MIGRATION_SCRIPT),
        "--db",
        str(db_path),
        "--busy-timeout-ms",
        "2500",
    ]
    first = subprocess.run(
        commands,
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        commands,
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(first.stdout)["db_path"] == str(db_path)
    assert json.loads(first.stdout)["schema_version"] == 4
    assert json.loads(second.stdout)["tables"] == EXPECTED_TABLES
    assert db_path.exists()


def test_daily_snapshots_accept_complete_stale_and_partial_rebuilds(tmp_path):
    db_path = tmp_path / "daily-snapshots.db"
    result = ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)

        _insert_account_snapshot(conn, account_id=account_id)
        _insert_position_snapshot(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        _insert_account_snapshot(
            conn,
            account_id=account_id,
            snapshot_date="2026-07-24",
            price_status="STALE",
            expected_price_date="2026-07-24",
        )
        _insert_position_snapshot(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
            snapshot_date="2026-07-24",
            price_date="2026-07-23",
            price_status="STALE",
        )
        _insert_account_snapshot(
            conn,
            account_id=account_id,
            snapshot_date="2026-07-25",
            market_value_cents=None,
            total_equity_cents=None,
            pnl_cents=None,
            return_ppb=None,
            unrealized_pnl_cents=None,
            unpriced_position_count=1,
            price_status="PARTIAL",
            expected_price_date="2026-07-25",
            performance_status="UNAVAILABLE",
            return_base_date=None,
        )
        _insert_position_snapshot(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
            snapshot_date="2026-07-25",
            price_micros=None,
            price_date=None,
            market_value_cents=None,
            unrealized_pnl_cents=None,
            price_status="MISSING",
        )

        conn.execute(
            """
            UPDATE portfolio_account_daily_snapshots
            SET pnl_cents = 200, updated_at = CURRENT_TIMESTAMP
            WHERE account_id = ? AND snapshot_date = '2026-07-23'
            """,
            (account_id,),
        )
        conn.execute(
            """
            DELETE FROM portfolio_position_daily_snapshots
            WHERE account_id = ? AND snapshot_date = '2026-07-25'
            """,
            (account_id,),
        )

        account_rows = conn.execute(
            """
            SELECT snapshot_date, price_status, typeof(cash_cents),
                   market_value_cents, pnl_cents
            FROM portfolio_account_daily_snapshots
            ORDER BY snapshot_date
            """
        ).fetchall()
        position_rows = conn.execute(
            """
            SELECT snapshot_date, price_status, price_date
            FROM portfolio_position_daily_snapshots
            ORDER BY snapshot_date
            """
        ).fetchall()
        indexes = {
            row[0]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index'
                  AND tbl_name IN (
                      'portfolio_account_daily_snapshots',
                      'portfolio_position_daily_snapshots'
                  )
                """
            )
        }

    assert account_rows == [
        ("2026-07-23", "COMPLETE", "integer", 14_235_000, 200),
        ("2026-07-24", "STALE", "integer", 14_235_000, 100),
        ("2026-07-25", "PARTIAL", "integer", None, None),
    ]
    assert position_rows == [
        ("2026-07-23", "CURRENT", "2026-07-23"),
        ("2026-07-24", "STALE", "2026-07-23"),
    ]
    expected_indexes = {
        "idx_portfolio_account_snapshots_account_date",
        "idx_portfolio_account_snapshots_status",
        "idx_portfolio_position_snapshots_account_date",
        "idx_portfolio_position_snapshots_status",
        "idx_portfolio_position_snapshots_symbol_date",
    }
    assert expected_indexes <= indexes
    assert expected_indexes <= set(result["indexes"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"snapshot_date": "2026-7-23"},
        {"ledger_revision": -1},
        {"cash_cents": 1.5},
        {"return_ppb": -1_000_000_001},
        {"return_ppb": 100_000_000_001},
        {"total_equity_cents": 15_235_001},
        {"price_status": "COMPLETE", "unpriced_position_count": 1},
        {
            "price_status": "PARTIAL",
            "unpriced_position_count": 1,
            "market_value_cents": 14_235_000,
        },
        {
            "price_status": "PARTIAL",
            "unpriced_position_count": 0,
            "market_value_cents": None,
            "total_equity_cents": None,
            "pnl_cents": None,
            "return_ppb": None,
            "unrealized_pnl_cents": None,
        },
        {"unpriced_position_count": 2},
        {"price_status": "UNKNOWN"},
        {"expected_price_date": None},
        {"expected_price_date": "2026-07-22"},
        {"expected_price_date": "2026/07/23"},
    ],
)
def test_account_daily_snapshot_rejects_invalid_values_and_status_contract(
    tmp_path,
    overrides,
):
    db_path = tmp_path / "invalid-account-snapshot.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_account_snapshot(
                conn,
                account_id=account_id,
                **overrides,
            )


def test_account_daily_snapshot_accepts_all_performance_states(tmp_path):
    db_path = tmp_path / "performance-states.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        _insert_account_snapshot(
            conn,
            account_id=account_id,
            performance_status="INITIAL",
            pnl_cents=0,
            return_ppb=0,
            return_base_date=None,
        )
        _insert_account_snapshot(
            conn,
            account_id=account_id,
            snapshot_date="2026-07-24",
            expected_price_date="2026-07-24",
            price_status="STALE",
            performance_status="GAP",
            pnl_cents=None,
            return_ppb=None,
            return_base_date=None,
        )
        _insert_account_snapshot(
            conn,
            account_id=account_id,
            snapshot_date="2026-07-25",
            expected_price_date="2026-07-25",
            price_status="PARTIAL",
            performance_status="UNAVAILABLE",
            market_value_cents=None,
            total_equity_cents=None,
            pnl_cents=None,
            return_ppb=None,
            unrealized_pnl_cents=None,
            unpriced_position_count=1,
            return_base_date=None,
        )

        assert conn.execute(
            """
            SELECT performance_status, price_status, pnl_cents,
                   return_ppb, return_base_date
            FROM portfolio_account_daily_snapshots
            ORDER BY snapshot_date
            """
        ).fetchall() == [
            ("INITIAL", "COMPLETE", 0, 0, None),
            ("GAP", "STALE", None, None, None),
            ("UNAVAILABLE", "PARTIAL", None, None, None),
        ]


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "performance_status": "INITIAL",
            "pnl_cents": 1,
            "return_ppb": 0,
            "return_base_date": None,
        },
        {
            "performance_status": "INITIAL",
            "pnl_cents": 0,
            "return_ppb": 1,
            "return_base_date": None,
        },
        {
            "performance_status": "INITIAL",
            "pnl_cents": 0,
            "return_ppb": 0,
        },
        {
            "performance_status": "INITIAL",
            "price_status": "PARTIAL",
            "market_value_cents": None,
            "total_equity_cents": None,
            "pnl_cents": 0,
            "return_ppb": 0,
            "unrealized_pnl_cents": None,
            "unpriced_position_count": 1,
            "return_base_date": None,
        },
        {"performance_status": "COMPLETE", "return_base_date": None},
        {"performance_status": "COMPLETE", "return_base_date": "2026-07-23"},
        {"performance_status": "COMPLETE", "return_base_date": "2026-07-24"},
        {
            "performance_status": "GAP",
            "pnl_cents": None,
            "return_ppb": None,
        },
        {
            "performance_status": "GAP",
            "pnl_cents": 0,
            "return_ppb": None,
            "return_base_date": None,
        },
        {
            "performance_status": "UNAVAILABLE",
            "pnl_cents": None,
            "return_ppb": None,
            "return_base_date": None,
        },
        {
            "performance_status": "GAP",
            "price_status": "PARTIAL",
            "market_value_cents": None,
            "total_equity_cents": None,
            "pnl_cents": None,
            "return_ppb": None,
            "unrealized_pnl_cents": None,
            "unpriced_position_count": 1,
            "return_base_date": None,
        },
        {"performance_status": "BROKEN"},
        {"return_base_date": "2026/07/22"},
    ],
)
def test_account_daily_snapshot_rejects_invalid_performance_contract(
    tmp_path,
    overrides,
):
    db_path = tmp_path / "invalid-performance-snapshot.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_account_snapshot(
                conn,
                account_id=account_id,
                **overrides,
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"snapshot_date": "2026-7-23"},
        {"symbol": "60051"},
        {"position_revision": 0},
        {"quantity": -1},
        {"available_quantity": 101},
        {"cost_basis_cents": -1},
        {"price_status": "MISSING"},
        {"price_status": "CURRENT", "price_micros": None},
        {"price_status": "CURRENT", "price_date": "2026-07-22"},
        {"price_status": "STALE", "price_date": "2026-07-23"},
        {"price_status": "STALE", "price_date": "2026-07-24"},
        {"price_status": "CURRENT", "market_value_cents": None},
        {"price_status": "CURRENT", "unrealized_pnl_cents": None},
        {"price_status": "BROKEN"},
    ],
)
def test_position_daily_snapshot_rejects_invalid_values_and_price_contract(
    tmp_path,
    overrides,
):
    db_path = tmp_path / "invalid-position-snapshot.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_position_snapshot(
                conn,
                account_id=account_id,
                last_transaction_id=transaction_id,
                **overrides,
            )


def test_position_daily_snapshot_enforces_account_transaction_composite_fk(
    tmp_path,
):
    db_path = tmp_path / "snapshot-foreign-keys.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        other_account_id = _insert_account(conn, name="账户B")
        transaction_id = _insert_buy(conn, account_id=account_id)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_position_snapshot(
                conn,
                account_id=other_account_id,
                last_transaction_id=transaction_id,
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_position_snapshot(
                conn,
                account_id=999,
                last_transaction_id=None,
            )


def test_v2_upgrades_to_v3_atomically_and_repeated_ensure_preserves_snapshots(
    tmp_path,
):
    db_path = tmp_path / "existing-v2.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v2_schema(conn)
        account_id = _insert_account(conn, is_default=1)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_position(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        conn.execute("PRAGMA user_version = 73")
        conn.commit()

    first = ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _insert_account_snapshot(conn, account_id=account_id)
        _insert_position_snapshot(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        conn.commit()
    second = ensure_portfolio_schema(db_path)

    assert first["schema_version"] == second["schema_version"] == 4
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 73
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert conn.execute("SELECT COUNT(*) FROM portfolio_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_position_daily_snapshots"
        ).fetchone()[0] == 1


def test_v3_upgrade_rolls_back_when_existing_snapshot_table_is_incompatible(
    tmp_path,
):
    db_path = tmp_path / "broken-v3.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v2_schema(conn)
        account_id = _insert_account(conn)
        _insert_buy(conn, account_id=account_id)
        conn.execute(
            "CREATE TABLE portfolio_account_daily_snapshots (account_id INTEGER)"
        )
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="portfolio_account_daily_snapshots.*missing required columns",
    ):
        ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table'
              AND name = 'portfolio_position_daily_snapshots'
            """
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM portfolio_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0] == 1


def test_exit_alert_schema_accepts_plan_state_event_and_expected_indexes(tmp_path):
    db_path = tmp_path / "exit-alerts.db"
    result = ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        _insert_exit_state(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        event_id = _insert_exit_event(conn, account_id=account_id)

        conn.execute(
            """
            UPDATE portfolio_exit_plans
            SET hard_stop_micros = 1310000000,
                enabled = 0,
                rule_version = 'exit-v2',
                updated_at = CURRENT_TIMESTAMP
            WHERE account_id = ? AND symbol = '600519'
              AND position_revision = 1
            """,
            (account_id,),
        )
        conn.execute(
            """
            UPDATE portfolio_exit_states
            SET state = 'EXIT_WATCH',
                highest_price_micros = 1520000000,
                trailing_stop_micros = 1444000000,
                rule_version = 'exit-v2',
                updated_at = CURRENT_TIMESTAMP
            WHERE account_id = ? AND symbol = '600519'
              AND position_revision = 1
            """,
            (account_id,),
        )
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE name LIKE '%portfolio_exit%'
                """
            )
        }

    assert event_id == 1
    expected_indexes = {
        "idx_portfolio_exit_plans_enabled",
        "idx_portfolio_exit_states_status",
        "idx_portfolio_exit_events_account_open",
        "idx_portfolio_exit_events_symbol_date",
    }
    assert expected_indexes <= {
        name for object_type, name in objects if object_type == "index"
    }
    assert expected_indexes <= set(result["indexes"])
    assert {
        "trg_portfolio_exit_plans_validate_entry_insert",
        "trg_portfolio_exit_plans_validate_entry_update",
        "trg_portfolio_exit_plans_protect_identity",
        "trg_portfolio_exit_plans_protect_event_history",
        "trg_portfolio_exit_events_no_delete",
        "trg_portfolio_exit_events_protect_facts",
    } <= set(result["triggers"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": 2},
        {"symbol": "60051"},
        {"position_revision": 0},
        {"hard_stop_micros": 0},
        {"take_profit_1_reduce_bps": None},
        {"take_profit_1_micros": None},
        {"take_profit_2_micros": 1_400_000_000},
        {
            "take_profit_2_micros": None,
            "take_profit_2_reduce_bps": None,
            "take_profit_3_micros": 1_700_000_000,
            "take_profit_3_reduce_bps": 4000,
        },
        {"take_profit_3_reduce_bps": 5000},
        {"trailing_distance_bps": None},
        {"trailing_activation_bps": None},
        {"time_stop_min_return_bps": None},
        {"time_stop_trading_days": None},
        {"trend_exit_enabled": 2},
        {"intraday_enabled": -1},
        {"rule_version": ""},
    ],
)
def test_exit_plan_rejects_invalid_rule_contract(tmp_path, overrides):
    db_path = tmp_path / "invalid-exit-plan.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_exit_plan(
                conn,
                account_id=account_id,
                entry_transaction_id=transaction_id,
                **overrides,
            )


def test_exit_plan_entry_must_be_same_account_symbol_posted_buy(tmp_path):
    db_path = tmp_path / "exit-plan-entry.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        other_account_id = _insert_account(conn, name="账户B")
        transaction_id = _insert_buy(conn, account_id=account_id)
        wrong_symbol_id = _insert_buy(
            conn,
            account_id=account_id,
            client_request_id="buy-0002",
            symbol="000001",
        )

        with pytest.raises(sqlite3.IntegrityError, match="invalid exit plan entry"):
            _insert_exit_plan(
                conn,
                account_id=account_id,
                entry_transaction_id=wrong_symbol_id,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_exit_plan(
                conn,
                account_id=other_account_id,
                entry_transaction_id=transaction_id,
            )


def test_exit_plan_identity_is_immutable_and_event_history_blocks_delete(tmp_path):
    db_path = tmp_path / "exit-plan-history.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )

        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            conn.execute(
                """
                UPDATE portfolio_exit_plans
                SET position_revision = 2
                WHERE account_id = ? AND symbol = '600519'
                  AND position_revision = 1
                """,
                (account_id,),
            )

        _insert_exit_event(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError, match="event history"):
            conn.execute(
                """
                DELETE FROM portfolio_exit_plans
                WHERE account_id = ? AND symbol = '600519'
                  AND position_revision = 1
                """,
                (account_id,),
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "BROKEN"},
        {"data_status": "BROKEN"},
        {"last_price_micros": 0},
        {"trailing_stop_micros": 1_600_000_000},
        {"highest_price_micros": None},
        {"last_price_date": None},
        {"last_bar_time": None},
        {
            "state": "DATA_BLOCKED",
            "data_status": "OK",
        },
        {
            "state": "NORMAL",
            "data_status": "MISSING",
            "highest_price_micros": None,
            "trailing_stop_micros": None,
            "last_price_micros": None,
            "last_price_date": None,
            "last_bar_time": None,
        },
        {
            "state": "DATA_BLOCKED",
            "data_status": "MISSING",
            "last_price_micros": 1_423_500_000,
            "last_price_date": None,
            "last_bar_time": None,
        },
        {"rule_version": ""},
    ],
)
def test_exit_state_rejects_invalid_price_and_status_contract(tmp_path, overrides):
    db_path = tmp_path / "invalid-exit-state.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_exit_state(
                conn,
                account_id=account_id,
                last_transaction_id=transaction_id,
                **overrides,
            )


def test_exit_state_accepts_stale_and_missing_blocked_data(tmp_path):
    db_path = tmp_path / "blocked-exit-state.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        first_transaction_id = _insert_buy(conn, account_id=account_id)
        second_transaction_id = _insert_buy(
            conn,
            account_id=account_id,
            client_request_id="buy-0002",
            symbol="000001",
        )
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=first_transaction_id,
        )
        _insert_exit_state(
            conn,
            account_id=account_id,
            last_transaction_id=first_transaction_id,
            state="DATA_BLOCKED",
            data_status="STALE",
        )

        conn.execute(
            """
            UPDATE portfolio_exit_states
            SET data_status = 'MISSING',
                last_price_micros = NULL,
                last_price_date = NULL,
                last_bar_time = NULL
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account_id,),
        )
        assert conn.execute(
            """
            SELECT highest_price_micros, trailing_stop_micros
            FROM portfolio_exit_states
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account_id,),
        ).fetchone() == (1_500_000_000, 1_425_000_000)
        with pytest.raises(sqlite3.IntegrityError, match="invalid exit state checkpoint"):
            conn.execute(
                """
                UPDATE portfolio_exit_states
                SET last_transaction_id = ?
                WHERE account_id = ? AND symbol = '600519'
                """,
                (second_transaction_id, account_id),
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_type": "BROKEN"},
        {"severity": "BROKEN"},
        {"event_status": "BROKEN"},
        {"trigger_date": "2026-7-23"},
        {"triggered_at": "not-a-time"},
        {"bar_time": "2026-07-23T15:00:00+08:00"},
        {"observed_price_micros": 0},
        {"available_quantity": -1},
        {"recommended_quantity": 101},
        {"executable": 2},
        {"executable": 1, "recommended_quantity": 0},
        {"tier": 4},
        {"tier": None},
        {"event_type": "HARD_STOP_TRIGGERED", "tier": 1},
        {"dedupe_key": ""},
        {"payload_json": "not-json"},
        {"payload_json": json.dumps({"data": "x" * 65_536})},
        {"rule_version": ""},
        {"event_status": "OPEN", "acknowledged_at": "2026-07-23T15:00:00+08:00"},
        {"event_status": "ACKNOWLEDGED", "acknowledged_at": None},
        {
            "event_status": "ACKNOWLEDGED",
            "acknowledged_at": "2026-07-23T14:00:00+08:00",
        },
        {
            "event_status": "RESOLVED",
            "acknowledged_at": None,
            "resolved_at": None,
        },
        {
            "event_status": "RESOLVED",
            "acknowledged_at": "2026-07-23T15:05:00+08:00",
            "resolved_at": "2026-07-23T15:00:00+08:00",
        },
    ],
)
def test_exit_event_rejects_invalid_facts_and_status_contract(tmp_path, overrides):
    db_path = tmp_path / "invalid-exit-event.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_exit_event(conn, account_id=account_id, **overrides)


def test_exit_events_are_append_only_except_forward_status_updates(tmp_path):
    db_path = tmp_path / "exit-event-history.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        event_id = _insert_exit_event(conn, account_id=account_id)

        with pytest.raises(sqlite3.IntegrityError, match="trigger facts"):
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET observed_price_micros = observed_price_micros + 1
                WHERE id = ?
                """,
                (event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM portfolio_exit_events WHERE id = ?",
                (event_id,),
            )

        conn.execute(
            """
            UPDATE portfolio_exit_events
            SET event_status = 'ACKNOWLEDGED',
                acknowledged_at = '2026-07-23T15:00:00+08:00',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (event_id,),
        )
        conn.execute(
            """
            UPDATE portfolio_exit_events
            SET event_status = 'RESOLVED',
                resolved_at = '2026-07-23T15:05:00+08:00',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (event_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="status transition"):
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET event_status = 'OPEN',
                    acknowledged_at = NULL,
                    resolved_at = NULL
                WHERE id = ?
                """,
                (event_id,),
            )


def test_exit_event_dedupe_and_plan_composite_foreign_key(tmp_path):
    db_path = tmp_path / "exit-event-foreign-keys.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        other_account_id = _insert_account(conn, name="账户B")
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        _insert_exit_event(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_exit_event(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_exit_event(
                conn,
                account_id=other_account_id,
                dedupe_key="other-account-missing-plan",
            )


def test_v3_upgrades_to_v4_atomically_idempotently_and_preserves_rows(tmp_path):
    db_path = tmp_path / "existing-v3.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v3_schema(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        conn.execute("PRAGMA user_version = 73")
        conn.commit()

    first = ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        conn.commit()
    second = ensure_portfolio_schema(db_path)

    assert first["schema_version"] == second["schema_version"] == 4
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 73
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert conn.execute("SELECT COUNT(*) FROM portfolio_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_exit_plans").fetchone()[0] == 1


def test_partial_v4_schema_is_rejected_without_recording_migration(tmp_path):
    db_path = tmp_path / "partial-v4.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v3_schema(conn)
        conn.execute("CREATE TABLE portfolio_exit_plans (account_id INTEGER)")
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="portfolio_exit_plans.*missing required columns",
    ):
        ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table'
              AND name IN ('portfolio_exit_states', 'portfolio_exit_events')
            """
        ).fetchone()[0] == 0


def test_v4_object_name_collision_rolls_back_entire_migration(tmp_path):
    db_path = tmp_path / "v4-object-collision.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v3_schema(conn)
        conn.execute("CREATE TABLE unrelated_exit_state (id INTEGER)")
        conn.execute(
            """
            CREATE INDEX idx_portfolio_exit_states_status
            ON unrelated_exit_state (id)
            """
        )
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="portfolio.*object",
    ):
        ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'portfolio_exit_plans',
                  'portfolio_exit_states',
                  'portfolio_exit_events'
              )
            """
        ).fetchone()[0] == 0


def test_exit_plan_requires_unreversed_buy_and_matching_open_revision(tmp_path):
    db_path = tmp_path / "exit-plan-identity.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_position(
            conn,
            account_id=account_id,
            last_transaction_id=transaction_id,
        )
        conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, cash_delta_cents, reversal_of
            ) VALUES (
                ?, 'reverse-exit-entry', 'REVERSAL', 'POSTED',
                '2026-07-24', '2026-07-24T10:00:00+08:00',
                14235500, ?
            )
            """,
            (account_id, transaction_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid exit plan entry"):
            _insert_exit_plan(
                conn,
                account_id=account_id,
                entry_transaction_id=transaction_id,
            )

    closed_db_path = tmp_path / "closed-exit-plan.db"
    ensure_portfolio_schema(closed_db_path)
    with sqlite3.connect(closed_db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_position(
            conn,
            account_id=account_id,
            quantity=0,
            available_quantity=0,
            cost_basis_cents=0,
            avg_cost_micros=0,
            status="CLOSED",
            last_transaction_id=transaction_id,
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid exit plan entry"):
            _insert_exit_plan(
                conn,
                account_id=account_id,
                entry_transaction_id=transaction_id,
            )

    revision_db_path = tmp_path / "wrong-revision-exit-plan.db"
    ensure_portfolio_schema(revision_db_path)
    with sqlite3.connect(revision_db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError, match="invalid exit plan entry"):
            _insert_exit_plan(
                conn,
                account_id=account_id,
                entry_transaction_id=transaction_id,
                position_revision=999,
            )


def test_exit_state_identity_checkpoint_and_closed_position_lifecycle(tmp_path):
    db_path = tmp_path / "exit-state-identity.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        entry_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=entry_id,
        )
        second_buy_id = _insert_buy(
            conn,
            account_id=account_id,
            client_request_id="buy-0002",
        )
        conn.execute(
            """
            UPDATE portfolio_positions
            SET last_transaction_id = ?
            WHERE account_id = ? AND symbol = '600519'
            """,
            (second_buy_id, account_id),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="invalid exit state checkpoint",
        ):
            _insert_exit_state(
                conn,
                account_id=account_id,
                last_transaction_id=entry_id,
            )
        _insert_exit_state(
            conn,
            account_id=account_id,
            last_transaction_id=second_buy_id,
        )

        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            conn.execute(
                """
                UPDATE portfolio_exit_states
                SET position_revision = 2
                WHERE account_id = ? AND symbol = '600519'
                """,
                (account_id,),
            )

        sell_cursor = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, price_micros,
                gross_amount_cents, cash_delta_cents, fee_cents, tax_cents
            ) VALUES (
                ?, 'sell-exit-close', 'SELL', 'POSTED',
                '2026-07-24', '2026-07-24T10:00:00+08:00',
                '600519', 100, 1423500000, 14235000, 14234500, 500, 0
            )
            """,
            (account_id,),
        )
        sell_id = int(sell_cursor.lastrowid)
        conn.execute(
            """
            UPDATE portfolio_positions
            SET quantity = 0, available_quantity = 0,
                cost_basis_cents = 0, avg_cost_micros = 0,
                status = 'CLOSED', last_trade_date = '2026-07-24',
                last_transaction_id = ?
            WHERE account_id = ? AND symbol = '600519'
            """,
            (sell_id, account_id),
        )
        conn.execute(
            """
            UPDATE portfolio_exit_states
            SET state = 'EXIT_READY', last_transaction_id = ?
            WHERE account_id = ? AND symbol = '600519'
            """,
            (sell_id, account_id),
        )


@pytest.mark.parametrize(
    ("event_status", "acknowledged_at", "resolved_at"),
    [
        ("ACKNOWLEDGED", "2026-07-23T15:00:00+08:00", None),
        ("RESOLVED", None, "2026-07-23T15:00:00+08:00"),
    ],
)
def test_exit_event_insert_must_start_open(
    tmp_path,
    event_status,
    acknowledged_at,
    resolved_at,
):
    db_path = tmp_path / "exit-event-insert-lifecycle.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        with pytest.raises(sqlite3.IntegrityError, match="must start OPEN"):
            _insert_exit_event(
                conn,
                account_id=account_id,
                event_status=event_status,
                acknowledged_at=acknowledged_at,
                resolved_at=resolved_at,
            )


def test_exit_event_status_timestamps_are_write_once(tmp_path):
    db_path = tmp_path / "exit-event-write-once.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        event_id = _insert_exit_event(conn, account_id=account_id)
        conn.execute(
            """
            UPDATE portfolio_exit_events
            SET event_status = 'ACKNOWLEDGED',
                acknowledged_at = '2026-07-23T15:00:00+08:00'
            WHERE id = ?
            """,
            (event_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="status transition"):
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET acknowledged_at = '2026-07-23T15:01:00+08:00'
                WHERE id = ?
                """,
                (event_id,),
            )
        conn.execute(
            """
            UPDATE portfolio_exit_events
            SET event_status = 'RESOLVED',
                resolved_at = '2026-07-23T15:05:00+08:00'
            WHERE id = ?
            """,
            (event_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="status transition"):
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET resolved_at = '2026-07-23T15:06:00+08:00'
                WHERE id = ?
                """,
                (event_id,),
            )


def test_exit_event_open_to_resolved_cannot_set_ack_time(tmp_path):
    db_path = tmp_path / "exit-event-open-resolved.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
        )
        event_id = _insert_exit_event(conn, account_id=account_id)
        with pytest.raises(sqlite3.IntegrityError, match="status transition"):
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET event_status = 'RESOLVED',
                    acknowledged_at = '2026-07-23T15:00:00+08:00',
                    resolved_at = '2026-07-23T15:05:00+08:00'
                WHERE id = ?
                """,
                (event_id,),
            )


def test_exit_event_insert_rejects_disabled_plan(tmp_path):
    db_path = tmp_path / "exit-event-disabled-plan.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        transaction_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=transaction_id,
            enabled=0,
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="enabled open unreversed exit plan",
        ):
            _insert_exit_event(conn, account_id=account_id)


def test_recorded_v3_rejects_even_canonical_untracked_v4_table(tmp_path):
    db_path = tmp_path / "untracked-v4-table.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v3_schema(conn)
        conn.execute(schema_module._CREATE_EXIT_PLANS_SQL)
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="unrecorded portfolio v4 object",
    ):
        ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT version FROM portfolio_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'portfolio_exit_states'
            """
        ).fetchone()[0] == 0


@pytest.mark.parametrize("tamper_kind", ["empty_trigger", "wrong_index"])
def test_recorded_v4_rejects_tampered_exit_objects(tmp_path, tamper_kind):
    db_path = tmp_path / f"tampered-v4-{tamper_kind}.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        if tamper_kind == "empty_trigger":
            conn.execute(
                "DROP TRIGGER trg_portfolio_exit_events_no_delete"
            )
            conn.execute(
                """
                CREATE TRIGGER trg_portfolio_exit_events_no_delete
                BEFORE DELETE ON portfolio_exit_events
                BEGIN SELECT 1; END
                """
            )
        else:
            conn.execute("DROP INDEX idx_portfolio_exit_states_status")
            conn.execute(
                """
                CREATE INDEX idx_portfolio_exit_states_status
                ON portfolio_exit_states (account_id)
                """
            )
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="incompatible portfolio exit schema object",
    ):
        ensure_portfolio_schema(db_path)


def test_recorded_v4_rejects_weak_exit_table_constraints(tmp_path):
    db_path = tmp_path / "weak-v4-table.db"
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        _create_v3_schema(conn)
        weak_plan_sql = schema_module._CREATE_EXIT_PLANS_SQL.replace(
            "CHECK (length(trim(rule_version)) > 0)",
            "CHECK (rule_version IS NOT NULL)",
            1,
        )
        conn.execute(weak_plan_sql)
        conn.execute(schema_module._CREATE_EXIT_STATES_SQL)
        conn.execute(schema_module._CREATE_EXIT_EVENTS_SQL)
        for statement in schema_module._CREATE_EXIT_INDEX_SQL:
            conn.execute(statement)
        for statement in schema_module._CREATE_EXIT_TRIGGER_SQL:
            conn.execute(statement)
        conn.execute(
            """
            INSERT INTO portfolio_schema_migrations (version, name)
            VALUES (4, 'create_portfolio_exit_alerts')
            """
        )
        conn.commit()

    with pytest.raises(
        RuntimeError,
        match="incompatible portfolio exit schema object portfolio_exit_plans",
    ):
        ensure_portfolio_schema(db_path)


def test_exit_event_insert_requires_current_open_unreversed_position(tmp_path):
    db_path = tmp_path / "exit-event-position-lifecycle.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        entry_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=entry_id,
        )
        event_id = _insert_exit_event(
            conn,
            account_id=account_id,
            dedupe_key="open-position-event",
        )
        assert event_id == 1

        conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, cash_delta_cents, reversal_of
            ) VALUES (
                ?, 'reverse-entry-after-plan', 'REVERSAL', 'POSTED',
                '2026-07-24', '2026-07-24T10:00:00+08:00',
                14235500, ?
            )
            """,
            (account_id, entry_id),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="open unreversed exit plan",
        ):
            _insert_exit_event(
                conn,
                account_id=account_id,
                event_type="EXIT_DATA_RECOVERED",
                severity="INFO",
                observed_price_micros=None,
                available_quantity=0,
                recommended_quantity=0,
                executable=0,
                tier=None,
                dedupe_key="reversed-entry-data-recovered",
            )


def test_exit_event_insert_rejects_position_closed_after_plan(tmp_path):
    db_path = tmp_path / "exit-event-closed-position.db"
    ensure_portfolio_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        account_id = _insert_account(conn)
        entry_id = _insert_buy(conn, account_id=account_id)
        _insert_exit_plan(
            conn,
            account_id=account_id,
            entry_transaction_id=entry_id,
        )
        sell_cursor = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, price_micros,
                gross_amount_cents, cash_delta_cents, fee_cents, tax_cents
            ) VALUES (
                ?, 'sell-before-exit-event', 'SELL', 'POSTED',
                '2026-07-24', '2026-07-24T10:00:00+08:00',
                '600519', 100, 1423500000, 14235000, 14234500, 500, 0
            )
            """,
            (account_id,),
        )
        sell_id = int(sell_cursor.lastrowid)
        conn.execute(
            """
            UPDATE portfolio_positions
            SET quantity = 0, available_quantity = 0,
                cost_basis_cents = 0, avg_cost_micros = 0,
                status = 'CLOSED', last_trade_date = '2026-07-24',
                last_transaction_id = ?
            WHERE account_id = ? AND symbol = '600519'
            """,
            (sell_id, account_id),
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="open unreversed exit plan",
        ):
            _insert_exit_event(
                conn,
                account_id=account_id,
                event_type="EXIT_DATA_RECOVERED",
                severity="INFO",
                observed_price_micros=None,
                available_quantity=0,
                recommended_quantity=0,
                executable=0,
                tier=None,
                dedupe_key="closed-position-data-recovered",
            )
