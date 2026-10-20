import hashlib
import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from portfolio.accounting import (
    AccountAsOfProjection,
    AccountNotFoundError,
    ArchivedAccountError,
    IdempotencyConflictError,
    InsufficientCashError,
    InsufficientPositionError,
    InvalidTradeError,
    PortfolioAccountingService,
    StaleAccountRevisionError,
    StaleTradePreviewError,
    TransactionNotReversibleError,
)
from portfolio.schema import configure_portfolio_connection, ensure_portfolio_schema


def _service(tmp_path, *, opening_cash_cents: int = 1_000_000):
    db_path = tmp_path / "portfolio-accounting.db"
    ensure_portfolio_schema(db_path)
    service = PortfolioAccountingService(db_path)
    account = service.create_account(
        name="主账户",
        opening_cash_cents=opening_cash_cents,
        is_default=True,
    )
    return db_path, service, account


def _post_trade(
    service: PortfolioAccountingService,
    account_id: int,
    *,
    client_request_id: str,
    business_type: str = "BUY",
    trade_date: str = "2026-07-21",
    symbol: str = "600519",
    quantity: int = 100,
    price_micros: int = 10_000_000,
    fee_cents: int = 0,
    tax_cents: int = 0,
    expected_ledger_revision: int | None = None,
    expected_quantity: int | None = None,
    expected_gross_amount_cents: int | None = None,
    expected_cash_delta_cents: int | None = None,
):
    return service.post_trade(
        account_id=account_id,
        client_request_id=client_request_id,
        business_type=business_type,
        trade_date=trade_date,
        occurred_at=f"{trade_date}T10:00:00+08:00",
        symbol=symbol,
        quantity=quantity,
        price_micros=price_micros,
        fee_cents=fee_cents,
        tax_cents=tax_cents,
        expected_ledger_revision=expected_ledger_revision,
        expected_quantity=expected_quantity,
        expected_gross_amount_cents=expected_gross_amount_cents,
        expected_cash_delta_cents=expected_cash_delta_cents,
    )


def test_post_cash_flow_updates_balance_without_touching_positions(tmp_path):
    db_path, service, account = _service(tmp_path, opening_cash_cents=100_000)
    revision = service.ledger_revision(account.id)

    deposit = service.post_cash_flow(
        account_id=account.id,
        client_request_id="cash-deposit-1",
        business_type="DEPOSIT",
        trade_date="2026-07-21",
        occurred_at="2026-07-21T09:30:00+08:00",
        amount_cents=50_000,
        expected_ledger_revision=revision,
        note="工资入金",
    )
    withdrawal = service.post_cash_flow(
        account_id=account.id,
        client_request_id="cash-withdrawal-1",
        business_type="WITHDRAWAL",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T14:30:00+08:00",
        amount_cents=20_000,
        expected_ledger_revision=deposit.ledger_revision,
        note="生活备用",
    )

    assert deposit.cash_before_cents == 100_000
    assert deposit.cash_after_cents == 150_000
    assert deposit.transaction.cash_delta_cents == 50_000
    assert deposit.transaction.note == "工资入金"
    assert withdrawal.cash_before_cents == 150_000
    assert withdrawal.cash_after_cents == 130_000
    assert withdrawal.transaction.cash_delta_cents == -20_000
    assert service.get_account_summary(account.id).cash_cents == 130_000
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_positions"
        ).fetchone()[0] == 0


def test_post_cash_flow_is_idempotent_and_rejects_stale_revision(tmp_path):
    db_path, service, account = _service(tmp_path, opening_cash_cents=100_000)
    request = {
        "account_id": account.id,
        "client_request_id": "cash-idempotent",
        "business_type": "DEPOSIT",
        "trade_date": "2026-07-21",
        "occurred_at": "2026-07-21T09:30:00+08:00",
        "amount_cents": 10_000,
        "expected_ledger_revision": 0,
        "note": None,
    }

    first = service.post_cash_flow(**request)
    retry = service.post_cash_flow(**request)
    assert retry.transaction.id == first.transaction.id
    assert retry.cash_after_cents == 110_000

    with pytest.raises(IdempotencyConflictError):
        service.post_cash_flow(**{**request, "amount_cents": 20_000})
    with pytest.raises(StaleAccountRevisionError):
        service.post_cash_flow(
            account_id=account.id,
            client_request_id="cash-stale",
            business_type="DEPOSIT",
            trade_date="2026-07-22",
            occurred_at="2026-07-22T09:30:00+08:00",
            amount_cents=10_000,
            expected_ledger_revision=0,
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0] == 1


