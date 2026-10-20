from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from portfolio.accounting import PortfolioAccountingService
from portfolio.exit_engine import (
    InvalidExitPlanError,
    PortfolioExitEngine,
    StaleExitPlanError,
)
from portfolio.schema import configure_portfolio_connection, ensure_portfolio_schema


@pytest.fixture()
def exit_env(tmp_path):
    db_path = tmp_path / "portfolio-exit.db"
    ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL NOT NULL,
                open REAL, high REAL, low REAL, volume REAL,
                adjustment_mode TEXT NOT NULL DEFAULT 'qfq',
                quality_flag TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE stock_daily_prices_raw (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close,
                source TEXT,
                quality_flag TEXT,
                run_id TEXT,
                updated_at TEXT,
                adjustment_mode TEXT,
                market TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            """
        )
        conn.commit()
    accounting = PortfolioAccountingService(db_path)
    engine = PortfolioExitEngine(db_path, accounting_service=accounting)
    return db_path, accounting, engine


def _account(accounting, name="账户"):
    return accounting.create_account(name=name, opening_cash_cents=10_000_000)


def _buy(
    accounting,
    account_id,
    *,
    symbol="600519",
    trade_date="2026-07-21",
    quantity=100,
    price_micros=10_000_000,
    request_id=None,
):
    return accounting.post_trade(
        account_id=account_id,
        client_request_id=request_id or f"buy-{account_id}-{symbol}-{trade_date}",
        business_type="BUY",
        trade_date=trade_date,
        occurred_at=f"{trade_date}T10:00:00+08:00",
        symbol=symbol,
        price_micros=price_micros,
        quantity=quantity,
    )


def _sell(
    accounting,
    account_id,
    *,
    symbol="600519",
    trade_date="2026-07-23",
    quantity=100,
    price_micros=10_000_000,
    request_id=None,
):
    return accounting.post_trade(
        account_id=account_id,
        client_request_id=request_id or f"sell-{account_id}-{symbol}-{trade_date}",
        business_type="SELL",
        trade_date=trade_date,
        occurred_at=f"{trade_date}T10:00:00+08:00",
        symbol=symbol,
        price_micros=price_micros,
        quantity=quantity,
    )


def _raw(
    db_path,
    symbol,
    trade_date,
    close,
    *,
    source="tdx_mootdx_raw",
    quality="ok_unadjusted",
    mode="none",
):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO stock_daily_prices_raw (
                symbol, trade_date, close, source, quality_flag,
                adjustment_mode, market
            ) VALUES (?, ?, ?, ?, ?, ?, 'SH')
            """,
            (symbol, trade_date, close, source, quality, mode),
        )
        conn.commit()
    # Existing raw-price tests need independent, trusted C warmup data.
    # Explicit C tests replace this neutral history with their own OHLCV.
    with sqlite3.connect(db_path) as conn:
        last = conn.execute("SELECT MAX(trade_date) FROM stock_daily_prices WHERE symbol=?",
                            (symbol,)).fetchone()[0]
    if last is None:
        _qfq_series(db_path, symbol, trade_date, [10.0] * 40)
    elif last < trade_date:
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO stock_daily_prices "
                         "(symbol,trade_date,close,open,high,low,volume,adjustment_mode,quality_flag) "
                         "VALUES (?,?,10,10,10.1,9.9,100,'qfq','ok')", (symbol, trade_date))


