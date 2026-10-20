import sqlite3
from contextlib import closing

import pytest

from portfolio.accounting import (PortfolioAccountingService, InsufficientCashError,
                                 StaleTradePreviewError, InvalidTradeError)
from portfolio.import_service import PortfolioImportService
from portfolio.schema import ensure_portfolio_schema


def setup_service(tmp_path, cash=1000000):
    path = tmp_path / 'batch.db'
    ensure_portfolio_schema(path)
    service = PortfolioAccountingService(path)
    account = service.create_account(name='test', opening_cash_cents=cash)
    return path, service, account.id, PortfolioImportService(path)


def trade(**overrides):
    row = dict(row_id='1', business_type='BUY', trade_date='2026-08-06',
               trade_time='09:31', symbol='600276', quantity=100,
               price_micros=10000000, fee_cents=100)
    row.update(overrides)
    return row


def test_atomic_batch_historical_deposit_and_retry(tmp_path):
    path, ledger, aid, imports = setup_service(tmp_path, cash=0)
    rows = [trade(), dict(row_id='0', business_type='DEPOSIT', trade_date='2026-08-06',
                         trade_time='08:46', amount_cents=2000000)]
    preview = imports.preview(account_id=aid, rows=rows, client_request_id='batch-one')
    assert preview['cash_after_cents'] == 1899900
    assert ledger.ledger_revision(aid) == 0
    receipt = imports.commit(draft_id=preview['draft_id'], version=1,
                             client_request_id='batch-one')
    assert receipt['booked_count'] == 2
    retry = imports.commit(draft_id=preview['draft_id'], version=1,
                           client_request_id='batch-one')
    assert retry['duplicate_replay']
    assert ledger.ledger_revision(aid) == 2
    assert ledger.get_account_summary(aid).cash_cents == 1899900


def test_failed_batch_leaves_no_trade_or_position(tmp_path):
    path, ledger, aid, imports = setup_service(tmp_path, cash=150000)
    with pytest.raises(InsufficientCashError):
        imports.preview(account_id=aid, rows=[trade(), trade(row_id='2', symbol='000792')],
                        client_request_id='too-large')
    assert ledger.ledger_revision(aid) == 0
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT count(*) FROM portfolio_positions').fetchone()[0] == 0


def test_duplicates_are_candidates_not_silently_removed(tmp_path):
    path, ledger, aid, imports = setup_service(tmp_path)
    rows = [trade(), trade(row_id='2')]
    preview = imports.preview(account_id=aid, rows=rows, client_request_id='duplicates')
    assert preview['needs_duplicate_confirmation']
    assert len(preview['rows']) == 2
    assert ledger.ledger_revision(aid) == 0
    for row in rows:
        row['duplicate_confirmed'] = True
    preview = imports.preview(account_id=aid, rows=rows, client_request_id='confirmed')
    receipt = imports.commit(draft_id=preview['draft_id'], version=1,
                             client_request_id='confirmed')
    assert receipt['booked_count'] == 2
    assert ledger.project_positions_as_of(aid, '2026-08-07')[0].quantity == 200


def test_changed_ledger_rejects_commit(tmp_path):
    path, ledger, aid, imports = setup_service(tmp_path)
    preview = imports.preview(account_id=aid, rows=[trade()], client_request_id='stale')
    ledger.post_trade(account_id=aid, client_request_id='other', business_type='BUY',
                      symbol='000792', quantity=100, price_micros=1000000,
                      trade_date='2026-08-06', occurred_at='2026-08-06T10:00:00+08:00')
    with pytest.raises(StaleTradePreviewError):
        imports.commit(draft_id=preview['draft_id'], version=1, client_request_id='stale')
    assert ledger.ledger_revision(aid) == 1


def test_replacement_of_sold_buy_is_atomic(tmp_path):
    path, ledger, aid, imports = setup_service(tmp_path)
    old = ledger.post_trade(account_id=aid, client_request_id='buy', business_type='BUY',
                            symbol='600276', quantity=200, price_micros=11000000,
                            trade_date='2026-08-06', occurred_at='2026-08-06T15:00:00+08:00')
    ledger.post_trade(account_id=aid, client_request_id='sell', business_type='SELL',
                      symbol='600276', quantity=100, price_micros=12000000,
                      trade_date='2026-08-07', occurred_at='2026-08-07T10:00:00+08:00')
    rows = [trade(), trade(row_id='2', trade_time='09:37', price_micros=12000000)]
    p = imports.preview(account_id=aid, rows=rows, client_request_id='replace',
                        replace_transaction_ids=[old.id])
    imports.commit(draft_id=p['draft_id'], version=1, client_request_id='replace')
    assert ledger.project_positions_as_of(aid, '2026-08-08')[0].quantity == 100
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT count(*) FROM portfolio_transactions WHERE reversal_of=?',
                            (old.id,)).fetchone()[0] == 1


def test_unknown_date_time_fee_and_net_are_not_invented(tmp_path):
    _, _, aid, imports = setup_service(tmp_path)
    for change in [{'trade_time': None}, {'fee_cents': None}, {'trade_date':'08-06'},
                   {'net_cash_cents':-9999}]:
        with pytest.raises(InvalidTradeError):
            imports.preview(account_id=aid, rows=[trade(**change)], client_request_id='invalid')


def test_replacement_clears_old_symbol_state_but_preserves_unrelated_state(tmp_path):
    from portfolio.exit_engine import PortfolioExitEngine

    path, ledger, aid, imports = setup_service(tmp_path)
    old = ledger.post_trade(account_id=aid, client_request_id='old-symbol', business_type='BUY',
                            symbol='600276', quantity=100, price_micros=10000000,
                            trade_date='2026-08-06', occurred_at='2026-08-06T09:31:00+08:00')
    other = ledger.post_trade(account_id=aid, client_request_id='unrelated', business_type='BUY',
                              symbol='600299', quantity=100, price_micros=10000000,
                              trade_date='2026-08-06', occurred_at='2026-08-06T10:00:00+08:00')
    engine = PortfolioExitEngine(path)
    for symbol, transaction in [('600276', old), ('600299', other)]:
        plan = engine.upsert_plan(account_id=aid, symbol=symbol, position_revision=1)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("""INSERT INTO portfolio_exit_states (
                account_id,symbol,position_revision,state,data_status,last_transaction_id,
                rule_version) VALUES(?,?,1,'DATA_BLOCKED','MISSING',?,?)""",
                (aid, symbol, transaction.id, plan.rule_version))
            conn.commit()
    preview = imports.preview(account_id=aid, rows=[trade(symbol='000792')],
                              client_request_id='different-symbol',
                              replace_transaction_ids=[old.id])
    imports.commit(draft_id=preview['draft_id'], version=1, client_request_id='different-symbol')
    with closing(sqlite3.connect(path)) as conn:
        remaining = conn.execute('SELECT symbol FROM portfolio_exit_states WHERE account_id=?',
                                 (aid,)).fetchall()
    assert remaining == [('600299',)]