def test_post_cash_flow_rejects_invalid_withdrawal_and_closed_history(tmp_path):
    db_path, service, account = _service(tmp_path, opening_cash_cents=100_000)

    with pytest.raises(InsufficientCashError):
        service.post_cash_flow(
            account_id=account.id,
            client_request_id="cash-too-large",
            business_type="WITHDRAWAL",
            trade_date="2026-07-21",
            occurred_at="2026-07-21T09:30:00+08:00",
            amount_cents=100_001,
            expected_ledger_revision=0,
        )
    with pytest.raises(InvalidTradeError, match="same date"):
        service.post_cash_flow(
            account_id=account.id,
            client_request_id="cash-time-mismatch",
            business_type="DEPOSIT",
            trade_date="2026-07-21",
            occurred_at="2026-07-22T09:30:00+08:00",
            amount_cents=10_000,
            expected_ledger_revision=0,
        )

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
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
            ) VALUES (
                ?, '2026-07-23', 0, 100000, 0, 100000,
                0, 0, NULL, NULL, 0, 0, 0, 0,
                'COMPLETE', '2026-07-23', 'INITIAL', NULL
            )
            """,
            (account.id,),
        )
        conn.commit()

    with pytest.raises(InvalidTradeError, match="settled performance"):
        service.post_cash_flow(
            account_id=account.id,
            client_request_id="cash-backdated",
            business_type="DEPOSIT",
            trade_date="2026-07-23",
            occurred_at="2026-07-23T09:30:00+08:00",
            amount_cents=10_000,
            expected_ledger_revision=0,
        )


def test_accounts_list_and_cash_summary_handle_transfer_and_reversal(tmp_path):
    db_path, service, source = _service(tmp_path, opening_cash_cents=100_000)
    target = service.create_account(name="账户B", opening_cash_cents=5_000)

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        deposit_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, 'deposit-1', 'DEPOSIT', 'POSTED',
                '2026-07-20', '2026-07-20T09:00:00+08:00', 50000, 50000
            )
            """,
            (source.id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, counterparty_account_id, client_request_id,
                business_type, status, trade_date, occurred_at,
                gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, ?, 'transfer-1', 'TRANSFER', 'POSTED',
                '2026-07-21', '2026-07-21T09:00:00+08:00', 20000, 0
            )
            """,
            (source.id, target.id),
        )
        conn.commit()

    assert [account.id for account in service.list_accounts()] == [
        source.id,
        target.id,
    ]
    assert service.get_account_summary(source.id).cash_cents == 130_000
    assert service.get_account_summary(target.id).cash_cents == 25_000

    reversal = service.reverse_transaction(
        account_id=source.id,
        transaction_id=deposit_id,
        client_request_id="reverse-deposit-1",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T09:00:00+08:00",
    )
    assert reversal.business_type == "REVERSAL"
    assert service.get_account_summary(source.id).cash_cents == 80_000


def test_preview_trade_supports_exactly_one_sizing_mode_and_a_share_lots(tmp_path):
    _, service, account = _service(tmp_path)

    by_quantity = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-21",
        symbol="600519",
        price_micros=10_000_000,
        quantity=200,
        fee_cents=500,
        tax_cents=0,
    )
    assert (
        by_quantity.quantity,
        by_quantity.gross_amount_cents,
        by_quantity.cash_delta_cents,
        by_quantity.target_position_bps,
    ) == (200, 200_000, -200_500, None)

    by_amount = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-21",
        symbol="000001",
        price_micros=12_300_000,
        amount_cents=250_000,
    )
    assert by_amount.quantity == 200
    assert by_amount.gross_amount_cents == 246_000

    by_target = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-21",
        symbol="000002",
        price_micros=10_000_000,
        target_position_bps=2500,
        account_equity_cents=1_000_000,
    )
    assert by_target.quantity == 200
    assert by_target.target_position_bps == 2500

    invalid_requests = (
        {"quantity": 100, "amount_cents": 100_000},
        {"quantity": 50},
        {"quantity": 100, "symbol": "60051"},
        {"quantity": 100, "price_micros": 0},
        {"target_position_bps": 2500},
    )
    for overrides in invalid_requests:
        request = {
            "account_id": account.id,
            "business_type": "BUY",
            "trade_date": "2026-07-21",
            "symbol": "600519",
            "price_micros": 10_000_000,
            **overrides,
        }
        with pytest.raises(InvalidTradeError):
            service.preview_trade(**request)


def test_explicit_buy_quantity_must_be_a_full_board_lot(tmp_path):
    db_path, service, account = _service(tmp_path)

    with pytest.raises(InvalidTradeError, match="100-share"):
        service.preview_trade(
            account_id=account.id,
            business_type="BUY",
            trade_date="2026-07-21",
            symbol="600519",
            price_micros=10_000_000,
            quantity=150,
        )
    with pytest.raises(InvalidTradeError, match="100-share"):
        _post_trade(
            service,
            account.id,
            client_request_id="explicit-150-share-buy",
            quantity=150,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM portfolio_transactions
            WHERE client_request_id = 'explicit-150-share-buy'
            """
        ).fetchone()[0] == 0