def _qfq_series(db_path, symbol, end_date, closes):
    end = date.fromisoformat(end_date)
    start = end - timedelta(days=len(closes) - 1)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO stock_daily_prices (
                symbol, trade_date, close, open, high, low, volume, adjustment_mode, quality_flag
            ) VALUES (?, ?, ?, ?, ?, ?, 100, 'qfq', 'ok')
            """,
            [
                (symbol, (start + timedelta(days=index)).isoformat(), close, close, close+.1, close-.1)
                for index, close in enumerate(closes)
            ],
        )
        conn.commit()


def _plan(engine, account_id, **overrides):
    values = {
        "account_id": account_id,
        "symbol": "600519",
        "position_revision": 1,
    }
    values.update(overrides)
    return engine.upsert_plan(**values)


def test_plan_identity_defaults_and_validation(exit_env):
    _, accounting, engine = exit_env
    account = _account(accounting)
    buy = _buy(accounting, account.id)

    plan = _plan(engine, account.id)

    assert plan.entry_transaction_id == buy.id
    assert plan.enabled
    assert plan.trend_exit_enabled and plan.intraday_enabled
    assert all(getattr(plan, field) is None for field in (
        "hard_stop_micros",
        "take_profit_1_micros",
        "trailing_activation_bps",
        "time_stop_trading_days",
    ))
    with pytest.raises(InvalidExitPlanError):
        _plan(
            engine,
            account.id,
            take_profit_1_micros=12_000_000,
        )
    with pytest.raises(StaleExitPlanError):
        engine.upsert_plan(
            account_id=account.id,
            symbol="600519",
            position_revision=2,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("enabled", 1),
        ("enabled", "true"),
        ("trend_exit_enabled", 0),
        ("trend_exit_enabled", None),
        ("intraday_enabled", 1),
        ("intraday_enabled", "false"),
    ),
)
def test_plan_flags_require_real_booleans(exit_env, field, value):
    _, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)

    with pytest.raises(InvalidExitPlanError, match=field):
        _plan(engine, account.id, **{field: value})


@pytest.mark.parametrize("invalid_limit", (0, 501, True, 1.5))
@pytest.mark.parametrize("method_name", ("list_plans", "list_states", "list_events"))
def test_exit_lists_reject_invalid_limits(
    exit_env,
    method_name,
    invalid_limit,
):
    _, _, engine = exit_env
    method = getattr(engine, method_name)

    with pytest.raises(InvalidExitPlanError, match="limit"):
        method(limit=invalid_limit)


def test_exit_list_limits_are_stable_and_applied_in_sql(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-23", 10)
    engine.run_daily("2026-07-23")

    assert len(engine.list_plans(limit=1)) == 1
    assert len(engine.list_states(limit=1)) == 1
    assert len(engine.list_events(limit=1)) == 1

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.executemany(
            """
            INSERT INTO portfolio_exit_events (
                account_id, symbol, position_revision, event_type,
                severity, event_status, trigger_date, triggered_at,
                bar_time, observed_price_micros, available_quantity,
                recommended_quantity, executable, tier, dedupe_key,
                payload_json, rule_version
            ) VALUES (
                ?, '600519', 1, 'EXIT_DATA_BLOCKED', 'WARNING', 'OPEN',
                ?, ?, NULL, NULL, 0, 0, 0, NULL, ?, '{"source":"test"}',
                'portfolio-exit-v1'
            )
            """,
            [
                (
                    account.id,
                    f"2027-{1 + index // 28:02d}-{1 + index % 28:02d}",
                    (
                        f"2027-{1 + index // 28:02d}-"
                        f"{1 + index % 28:02d}T15:00:00+08:00"
                    ),
                    f"default-limit-{index:03d}",
                )
                for index in range(205)
            ],
        )
        conn.commit()

    events = engine.list_events()

    assert len(events) == 200
    assert [item.id for item in events] == sorted(
        (item.id for item in events),
        reverse=True,
    )
    assert events[0].dedupe_key == "default-limit-204"


def test_same_symbol_is_isolated_by_account_and_run_filter(exit_env):
    db_path, accounting, engine = exit_env
    first = _account(accounting, "A")
    second = _account(accounting, "B")
    _buy(accounting, first.id)
    _buy(accounting, second.id)
    _plan(engine, first.id, hard_stop_micros=9_000_000)
    _plan(engine, second.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-23", 10)

    result = engine.run_daily("2026-07-23", account_id=second.id)

    assert [item.account_id for item in result.accounts] == [second.id]
    events = engine.list_events()
    assert [(item.account_id, item.event_type) for item in events] == [
        (second.id, "HARD_STOP_TRIGGERED")
    ]
    assert len(engine.list_plans(account_id=first.id)) == 1


def test_hard_stop_gap_warns_on_t_plus_one_without_executable_quantity(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, trade_date="2026-07-23")
    _plan(engine, account.id, hard_stop_micros=9_500_000)
    _raw(db_path, "600519", "2026-07-23", 8)

    engine.run_daily("2026-07-23")
    event = engine.list_events(account_id=account.id)[0]

    assert event.event_type == "HARD_STOP_TRIGGERED"
    assert event.available_quantity == 0
    assert event.recommended_quantity == 0
    assert not event.executable
    assert engine.list_states(account_id=account.id)[0].state == "EXIT_READY"


def test_exit_signal_realerts_when_t_plus_one_becomes_executable(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, trade_date="2026-07-23")
    _plan(engine, account.id, hard_stop_micros=9_500_000)
    _raw(db_path, "600519", "2026-07-23", 8)
    _raw(db_path, "600519", "2026-07-24", 8)

    first = engine.run_daily("2026-07-23")
    second = engine.run_daily("2026-07-24")
    duplicate = engine.run_daily("2026-07-24")
    events = sorted(
        engine.list_events(account_id=account.id),
        key=lambda item: item.id,
    )

    assert first.created_event_count == second.created_event_count == 1
    assert duplicate.created_event_count == 0
    assert [(item.executable, item.recommended_quantity) for item in events] == [
        (False, 0),
        (True, 100),
    ]
    assert events[0].dedupe_key != events[1].dedupe_key


def test_take_profit_tiers_are_once_only_and_capped_to_available(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(
        engine,
        account.id,
        take_profit_1_micros=11_000_000,
        take_profit_1_reduce_bps=4000,
        take_profit_2_micros=12_000_000,
        take_profit_2_reduce_bps=4000,
        take_profit_3_micros=13_000_000,
        take_profit_3_reduce_bps=2000,
    )
    _raw(db_path, "600519", "2026-07-23", 13.5)

    first = engine.run_daily("2026-07-23")
    second = engine.run_daily("2026-07-23")
    events = sorted(engine.list_events(account_id=account.id), key=lambda item: item.tier or 0)

    assert first.created_event_count == 3
    assert second.created_event_count == 0
    assert [item.tier for item in events] == [1, 2, 3]
    assert [item.recommended_quantity for item in events] == [40, 40, 20]
    assert sum(item.recommended_quantity for item in events) == 100


def test_unresolved_take_profit_recommendations_reserve_later_tier_budget(
    exit_env,
):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(
        engine,
        account.id,
        take_profit_1_micros=11_000_000,
        take_profit_1_reduce_bps=6000,
        take_profit_2_micros=12_000_000,
        take_profit_2_reduce_bps=4000,
    )
    _raw(db_path, "600519", "2026-07-22", 11)
    engine.run_daily("2026-07-22")
    _sell(
        accounting,
        account.id,
        trade_date="2026-07-23",
        quantity=20,
        request_id="partial-sell-after-tp1",
    )
    _raw(db_path, "600519", "2026-07-23", 12)

    engine.run_daily("2026-07-23")
    events = sorted(engine.list_events(account_id=account.id), key=lambda item: item.tier or 0)

    assert [item.recommended_quantity for item in events] == [60, 40]
    assert sum(
        item.recommended_quantity
        for item in events
        if item.event_status in {"OPEN", "ACKNOWLEDGED"}
    ) == 100


def test_take_profit_uses_cumulative_lifecycle_target_after_execution(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, quantity=100)
    _plan(
        engine,
        account.id,
        take_profit_1_micros=11_000_000,
        take_profit_1_reduce_bps=4_000,
        take_profit_2_micros=12_000_000,
        take_profit_2_reduce_bps=4_000,
    )
    _raw(db_path, "600519", "2026-07-22", 11)
    engine.run_daily("2026-07-22")
    _sell(
        accounting,
        account.id,
        trade_date="2026-07-23",
        quantity=40,
        request_id="execute-tp1",
    )
    _raw(db_path, "600519", "2026-07-23", 12)

    engine.run_daily("2026-07-23")
    events = sorted(
        engine.list_events(account_id=account.id),
        key=lambda item: item.tier or 0,
    )

    assert [item.recommended_quantity for item in events] == [40, 40]


def test_trailing_stop_only_moves_up_and_then_triggers(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(
        engine,
        account.id,
        trailing_activation_bps=1000,
        trailing_distance_bps=500,
    )
    _raw(db_path, "600519", "2026-07-22", 12)
    engine.run_daily("2026-07-22")
    first_stop = engine.list_states(account_id=account.id)[0].trailing_stop_micros

    engine.run_daily("2026-07-23")
    blocked_stop = engine.list_states(account_id=account.id)[0].trailing_stop_micros

    _raw(db_path, "600519", "2026-07-24", 11.3)
    engine.run_daily("2026-07-24")
    recovered_stop = engine.list_states(account_id=account.id)[0].trailing_stop_micros

    _raw(db_path, "600519", "2026-07-25", 11.3)
    engine.run_daily("2026-07-25")

    assert first_stop == blocked_stop == recovered_stop == 11_400_000
    assert engine.list_states(account_id=account.id)[0].state == "EXIT_READY"
    assert any(
        item.event_type == "EXIT_DATA_RECOVERED"
        for item in engine.list_events(account_id=account.id)
    )
    assert any(
        item.event_type == "TRAILING_STOP_TRIGGERED"
        for item in engine.list_events(account_id=account.id)
    )


def test_recovery_without_signal_returns_to_normal(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    engine.run_daily("2026-07-22")
    _raw(db_path, "600519", "2026-07-24", 10)
    engine.run_daily("2026-07-24")
    assert engine.list_states(account_id=account.id)[0].state == "NORMAL"


def test_c_watch_first_weak_day_and_exit_second_weak_day(exit_env):
    db_path, accounting, engine = exit_env
    watch_account = _account(accounting, "watch")
    trigger_account = _account(accounting, "trigger")
    _buy(accounting, watch_account.id, symbol="600519")
    _buy(accounting, trigger_account.id, symbol="000001")
    _raw(db_path, "600519", "2026-07-23", 9)
    _raw(db_path, "000001", "2026-07-23", 9)
    _qfq_series(db_path, "600519", "2026-07-23", [10.0] * 39 + [9.0])
    _qfq_series(
        db_path,
        "000001",
        "2026-07-23",
        [10.0] * 38 + [9.5, 9.0],
    )

    engine.run_daily("2026-07-23")
    events = engine.list_events()

    assert any(
        item.account_id == watch_account.id
        and item.event_type == "TREND_EXIT_WATCH"
        for item in events
    )
    assert any(
        item.account_id == trigger_account.id
        and item.event_type == "TREND_EXIT_TRIGGERED"
        for item in events
    )


@pytest.mark.parametrize(("latest_date", "quality"), (
    ("2026-07-23", "ok"),
    ("2026-07-24", "suspect"),
))
def test_trend_signal_rejects_stale_or_untrusted_target_bar(
    exit_env,
    latest_date,
    quality,
):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _raw(db_path, "600519", "2026-07-24", 9)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM stock_daily_prices WHERE symbol='600519'")
    closes = [20 - index * 0.3 for index in range(40)]
    _qfq_series(db_path, "600519", latest_date, closes)
    if latest_date == "2026-07-24":
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE stock_daily_prices
                SET quality_flag = ?
                WHERE symbol = '600519' AND trade_date = '2026-07-24'
                """,
                (quality,),
            )
            conn.commit()

    engine.run_daily("2026-07-24")

    assert not any(
        item.event_type in {"TREND_EXIT_WATCH", "TREND_EXIT_TRIGGERED"}
        for item in engine.list_events(account_id=account.id)
    )
    assert engine.list_states(account_id=account.id)[0].state == "DATA_BLOCKED"


