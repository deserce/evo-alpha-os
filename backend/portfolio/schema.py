"""Versioned SQLite schema for the portfolio ledger and rebuildable projections."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULT_BUSY_TIMEOUT_MS = 5_000
MAX_BUSY_TIMEOUT_MS = 60_000
SCHEMA_VERSION = 4
MAX_QUANTITY = 100_000_000
MAX_PRICE_MICROS = 10_000_000_000
MAX_METADATA_BYTES = 65_536
MAX_RETURN_PPB = 100_000_000_000

ACCOUNT_STATUSES = frozenset({"ACTIVE", "ARCHIVED"})
ACCOUNT_TYPES = frozenset({"CASH", "MARGIN", "SIMULATED"})
BUSINESS_TYPES = frozenset(
    {
        "BUY",
        "SELL",
        "DEPOSIT",
        "WITHDRAWAL",
        "DIVIDEND",
        "FEE",
        "TAX",
        "TRANSFER",
        "CORPORATE_ACTION",
        "REVERSAL",
    }
)
TRANSACTION_STATUSES = frozenset({"POSTED"})

PORTFOLIO_TABLES = (
    "portfolio_schema_migrations",
    "portfolio_accounts",
    "portfolio_transactions",
    "portfolio_positions",
    "portfolio_account_daily_snapshots",
    "portfolio_position_daily_snapshots",
    "portfolio_exit_plans",
    "portfolio_exit_states",
    "portfolio_exit_events",
)
PORTFOLIO_INDEXES = (
    "uq_portfolio_accounts_single_default",
    "idx_portfolio_accounts_status",
    "idx_portfolio_transactions_account_date",
    "idx_portfolio_transactions_symbol_date",
    "idx_portfolio_transactions_status_date",
    "idx_portfolio_transactions_counterparty_date",
    "uq_portfolio_transactions_reversal_of",
    "idx_portfolio_positions_account_status",
    "idx_portfolio_positions_status_updated",
    "idx_portfolio_account_snapshots_account_date",
    "idx_portfolio_account_snapshots_status",
    "idx_portfolio_position_snapshots_account_date",
    "idx_portfolio_position_snapshots_status",
    "idx_portfolio_position_snapshots_symbol_date",
    "idx_portfolio_exit_plans_enabled",
    "idx_portfolio_exit_states_status",
    "idx_portfolio_exit_events_account_open",
    "idx_portfolio_exit_events_symbol_date",
)
PORTFOLIO_TRIGGERS = (
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
)

_REQUIRED_ACCOUNT_COLUMNS = frozenset(
    {
        "id",
        "name",
        "broker_name",
        "account_type",
        "currency",
        "opening_cash_cents",
        "cost_method",
        "version",
        "status",
        "is_default",
        "created_at",
        "updated_at",
        "archived_at",
    }
)
_REQUIRED_TRANSACTION_COLUMNS = frozenset(
    {
        "id",
        "account_id",
        "counterparty_account_id",
        "client_request_id",
        "business_type",
        "status",
        "trade_date",
        "occurred_at",
        "symbol",
        "quantity",
        "price_micros",
        "gross_amount_cents",
        "cash_delta_cents",
        "fee_cents",
        "tax_cents",
        "target_position_bps",
        "source",
        "reversal_of",
        "note",
        "metadata_json",
        "created_at",
        "updated_at",
    }
)
_REQUIRED_MIGRATION_COLUMNS = frozenset({"version", "name", "applied_at"})
_REQUIRED_POSITION_COLUMNS = frozenset(
    {
        "account_id",
        "symbol",
        "position_revision",
        "quantity",
        "available_quantity",
        "cost_basis_cents",
        "avg_cost_micros",
        "realized_pnl_cents",
        "first_buy_date",
        "last_trade_date",
        "status",
        "last_transaction_id",
        "updated_at",
    }
)
_REQUIRED_ACCOUNT_SNAPSHOT_COLUMNS = frozenset(
    {
        "account_id",
        "snapshot_date",
        "ledger_revision",
        "cash_cents",
        "market_value_cents",
        "total_equity_cents",
        "net_external_flow_cents",
        "weighted_external_flow_micros",
        "pnl_cents",
        "return_ppb",
        "realized_pnl_cents",
        "unrealized_pnl_cents",
        "position_count",
        "unpriced_position_count",
        "price_status",
        "expected_price_date",
        "performance_status",
        "return_base_date",
        "created_at",
        "updated_at",
    }
)
_REQUIRED_POSITION_SNAPSHOT_COLUMNS = frozenset(
    {
        "account_id",
        "snapshot_date",
        "symbol",
        "position_revision",
        "quantity",
        "available_quantity",
        "cost_basis_cents",
        "realized_pnl_cents",
        "price_micros",
        "price_date",
        "market_value_cents",
        "unrealized_pnl_cents",
        "price_status",
        "last_transaction_id",
        "created_at",
        "updated_at",
    }
)
_REQUIRED_EXIT_PLAN_COLUMNS = frozenset(
    {
        "account_id",
        "symbol",
        "position_revision",
        "enabled",
        "entry_transaction_id",
        "hard_stop_micros",
        "take_profit_1_micros",
        "take_profit_1_reduce_bps",
        "take_profit_2_micros",
        "take_profit_2_reduce_bps",
        "take_profit_3_micros",
        "take_profit_3_reduce_bps",
        "trailing_activation_bps",
        "trailing_distance_bps",
        "time_stop_trading_days",
        "time_stop_min_return_bps",
        "trend_exit_enabled",
        "intraday_enabled",
        "rule_version",
        "created_at",
        "updated_at",
    }
)
_REQUIRED_EXIT_STATE_COLUMNS = frozenset(
    {
        "account_id",
        "symbol",
        "position_revision",
        "state",
        "highest_price_micros",
        "trailing_stop_micros",
        "last_price_micros",
        "last_price_date",
        "last_bar_time",
        "data_status",
        "last_transaction_id",
        "rule_version",
        "created_at",
        "updated_at",
    }
)
_REQUIRED_EXIT_EVENT_COLUMNS = frozenset(
    {
        "id",
        "account_id",
        "symbol",
        "position_revision",
        "event_type",
        "severity",
        "event_status",
        "trigger_date",
        "triggered_at",
        "bar_time",
        "observed_price_micros",
        "available_quantity",
        "recommended_quantity",
        "executable",
        "tier",
        "dedupe_key",
        "payload_json",
        "rule_version",
        "acknowledged_at",
        "resolved_at",
        "created_at",
        "updated_at",
    }
)

_MIGRATION_NAMES = {
    1: "create_accounts_and_transactions",
    2: "create_rebuildable_positions",
    3: "create_rebuildable_daily_snapshots",
    4: "create_portfolio_exit_alerts",
}

_CREATE_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_schema_migrations (
    version INTEGER PRIMARY KEY
        CHECK (typeof(version) = 'integer' AND version >= 1),
    name TEXT NOT NULL
        CHECK (length(trim(name)) > 0),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_ACCOUNTS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    broker_name TEXT,
    account_type TEXT NOT NULL DEFAULT 'CASH'
        CHECK (account_type IN ('CASH', 'MARGIN', 'SIMULATED')),
    currency TEXT NOT NULL DEFAULT 'CNY'
        CHECK (
            length(currency) = 3
            AND currency = upper(currency)
            AND currency NOT GLOB '*[^A-Z]*'
        ),
    opening_cash_cents INTEGER NOT NULL DEFAULT 0
        CHECK (
            typeof(opening_cash_cents) = 'integer'
            AND opening_cash_cents >= 0
        ),
    cost_method TEXT NOT NULL DEFAULT 'AVERAGE'
        CHECK (cost_method = 'AVERAGE'),
    version INTEGER NOT NULL DEFAULT 1
        CHECK (typeof(version) = 'integer' AND version >= 1),
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    is_default INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(is_default) = 'integer' AND is_default IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    CHECK (
        (status = 'ACTIVE' AND archived_at IS NULL)
        OR
        (status = 'ARCHIVED' AND archived_at IS NOT NULL)
    ),
    CHECK (status = 'ACTIVE' OR is_default = 0)
)
"""