def test_sell_preview_allows_odd_lots_but_respects_t1_available_quantity(tmp_path):
    _, service, account = _service(tmp_path)
    _post_trade(service, account.id, client_request_id="buy-available")
    service.refresh_positions(account.id, as_of_date="2026-07-22")

    odd_lot = service.preview_trade(
        account_id=account.id,
        business_type="SELL",
        trade_date="2026-07-22",
        symbol="600519",
        price_micros=11_000_000,
        quantity=55,
        fee_cents=100,
        tax_cents=50,
    )
    assert odd_lot.quantity == 55
    assert odd_lot.cash_delta_cents == 60_350

    with pytest.raises(InsufficientPositionError):
        service.preview_trade(
            account_id=account.id,
            business_type="SELL",
            trade_date="2026-07-22",
            symbol="600519",
            price_micros=11_000_000,
            quantity=101,
        )


def test_sell_preview_derives_next_day_t1_from_ledger_without_refresh(tmp_path):
    db_path, service, account = _service(tmp_path)
    _post_trade(
        service,
        account.id,
        client_request_id="ledger-preview-buy",
        trade_date="2026-07-21",
    )

    with pytest.raises(InsufficientPositionError):
        service.preview_trade(
            account_id=account.id,
            business_type="SELL",
            trade_date="2026-07-21",
            symbol="600519",
            price_micros=11_000_000,
            quantity=1,
        )

    preview = service.preview_trade(
        account_id=account.id,
        business_type="SELL",
        trade_date="2026-07-22",
        symbol="600519",
        price_micros=11_000_000,
        quantity=100,
    )
    assert preview.quantity == 100

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT available_quantity
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account.id,),
        ).fetchone()[0] == 0


def test_sell_preview_handles_partial_sales_and_same_day_new_buys(tmp_path):
    _, service, account = _service(tmp_path)
    _post_trade(
        service,
        account.id,
        client_request_id="available-base-buy",
        trade_date="2026-07-20",
        quantity=200,
    )
    _post_trade(
        service,
        account.id,
        client_request_id="available-partial-sell",
        business_type="SELL",
        trade_date="2026-07-21",
        quantity=50,
        price_micros=11_000_000,
    )
    _post_trade(
        service,
        account.id,
        client_request_id="available-same-day-buy",
        trade_date="2026-07-22",
        quantity=100,
    )

    preview = service.preview_trade(
        account_id=account.id,
        business_type="SELL",
        trade_date="2026-07-22",
        symbol="600519",
        price_micros=12_000_000,
        quantity=150,
    )
    assert preview.quantity == 150
    with pytest.raises(InsufficientPositionError):
        service.preview_trade(
            account_id=account.id,
            business_type="SELL",
            trade_date="2026-07-22",
            symbol="600519",
            price_micros=12_000_000,
            quantity=151,
        )


def test_post_trade_is_idempotent_and_rebuilds_average_cost_projection(tmp_path):
    db_path, service, account = _service(tmp_path)
    first = _post_trade(
        service,
        account.id,
        client_request_id="buy-1",
        price_micros=10_000_000,
        fee_cents=500,
    )
    retry = _post_trade(
        service,
        account.id,
        client_request_id="buy-1",
        price_micros=10_000_000,
        fee_cents=500,
    )
    assert retry.id == first.id

    with pytest.raises(IdempotencyConflictError):
        _post_trade(
            service,
            account.id,
            client_request_id="buy-1",
            price_micros=10_000_000,
            fee_cents=501,
        )

    _post_trade(
        service,
        account.id,
        client_request_id="buy-2",
        trade_date="2026-07-22",
        price_micros=20_000_000,
        fee_cents=500,
    )
    _post_trade(
        service,
        account.id,
        client_request_id="sell-partial",
        business_type="SELL",
        trade_date="2026-07-23",
        quantity=150,
        price_micros=30_000_000,
        fee_cents=500,
        tax_cents=1_000,
    )

    with sqlite3.connect(db_path) as conn:
        position = conn.execute(
            """
            SELECT position_revision, quantity, available_quantity,
                   cost_basis_cents, avg_cost_micros, realized_pnl_cents,
                   status, last_transaction_id
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account.id,),
        ).fetchone()
    assert position[:7] == (
        1,
        50,
        50,
        75_250,
        15_050_000,
        222_750,
        "OPEN",
    )
    assert position[7] > 0

    _post_trade(
        service,
        account.id,
        client_request_id="sell-close",
        business_type="SELL",
        trade_date="2026-07-24",
        quantity=50,
        price_micros=25_000_000,
    )
    _post_trade(
        service,
        account.id,
        client_request_id="buy-new-cycle",
        trade_date="2026-07-24",
        quantity=100,
        price_micros=8_000_000,
    )
    service.refresh_positions(account.id, as_of_date="2026-07-24")

    with sqlite3.connect(db_path) as conn:
        reopened = conn.execute(
            """
            SELECT position_revision, quantity, available_quantity,
                   cost_basis_cents, avg_cost_micros,
                   realized_pnl_cents, status, first_buy_date
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account.id,),
        ).fetchone()
    assert reopened == (
        2,
        100,
        0,
        80_000,
        8_000_000,
        0,
        "OPEN",
        "2026-07-24",
    )