def test_time_stop_uses_trading_days_and_raw_return(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, trade_date="2026-07-20")
    _plan(
        engine,
        account.id,
        time_stop_trading_days=3,
        time_stop_min_return_bps=0,
    )
    _qfq_series(db_path, "000001", "2026-07-23", [1, 1, 1, 1])
    _raw(db_path, "600519", "2026-07-23", 9.5)

    engine.run_daily("2026-07-23")

    assert engine.list_events(account_id=account.id)[0].event_type == (
        "TIME_STOP_TRIGGERED"
    )


def test_missing_raw_is_fail_closed_and_recovery_resolves_block(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)

    missing = engine.run_daily("2026-07-23")
    blocked = engine.list_events(account_id=account.id)[0]
    assert missing.accounts[0].blocked_positions == 1
    assert blocked.event_type == "EXIT_DATA_BLOCKED"
    assert engine.list_states(account_id=account.id)[0].data_status == "MISSING"

    _raw(db_path, "600519", "2026-07-24", 10)
    engine.run_daily("2026-07-24")
    events = engine.list_events(account_id=account.id)

    assert next(
        item for item in events if item.event_type == "EXIT_DATA_BLOCKED"
    ).event_status == "RESOLVED"
    assert any(item.event_type == "EXIT_DATA_RECOVERED" for item in events)
    assert engine.list_states(account_id=account.id)[0].data_status == "OK"