_CREATE_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    counterparty_account_id INTEGER,
    client_request_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(client_request_id)) > 0),
    business_type TEXT NOT NULL
        CHECK (
            business_type IN (
                'BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL', 'DIVIDEND',
                'FEE', 'TAX', 'TRANSFER',
                'CORPORATE_ACTION', 'REVERSAL'
            )
        ),
    status TEXT NOT NULL DEFAULT 'POSTED'
        CHECK (status = 'POSTED'),
    trade_date TEXT NOT NULL
        CHECK (
            date(trade_date) IS NOT NULL
            AND trade_date = date(trade_date)
        ),
    occurred_at TEXT NOT NULL
        CHECK (
            length(trim(occurred_at)) > 0
            AND datetime(occurred_at) IS NOT NULL
        ),
    symbol TEXT,
    quantity INTEGER
        CHECK (
            quantity IS NULL
            OR (
                typeof(quantity) = 'integer'
                AND quantity BETWEEN 1 AND 100000000
            )
        ),
    price_micros INTEGER
        CHECK (
            price_micros IS NULL
            OR (
                typeof(price_micros) = 'integer'
                AND price_micros BETWEEN 1 AND 10000000000
            )
        ),
    gross_amount_cents INTEGER
        CHECK (
            gross_amount_cents IS NULL
            OR (
                typeof(gross_amount_cents) = 'integer'
                AND gross_amount_cents > 0
            )
        ),
    cash_delta_cents INTEGER NOT NULL
        CHECK (typeof(cash_delta_cents) = 'integer'),
    fee_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(fee_cents) = 'integer' AND fee_cents >= 0),
    tax_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(tax_cents) = 'integer' AND tax_cents >= 0),
    target_position_bps INTEGER
        CHECK (
            target_position_bps IS NULL
            OR (
                typeof(target_position_bps) = 'integer'
                AND target_position_bps BETWEEN 1 AND 10000
            )
        ),
    source TEXT NOT NULL DEFAULT 'MANUAL'
        CHECK (source IN ('MANUAL', 'IMPORT', 'SYSTEM', 'API')),
    reversal_of INTEGER,
    note TEXT,
    metadata_json TEXT
        CHECK (
            metadata_json IS NULL
            OR (
                typeof(metadata_json) = 'text'
                AND json_valid(metadata_json)
                AND length(CAST(metadata_json AS BLOB)) <= 65536
            )
        ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, account_id),
    FOREIGN KEY (account_id)
        REFERENCES portfolio_accounts(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    FOREIGN KEY (counterparty_account_id)
        REFERENCES portfolio_accounts(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    FOREIGN KEY (reversal_of, account_id)
        REFERENCES portfolio_transactions(id, account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (reversal_of IS NULL OR reversal_of <> id),
    CHECK (
        (
            business_type = 'TRANSFER'
            AND counterparty_account_id IS NOT NULL
            AND counterparty_account_id <> account_id
        )
        OR
        (
            business_type <> 'TRANSFER'
            AND counterparty_account_id IS NULL
        )
    ),
    CHECK (
        business_type IN ('BUY', 'SELL', 'DIVIDEND')
        OR (fee_cents = 0 AND tax_cents = 0)
    ),
    CHECK (
        business_type NOT IN ('BUY', 'SELL')
        OR gross_amount_cents = (
            (price_micros * quantity + 5000) / 10000
        )
    ),
    CHECK (
        (
            business_type = 'BUY'
            AND cash_delta_cents = -(gross_amount_cents + fee_cents + tax_cents)
        )
        OR
        (
            business_type = 'SELL'
            AND cash_delta_cents = gross_amount_cents - fee_cents - tax_cents
            AND cash_delta_cents > 0
        )
        OR
        (
            business_type = 'DEPOSIT'
            AND cash_delta_cents = gross_amount_cents
        )
        OR
        (
            business_type IN (
                'WITHDRAWAL', 'FEE', 'TAX'
            )
            AND cash_delta_cents = -gross_amount_cents
        )
        OR
        (
            business_type = 'DIVIDEND'
            AND cash_delta_cents = gross_amount_cents - fee_cents - tax_cents
            AND cash_delta_cents > 0
        )
        OR
        (
            business_type = 'TRANSFER'
            AND cash_delta_cents = 0
        )
        OR
        (
            business_type = 'CORPORATE_ACTION'
            AND cash_delta_cents = 0
        )
        OR business_type = 'REVERSAL'
    ),
    CHECK (
        (
            business_type IN ('BUY', 'SELL')
            AND symbol IS NOT NULL
            AND length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
            AND quantity IS NOT NULL
            AND price_micros IS NOT NULL
            AND gross_amount_cents IS NOT NULL
            AND reversal_of IS NULL
        )
        OR
        (
            business_type IN (
                'DEPOSIT', 'WITHDRAWAL', 'FEE', 'TAX',
                'TRANSFER'
            )
            AND symbol IS NULL
            AND quantity IS NULL
            AND price_micros IS NULL
            AND gross_amount_cents IS NOT NULL
            AND reversal_of IS NULL
        )
        OR
        (
            business_type = 'DIVIDEND'
            AND symbol IS NOT NULL
            AND length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
            AND quantity IS NULL
            AND price_micros IS NULL
            AND gross_amount_cents IS NOT NULL
            AND reversal_of IS NULL
        )
        OR
        (
            business_type = 'CORPORATE_ACTION'
            AND symbol IS NOT NULL
            AND length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
            AND quantity IS NOT NULL
            AND price_micros IS NULL
            AND reversal_of IS NULL
        )
        OR
        (
            business_type = 'REVERSAL'
            AND symbol IS NULL
            AND quantity IS NULL
            AND price_micros IS NULL
            AND gross_amount_cents IS NULL
            AND fee_cents = 0
            AND tax_cents = 0
            AND target_position_bps IS NULL
            AND reversal_of IS NOT NULL
        )
    )
)
"""

_CREATE_POSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL
        CHECK (
            length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
        ),
    position_revision INTEGER NOT NULL DEFAULT 1
        CHECK (
            typeof(position_revision) = 'integer'
            AND position_revision >= 1
        ),
    quantity INTEGER NOT NULL
        CHECK (
            typeof(quantity) = 'integer'
            AND quantity BETWEEN 0 AND 100000000
        ),
    available_quantity INTEGER NOT NULL
        CHECK (
            typeof(available_quantity) = 'integer'
            AND available_quantity BETWEEN 0 AND quantity
        ),
    cost_basis_cents INTEGER NOT NULL
        CHECK (
            typeof(cost_basis_cents) = 'integer'
            AND cost_basis_cents >= 0
        ),
    avg_cost_micros INTEGER NOT NULL
        CHECK (
            typeof(avg_cost_micros) = 'integer'
            AND avg_cost_micros >= 0
        ),
    realized_pnl_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(realized_pnl_cents) = 'integer'),
    first_buy_date TEXT NOT NULL
        CHECK (
            date(first_buy_date) IS NOT NULL
            AND first_buy_date = date(first_buy_date)
        ),
    last_trade_date TEXT NOT NULL
        CHECK (
            date(last_trade_date) IS NOT NULL
            AND last_trade_date = date(last_trade_date)
            AND date(last_trade_date) >= date(first_buy_date)
        ),
    status TEXT NOT NULL
        CHECK (status IN ('OPEN', 'CLOSED')),
    last_transaction_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol),
    FOREIGN KEY (account_id)
        REFERENCES portfolio_accounts(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    FOREIGN KEY (last_transaction_id, account_id)
        REFERENCES portfolio_transactions(id, account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (
        (
            status = 'OPEN'
            AND quantity > 0
        )
        OR
        (
            status = 'CLOSED'
            AND quantity = 0
            AND available_quantity = 0
            AND cost_basis_cents = 0
            AND avg_cost_micros = 0
        )
    )
)
"""

_CREATE_ACCOUNT_DAILY_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_account_daily_snapshots (
    account_id INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL
        CHECK (
            date(snapshot_date) IS NOT NULL
            AND snapshot_date = date(snapshot_date)
        ),
    ledger_revision INTEGER NOT NULL
        CHECK (
            typeof(ledger_revision) = 'integer'
            AND ledger_revision >= 0
        ),
    cash_cents INTEGER NOT NULL
        CHECK (typeof(cash_cents) = 'integer'),
    market_value_cents INTEGER
        CHECK (
            market_value_cents IS NULL
            OR (
                typeof(market_value_cents) = 'integer'
                AND market_value_cents >= 0
            )
        ),
    total_equity_cents INTEGER
        CHECK (
            total_equity_cents IS NULL
            OR typeof(total_equity_cents) = 'integer'
        ),
    net_external_flow_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(net_external_flow_cents) = 'integer'),
    weighted_external_flow_micros INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(weighted_external_flow_micros) = 'integer'),
    pnl_cents INTEGER
        CHECK (
            pnl_cents IS NULL
            OR typeof(pnl_cents) = 'integer'
        ),
    return_ppb INTEGER
        CHECK (
            return_ppb IS NULL
            OR (
                typeof(return_ppb) = 'integer'
                AND return_ppb BETWEEN -1000000000 AND 100000000000
            )
        ),
    realized_pnl_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(realized_pnl_cents) = 'integer'),
    unrealized_pnl_cents INTEGER
        CHECK (
            unrealized_pnl_cents IS NULL
            OR typeof(unrealized_pnl_cents) = 'integer'
        ),
    position_count INTEGER NOT NULL DEFAULT 0
        CHECK (
            typeof(position_count) = 'integer'
            AND position_count >= 0
        ),
    unpriced_position_count INTEGER NOT NULL DEFAULT 0
        CHECK (
            typeof(unpriced_position_count) = 'integer'
            AND unpriced_position_count BETWEEN 0 AND position_count
        ),
    price_status TEXT NOT NULL
        CHECK (price_status IN ('COMPLETE', 'STALE', 'PARTIAL')),
    expected_price_date TEXT NOT NULL
        CHECK (
            date(expected_price_date) IS NOT NULL
            AND expected_price_date = date(expected_price_date)
            AND expected_price_date = snapshot_date
        ),
    performance_status TEXT NOT NULL
        CHECK (
            performance_status IN (
                'INITIAL', 'COMPLETE', 'GAP', 'UNAVAILABLE'
            )
        ),
    return_base_date TEXT
        CHECK (
            return_base_date IS NULL
            OR (
                date(return_base_date) IS NOT NULL
                AND return_base_date = date(return_base_date)
            )
        ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(updated_at)) > 0),
    PRIMARY KEY (account_id, snapshot_date),
    FOREIGN KEY (account_id)
        REFERENCES portfolio_accounts(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (
        (
            price_status IN ('COMPLETE', 'STALE')
            AND unpriced_position_count = 0
            AND market_value_cents IS NOT NULL
            AND total_equity_cents IS NOT NULL
            AND total_equity_cents = cash_cents + market_value_cents
            AND unrealized_pnl_cents IS NOT NULL
        )
        OR
        (
            price_status = 'PARTIAL'
            AND unpriced_position_count > 0
            AND market_value_cents IS NULL
            AND total_equity_cents IS NULL
            AND unrealized_pnl_cents IS NULL
        )
    ),
    CHECK (
        (
            performance_status = 'INITIAL'
            AND price_status <> 'PARTIAL'
            AND pnl_cents = 0
            AND return_ppb = 0
            AND return_base_date IS NULL
        )
        OR
        (
            performance_status = 'COMPLETE'
            AND price_status <> 'PARTIAL'
            AND pnl_cents IS NOT NULL
            AND return_ppb IS NOT NULL
            AND return_base_date IS NOT NULL
            AND date(return_base_date) < date(snapshot_date)
        )
        OR
        (
            performance_status = 'GAP'
            AND price_status <> 'PARTIAL'
            AND pnl_cents IS NULL
            AND return_ppb IS NULL
            AND return_base_date IS NULL
        )
        OR
        (
            performance_status = 'UNAVAILABLE'
            AND price_status = 'PARTIAL'
            AND pnl_cents IS NULL
            AND return_ppb IS NULL
            AND return_base_date IS NULL
        )
    )
)
"""

_CREATE_POSITION_DAILY_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_position_daily_snapshots (
    account_id INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL
        CHECK (
            date(snapshot_date) IS NOT NULL
            AND snapshot_date = date(snapshot_date)
        ),
    symbol TEXT NOT NULL
        CHECK (
            length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
        ),
    position_revision INTEGER NOT NULL
        CHECK (
            typeof(position_revision) = 'integer'
            AND position_revision >= 1
        ),
    quantity INTEGER NOT NULL
        CHECK (
            typeof(quantity) = 'integer'
            AND quantity BETWEEN 0 AND 100000000
        ),
    available_quantity INTEGER NOT NULL
        CHECK (
            typeof(available_quantity) = 'integer'
            AND available_quantity BETWEEN 0 AND quantity
        ),
    cost_basis_cents INTEGER NOT NULL
        CHECK (
            typeof(cost_basis_cents) = 'integer'
            AND cost_basis_cents >= 0
        ),
    realized_pnl_cents INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(realized_pnl_cents) = 'integer'),
    price_micros INTEGER
        CHECK (
            price_micros IS NULL
            OR (
                typeof(price_micros) = 'integer'
                AND price_micros BETWEEN 1 AND 10000000000
            )
        ),
    price_date TEXT
        CHECK (
            price_date IS NULL
            OR (
                date(price_date) IS NOT NULL
                AND price_date = date(price_date)
                AND date(price_date) <= date(snapshot_date)
            )
        ),
    market_value_cents INTEGER
        CHECK (
            market_value_cents IS NULL
            OR (
                typeof(market_value_cents) = 'integer'
                AND market_value_cents >= 0
            )
        ),
    unrealized_pnl_cents INTEGER
        CHECK (
            unrealized_pnl_cents IS NULL
            OR typeof(unrealized_pnl_cents) = 'integer'
        ),
    price_status TEXT NOT NULL
        CHECK (price_status IN ('CURRENT', 'STALE', 'MISSING')),
    last_transaction_id INTEGER NOT NULL
        CHECK (typeof(last_transaction_id) = 'integer'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(updated_at)) > 0),
    PRIMARY KEY (account_id, snapshot_date, symbol),
    FOREIGN KEY (account_id)
        REFERENCES portfolio_accounts(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    FOREIGN KEY (last_transaction_id, account_id)
        REFERENCES portfolio_transactions(id, account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (
        (
            price_status = 'CURRENT'
            AND price_micros IS NOT NULL
            AND price_date = snapshot_date
            AND market_value_cents IS NOT NULL
            AND market_value_cents = (
                (price_micros * quantity + 5000) / 10000
            )
            AND unrealized_pnl_cents IS NOT NULL
            AND unrealized_pnl_cents = market_value_cents - cost_basis_cents
        )
        OR
        (
            price_status = 'STALE'
            AND price_micros IS NOT NULL
            AND date(price_date) < date(snapshot_date)
            AND market_value_cents IS NOT NULL
            AND market_value_cents = (
                (price_micros * quantity + 5000) / 10000
            )
            AND unrealized_pnl_cents IS NOT NULL
            AND unrealized_pnl_cents = market_value_cents - cost_basis_cents
        )
        OR
        (
            price_status = 'MISSING'
            AND price_micros IS NULL
            AND price_date IS NULL
            AND market_value_cents IS NULL
            AND unrealized_pnl_cents IS NULL
        )
    )
)
"""