def test_preview_revision_and_expected_values_bind_post_atomically(tmp_path):
    db_path, service, account = _service(tmp_path)
    preview = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-21",
        symbol="600519",
        price_micros=10_000_000,
        quantity=100,
    )
    assert preview.ledger_revision == 0

    posted = _post_trade(
        service,
        account.id,
        client_request_id="bound-preview-buy",
        expected_ledger_revision=preview.ledger_revision,
        expected_quantity=preview.quantity,
        expected_gross_amount_cents=preview.gross_amount_cents,
        expected_cash_delta_cents=preview.cash_delta_cents,
    )
    assert service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-22",
        symbol="000001",
        price_micros=10_000_000,
        quantity=100,
    ).ledger_revision == posted.id

    with pytest.raises(StaleTradePreviewError):
        _post_trade(
            service,
            account.id,
            client_request_id="wrong-resolved-values",
            trade_date="2026-07-22",
            symbol="000001",
            expected_ledger_revision=posted.id,
            expected_quantity=200,
            expected_gross_amount_cents=100_000,
            expected_cash_delta_cents=-100_000,
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM portfolio_transactions
            WHERE client_request_id = 'wrong-resolved-values'
            """
        ).fetchone()[0] == 0


def test_expected_preview_rejects_incomplete_binding_fields(tmp_path):
    _, service, account = _service(tmp_path)
    with pytest.raises(InvalidTradeError, match="expected preview"):
        _post_trade(
            service,
            account.id,
            client_request_id="incomplete-preview-binding",
            expected_ledger_revision=0,
            expected_quantity=100,
        )


def test_account_revision_tracks_only_related_ledger_and_transfer_counterparty(
    tmp_path,
):
    db_path, service, account = _service(tmp_path, opening_cash_cents=1_000_000)
    source = service.create_account(
        name="转出账户",
        opening_cash_cents=1_000_000,
    )
    unrelated = service.create_account(
        name="无关账户",
        opening_cash_cents=1_000_000,
    )
    preview = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-22",
        symbol="600519",
        price_micros=10_000_000,
        quantity=100,
    )

    _post_trade(
        service,
        unrelated.id,
        client_request_id="unrelated-account-buy",
    )
    unchanged = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-22",
        symbol="600519",
        price_micros=10_000_000,
        quantity=100,
    )
    assert unchanged.ledger_revision == preview.ledger_revision

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        transfer_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, counterparty_account_id, client_request_id,
                business_type, status, trade_date, occurred_at,
                gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, ?, 'revision-transfer', 'TRANSFER', 'POSTED',
                '2026-07-21', '2026-07-21T09:00:00+08:00', 10000, 0
            )
            """,
            (source.id, account.id),
        ).lastrowid
        before_positions = conn.execute(
            "SELECT * FROM portfolio_positions WHERE account_id = ?",
            (account.id,),
        ).fetchall()
        conn.commit()

    with pytest.raises(StaleTradePreviewError):
        _post_trade(
            service,
            account.id,
            client_request_id="stale-after-counterparty-transfer",
            trade_date="2026-07-22",
            expected_ledger_revision=preview.ledger_revision,
            expected_quantity=preview.quantity,
            expected_gross_amount_cents=preview.gross_amount_cents,
            expected_cash_delta_cents=preview.cash_delta_cents,
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM portfolio_transactions
            WHERE client_request_id = 'stale-after-counterparty-transfer'
            """
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT * FROM portfolio_positions WHERE account_id = ?",
            (account.id,),
        ).fetchall() == before_positions

    after_transfer = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-22",
        symbol="600519",
        price_micros=10_000_000,
        quantity=100,
    )
    assert after_transfer.ledger_revision == transfer_id
    reversal = service.reverse_transaction(
        account_id=source.id,
        transaction_id=transfer_id,
        client_request_id="reverse-revision-transfer",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T09:00:00+08:00",
    )
    assert reversal.id > transfer_id

    with pytest.raises(StaleTradePreviewError):
        _post_trade(
            service,
            account.id,
            client_request_id="stale-after-transfer-reversal",
            trade_date="2026-07-22",
            expected_ledger_revision=after_transfer.ledger_revision,
            expected_quantity=after_transfer.quantity,
            expected_gross_amount_cents=after_transfer.gross_amount_cents,
            expected_cash_delta_cents=after_transfer.cash_delta_cents,
        )


def test_idempotent_bound_post_retry_ignores_later_revision_changes(tmp_path):
    db_path, service, account = _service(tmp_path)
    preview = service.preview_trade(
        account_id=account.id,
        business_type="BUY",
        trade_date="2026-07-21",
        symbol="600519",
        price_micros=10_000_000,
        quantity=100,
    )
    expected = {
        "expected_ledger_revision": preview.ledger_revision,
        "expected_quantity": preview.quantity,
        "expected_gross_amount_cents": preview.gross_amount_cents,
        "expected_cash_delta_cents": preview.cash_delta_cents,
    }
    original = _post_trade(
        service,
        account.id,
        client_request_id="bound-idempotent-buy",
        **expected,
    )
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, 'later-deposit', 'DEPOSIT', 'POSTED',
                '2026-07-22', '2026-07-22T09:00:00+08:00', 10000, 10000
            )
            """,
            (account.id,),
        )
        conn.commit()

    retry = _post_trade(
        service,
        account.id,
        client_request_id="bound-idempotent-buy",
        **expected,
    )
    assert retry.id == original.id
    with pytest.raises(IdempotencyConflictError):
        _post_trade(
            service,
            account.id,
            client_request_id="bound-idempotent-buy",
            expected_ledger_revision=preview.ledger_revision,
            expected_quantity=200,
            expected_gross_amount_cents=preview.gross_amount_cents,
            expected_cash_delta_cents=preview.cash_delta_cents,
        )