def test_each_continuous_data_block_episode_creates_exactly_one_event(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)

    engine.run_daily("2026-07-22")
    engine.run_daily("2026-07-23")
    _raw(db_path, "600519", "2026-07-24", 10)
    engine.run_daily("2026-07-24")
    engine.run_daily("2026-07-25")
    engine.run_daily("2026-07-26")
    events = engine.list_events(account_id=account.id)
    blocked = sorted(
        (item for item in events if item.event_type == "EXIT_DATA_BLOCKED"),
        key=lambda item: item.id,
    )

    assert len(blocked) == 2
    assert [item.event_status for item in blocked] == ["RESOLVED", "OPEN"]
    assert blocked[0].dedupe_key != blocked[1].dedupe_key
    assert len(
        [item for item in events if item.event_type == "EXIT_DATA_RECOVERED"]
    ) == 1


def test_every_daily_event_payload_declares_daily_source(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-23", 10)

    engine.run_daily("2026-07-23")

    assert all(
        json.loads(item.payload_json or "{}").get("source") == "daily"
        for item in engine.list_events(account_id=account.id)
    )


def test_closed_or_reversed_position_disables_plan_and_resolves_event(exit_env):
    db_path, accounting, engine = exit_env
    closed = _account(accounting, "closed")
    reversed_account = _account(accounting, "reversed")
    _buy(accounting, closed.id)
    reversed_buy = _buy(accounting, reversed_account.id)
    _plan(engine, closed.id, hard_stop_micros=11_000_000)
    _plan(engine, reversed_account.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-22", 10)
    engine.run_daily("2026-07-22")
    _sell(accounting, closed.id, trade_date="2026-07-23")
    accounting.reverse_transaction(
        transaction_id=reversed_buy.id,
        account_id=reversed_account.id,
        client_request_id="reverse-buy",
        trade_date="2026-07-23",
        occurred_at="2026-07-23T11:00:00+08:00",
    )

    engine.run_daily("2026-07-23")

    assert all(not item.enabled for item in engine.list_plans())
    assert all(item.event_status == "RESOLVED" for item in engine.list_events())


def test_event_acknowledge_and_resolve_are_forward_only(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-23", 10)
    engine.run_daily("2026-07-23")
    event = engine.list_events(account_id=account.id)[0]

    acknowledged = engine.acknowledge_event(event.id)
    resolved = engine.resolve_event(event.id)

    assert acknowledged.event_status == "ACKNOWLEDGED"
    assert acknowledged.acknowledged_at is not None
    assert resolved.event_status == "RESOLVED"
    assert resolved.resolved_at is not None
    with pytest.raises(InvalidExitPlanError):
        engine.acknowledge_event(event.id)


def test_transition_time_compares_instants_and_returns_utc(exit_env):
    db_path, _, engine = exit_env
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                '2099-01-01T23:30:00-10:00' AS triggered_at,
                '2099-01-02T01:00:00+14:00' AS acknowledged_at
            """
        ).fetchone()

    assert engine._transition_time(row) == "2099-01-02T09:30:00+00:00"


def test_out_of_order_trade_date_is_rejected_without_writes(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=9_000_000)
    _raw(db_path, "600519", "2026-07-24", 10)
    _raw(db_path, "600519", "2026-07-23", 8)
    engine.run_daily("2026-07-24")
    before_state = engine.list_states(account_id=account.id)
    before_events = engine.list_events(account_id=account.id)

    with pytest.raises(InvalidExitPlanError, match="earlier"):
        engine.run_daily("2026-07-23")

    assert engine.list_states(account_id=account.id) == before_state
    assert engine.list_events(account_id=account.id) == before_events


def test_data_block_keeps_monotonic_last_price_checkpoint(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _raw(db_path, "600519", "2026-07-24", 10)
    _raw(db_path, "600519", "2026-07-23", 9)
    engine.run_daily("2026-07-24")
    engine.run_daily("2026-07-25")
    state = engine.list_states(account_id=account.id)[0]

    assert state.state == "DATA_BLOCKED"
    assert state.data_status == "STALE"
    assert state.last_price_date == "2026-07-24"
    with pytest.raises(InvalidExitPlanError, match="earlier"):
        engine.run_daily("2026-07-23")


def test_reversed_entry_rebuilds_plan_from_effective_buy(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    first = _buy(
        accounting,
        account.id,
        quantity=100,
        request_id="entry-to-reverse",
    )
    second = _buy(
        accounting,
        account.id,
        trade_date="2026-07-22",
        quantity=100,
        request_id="effective-entry",
    )
    _plan(engine, account.id, hard_stop_micros=9_000_000)
    accounting.reverse_transaction(
        transaction_id=first.id,
        account_id=account.id,
        client_request_id="reverse-first-entry",
        trade_date="2026-07-23",
        occurred_at="2026-07-23T11:00:00+08:00",
    )
    _raw(db_path, "600519", "2026-07-23", 10)

    result = engine.run_daily("2026-07-23")
    plan = engine.list_plans(account_id=account.id)[0]

    assert result.accounts[0].blocked_positions == 0
    assert plan.enabled
    assert plan.entry_transaction_id == second.id
    assert plan.hard_stop_micros == 9_000_000
    assert engine.list_states(account_id=account.id)[0].data_status == "OK"


def test_unrepairable_reversed_entry_blocks_only_that_position(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    first = _buy(
        accounting,
        account.id,
        symbol="600519",
        quantity=100,
        request_id="history-entry",
    )
    _buy(
        accounting,
        account.id,
        symbol="600519",
        trade_date="2026-07-22",
        quantity=100,
        request_id="history-effective-entry",
    )
    _buy(
        accounting,
        account.id,
        symbol="000001",
        quantity=100,
        request_id="healthy-symbol",
    )
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    _plan(
        engine,
        account.id,
        symbol="000001",
        hard_stop_micros=9_000_000,
    )
    _raw(db_path, "600519", "2026-07-22", 10)
    _raw(db_path, "000001", "2026-07-22", 10)
    engine.run_daily("2026-07-22")
    accounting.reverse_transaction(
        transaction_id=first.id,
        account_id=account.id,
        client_request_id="reverse-history-entry",
        trade_date="2026-07-23",
        occurred_at="2026-07-23T11:00:00+08:00",
    )
    _raw(db_path, "600519", "2026-07-23", 10)
    _raw(db_path, "000001", "2026-07-23", 10)

    result = engine.run_daily("2026-07-23")
    plans = {
        item.symbol: item
        for item in engine.list_plans(account_id=account.id)
    }

    assert result.accounts[0].blocked_positions == 1
    assert not plans["600519"].enabled
    assert engine.list_states(account_id=account.id)[-1].symbol in {
        "000001",
        "600519",
    }
    assert any(
        item.symbol == "000001" and item.last_price_date == "2026-07-23"
        for item in engine.list_states(account_id=account.id)
    )


def test_concurrent_position_change_is_skipped_without_old_fact_writes(
    exit_env,
    monkeypatch,
):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-23", 10)
    original = engine._load_raw_prices
    changed = False

    def sell_during_market_read(conn, symbols, trade_date):
        nonlocal changed
        prices = original(conn, symbols, trade_date)
        if not changed:
            changed = True
            _sell(
                accounting,
                account.id,
                trade_date=trade_date,
                quantity=20,
                request_id="concurrent-partial-sell",
            )
        return prices

    monkeypatch.setattr(engine, "_load_raw_prices", sell_during_market_read)

    result = engine.run_daily("2026-07-23")

    assert result.accounts[0].blocked_positions == 1
    assert engine.list_states(account_id=account.id) == []
    assert engine.list_events(account_id=account.id) == []


def test_account_write_rolls_back_state_and_event_together(
    exit_env,
    monkeypatch,
):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    _raw(db_path, "600519", "2026-07-23", 10)

    def fail_event(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(engine, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="injected"):
        engine.run_daily("2026-07-23")

    assert engine.list_states(account_id=account.id) == []
    assert engine.list_events(account_id=account.id) == []


def test_market_reads_are_batched_once_per_account(exit_env, monkeypatch):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    for index, symbol in enumerate(("600519", "000001", "300750")):
        _buy(
            accounting,
            account.id,
            symbol=symbol,
            request_id=f"batch-buy-{index}",
        )
        _raw(db_path, symbol, "2026-07-23", 10)
    counts = {"raw": 0, "technical": 0}
    original_raw = engine._load_raw_prices
    original_technical = engine._load_technicals

    def raw_wrapper(conn, symbols, trade_date):
        counts["raw"] += 1
        return original_raw(conn, symbols, trade_date)

    def technical_wrapper(conn, symbols, trade_date):
        counts["technical"] += 1
        return original_technical(conn, symbols, trade_date)

    monkeypatch.setattr(engine, "_load_raw_prices", raw_wrapper)
    monkeypatch.setattr(engine, "_load_technicals", technical_wrapper)

    engine.run_daily("2026-07-23")

    assert counts == {"raw": 1, "technical": 1}
    assert len(engine.list_states(account_id=account.id)) == 3


def test_intraday_hard_stop_is_idempotent_and_preserves_daily_price(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=9_000_000)
    _raw(db_path, "600519", "2026-07-22", 10)
    engine.run_daily("2026-07-22")

    first = engine.run_intraday_bars(
        "2026-07-23T09:35:00+08:00",
        {"600519": 8_000_000},
        observed_at="2026-07-23T09:35:30+08:00",
    )
    duplicate = engine.run_intraday_bars(
        "2026-07-23T09:35:00+08:00",
        {"600519": 8_000_000},
        observed_at="2026-07-23T09:36:00+08:00",
    )
    event = engine.list_events(account_id=account.id)[0]
    state = engine.list_states(account_id=account.id)[0]

    assert first.created_event_count == 1
    assert duplicate.created_event_count == 0
    assert event.event_type == "HARD_STOP_TRIGGERED"
    assert event.bar_time == "2026-07-23T09:35:00+08:00"
    assert json.loads(event.payload_json or "{}")["source"] == "intraday_5min"
    assert state.last_price_date == "2026-07-22"
    assert state.last_price_micros == 10_000_000
    assert state.last_bar_time == "2026-07-23T09:35:00+08:00"


def test_intraday_t_plus_one_realerts_on_next_day_available_quantity(exit_env):
    _, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, trade_date="2026-07-23")
    _plan(engine, account.id, hard_stop_micros=9_000_000)

    same_day = engine.run_intraday_bars(
        "2026-07-23T14:55:00+08:00",
        {"600519": 8_000_000},
        observed_at="2026-07-23T14:56:00+08:00",
    )
    next_day = engine.run_intraday_bars(
        "2026-07-24T09:35:00+08:00",
        {"600519": 8_000_000},
        observed_at="2026-07-24T09:36:00+08:00",
    )
    events = sorted(
        engine.list_events(account_id=account.id),
        key=lambda item: item.id,
    )

    assert same_day.created_event_count == next_day.created_event_count == 1
    assert [(item.executable, item.recommended_quantity) for item in events] == [
        (False, 0),
        (True, 100),
    ]


def test_intraday_same_symbol_isolated_across_accounts(exit_env):
    _, accounting, engine = exit_env
    first = _account(accounting, "intraday-A")
    second = _account(accounting, "intraday-B")
    _buy(accounting, first.id)
    _buy(accounting, second.id)
    _plan(engine, first.id, hard_stop_micros=9_000_000)
    _plan(engine, second.id, hard_stop_micros=7_000_000)

    result = engine.run_intraday_bars(
        "2026-07-23T10:00:00+08:00",
        {"600519": 8_000_000},
        observed_at="2026-07-23T10:01:00+08:00",
    )

    assert [item.account_id for item in result.accounts] == [
        first.id,
        second.id,
    ]
    assert [(item.account_id, item.event_type) for item in engine.list_events()] == [
        (first.id, "HARD_STOP_TRIGGERED"),
    ]


@pytest.mark.parametrize(
    "plan_overrides",
    (
        {"enabled": False},
        {"intraday_enabled": False},
    ),
)
def test_intraday_skips_disabled_plans(exit_env, plan_overrides):
    _, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(
        engine,
        account.id,
        hard_stop_micros=11_000_000,
        **plan_overrides,
    )

    result = engine.run_intraday_bars(
        "2026-07-23T10:05:00+08:00",
        {"600519": 10_000_000},
        observed_at="2026-07-23T10:06:00+08:00",
    )

    assert result.created_event_count == 0
    assert engine.list_events(account_id=account.id) == []


def test_intraday_omitted_symbol_does_not_create_data_block(exit_env):
    _, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)

    result = engine.run_intraday_bars(
        "2026-07-23T10:10:00+08:00",
        {"000001": 10_000_000},
        observed_at="2026-07-23T10:11:00+08:00",
    )

    assert result.accounts[0].evaluated_positions == 0
    assert engine.list_states(account_id=account.id) == []
    assert engine.list_events(account_id=account.id) == []


def test_intraday_does_not_run_time_or_trend_rules(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, trade_date="2026-07-20")
    _plan(
        engine,
        account.id,
        time_stop_trading_days=1,
        time_stop_min_return_bps=10_000,
        trend_exit_enabled=True,
    )
    _qfq_series(
        db_path,
        "600519",
        "2026-07-23",
        [20 - index * 0.3 for index in range(40)],
    )

    engine.run_intraday_bars(
        "2026-07-23T10:15:00+08:00",
        {"600519": 5_000_000},
        observed_at="2026-07-23T10:16:00+08:00",
    )

    assert engine.list_events(account_id=account.id) == []


def test_intraday_take_profit_uses_cumulative_lifecycle_budget(exit_env):
    _, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, quantity=100)
    _plan(
        engine,
        account.id,
        take_profit_1_micros=11_000_000,
        take_profit_1_reduce_bps=4_000,
        take_profit_2_micros=12_000_000,
        take_profit_2_reduce_bps=4_000,
    )
    engine.run_intraday_bars(
        "2026-07-22T14:55:00+08:00",
        {"600519": 11_000_000},
        observed_at="2026-07-22T14:56:00+08:00",
    )
    _sell(
        accounting,
        account.id,
        trade_date="2026-07-23",
        quantity=40,
        request_id="intraday-execute-tp1",
    )

    engine.run_intraday_bars(
        "2026-07-23T09:35:00+08:00",
        {"600519": 12_000_000},
        observed_at="2026-07-23T09:36:00+08:00",
    )
    events = sorted(
        engine.list_events(account_id=account.id),
        key=lambda item: item.tier or 0,
    )

    assert [item.recommended_quantity for item in events] == [40, 40]
    assert all(
        json.loads(item.payload_json or "{}")["source"] == "intraday_5min"
        for item in events
    )


def test_intraday_trailing_high_is_monotonic_and_old_bar_is_skipped(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(
        engine,
        account.id,
        trailing_activation_bps=1_000,
        trailing_distance_bps=500,
    )
    _raw(db_path, "600519", "2026-07-22", 10)
    engine.run_daily("2026-07-22")
    engine.run_intraday_bars(
        "2026-07-23T10:00:00+08:00",
        {"600519": 12_000_000},
        observed_at="2026-07-23T10:01:00+08:00",
    )
    first = engine.list_states(account_id=account.id)[0]

    old = engine.run_intraday_bars(
        "2026-07-23T09:55:00+08:00",
        {"600519": 13_000_000},
        observed_at="2026-07-23T10:02:00+08:00",
    )
    state = engine.list_states(account_id=account.id)[0]

    assert old.accounts[0].skipped_positions == 1
    assert state.highest_price_micros == first.highest_price_micros == 12_000_000
    assert state.trailing_stop_micros == first.trailing_stop_micros == 11_400_000


@pytest.mark.parametrize(
    ("bar_time", "prices"),
    (
        ("2026-07-23T10:02:00+08:00", {"600519": 10_000_000}),
        ("2026-07-23T12:00:00+08:00", {"600519": 10_000_000}),
        ("2026-07-23T10:00:01+08:00", {"600519": 10_000_000}),
        ("2026-07-23T10:00:00+00:00", {"600519": 10_000_000}),
        ("2026-07-23T10:00:00+08:00", {"BAD": 10_000_000}),
        ("2026-07-23T10:00:00+08:00", {"600519": True}),
    ),
)
def test_intraday_rejects_invalid_completed_bar_inputs(
    exit_env,
    bar_time,
    prices,
):
    _, _, engine = exit_env
    with pytest.raises((InvalidExitPlanError, TypeError)):
        engine.run_intraday_bars(
            bar_time,
            prices,
            observed_at="2026-07-23T10:05:00+08:00",
        )


def test_intraday_rejects_future_bar_and_symbol_limit(exit_env):
    _, _, engine = exit_env
    with pytest.raises(InvalidExitPlanError, match="future"):
        engine.run_intraday_bars(
            "2026-07-23T10:00:00+08:00",
            {"600519": 10_000_000},
            observed_at="2026-07-23T09:59:59+08:00",
        )
    prices = {
        f"{index:06d}": 10_000_000
        for index in range(1, 52)
    }
    with pytest.raises(InvalidExitPlanError, match="50"):
        engine.run_intraday_bars(
            "2026-07-23T10:00:00+08:00",
            prices,
            observed_at="2026-07-23T10:01:00+08:00",
        )


def test_intraday_position_race_skips_stale_projection(
    exit_env,
    monkeypatch,
):
    _, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id)
    _plan(engine, account.id, hard_stop_micros=11_000_000)
    original = engine._load_intraday_plan_keys
    changed = False

    def sell_during_plan_read(conn, account_id, symbols):
        nonlocal changed
        keys = original(conn, account_id, symbols)
        if not changed:
            changed = True
            _sell(
                accounting,
                account.id,
                trade_date="2026-07-23",
                quantity=20,
                request_id="intraday-race-sell",
            )
        return keys

    monkeypatch.setattr(
        engine,
        "_load_intraday_plan_keys",
        sell_during_plan_read,
    )

    result = engine.run_intraday_bars(
        "2026-07-23T10:00:00+08:00",
        {"600519": 10_000_000},
        observed_at="2026-07-23T10:01:00+08:00",
    )

    assert result.created_event_count == 0
    assert result.accounts[0].skipped_positions == 1
    assert engine.list_events(account_id=account.id) == []


def _c_rows():
    return [dict(trade_date=(date(2026, 7, 1)+timedelta(days=i)).isoformat(),
                 open=20-max(0,i-30)*.2, high=20.1-max(0,i-30)*.2,
                 low=19.9-max(0,i-30)*.2, close=20-max(0,i-30)*.2, volume=100) for i in range(40)]


def test_c_exit_real_position_without_simulated_buy():
    from portfolio.structure_exit import evaluate_position_structure
    rows = _c_rows()
    result = evaluate_position_structure(rows, entry_date=rows[-3]['trade_date'])
    assert result['exit'] and result['weak_days'] == 3
    assert result['strategy'] == 'C' and result['stop_price'] is None


def test_c_exit_does_not_import_pre_entry_weak_days():
    from portfolio.structure_exit import evaluate_position_structure
    rows = _c_rows()
    result = evaluate_position_structure(rows, entry_date=rows[-1]['trade_date'])
    assert not result['exit'] and result['warning'] and result['weak_days'] == 1


def test_c_exit_rejects_invalid_ohlc():
    from portfolio.structure_exit import evaluate_position_structure
    rows = _c_rows()
    rows[-1]['close'] = float('nan')
    with pytest.raises(ValueError):
        evaluate_position_structure(rows, entry_date=rows[-3]['trade_date'])


def test_c_entry_requires_exact_price_day():
    from portfolio.structure_exit import evaluate_position_structure
    rows = _c_rows()
    entry = rows[-3]['trade_date']
    rows.pop(-3)
    result = evaluate_position_structure(rows, entry_date=entry)
    assert result['data_status'] == 'ENTRY_HISTORY_MISSING'
    assert not result['exit'] and result['stop_price'] is None


def test_c_real_structure_binds_only_at_actual_entry():
    from portfolio.structure_exit import evaluate_position_structure
    from quant_engine.research.structure_turning import evaluate_structure_turning
    closes = [30-i*.25 for i in range(30)] + [22,21,20,20.5,21,22,23,22,21.5,21,
                                           21.5,22,23,24,25,26,26,25,24,23,22,21,20]
    rows = [dict(trade_date=str(date(2026,1,1)+timedelta(days=i)), open=c,
                 high=c+.35, low=c-.35, close=c, volume=1000) for i,c in enumerate(closes)]
    entry = evaluate_structure_turning(rows[:45])['state']['entry_date']
    bound = evaluate_position_structure(rows, entry_date=entry)
    assert bound['stop_binding'] == 'BOUND_AT_ENTRY' and bound['stop_price'] > 0
    assert bound['exit']
    # A later candidate BUY must not retroactively bind a real earlier entry.
    unbound = evaluate_position_structure(rows, entry_date=rows[29]['trade_date'])
    assert unbound['stop_binding'] == 'UNBOUND' and unbound['stop_price'] is None


@pytest.mark.parametrize('warning_offset,entry_offset,expected', [
    (2, 4, True), (7, 10, False), (3, 2, False),
])
def test_c_top_divergence_window_and_real_entry_boundary(
    monkeypatch, warning_offset, entry_offset, expected,
):
    from portfolio import structure_exit
    rows = [dict(trade_date=str(date(2026,7,1)+timedelta(days=i)), open=10,
                 high=10.1, low=9.9, close=10, volume=100) for i in range(40)]
    rows[-1].update(open=9,high=9.1,low=8.9,close=9)
    # Freeze shared C oscillator evidence to isolate adapter window/lifecycle rules.
    def evidence(prices, lookback, gap):
        result = [dict(macd_hist=i*.01, top_hist_divergence=False,
                       price_breakdown=False) for i in range(len(prices))]
        result[-warning_offset]['top_hist_divergence'] = True
        result[-1].update(macd_hist=result[-2]['macd_hist']-.01, price_breakdown=True)
        return result
    monkeypatch.setattr(structure_exit, '_indicator_evidence', evidence)
    result = structure_exit.evaluate_position_structure(
        rows, entry_date=rows[-entry_offset]['trade_date'],
    )
    assert result['exit'] is expected
    assert result['weak_days'] == 1


def test_c_qfq_scale_does_not_compare_against_raw_account_cost(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting, account.id, price_micros=100_000_000)
    _raw(db_path,'600519','2026-07-23', 90)
    _qfq_series(db_path,'600519','2026-07-23',[10.0]*38+[9.5,9])
    first = engine.run_daily('2026-07-23')
    event = next(e for e in engine.list_events() if e.event_type == 'TREND_EXIT_TRIGGERED')
    assert json.loads(event.payload_json)['qfq_close'] == 9 and event.observed_price_micros == 90_000_000
    assert event.executable and event.recommended_quantity == 100
    assert engine.run_daily('2026-07-23').created_event_count == 0
    assert first.created_event_count == 1


def test_c_migration_resolves_only_legacy_trend_and_preserves_custom(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting,account.id)
    _plan(engine,account.id,hard_stop_micros=11_000_000,rule_version='portfolio-exit-v1')
    _raw(db_path,'600519','2026-07-23',10)
    engine.run_daily('2026-07-23')
    event = engine.list_events()[0]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        old_values = dict(conn.execute("SELECT * FROM portfolio_exit_events WHERE id=?",
                                       (event.id,)).fetchone())
        old_values.pop('id')
        old_values.update(event_type='TREND_EXIT_WATCH', severity='INFO', recommended_quantity=0,
                          executable=0, payload_json='{}', rule_version='portfolio-exit-v1',
                          dedupe_key='legacy-trend-test')
        keys = ','.join(old_values)
        placeholders = ','.join('?' for _ in old_values)
        old_id = conn.execute(f"INSERT INTO portfolio_exit_events ({keys}) "
                              f"VALUES ({placeholders})", tuple(old_values.values())).lastrowid
        conn.execute("UPDATE portfolio_exit_plans SET rule_version='portfolio-exit-v1'")
    engine.run_daily('2026-07-23')
    old = next(e for e in engine.list_events() if e.id == old_id)
    assert old.event_status == 'RESOLVED'
    assert engine.list_plans()[0].rule_version == 'portfolio-exit-c-v2'
    _plan(engine,account.id,rule_version='custom-risk-plan')
    engine.run_daily('2026-07-23')
    assert engine.list_plans()[0].rule_version == 'custom-risk-plan'


def test_c_stale_history_blocks_but_manual_stop_remains_executable(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting,account.id)
    _plan(engine,account.id,hard_stop_micros=11_000_000)
    _raw(db_path,'600519','2026-07-23',10)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM stock_daily_prices")
    first = engine.run_daily('2026-07-23')
    events = engine.list_events()
    assert any(e.event_type == 'HARD_STOP_TRIGGERED' and e.executable for e in events)
    assert any(e.event_type == 'EXIT_DATA_BLOCKED' and json.loads(e.payload_json)['strategy']=='C' for e in events)
    assert first.accounts[0].blocked_positions == 1
    assert engine.run_daily('2026-07-23').created_event_count == 0
    _qfq_series(db_path,'600519','2026-07-23',[10.0]*40)
    engine.run_daily('2026-07-23')
    assert next(e for e in engine.list_events() if e.event_type=='EXIT_DATA_BLOCKED').event_status == 'RESOLVED'


def test_c_continuous_exit_no_duplicate_and_recovery_resolves(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting,account.id)
    for day, prices in [('2026-07-23',[10.0]*38+[9.5,9]),
                        ('2026-07-24',[10.0]*38+[9.5,9,8.5]),
                        ('2026-07-25',[10.0]*38+[9.5,9,8.5,12]),
                        ('2026-07-27',[10.0]*38+[9.5,9,8.5,12,8.5,8])]:
        _raw(db_path,'600519',day,prices[-1])
        # Fixed dates preserve history prefix (these tests use a synthetic calendar).
        _qfq_series(db_path,'600519',day,prices)
        engine.run_daily(day)
        if day == '2026-07-24':
            assert len([e for e in engine.list_events() if e.event_type=='TREND_EXIT_TRIGGERED']) == 1
        if day == '2026-07-25':
            assert all(e.event_status=='RESOLVED' for e in engine.list_events()
                       if e.event_type=='TREND_EXIT_TRIGGERED')
    assert len([e for e in engine.list_events() if e.event_type=='TREND_EXIT_TRIGGERED']) == 2


def test_c_entry_day_warning_t_plus_one_and_next_day_exit(exit_env):
    db_path, accounting, engine = exit_env
    account = _account(accounting)
    _buy(accounting,account.id,trade_date='2026-07-23')
    _raw(db_path,'600519','2026-07-23',9.5)
    _qfq_series(db_path,'600519','2026-07-23',[10.0]*39+[9.5])
    engine.run_daily('2026-07-23')
    watch = next(e for e in engine.list_events() if e.event_type=='TREND_EXIT_WATCH')
    assert not watch.executable and watch.available_quantity == 0
    _raw(db_path,'600519','2026-07-24',9)
    _qfq_series(db_path,'600519','2026-07-24',[10.0]*39+[9.5,9])
    engine.run_daily('2026-07-24')
    trigger = next(e for e in engine.list_events() if e.event_type=='TREND_EXIT_TRIGGERED')
    assert trigger.executable and trigger.recommended_quantity == 100
    assert next(e for e in engine.list_events() if e.id == watch.id).event_status=='RESOLVED'


def test_c_adapter_resource_bar_limit():
    from portfolio.structure_exit import evaluate_position_structure
    with pytest.raises(ValueError,match='bounded bar limit'):
        evaluate_position_structure(_c_rows()*38,entry_date='2026-07-21')