_CREATE_EXIT_PLANS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_exit_plans (
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL
        CHECK (
            length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
        ),
    position_revision INTEGER NOT NULL
        CHECK (
            typeof(position_revision) = 'integer'
            AND position_revision >= 1
        ),
    enabled INTEGER NOT NULL DEFAULT 1
        CHECK (typeof(enabled) = 'integer' AND enabled IN (0, 1)),
    entry_transaction_id INTEGER NOT NULL
        CHECK (typeof(entry_transaction_id) = 'integer'),
    hard_stop_micros INTEGER
        CHECK (
            hard_stop_micros IS NULL
            OR (
                typeof(hard_stop_micros) = 'integer'
                AND hard_stop_micros BETWEEN 1 AND 10000000000
            )
        ),
    take_profit_1_micros INTEGER
        CHECK (
            take_profit_1_micros IS NULL
            OR (
                typeof(take_profit_1_micros) = 'integer'
                AND take_profit_1_micros BETWEEN 1 AND 10000000000
            )
        ),
    take_profit_1_reduce_bps INTEGER
        CHECK (
            take_profit_1_reduce_bps IS NULL
            OR (
                typeof(take_profit_1_reduce_bps) = 'integer'
                AND take_profit_1_reduce_bps BETWEEN 1 AND 10000
            )
        ),
    take_profit_2_micros INTEGER
        CHECK (
            take_profit_2_micros IS NULL
            OR (
                typeof(take_profit_2_micros) = 'integer'
                AND take_profit_2_micros BETWEEN 1 AND 10000000000
            )
        ),
    take_profit_2_reduce_bps INTEGER
        CHECK (
            take_profit_2_reduce_bps IS NULL
            OR (
                typeof(take_profit_2_reduce_bps) = 'integer'
                AND take_profit_2_reduce_bps BETWEEN 1 AND 10000
            )
        ),
    take_profit_3_micros INTEGER
        CHECK (
            take_profit_3_micros IS NULL
            OR (
                typeof(take_profit_3_micros) = 'integer'
                AND take_profit_3_micros BETWEEN 1 AND 10000000000
            )
        ),
    take_profit_3_reduce_bps INTEGER
        CHECK (
            take_profit_3_reduce_bps IS NULL
            OR (
                typeof(take_profit_3_reduce_bps) = 'integer'
                AND take_profit_3_reduce_bps BETWEEN 1 AND 10000
            )
        ),
    trailing_activation_bps INTEGER
        CHECK (
            trailing_activation_bps IS NULL
            OR (
                typeof(trailing_activation_bps) = 'integer'
                AND trailing_activation_bps BETWEEN 1 AND 100000
            )
        ),
    trailing_distance_bps INTEGER
        CHECK (
            trailing_distance_bps IS NULL
            OR (
                typeof(trailing_distance_bps) = 'integer'
                AND trailing_distance_bps BETWEEN 1 AND 10000
            )
        ),
    time_stop_trading_days INTEGER
        CHECK (
            time_stop_trading_days IS NULL
            OR (
                typeof(time_stop_trading_days) = 'integer'
                AND time_stop_trading_days BETWEEN 1 AND 10000
            )
        ),
    time_stop_min_return_bps INTEGER
        CHECK (
            time_stop_min_return_bps IS NULL
            OR (
                typeof(time_stop_min_return_bps) = 'integer'
                AND time_stop_min_return_bps BETWEEN -10000 AND 100000
            )
        ),
    trend_exit_enabled INTEGER NOT NULL DEFAULT 1
        CHECK (
            typeof(trend_exit_enabled) = 'integer'
            AND trend_exit_enabled IN (0, 1)
        ),
    intraday_enabled INTEGER NOT NULL DEFAULT 1
        CHECK (
            typeof(intraday_enabled) = 'integer'
            AND intraday_enabled IN (0, 1)
        ),
    rule_version TEXT NOT NULL
        CHECK (length(trim(rule_version)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(updated_at)) > 0),
    PRIMARY KEY (account_id, symbol, position_revision),
    FOREIGN KEY (account_id)
        REFERENCES portfolio_accounts(id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    FOREIGN KEY (entry_transaction_id, account_id)
        REFERENCES portfolio_transactions(id, account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (
        (take_profit_1_micros IS NULL)
        = (take_profit_1_reduce_bps IS NULL)
    ),
    CHECK (
        (take_profit_2_micros IS NULL)
        = (take_profit_2_reduce_bps IS NULL)
    ),
    CHECK (
        (take_profit_3_micros IS NULL)
        = (take_profit_3_reduce_bps IS NULL)
    ),
    CHECK (
        take_profit_2_micros IS NULL
        OR (
            take_profit_1_micros IS NOT NULL
            AND take_profit_2_micros > take_profit_1_micros
        )
    ),
    CHECK (
        take_profit_3_micros IS NULL
        OR (
            take_profit_2_micros IS NOT NULL
            AND take_profit_3_micros > take_profit_2_micros
        )
    ),
    CHECK (
        coalesce(take_profit_1_reduce_bps, 0)
        + coalesce(take_profit_2_reduce_bps, 0)
        + coalesce(take_profit_3_reduce_bps, 0) <= 10000
    ),
    CHECK (
        (trailing_activation_bps IS NULL)
        = (trailing_distance_bps IS NULL)
    ),
    CHECK (
        (time_stop_trading_days IS NULL)
        = (time_stop_min_return_bps IS NULL)
    )
)
"""

_CREATE_EXIT_STATES_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_exit_states (
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL
        CHECK (
            length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
        ),
    position_revision INTEGER NOT NULL
        CHECK (
            typeof(position_revision) = 'integer'
            AND position_revision >= 1
        ),
    state TEXT NOT NULL
        CHECK (
            state IN (
                'NORMAL', 'EXIT_WATCH', 'REDUCE_READY',
                'EXIT_READY', 'DATA_BLOCKED'
            )
        ),
    highest_price_micros INTEGER
        CHECK (
            highest_price_micros IS NULL
            OR (
                typeof(highest_price_micros) = 'integer'
                AND highest_price_micros BETWEEN 1 AND 10000000000
            )
        ),
    trailing_stop_micros INTEGER
        CHECK (
            trailing_stop_micros IS NULL
            OR (
                typeof(trailing_stop_micros) = 'integer'
                AND trailing_stop_micros BETWEEN 1 AND 10000000000
            )
        ),
    last_price_micros INTEGER
        CHECK (
            last_price_micros IS NULL
            OR (
                typeof(last_price_micros) = 'integer'
                AND last_price_micros BETWEEN 1 AND 10000000000
            )
        ),
    last_price_date TEXT
        CHECK (
            last_price_date IS NULL
            OR (
                date(last_price_date) IS NOT NULL
                AND last_price_date = date(last_price_date)
            )
        ),
    last_bar_time TEXT
        CHECK (
            last_bar_time IS NULL
            OR (
                length(trim(last_bar_time)) > 0
                AND datetime(last_bar_time) IS NOT NULL
            )
        ),
    data_status TEXT NOT NULL
        CHECK (data_status IN ('OK', 'STALE', 'MISSING')),
    last_transaction_id INTEGER NOT NULL
        CHECK (typeof(last_transaction_id) = 'integer'),
    rule_version TEXT NOT NULL
        CHECK (length(trim(rule_version)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(updated_at)) > 0),
    PRIMARY KEY (account_id, symbol, position_revision),
    FOREIGN KEY (account_id, symbol, position_revision)
        REFERENCES portfolio_exit_plans(
            account_id, symbol, position_revision
        )
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    FOREIGN KEY (last_transaction_id, account_id)
        REFERENCES portfolio_transactions(id, account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (
        trailing_stop_micros IS NULL
        OR (
            highest_price_micros IS NOT NULL
            AND trailing_stop_micros <= highest_price_micros
        )
    ),
    CHECK (
        (
            data_status IN ('OK', 'STALE')
            AND last_price_micros IS NOT NULL
            AND last_price_date IS NOT NULL
            AND last_bar_time IS NOT NULL
        )
        OR
        (
            data_status = 'MISSING'
            AND last_price_micros IS NULL
            AND last_price_date IS NULL
            AND last_bar_time IS NULL
        )
    ),
    CHECK (
        (
            state = 'DATA_BLOCKED'
            AND data_status IN ('STALE', 'MISSING')
        )
        OR
        (
            state <> 'DATA_BLOCKED'
            AND data_status = 'OK'
        )
    )
)
"""

_CREATE_EXIT_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_exit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL
        CHECK (
            length(symbol) = 6
            AND symbol NOT GLOB '*[^0-9]*'
        ),
    position_revision INTEGER NOT NULL
        CHECK (
            typeof(position_revision) = 'integer'
            AND position_revision >= 1
        ),
    event_type TEXT NOT NULL
        CHECK (
            event_type IN (
                'HARD_STOP_TRIGGERED', 'TAKE_PROFIT_TRIGGERED',
                'TRAILING_STOP_TRIGGERED', 'TREND_EXIT_WATCH',
                'TREND_EXIT_TRIGGERED', 'TIME_STOP_TRIGGERED',
                'PORTFOLIO_RISK', 'EXIT_DATA_BLOCKED',
                'EXIT_DATA_RECOVERED'
            )
        ),
    severity TEXT NOT NULL
        CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    event_status TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (event_status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
    trigger_date TEXT NOT NULL
        CHECK (
            date(trigger_date) IS NOT NULL
            AND trigger_date = date(trigger_date)
        ),
    triggered_at TEXT NOT NULL
        CHECK (
            length(trim(triggered_at)) > 0
            AND datetime(triggered_at) IS NOT NULL
            AND substr(triggered_at, 1, 10) = trigger_date
        ),
    bar_time TEXT
        CHECK (
            bar_time IS NULL
            OR (
                length(trim(bar_time)) > 0
                AND datetime(bar_time) IS NOT NULL
                AND substr(bar_time, 1, 10) = trigger_date
            )
        ),
    observed_price_micros INTEGER
        CHECK (
            observed_price_micros IS NULL
            OR (
                typeof(observed_price_micros) = 'integer'
                AND observed_price_micros BETWEEN 1 AND 10000000000
            )
        ),
    available_quantity INTEGER NOT NULL DEFAULT 0
        CHECK (
            typeof(available_quantity) = 'integer'
            AND available_quantity BETWEEN 0 AND 100000000
        ),
    recommended_quantity INTEGER NOT NULL DEFAULT 0
        CHECK (
            typeof(recommended_quantity) = 'integer'
            AND recommended_quantity BETWEEN 0 AND available_quantity
        ),
    executable INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(executable) = 'integer' AND executable IN (0, 1)),
    tier INTEGER
        CHECK (
            tier IS NULL
            OR (typeof(tier) = 'integer' AND tier BETWEEN 1 AND 3)
        ),
    dedupe_key TEXT NOT NULL UNIQUE
        CHECK (
            length(trim(dedupe_key)) > 0
            AND length(CAST(dedupe_key AS BLOB)) <= 512
        ),
    payload_json TEXT
        CHECK (
            payload_json IS NULL
            OR (
                typeof(payload_json) = 'text'
                AND json_valid(payload_json)
                AND length(CAST(payload_json AS BLOB)) <= 65536
            )
        ),
    rule_version TEXT NOT NULL
        CHECK (length(trim(rule_version)) > 0),
    acknowledged_at TEXT
        CHECK (
            acknowledged_at IS NULL
            OR (
                length(trim(acknowledged_at)) > 0
                AND datetime(acknowledged_at) IS NOT NULL
            )
        ),
    resolved_at TEXT
        CHECK (
            resolved_at IS NULL
            OR (
                length(trim(resolved_at)) > 0
                AND datetime(resolved_at) IS NOT NULL
            )
        ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (length(trim(updated_at)) > 0),
    FOREIGN KEY (account_id, symbol, position_revision)
        REFERENCES portfolio_exit_plans(
            account_id, symbol, position_revision
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CHECK (
        (
            event_type = 'TAKE_PROFIT_TRIGGERED'
            AND tier IS NOT NULL
        )
        OR
        (
            event_type <> 'TAKE_PROFIT_TRIGGERED'
            AND tier IS NULL
        )
    ),
    CHECK (
        executable = 0
        OR (
            recommended_quantity > 0
            AND observed_price_micros IS NOT NULL
        )
    ),
    CHECK (
        event_type NOT IN (
            'TREND_EXIT_WATCH', 'EXIT_DATA_BLOCKED', 'EXIT_DATA_RECOVERED'
        )
        OR (executable = 0 AND recommended_quantity = 0)
    ),
    CHECK (
        event_type NOT IN (
            'HARD_STOP_TRIGGERED', 'TAKE_PROFIT_TRIGGERED',
            'TRAILING_STOP_TRIGGERED', 'TREND_EXIT_TRIGGERED',
            'TIME_STOP_TRIGGERED'
        )
        OR observed_price_micros IS NOT NULL
    ),
    CHECK (
        (
            event_status = 'OPEN'
            AND acknowledged_at IS NULL
            AND resolved_at IS NULL
        )
        OR
        (
            event_status = 'ACKNOWLEDGED'
            AND acknowledged_at IS NOT NULL
            AND resolved_at IS NULL
        )
        OR
        (
            event_status = 'RESOLVED'
            AND resolved_at IS NOT NULL
        )
    ),
    CHECK (
        bar_time IS NULL
        OR datetime(bar_time) <= datetime(triggered_at)
    ),
    CHECK (
        acknowledged_at IS NULL
        OR datetime(acknowledged_at) >= datetime(triggered_at)
    ),
    CHECK (
        resolved_at IS NULL
        OR datetime(resolved_at) >= datetime(triggered_at)
    ),
    CHECK (
        acknowledged_at IS NULL
        OR resolved_at IS NULL
        OR datetime(resolved_at) >= datetime(acknowledged_at)
    )
)
"""

_CREATE_V1_INDEX_SQL = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_accounts_single_default
    ON portfolio_accounts (is_default)
    WHERE is_default = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_accounts_status
    ON portfolio_accounts (status, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_account_date
    ON portfolio_transactions (account_id, trade_date DESC, occurred_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_symbol_date
    ON portfolio_transactions (symbol, trade_date DESC, occurred_at DESC)
    WHERE symbol IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_status_date
    ON portfolio_transactions (status, trade_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_counterparty_date
    ON portfolio_transactions (
        counterparty_account_id, trade_date DESC, occurred_at DESC
    )
    WHERE counterparty_account_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_transactions_reversal_of
    ON portfolio_transactions (reversal_of)
    WHERE reversal_of IS NOT NULL
    """,
)

_CREATE_POSITION_INDEX_SQL = (
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_positions_account_status
    ON portfolio_positions (account_id, status, symbol)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_positions_status_updated
    ON portfolio_positions (status, updated_at DESC)
    """,
)

_CREATE_DAILY_SNAPSHOT_INDEX_SQL = (
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_account_snapshots_account_date
    ON portfolio_account_daily_snapshots (account_id, snapshot_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_account_snapshots_status
    ON portfolio_account_daily_snapshots (price_status, snapshot_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_position_snapshots_account_date
    ON portfolio_position_daily_snapshots (account_id, snapshot_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_position_snapshots_status
    ON portfolio_position_daily_snapshots (price_status, snapshot_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_position_snapshots_symbol_date
    ON portfolio_position_daily_snapshots (symbol, snapshot_date DESC)
    """,
)

_CREATE_EXIT_INDEX_SQL = (
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_exit_plans_enabled
    ON portfolio_exit_plans (account_id, enabled, symbol, position_revision)
    WHERE enabled = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_exit_states_status
    ON portfolio_exit_states (
        state, data_status, account_id, symbol, position_revision
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_exit_events_account_open
    ON portfolio_exit_events (
        account_id, severity, trigger_date DESC, triggered_at DESC
    )
    WHERE event_status = 'OPEN'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_portfolio_exit_events_symbol_date
    ON portfolio_exit_events (
        symbol, trigger_date DESC, triggered_at DESC
    )
    """,
)

_CREATE_POSITION_TRIGGER_SQL = (
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_positions_validate_checkpoint_insert
    BEFORE INSERT ON portfolio_positions
    FOR EACH ROW
    WHEN (
        NEW.status = 'OPEN'
        AND NEW.last_transaction_id IS NULL
    )
    OR (
        NEW.last_transaction_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM portfolio_transactions AS checkpoint
            WHERE checkpoint.id = NEW.last_transaction_id
              AND checkpoint.account_id = NEW.account_id
              AND checkpoint.symbol = NEW.symbol
              AND checkpoint.status = 'POSTED'
              AND checkpoint.business_type IN (
                  'BUY', 'SELL', 'CORPORATE_ACTION'
              )
        )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'invalid position checkpoint'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_positions_validate_checkpoint_update
    BEFORE UPDATE ON portfolio_positions
    FOR EACH ROW
    WHEN (
        NEW.status = 'OPEN'
        AND NEW.last_transaction_id IS NULL
    )
    OR (
        NEW.last_transaction_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM portfolio_transactions AS checkpoint
            WHERE checkpoint.id = NEW.last_transaction_id
              AND checkpoint.account_id = NEW.account_id
              AND checkpoint.symbol = NEW.symbol
              AND checkpoint.status = 'POSTED'
              AND checkpoint.business_type IN (
                  'BUY', 'SELL', 'CORPORATE_ACTION'
              )
        )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'invalid position checkpoint'
        );
    END
    """,
)

_CREATE_EXIT_TRIGGER_SQL = (
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_plans_validate_entry_insert
    BEFORE INSERT ON portfolio_exit_plans
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1
        FROM portfolio_transactions AS entry
        JOIN portfolio_positions AS position
          ON position.account_id = entry.account_id
         AND position.symbol = entry.symbol
         AND position.position_revision = NEW.position_revision
         AND position.status = 'OPEN'
        WHERE entry.id = NEW.entry_transaction_id
          AND entry.account_id = NEW.account_id
          AND entry.symbol = NEW.symbol
          AND entry.status = 'POSTED'
          AND entry.business_type = 'BUY'
          AND entry.trade_date = position.first_buy_date
          AND NOT EXISTS (
              SELECT 1
              FROM portfolio_transactions AS reversal
              WHERE reversal.account_id = entry.account_id
                AND reversal.business_type = 'REVERSAL'
                AND reversal.status = 'POSTED'
                AND reversal.reversal_of = entry.id
          )
    )
    BEGIN
        SELECT RAISE(ABORT, 'invalid exit plan entry');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_plans_validate_entry_update
    BEFORE UPDATE ON portfolio_exit_plans
    FOR EACH ROW
    WHEN (
        NOT EXISTS (
            SELECT 1
            FROM portfolio_transactions AS entry
            WHERE entry.id = NEW.entry_transaction_id
              AND entry.account_id = NEW.account_id
              AND entry.symbol = NEW.symbol
              AND entry.status = 'POSTED'
              AND entry.business_type = 'BUY'
        )
        OR (
            NEW.enabled = 1
            AND NOT EXISTS (
                SELECT 1
                FROM portfolio_transactions AS entry
                JOIN portfolio_positions AS position
                  ON position.account_id = entry.account_id
                 AND position.symbol = entry.symbol
                 AND position.position_revision = NEW.position_revision
                 AND position.status = 'OPEN'
                WHERE entry.id = NEW.entry_transaction_id
                  AND entry.account_id = NEW.account_id
                  AND entry.symbol = NEW.symbol
                  AND entry.status = 'POSTED'
                  AND entry.business_type = 'BUY'
                  AND entry.trade_date = position.first_buy_date
                  AND NOT EXISTS (
                      SELECT 1
                      FROM portfolio_transactions AS reversal
                      WHERE reversal.account_id = entry.account_id
                        AND reversal.business_type = 'REVERSAL'
                        AND reversal.status = 'POSTED'
                        AND reversal.reversal_of = entry.id
                  )
            )
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'invalid exit plan entry');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_plans_protect_identity
    BEFORE UPDATE ON portfolio_exit_plans
    FOR EACH ROW
    WHEN OLD.account_id IS NOT NEW.account_id
      OR OLD.symbol IS NOT NEW.symbol
      OR OLD.position_revision IS NOT NEW.position_revision
      OR OLD.entry_transaction_id IS NOT NEW.entry_transaction_id
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit plan identity and history are immutable'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_plans_protect_event_history
    BEFORE DELETE ON portfolio_exit_plans
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1
        FROM portfolio_exit_events AS event
        WHERE event.account_id = OLD.account_id
          AND event.symbol = OLD.symbol
          AND event.position_revision = OLD.position_revision
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit plan with event history cannot be deleted'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_states_validate_checkpoint_insert
    BEFORE INSERT ON portfolio_exit_states
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1
        FROM portfolio_positions AS position
        JOIN portfolio_transactions AS checkpoint
          ON checkpoint.id = position.last_transaction_id
         AND checkpoint.account_id = position.account_id
        WHERE position.account_id = NEW.account_id
          AND position.symbol = NEW.symbol
          AND position.position_revision = NEW.position_revision
          AND position.status = 'OPEN'
          AND position.last_transaction_id = NEW.last_transaction_id
          AND checkpoint.symbol = NEW.symbol
          AND checkpoint.status = 'POSTED'
          AND checkpoint.business_type IN (
              'BUY', 'SELL', 'CORPORATE_ACTION'
          )
    )
    BEGIN
        SELECT RAISE(ABORT, 'invalid exit state checkpoint');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_states_validate_checkpoint_update
    BEFORE UPDATE ON portfolio_exit_states
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1
        FROM portfolio_positions AS position
        JOIN portfolio_transactions AS checkpoint
          ON checkpoint.id = position.last_transaction_id
         AND checkpoint.account_id = position.account_id
        WHERE position.account_id = NEW.account_id
          AND position.symbol = NEW.symbol
          AND position.position_revision = NEW.position_revision
          AND position.status IN ('OPEN', 'CLOSED')
          AND position.last_transaction_id = NEW.last_transaction_id
          AND checkpoint.symbol = NEW.symbol
          AND checkpoint.status = 'POSTED'
          AND checkpoint.business_type IN (
              'BUY', 'SELL', 'CORPORATE_ACTION'
          )
    )
    BEGIN
        SELECT RAISE(ABORT, 'invalid exit state checkpoint');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_states_protect_identity
    BEFORE UPDATE ON portfolio_exit_states
    FOR EACH ROW
    WHEN OLD.account_id IS NOT NEW.account_id
      OR OLD.symbol IS NOT NEW.symbol
      OR OLD.position_revision IS NOT NEW.position_revision
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit state identity is immutable'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_events_validate_insert_lifecycle
    BEFORE INSERT ON portfolio_exit_events
    FOR EACH ROW
    WHEN NEW.event_status <> 'OPEN'
      OR NEW.acknowledged_at IS NOT NULL
      OR NEW.resolved_at IS NOT NULL
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit event must start OPEN'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_events_require_enabled_plan
    BEFORE INSERT ON portfolio_exit_events
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1
        FROM portfolio_exit_plans AS plan
        JOIN portfolio_positions AS position
          ON position.account_id = plan.account_id
         AND position.symbol = plan.symbol
         AND position.position_revision = plan.position_revision
         AND position.status = 'OPEN'
        JOIN portfolio_transactions AS entry
          ON entry.id = plan.entry_transaction_id
         AND entry.account_id = plan.account_id
         AND entry.symbol = plan.symbol
         AND entry.status = 'POSTED'
         AND entry.business_type = 'BUY'
        WHERE plan.account_id = NEW.account_id
          AND plan.symbol = NEW.symbol
          AND plan.position_revision = NEW.position_revision
          AND plan.enabled = 1
          AND entry.trade_date = position.first_buy_date
          AND NOT EXISTS (
              SELECT 1
              FROM portfolio_transactions AS reversal
              WHERE reversal.account_id = entry.account_id
                AND reversal.business_type = 'REVERSAL'
                AND reversal.status = 'POSTED'
                AND reversal.reversal_of = entry.id
          )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit event requires an enabled open unreversed exit plan'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_exit_events_no_delete
    BEFORE DELETE ON portfolio_exit_events
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit events cannot be deleted'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_exit_events_protect_facts
    BEFORE UPDATE ON portfolio_exit_events
    FOR EACH ROW
    WHEN OLD.id IS NOT NEW.id
      OR OLD.account_id IS NOT NEW.account_id
      OR OLD.symbol IS NOT NEW.symbol
      OR OLD.position_revision IS NOT NEW.position_revision
      OR OLD.event_type IS NOT NEW.event_type
      OR OLD.severity IS NOT NEW.severity
      OR OLD.trigger_date IS NOT NEW.trigger_date
      OR OLD.triggered_at IS NOT NEW.triggered_at
      OR OLD.bar_time IS NOT NEW.bar_time
      OR OLD.observed_price_micros IS NOT NEW.observed_price_micros
      OR OLD.available_quantity IS NOT NEW.available_quantity
      OR OLD.recommended_quantity IS NOT NEW.recommended_quantity
      OR OLD.executable IS NOT NEW.executable
      OR OLD.tier IS NOT NEW.tier
      OR OLD.dedupe_key IS NOT NEW.dedupe_key
      OR OLD.payload_json IS NOT NEW.payload_json
      OR OLD.rule_version IS NOT NEW.rule_version
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio exit event trigger facts are immutable'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
        trg_portfolio_exit_events_validate_status_transition
    BEFORE UPDATE ON portfolio_exit_events
    FOR EACH ROW
    WHEN NOT (
        (
            OLD.event_status = NEW.event_status
            AND OLD.acknowledged_at IS NEW.acknowledged_at
            AND OLD.resolved_at IS NEW.resolved_at
        )
        OR (
            OLD.event_status = 'OPEN'
            AND NEW.event_status = 'ACKNOWLEDGED'
            AND OLD.acknowledged_at IS NULL
            AND NEW.acknowledged_at IS NOT NULL
            AND OLD.resolved_at IS NULL
            AND NEW.resolved_at IS NULL
        )
        OR (
            OLD.event_status = 'OPEN'
            AND NEW.event_status = 'RESOLVED'
            AND OLD.acknowledged_at IS NEW.acknowledged_at
            AND OLD.resolved_at IS NULL
            AND NEW.resolved_at IS NOT NULL
        )
        OR (
            OLD.event_status = 'ACKNOWLEDGED'
            AND NEW.event_status = 'RESOLVED'
            AND OLD.acknowledged_at IS NEW.acknowledged_at
            AND OLD.resolved_at IS NULL
            AND NEW.resolved_at IS NOT NULL
        )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'invalid portfolio exit event status transition'
        );
    END
    """,
)

_EXIT_OBJECT_TABLES = {
    "portfolio_exit_plans": ("table", "portfolio_exit_plans"),
    "portfolio_exit_states": ("table", "portfolio_exit_states"),
    "portfolio_exit_events": ("table", "portfolio_exit_events"),
    "idx_portfolio_exit_plans_enabled": ("index", "portfolio_exit_plans"),
    "idx_portfolio_exit_states_status": ("index", "portfolio_exit_states"),
    "idx_portfolio_exit_events_account_open": (
        "index",
        "portfolio_exit_events",
    ),
    "idx_portfolio_exit_events_symbol_date": (
        "index",
        "portfolio_exit_events",
    ),
    "trg_portfolio_exit_plans_validate_entry_insert": (
        "trigger",
        "portfolio_exit_plans",
    ),
    "trg_portfolio_exit_plans_validate_entry_update": (
        "trigger",
        "portfolio_exit_plans",
    ),
    "trg_portfolio_exit_plans_protect_identity": (
        "trigger",
        "portfolio_exit_plans",
    ),
    "trg_portfolio_exit_plans_protect_event_history": (
        "trigger",
        "portfolio_exit_plans",
    ),
    "trg_portfolio_exit_states_validate_checkpoint_insert": (
        "trigger",
        "portfolio_exit_states",
    ),
    "trg_portfolio_exit_states_validate_checkpoint_update": (
        "trigger",
        "portfolio_exit_states",
    ),
    "trg_portfolio_exit_states_protect_identity": (
        "trigger",
        "portfolio_exit_states",
    ),
    "trg_portfolio_exit_events_validate_insert_lifecycle": (
        "trigger",
        "portfolio_exit_events",
    ),
    "trg_portfolio_exit_events_require_enabled_plan": (
        "trigger",
        "portfolio_exit_events",
    ),
    "trg_portfolio_exit_events_no_delete": (
        "trigger",
        "portfolio_exit_events",
    ),
    "trg_portfolio_exit_events_protect_facts": (
        "trigger",
        "portfolio_exit_events",
    ),
    "trg_portfolio_exit_events_validate_status_transition": (
        "trigger",
        "portfolio_exit_events",
    ),
}

_CREATE_MIGRATION_TRIGGER_SQL = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_schema_migrations_no_update
    BEFORE UPDATE ON portfolio_schema_migrations
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio_schema_migrations is append-only'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_schema_migrations_no_delete
    BEFORE DELETE ON portfolio_schema_migrations
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio_schema_migrations is append-only'
        );
    END
    """,
)

# Kept as a compatibility alias for v1 tests and callers that build legacy fixtures.
_CREATE_INDEX_SQL = _CREATE_V1_INDEX_SQL

_CREATE_TRIGGER_SQL = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_transactions_no_update
    BEFORE UPDATE ON portfolio_transactions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio_transactions is append-only; insert a reversal instead'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_transactions_no_delete
    BEFORE DELETE ON portfolio_transactions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio_transactions is append-only; insert a reversal instead'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_transactions_validate_reversal
    BEFORE INSERT ON portfolio_transactions
    FOR EACH ROW
    WHEN NEW.business_type = 'REVERSAL'
    BEGIN
        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM portfolio_transactions AS original
                WHERE original.id = NEW.reversal_of
                  AND original.account_id = NEW.account_id
                  AND original.business_type <> 'REVERSAL'
                  AND original.status = 'POSTED'
                  AND NEW.cash_delta_cents = -original.cash_delta_cents
                  AND date(NEW.trade_date) >= date(original.trade_date)
            )
            THEN RAISE(
                ABORT,
                'invalid reversal: target, cash delta, account, or date mismatch'
            )
        END;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_transactions_validate_transfer
    BEFORE INSERT ON portfolio_transactions
    FOR EACH ROW
    WHEN NEW.business_type = 'TRANSFER'
    BEGIN
        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1
                FROM portfolio_accounts AS source_account
                JOIN portfolio_accounts AS counterparty
                  ON counterparty.id = NEW.counterparty_account_id
                WHERE source_account.id = NEW.account_id
                  AND source_account.id <> counterparty.id
                  AND source_account.currency = counterparty.currency
            )
            THEN RAISE(
                ABORT,
                'invalid transfer: accounts must exist and use the same currency'
            )
        END;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_accounts_no_delete
    BEFORE DELETE ON portfolio_accounts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio accounts cannot be deleted; archive the account instead'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_portfolio_accounts_protect_history
    BEFORE UPDATE ON portfolio_accounts
    FOR EACH ROW
    WHEN OLD.id IS NOT NEW.id
      OR OLD.account_type IS NOT NEW.account_type
      OR OLD.currency IS NOT NEW.currency
      OR OLD.opening_cash_cents IS NOT NEW.opening_cash_cents
      OR OLD.cost_method IS NOT NEW.cost_method
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(
            ABORT,
            'portfolio account historical terms cannot be changed'
        );
    END
    """,
)


class PortfolioSchemaError(RuntimeError):
    """Raised when existing portfolio objects are incompatible with this schema."""


def _validated_busy_timeout_ms(busy_timeout_ms: int) -> int:
    if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
        raise TypeError("busy_timeout_ms must be an integer")
    if not 0 <= busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS:
        raise ValueError(
            f"busy_timeout_ms must be between 0 and {MAX_BUSY_TIMEOUT_MS}"
        )
    return busy_timeout_ms


def configure_portfolio_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> None:
    """Enable the connection-local safety settings required by this ledger."""
    timeout = _validated_busy_timeout_ms(busy_timeout_ms)
    if conn.in_transaction:
        raise RuntimeError(
            "configure_portfolio_connection must run before a transaction starts"
        )

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {timeout}")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign key enforcement could not be enabled")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def validate_existing_portfolio_schema(conn: sqlite3.Connection) -> None:
    """Reject malformed portfolio tables before creating migration metadata."""
    required_by_table = {
        "portfolio_schema_migrations": _REQUIRED_MIGRATION_COLUMNS,
        "portfolio_accounts": _REQUIRED_ACCOUNT_COLUMNS,
        "portfolio_transactions": _REQUIRED_TRANSACTION_COLUMNS,
        "portfolio_positions": _REQUIRED_POSITION_COLUMNS,
        "portfolio_account_daily_snapshots": (
            _REQUIRED_ACCOUNT_SNAPSHOT_COLUMNS
        ),
        "portfolio_position_daily_snapshots": (
            _REQUIRED_POSITION_SNAPSHOT_COLUMNS
        ),
        "portfolio_exit_plans": _REQUIRED_EXIT_PLAN_COLUMNS,
        "portfolio_exit_states": _REQUIRED_EXIT_STATE_COLUMNS,
        "portfolio_exit_events": _REQUIRED_EXIT_EVENT_COLUMNS,
    }
    for table_name, required_columns in required_by_table.items():
        if not _table_exists(conn, table_name):
            continue
        missing = sorted(required_columns - _table_columns(conn, table_name))
        if missing:
            joined = ", ".join(missing)
            raise PortfolioSchemaError(
                f"{table_name} has incompatible schema; "
                f"missing required columns: {joined}"
            )


def create_portfolio_tables(conn: sqlite3.Connection) -> None:
    """Create the v1 ledger objects; migration metadata is handled separately."""
    conn.execute(_CREATE_ACCOUNTS_SQL)
    conn.execute(_CREATE_TRANSACTIONS_SQL)
    for statement in _CREATE_V1_INDEX_SQL:
        conn.execute(statement)
    for statement in _CREATE_TRIGGER_SQL:
        conn.execute(statement)


def _create_positions(conn: sqlite3.Connection) -> None:
    """Create the disposable current-position projection and query indexes."""
    conn.execute(_CREATE_POSITIONS_SQL)
    for statement in _CREATE_POSITION_INDEX_SQL:
        conn.execute(statement)
    for statement in _CREATE_POSITION_TRIGGER_SQL:
        conn.execute(statement)


def _create_daily_snapshots(conn: sqlite3.Connection) -> None:
    """Create rebuildable daily account/position snapshots and query indexes."""
    conn.execute(_CREATE_ACCOUNT_DAILY_SNAPSHOTS_SQL)
    conn.execute(_CREATE_POSITION_DAILY_SNAPSHOTS_SQL)
    for statement in _CREATE_DAILY_SNAPSHOT_INDEX_SQL:
        conn.execute(statement)


def _create_exit_alerts(conn: sqlite3.Connection) -> None:
    """Create independent position-exit plans, state and event history."""
    conn.execute(_CREATE_EXIT_PLANS_SQL)
    conn.execute(_CREATE_EXIT_STATES_SQL)
    conn.execute(_CREATE_EXIT_EVENTS_SQL)
    for statement in _CREATE_EXIT_INDEX_SQL:
        conn.execute(statement)
    for statement in _CREATE_EXIT_TRIGGER_SQL:
        conn.execute(statement)
    _validate_exit_schema_objects(conn)


def _normalized_schema_sql(sql: str) -> str:
    normalized = " ".join(sql.strip().rstrip(";").lower().split())
    return normalized.replace(" if not exists ", " ")


def _expected_exit_schema_sql() -> dict[str, str]:
    statements_by_type = {
        "table": (
            _CREATE_EXIT_PLANS_SQL,
            _CREATE_EXIT_STATES_SQL,
            _CREATE_EXIT_EVENTS_SQL,
        ),
        "index": _CREATE_EXIT_INDEX_SQL,
        "trigger": _CREATE_EXIT_TRIGGER_SQL,
    }
    expected: dict[str, str] = {}
    for object_type, statements in statements_by_type.items():
        names = [
            name
            for name, definition in _EXIT_OBJECT_TABLES.items()
            if definition[0] == object_type
        ]
        if len(names) != len(statements):
            raise RuntimeError(
                f"portfolio exit {object_type} definition list is inconsistent"
            )
        expected.update(
            {
                name: _normalized_schema_sql(statement)
                for name, statement in zip(names, statements, strict=True)
            }
        )
    return expected


def _validate_exit_schema_objects(conn: sqlite3.Connection) -> None:
    """Reject missing, renamed, or weakened SQLite v4 schema objects."""
    expected_sql = _expected_exit_schema_sql()
    for object_name, expected in _EXIT_OBJECT_TABLES.items():
        row = conn.execute(
            """
            SELECT type, tbl_name, sql
            FROM sqlite_master
            WHERE name = ?
            LIMIT 1
            """,
            (object_name,),
        ).fetchone()
        actual = None if row is None else (str(row[0]), str(row[1]))
        actual_sql = None if row is None or row[2] is None else str(row[2])
        if (
            actual != expected
            or actual_sql is None
            or _normalized_schema_sql(actual_sql) != expected_sql[object_name]
        ):
            raise PortfolioSchemaError(
                "incompatible portfolio exit schema object "
                f"{object_name}: expected {expected}, found {actual}"
            )


def _reject_unrecorded_v4_objects(conn: sqlite3.Connection) -> None:
    placeholders = ", ".join("?" for _ in _EXIT_OBJECT_TABLES)
    row = conn.execute(
        f"""
        SELECT type, name
        FROM sqlite_master
        WHERE name IN ({placeholders})
        ORDER BY name
        LIMIT 1
        """,
        tuple(_EXIT_OBJECT_TABLES),
    ).fetchone()
    if row is not None:
        raise PortfolioSchemaError(
            "unrecorded portfolio v4 object collision: "
            f"{row[0]} {row[1]}"
        )


def _create_migration_triggers(conn: sqlite3.Connection) -> None:
    for statement in _CREATE_MIGRATION_TRIGGER_SQL:
        conn.execute(statement)


def _record_migration(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_schema_migrations (version, name)
        VALUES (?, ?)
        """,
        (version, _MIGRATION_NAMES[version]),
    )