def test_post_trade_rolls_back_cash_t1_and_backdated_invalid_sequences(tmp_path):
    db_path, service, account = _service(tmp_path, opening_cash_cents=100_000)

    with pytest.raises(InsufficientCashError):
        _post_trade(
            service,
            account.id,
            client_request_id="too-expensive",
            quantity=200,
            price_micros=10_000_000,
        )

    _post_trade(
        service,
        account.id,
        client_request_id="buy-day-1",
        quantity=100,
        price_micros=5_000_000,
    )
    with pytest.raises(InsufficientPositionError):
        _post_trade(
            service,
            account.id,
            client_request_id="same-day-sell",
            business_type="SELL",
            quantity=1,
            price_micros=5_000_000,
        )

    _post_trade(
        service,
        account.id,
        client_request_id="sell-day-2",
        business_type="SELL",
        trade_date="2026-07-22",
        quantity=100,
        price_micros=6_000_000,
    )
    with pytest.raises(InsufficientPositionError):
        _post_trade(
            service,
            account.id,
            client_request_id="backdated-sell",
            business_type="SELL",
            trade_date="2026-07-20",
            quantity=1,
            price_micros=6_000_000,
        )

    with sqlite3.connect(db_path) as conn:
        request_ids = {
            row[0]
            for row in conn.execute(
                "SELECT client_request_id FROM portfolio_transactions"
            )
        }
    assert "too-expensive" not in request_ids
    assert "same-day-sell" not in request_ids
    assert "backdated-sell" not in request_ids


def test_refresh_positions_rejects_historical_projection_overwrite(tmp_path):
    db_path, service, account = _service(tmp_path)
    _post_trade(service, account.id, client_request_id="refresh-buy-1")
    _post_trade(
        service,
        account.id,
        client_request_id="refresh-buy-2",
        trade_date="2026-07-22",
    )

    with pytest.raises(InvalidTradeError, match="latest ledger"):
        service.refresh_positions(account.id, as_of_date="2026-07-21")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT quantity, last_trade_date
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account.id,),
        ).fetchone() == (200, "2026-07-22")


def test_reverse_transaction_rebuilds_or_atomically_rejects_invalid_history(tmp_path):
    db_path, service, account = _service(tmp_path)
    buy = _post_trade(service, account.id, client_request_id="buy-to-reverse")
    sell = _post_trade(
        service,
        account.id,
        client_request_id="sell-to-reverse",
        business_type="SELL",
        trade_date="2026-07-22",
        quantity=40,
        price_micros=12_000_000,
    )
    blocked_buy = _post_trade(
        service,
        account.id,
        client_request_id="blocked-buy",
        symbol="000001",
    )
    _post_trade(
        service,
        account.id,
        client_request_id="blocked-buy-later-sell",
        business_type="SELL",
        trade_date="2026-07-22",
        symbol="000001",
        quantity=100,
        price_micros=11_000_000,
    )

    reversal = service.reverse_transaction(
        account_id=account.id,
        transaction_id=sell.id,
        client_request_id="reverse-sell",
        trade_date="2026-07-23",
        occurred_at="2026-07-23T10:00:00+08:00",
    )
    retry = service.reverse_transaction(
        account_id=account.id,
        transaction_id=sell.id,
        client_request_id="reverse-sell",
        trade_date="2026-07-23",
        occurred_at="2026-07-23T10:00:00+08:00",
    )
    assert retry.id == reversal.id

    restored_preview = service.preview_trade(
        account_id=account.id,
        business_type="SELL",
        trade_date="2026-07-23",
        symbol="600519",
        price_micros=12_000_000,
        quantity=100,
    )
    assert restored_preview.quantity == 100

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT quantity, cost_basis_cents, realized_pnl_cents
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = '600519'
            """,
            (account.id,),
        ).fetchone() == (100, 100_000, 0)

    with pytest.raises(TransactionNotReversibleError):
        service.reverse_transaction(
            account_id=account.id,
            transaction_id=sell.id,
            client_request_id="reverse-sell-again",
            trade_date="2026-07-23",
            occurred_at="2026-07-23T10:01:00+08:00",
        )
    with pytest.raises(TransactionNotReversibleError):
        service.reverse_transaction(
            account_id=account.id,
            transaction_id=reversal.id,
            client_request_id="reverse-a-reversal",
            trade_date="2026-07-23",
            occurred_at="2026-07-23T10:02:00+08:00",
        )
    with pytest.raises(InsufficientPositionError):
        service.reverse_transaction(
            account_id=account.id,
            transaction_id=blocked_buy.id,
            client_request_id="reverse-buy-with-later-sell",
            trade_date="2026-07-23",
            occurred_at="2026-07-23T10:03:00+08:00",
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM portfolio_transactions
            WHERE client_request_id = 'reverse-buy-with-later-sell'
            """
        ).fetchone()[0] == 0


def test_reverse_transfer_validates_counterparty_cash_and_rolls_back(tmp_path):
    db_path, service, source = _service(tmp_path, opening_cash_cents=100_000)
    target = service.create_account(name="转入账户", opening_cash_cents=0)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        transfer_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, counterparty_account_id, client_request_id,
                business_type, status, trade_date, occurred_at,
                gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, ?, 'transfer-for-reversal', 'TRANSFER', 'POSTED',
                '2026-07-20', '2026-07-20T09:00:00+08:00', 50000, 0
            )
            """,
            (source.id, target.id),
        ).lastrowid
        conn.commit()

    _post_trade(
        service,
        target.id,
        client_request_id="spend-transferred-cash",
        trade_date="2026-07-21",
        price_micros=5_000_000,
    )

    with pytest.raises(InsufficientCashError):
        service.reverse_transaction(
            account_id=source.id,
            transaction_id=transfer_id,
            client_request_id="reverse-required-transfer",
            trade_date="2026-07-22",
            occurred_at="2026-07-22T10:00:00+08:00",
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM portfolio_transactions
            WHERE client_request_id = 'reverse-required-transfer'
            """
        ).fetchone()[0] == 0
    assert service.get_account_summary(target.id).cash_cents == 0

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, 'fund-transfer-reversal', 'DEPOSIT', 'POSTED',
                '2026-07-21', '2026-07-21T09:00:00+08:00', 50000, 50000
            )
            """,
            (target.id,),
        )
        conn.commit()
    reversal = service.reverse_transaction(
        account_id=source.id,
        transaction_id=transfer_id,
        client_request_id="reverse-funded-transfer",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T10:01:00+08:00",
    )
    assert reversal.reversal_of == transfer_id
    assert service.get_account_summary(target.id).cash_cents == 0


def test_margin_accounts_fail_closed_without_a_credit_model(tmp_path):
    db_path = tmp_path / "margin-account.db"
    ensure_portfolio_schema(db_path)
    service = PortfolioAccountingService(db_path)

    with pytest.raises(InvalidTradeError, match="MARGIN"):
        service.create_account(
            name="不受支持的融资账户",
            account_type="MARGIN",
            opening_cash_cents=1_000_000,
        )

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        margin_account_id = conn.execute(
            """
            INSERT INTO portfolio_accounts (
                name, account_type, currency, opening_cash_cents
            ) VALUES ('遗留融资账户', 'MARGIN', 'CNY', 1000000)
            """
        ).lastrowid
        conn.commit()

    request = {
        "account_id": margin_account_id,
        "business_type": "BUY",
        "trade_date": "2026-07-21",
        "symbol": "600519",
        "price_micros": 10_000_000,
        "quantity": 100,
    }
    with pytest.raises(InvalidTradeError, match="MARGIN"):
        service.preview_trade(**request)
    with pytest.raises(InvalidTradeError, match="MARGIN"):
        service.post_trade(
            **request,
            client_request_id="margin-buy",
            occurred_at="2026-07-21T10:00:00+08:00",
        )


def test_archived_account_rejects_new_trade(tmp_path):
    db_path, service, account = _service(tmp_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            UPDATE portfolio_accounts
            SET status = 'ARCHIVED',
                is_default = 0,
                archived_at = '2026-07-23T12:00:00+08:00',
                version = version + 1
            WHERE id = ?
            """,
            (account.id,),
        )
        conn.commit()

    with pytest.raises(ArchivedAccountError):
        _post_trade(service, account.id, client_request_id="archived-buy")