def _read_recorded_versions(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT version, name
        FROM portfolio_schema_migrations
        ORDER BY version
        """
    ).fetchall()
    versions = [int(row[0]) for row in rows]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise PortfolioSchemaError(
            "portfolio_schema_migrations must contain contiguous versions "
            f"starting at 1; found {versions}"
        )
    if versions and versions[-1] > SCHEMA_VERSION:
        raise PortfolioSchemaError(
            "database portfolio schema is newer than this application: "
            f"{versions[-1]} > {SCHEMA_VERSION}"
        )
    for version, name in rows:
        expected_name = _MIGRATION_NAMES[int(version)]
        if name != expected_name:
            raise PortfolioSchemaError(
                f"migration v{version} name mismatch: "
                f"expected {expected_name!r}, found {name!r}"
            )
    return versions


def _infer_untracked_schema_version(conn: sqlite3.Connection) -> int:
    """Identify the deployed v1 layout when migration metadata is absent."""
    has_accounts = _table_exists(conn, "portfolio_accounts")
    has_transactions = _table_exists(conn, "portfolio_transactions")
    has_positions = _table_exists(conn, "portfolio_positions")
    has_account_snapshots = _table_exists(
        conn,
        "portfolio_account_daily_snapshots",
    )
    has_position_snapshots = _table_exists(
        conn,
        "portfolio_position_daily_snapshots",
    )
    exit_tables = (
        "portfolio_exit_plans",
        "portfolio_exit_states",
        "portfolio_exit_events",
    )
    existing_exit_tables = tuple(
        table_name
        for table_name in exit_tables
        if _table_exists(conn, table_name)
    )

    if has_accounts != has_transactions:
        raise PortfolioSchemaError(
            "partial portfolio v1 schema: portfolio_accounts and "
            "portfolio_transactions must both exist"
        )
    if has_positions and not has_accounts:
        raise PortfolioSchemaError(
            "portfolio_positions exists without the v1 ledger tables"
        )
    if has_account_snapshots != has_position_snapshots:
        raise PortfolioSchemaError(
            "partial portfolio v3 schema: account and position daily "
            "snapshot tables must both exist"
        )
    if has_account_snapshots and not has_positions:
        raise PortfolioSchemaError(
            "portfolio daily snapshots exist without the v2 positions table"
        )
    if existing_exit_tables and len(existing_exit_tables) != len(exit_tables):
        raise PortfolioSchemaError(
            "partial portfolio v4 schema: exit plan, state, and event "
            "tables must all exist"
        )
    if existing_exit_tables and not has_account_snapshots:
        raise PortfolioSchemaError(
            "portfolio exit alerts exist without the v3 daily snapshots"
        )
    if existing_exit_tables:
        return 4
    if has_account_snapshots:
        return 3
    if has_positions:
        return 2
    if has_accounts:
        return 1
    return 0


def _bootstrap_migration_history(
    conn: sqlite3.Connection,
    inferred_version: int,
) -> list[int]:
    conn.execute(_CREATE_MIGRATIONS_SQL)
    _create_migration_triggers(conn)
    for version in range(1, inferred_version + 1):
        _record_migration(conn, version)
    return list(range(1, inferred_version + 1))


def _validate_recorded_schema_objects(
    conn: sqlite3.Connection,
    versions: list[int],
) -> None:
    if 1 in versions:
        for table_name in ("portfolio_accounts", "portfolio_transactions"):
            if not _table_exists(conn, table_name):
                raise PortfolioSchemaError(
                    f"migration v1 is recorded but {table_name} is missing"
                )
    if 2 in versions and not _table_exists(conn, "portfolio_positions"):
        raise PortfolioSchemaError(
            "migration v2 is recorded but portfolio_positions is missing"
        )
    if 3 in versions:
        for table_name in (
            "portfolio_account_daily_snapshots",
            "portfolio_position_daily_snapshots",
        ):
            if not _table_exists(conn, table_name):
                raise PortfolioSchemaError(
                    f"migration v3 is recorded but {table_name} is missing"
                )
    if 4 in versions:
        for table_name in (
            "portfolio_exit_plans",
            "portfolio_exit_states",
            "portfolio_exit_events",
        ):
            if not _table_exists(conn, table_name):
                raise PortfolioSchemaError(
                    f"migration v4 is recorded but {table_name} is missing"
                )


def ensure_portfolio_schema(
    db_path: str | Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict[str, object]:
    """Upgrade the dedicated portfolio schema atomically and idempotently."""
    timeout = _validated_busy_timeout_ms(busy_timeout_ms)
    db = Path(db_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)

    with closing(
        sqlite3.connect(
            db,
            timeout=timeout / 1_000,
        )
    ) as conn:
        configure_portfolio_connection(conn, busy_timeout_ms=timeout)
        conn.execute("BEGIN IMMEDIATE")
        try:
            validate_existing_portfolio_schema(conn)
            if _table_exists(conn, "portfolio_schema_migrations"):
                versions = _read_recorded_versions(conn)
                _validate_recorded_schema_objects(conn, versions)
                _create_migration_triggers(conn)
            else:
                inferred_version = _infer_untracked_schema_version(conn)
                versions = _bootstrap_migration_history(conn, inferred_version)

            if not versions or versions[-1] < 4:
                _reject_unrecorded_v4_objects(conn)

            current_version = versions[-1] if versions else 0
            if current_version < 1:
                create_portfolio_tables(conn)
                _record_migration(conn, 1)
                current_version = 1
            else:
                # Recreate harmless missing v1 indexes/triggers for deployed v1.
                create_portfolio_tables(conn)

            if current_version < 2:
                _create_positions(conn)
                _record_migration(conn, 2)
                current_version = 2
            else:
                _create_positions(conn)

            if current_version < 3:
                _create_daily_snapshots(conn)
                _record_migration(conn, 3)
                current_version = 3
            else:
                _create_daily_snapshots(conn)

            if current_version < 4:
                _create_exit_alerts(conn)
                _record_migration(conn, 4)
                current_version = 4
            else:
                _create_exit_alerts(conn)

            validate_existing_portfolio_schema(conn)
            recorded_versions = _read_recorded_versions(conn)
            _validate_recorded_schema_objects(conn, recorded_versions)
            applied_version = recorded_versions[-1]
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "schema_version": applied_version,
        "db_path": str(db),
        "tables": list(PORTFOLIO_TABLES),
        "indexes": list(PORTFOLIO_INDEXES),
        "triggers": list(PORTFOLIO_TRIGGERS),
        "busy_timeout_ms": timeout,
        "foreign_keys": True,
    }