def test_historical_projection_replays_average_cost_t1_without_writing_positions(
    tmp_path,
):
    db_path, service, account = _service(tmp_path)
    first_buy = _post_trade(
        service,
        account.id,
        client_request_id="history-buy-1",
        fee_cents=500,
    )
    second_buy = _post_trade(
        service,
        account.id,
        client_request_id="history-buy-2",
        trade_date="2026-07-22",
        price_micros=20_000_000,
        fee_cents=500,
    )
    sell = _post_trade(
        service,
        account.id,
        client_request_id="history-sell",
        business_type="SELL",
        trade_date="2026-07-23",
        quantity=150,
        price_micros=30_000_000,
        fee_cents=500,
        tax_cents=1_000,
    )

    with sqlite3.connect(db_path) as conn:
        positions_before = conn.execute(
            "SELECT * FROM portfolio_positions ORDER BY account_id, symbol"
        ).fetchall()
    database_bytes_before = hashlib.sha256(db_path.read_bytes()).digest()

    day_one = service.project_positions_as_of(account.id, "2026-07-21")
    day_two = service.project_positions_as_of(account.id, "2026-07-22")
    day_three = service.project_positions_as_of(account.id, "2026-07-23")

    assert [
        (
            row.quantity,
            row.available_quantity,
            row.cost_basis_cents,
            row.avg_cost_micros,
            row.realized_pnl_cents,
            row.last_transaction_id,
        )
        for row in day_one
    ] == [(100, 0, 100_500, 10_050_000, 0, first_buy.id)]
    assert [
        (
            row.quantity,
            row.available_quantity,
            row.cost_basis_cents,
            row.avg_cost_micros,
            row.realized_pnl_cents,
            row.last_transaction_id,
        )
        for row in day_two
    ] == [(200, 100, 301_000, 15_050_000, 0, second_buy.id)]
    assert [
        (
            row.quantity,
            row.available_quantity,
            row.cost_basis_cents,
            row.avg_cost_micros,
            row.realized_pnl_cents,
            row.last_transaction_id,
        )
        for row in day_three
    ] == [(50, 50, 75_250, 15_050_000, 222_750, sell.id)]
    assert service.cash_balance_as_of(account.id, "2026-07-23") == 1_147_500
    assert service.ledger_revision_as_of(account.id, "2026-07-21") == first_buy.id
    assert service.ledger_revision_as_of(account.id, "2026-07-22") == second_buy.id
    assert service.ledger_revision_as_of(account.id, "2026-07-23") == sell.id

    with sqlite3.connect(db_path) as conn:
        positions_after = conn.execute(
            "SELECT * FROM portfolio_positions ORDER BY account_id, symbol"
        ).fetchall()
    assert positions_after == positions_before
    assert hashlib.sha256(db_path.read_bytes()).digest() == database_bytes_before


def test_account_as_of_projection_uses_one_read_only_snapshot_and_matches_legacy(
    tmp_path,
    monkeypatch,
):
    db_path, service, account = _service(tmp_path)
    buy = _post_trade(
        service,
        account.id,
        client_request_id="unified-history-buy",
        fee_cents=500,
    )
    expected_positions = tuple(
        service.project_positions_as_of(account.id, "2026-07-21")
    )
    expected_cash = service.cash_balance_as_of(account.id, "2026-07-21")
    expected_revision = service.ledger_revision_as_of(
        account.id,
        "2026-07-21",
    )
    with sqlite3.connect(db_path) as conn:
        positions_before = conn.execute(
            "SELECT * FROM portfolio_positions ORDER BY account_id, symbol"
        ).fetchall()
        snapshot_counts_before = (
            conn.execute(
                "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
            ).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM portfolio_position_daily_snapshots"
            ).fetchone()[0],
        )
    database_bytes_before = hashlib.sha256(db_path.read_bytes()).digest()

    original_connect = service._connect_read_only
    connection_count = 0
    statements: list[str] = []

    class TrackingConnection:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, statement, parameters=()):
            statements.append(" ".join(statement.split()).upper())
            return self._inner.execute(statement, parameters)

        def commit(self):
            return self._inner.commit()

        def rollback(self):
            return self._inner.rollback()

        def close(self):
            return self._inner.close()

    def counted_connect():
        nonlocal connection_count
        connection_count += 1
        conn = original_connect()
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        return TrackingConnection(conn)

    monkeypatch.setattr(service, "_connect_read_only", counted_connect)
    result = service.project_account_as_of(account.id, "2026-07-21")

    assert isinstance(result, AccountAsOfProjection)
    assert result.account == account
    assert result.positions == expected_positions
    assert result.cash_cents == expected_cash
    assert result.ledger_revision == expected_revision == buy.id
    assert result.positions[0].available_quantity == 0
    assert connection_count == 1
    assert statements[0] == "BEGIN"
    with pytest.raises(FrozenInstanceError):
        result.cash_cents = 0

    with sqlite3.connect(db_path) as conn:
        positions_after = conn.execute(
            "SELECT * FROM portfolio_positions ORDER BY account_id, symbol"
        ).fetchall()
        snapshot_counts_after = (
            conn.execute(
                "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
            ).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM portfolio_position_daily_snapshots"
            ).fetchone()[0],
        )
    assert positions_after == positions_before
    assert snapshot_counts_after == snapshot_counts_before == (0, 0)
    assert hashlib.sha256(db_path.read_bytes()).digest() == database_bytes_before


def test_historical_projection_handles_close_reopen_and_corporate_action(tmp_path):
    db_path, service, account = _service(tmp_path)
    _post_trade(
        service,
        account.id,
        client_request_id="cycle-buy-1",
        trade_date="2026-07-20",
    )
    close = _post_trade(
        service,
        account.id,
        client_request_id="cycle-close",
        business_type="SELL",
        trade_date="2026-07-21",
        price_micros=12_000_000,
    )
    reopen = _post_trade(
        service,
        account.id,
        client_request_id="cycle-buy-2",
        trade_date="2026-07-22",
        price_micros=8_000_000,
    )
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        corporate_action_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, symbol, quantity, cash_delta_cents
            ) VALUES (
                ?, 'cycle-bonus-shares', 'CORPORATE_ACTION', 'POSTED',
                '2026-07-23', '2026-07-23T09:00:00+08:00',
                '600519', 10, 0
            )
            """,
            (account.id,),
        ).lastrowid
        conn.commit()

    closed = service.project_positions_as_of(account.id, "2026-07-21")[0]
    assert (
        closed.position_revision,
        closed.quantity,
        closed.available_quantity,
        closed.cost_basis_cents,
        closed.avg_cost_micros,
        closed.realized_pnl_cents,
        closed.status,
        closed.last_transaction_id,
    ) == (1, 0, 0, 0, 0, 20_000, "CLOSED", close.id)

    reopened = service.project_positions_as_of(account.id, "2026-07-22")[0]
    assert (
        reopened.position_revision,
        reopened.quantity,
        reopened.available_quantity,
        reopened.cost_basis_cents,
        reopened.realized_pnl_cents,
        reopened.first_buy_date,
        reopened.last_transaction_id,
    ) == (2, 100, 0, 80_000, 0, "2026-07-22", reopen.id)

    adjusted = service.project_positions_as_of(account.id, "2026-07-23")[0]
    assert (
        adjusted.position_revision,
        adjusted.quantity,
        adjusted.available_quantity,
        adjusted.cost_basis_cents,
        adjusted.avg_cost_micros,
        adjusted.realized_pnl_cents,
        adjusted.last_transaction_id,
    ) == (2, 110, 100, 80_000, 7_272_727, 0, corporate_action_id)


def test_historical_cash_and_revision_include_transfer_and_traceable_reversal(
    tmp_path,
):
    db_path, service, source = _service(tmp_path, opening_cash_cents=100_000)
    target = service.create_account(name="历史转入账户", opening_cash_cents=5_000)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        deposit_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, status,
                trade_date, occurred_at, gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, 'history-deposit', 'DEPOSIT', 'POSTED',
                '2026-07-20', '2026-07-20T09:00:00+08:00', 50000, 50000
            )
            """,
            (source.id,),
        ).lastrowid
        transfer_id = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                account_id, counterparty_account_id, client_request_id,
                business_type, status, trade_date, occurred_at,
                gross_amount_cents, cash_delta_cents
            ) VALUES (
                ?, ?, 'history-transfer', 'TRANSFER', 'POSTED',
                '2026-07-21', '2026-07-21T09:00:00+08:00', 20000, 0
            )
            """,
            (source.id, target.id),
        ).lastrowid
        conn.commit()

    assert service.cash_balance_as_of(source.id, "2026-07-20") == 150_000
    assert service.cash_balance_as_of(target.id, "2026-07-20") == 5_000
    assert service.cash_balance_as_of(source.id, "2026-07-21") == 130_000
    assert service.cash_balance_as_of(target.id, "2026-07-21") == 25_000
    assert service.ledger_revision_as_of(source.id, "2026-07-20") == deposit_id
    assert service.ledger_revision_as_of(target.id, "2026-07-20") == 0
    assert service.ledger_revision_as_of(source.id, "2026-07-21") == transfer_id
    assert service.ledger_revision_as_of(target.id, "2026-07-21") == transfer_id

    reversal = service.reverse_transaction(
        account_id=source.id,
        transaction_id=transfer_id,
        client_request_id="history-reverse-transfer",
        trade_date="2026-07-22",
        occurred_at="2026-07-22T10:00:00+08:00",
    )
    assert service.cash_balance_as_of(source.id, "2026-07-21") == 150_000
    assert service.cash_balance_as_of(target.id, "2026-07-21") == 5_000
    assert service.ledger_revision_as_of(source.id, "2026-07-21") == reversal.id
    assert service.ledger_revision_as_of(target.id, "2026-07-21") == reversal.id
    source_projection = service.project_account_as_of(source.id, "2026-07-21")
    target_projection = service.project_account_as_of(target.id, "2026-07-21")
    assert source_projection.positions == target_projection.positions == ()
    assert source_projection.cash_cents == 150_000
    assert target_projection.cash_cents == 5_000
    assert source_projection.ledger_revision == reversal.id
    assert target_projection.ledger_revision == reversal.id


@pytest.mark.parametrize(
    "method_name",
    [
        "project_positions_as_of",
        "cash_balance_as_of",
        "ledger_revision_as_of",
        "project_account_as_of",
    ],
)
def test_historical_read_methods_validate_account_and_date(tmp_path, method_name):
    _, service, account = _service(tmp_path)
    method = getattr(service, method_name)

    with pytest.raises(InvalidTradeError, match="ISO date"):
        method(account.id, "2026-7-23")
    with pytest.raises(AccountNotFoundError):
        method(999, "2026-07-23")
