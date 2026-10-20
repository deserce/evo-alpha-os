"""Small, transactional accounting core for portfolio accounts and A-share trades."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio.schema import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    MAX_PRICE_MICROS,
    MAX_QUANTITY,
    configure_portfolio_connection,
)


class PortfolioAccountingError(RuntimeError):
    """Base error for portfolio accounting operations."""


class InvalidTradeError(PortfolioAccountingError):
    """Raised when a trade request is malformed or cannot produce shares."""


class AccountNotFoundError(PortfolioAccountingError):
    """Raised when an account does not exist."""


class ArchivedAccountError(PortfolioAccountingError):
    """Raised when a write is attempted against an archived account."""


class IdempotencyConflictError(PortfolioAccountingError):
    """Raised when a request id is reused with a different payload."""


class StaleTradePreviewError(PortfolioAccountingError):
    """Raised when a confirmed trade no longer matches its preview."""


class StaleAccountRevisionError(PortfolioAccountingError):
    """Raised when an account write targets an outdated ledger revision."""


class InsufficientCashError(PortfolioAccountingError):
    """Raised when a CASH account would have a negative cash balance."""


class InsufficientPositionError(PortfolioAccountingError):
    """Raised when a sale exceeds holdings or T+1 available shares."""


class TransactionNotReversibleError(PortfolioAccountingError):
    """Raised when a transaction is itself a reversal or already reversed."""


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    name: str
    broker_name: str | None
    account_type: str
    currency: str
    opening_cash_cents: int
    status: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class AccountSummary:
    account: Account
    cash_cents: int


@dataclass(frozen=True, slots=True)
class TradePreview:
    account_id: int
    business_type: str
    trade_date: str
    symbol: str
    quantity: int
    price_micros: int
    gross_amount_cents: int
    cash_delta_cents: int
    fee_cents: int
    tax_cents: int
    target_position_bps: int | None
    ledger_revision: int


@dataclass(frozen=True, slots=True)
class Transaction:
    id: int
    account_id: int
    client_request_id: str
    business_type: str
    trade_date: str
    occurred_at: str
    symbol: str | None
    quantity: int | None
    price_micros: int | None
    gross_amount_cents: int | None
    cash_delta_cents: int
    fee_cents: int
    tax_cents: int
    target_position_bps: int | None
    reversal_of: int | None
    note: str | None


@dataclass(frozen=True, slots=True)
class CashFlowResult:
    transaction: Transaction
    cash_before_cents: int
    cash_after_cents: int
    ledger_revision: int


@dataclass(frozen=True, slots=True)
class ProjectedPosition:
    """Read-only position state produced by replaying the immutable ledger."""

    account_id: int
    symbol: str
    position_revision: int
    quantity: int
    available_quantity: int
    cost_basis_cents: int
    avg_cost_micros: int
    realized_pnl_cents: int
    first_buy_date: str
    last_trade_date: str
    status: str
    last_transaction_id: int


@dataclass(frozen=True, slots=True)
class AccountAsOfProjection:
    """One-account ledger projection read from a single SQLite snapshot."""

    account: Account
    positions: tuple[ProjectedPosition, ...]
    cash_cents: int
    ledger_revision: int


@dataclass(slots=True)
class _Lot:
    trade_date: str
    quantity: int


@dataclass(slots=True)
class _PositionState:
    symbol: str
    revision: int
    quantity: int
    cost_basis_cents: int
    avg_cost_micros: int
    realized_pnl_cents: int
    first_buy_date: str
    last_trade_date: str
    status: str
    last_transaction_id: int
    lots: list[_Lot] = field(default_factory=list)


RequestValue = str | int | None
RequestPayload = dict[str, RequestValue]


class PortfolioAccountingService:
    """Perform short, serialized writes against the portfolio ledger."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
            raise TypeError("busy_timeout_ms must be an integer")
        if not 0 <= busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS:
            raise ValueError(
                f"busy_timeout_ms must be between 0 and {MAX_BUSY_TIMEOUT_MS}"
            )
        self._db_path = Path(db_path).expanduser()
        self._busy_timeout_ms = busy_timeout_ms

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path,
            timeout=self._busy_timeout_ms / 1_000,
        )
        conn.row_factory = sqlite3.Row
        configure_portfolio_connection(
            conn,
            busy_timeout_ms=self._busy_timeout_ms,
        )
        return conn

    def _connect_read_only(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"{self._db_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=self._busy_timeout_ms / 1_000,
        )
        try:
            conn.row_factory = sqlite3.Row
            configure_portfolio_connection(
                conn,
                busy_timeout_ms=self._busy_timeout_ms,
            )
            conn.execute("PRAGMA query_only = ON")
        except Exception:
            conn.close()
            raise
        return conn

    def create_account(
        self,
        *,
        name: str,
        opening_cash_cents: int = 0,
        broker_name: str | None = None,
        account_type: str = "CASH",
        currency: str = "CNY",
        is_default: bool = False,
    ) -> Account:
        normalized_name = name.strip() if isinstance(name, str) else ""
        if not normalized_name:
            raise ValueError("account name must not be empty")
        self._require_nonnegative_integer(
            opening_cash_cents,
            "opening_cash_cents",
        )
        if account_type == "MARGIN":
            raise InvalidTradeError(
                "MARGIN accounts require a credit model and are not supported"
            )
        if account_type not in {"CASH", "SIMULATED"}:
            raise ValueError("unsupported account_type")
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or currency != currency.upper()
        ):
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(is_default, bool):
            raise TypeError("is_default must be a boolean")

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO portfolio_accounts (
                        name, broker_name, account_type, currency,
                        opening_cash_cents, is_default
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_name,
                        broker_name,
                        account_type,
                        currency,
                        opening_cash_cents,
                        int(is_default),
                    ),
                )
                account = self._get_account(conn, int(cursor.lastrowid))
                conn.commit()
                return account
            except Exception:
                conn.rollback()
                raise

    def list_accounts(self, *, include_archived: bool = True) -> list[Account]:
        with closing(self._connect()) as conn:
            if include_archived:
                rows = conn.execute(
                    "SELECT * FROM portfolio_accounts ORDER BY id"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM portfolio_accounts
                    WHERE status = 'ACTIVE'
                    ORDER BY id
                    """
                ).fetchall()
            return [self._account_from_row(row) for row in rows]

    def get_account_summary(self, account_id: int) -> AccountSummary:
        self._require_positive_integer(account_id, "account_id")
        with closing(self._connect()) as conn:
            account = self._get_account(conn, account_id)
            cash_cents = self._cash_balance(conn, account_id)
            return AccountSummary(account=account, cash_cents=cash_cents)

    def project_account_as_of(
        self,
        account_id: int,
        as_of_date: str,
    ) -> AccountAsOfProjection:
        """Project account, positions, cash, and revision in one read snapshot."""
        self._require_positive_integer(account_id, "account_id")
        self._validate_trade_date(as_of_date)
        with closing(self._connect_read_only()) as conn:
            conn.execute("BEGIN")
            try:
                account = self._get_account(conn, account_id)
                states = self._project_position_states(
                    conn,
                    account_id,
                    as_of_date,
                )
                positions = tuple(
                    self._projected_position(
                        account_id,
                        states[symbol],
                        as_of_date,
                    )
                    for symbol in sorted(states)
                )
                cash_cents = self._cash_balance(
                    conn,
                    account_id,
                    as_of_date=as_of_date,
                )
                ledger_revision = self._account_ledger_revision_as_of(
                    conn,
                    account_id,
                    as_of_date,
                )
                result = AccountAsOfProjection(
                    account=account,
                    positions=positions,
                    cash_cents=cash_cents,
                    ledger_revision=ledger_revision,
                )
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def project_positions_as_of(
        self,
        account_id: int,
        as_of_date: str,
    ) -> list[ProjectedPosition]:
        """Replay holdings through a date without mutating current projections."""
        self._require_positive_integer(account_id, "account_id")
        self._validate_trade_date(as_of_date)
        with closing(self._connect_read_only()) as conn:
            self._get_account(conn, account_id)
            states = self._project_position_states(
                conn,
                account_id,
                as_of_date,
            )
            return [
                self._projected_position(
                    account_id,
                    states[symbol],
                    as_of_date,
                )
                for symbol in sorted(states)
            ]

    def cash_balance_as_of(self, account_id: int, as_of_date: str) -> int:
        """Return ledger cash through a date using traceable correction semantics."""
        self._require_positive_integer(account_id, "account_id")
        self._validate_trade_date(as_of_date)
        with closing(self._connect_read_only()) as conn:
            self._get_account(conn, account_id)
            return self._cash_balance(
                conn,
                account_id,
                as_of_date=as_of_date,
            )

    def ledger_revision_as_of(self, account_id: int, as_of_date: str) -> int:
        """Return the newest ledger row that can affect an as-of projection."""
        self._require_positive_integer(account_id, "account_id")
        self._validate_trade_date(as_of_date)
        with closing(self._connect_read_only()) as conn:
            self._get_account(conn, account_id)
            return self._account_ledger_revision_as_of(
                conn,
                account_id,
                as_of_date,
            )

    def ledger_revision(self, account_id: int) -> int:
        """Return the current account ledger revision."""
        self._require_positive_integer(account_id, "account_id")
        with closing(self._connect_read_only()) as conn:
            self._get_account(conn, account_id)
            return self._account_ledger_revision(conn, account_id)

    def preview_trade(
        self,
        *,
        account_id: int,
        business_type: str,
        trade_date: str,
        symbol: str,
        price_micros: int,
        quantity: int | None = None,
        amount_cents: int | None = None,
        target_position_bps: int | None = None,
        account_equity_cents: int | None = None,
        fee_cents: int = 0,
        tax_cents: int = 0,
    ) -> TradePreview:
        with closing(self._connect()) as conn:
            account = self._get_account(conn, account_id)
            self._require_active(account)
            return self._preview_trade(
                conn,
                account_id=account_id,
                business_type=business_type,
                trade_date=trade_date,
                symbol=symbol,
                price_micros=price_micros,
                quantity=quantity,
                amount_cents=amount_cents,
                target_position_bps=target_position_bps,
                account_equity_cents=account_equity_cents,
                fee_cents=fee_cents,
                tax_cents=tax_cents,
            )

    def post_trade(
        self,
        *,
        account_id: int,
        client_request_id: str,
        business_type: str,
        trade_date: str,
        occurred_at: str,
        symbol: str,
        price_micros: int,
        quantity: int | None = None,
        amount_cents: int | None = None,
        target_position_bps: int | None = None,
        account_equity_cents: int | None = None,
        fee_cents: int = 0,
        tax_cents: int = 0,
        expected_ledger_revision: int | None = None,
        expected_quantity: int | None = None,
        expected_gross_amount_cents: int | None = None,
        expected_cash_delta_cents: int | None = None,
    ) -> Transaction:
        request = self._trade_request_payload(
            account_id=account_id,
            client_request_id=client_request_id,
            business_type=business_type,
            trade_date=trade_date,
            occurred_at=occurred_at,
            symbol=symbol,
            price_micros=price_micros,
            quantity=quantity,
            amount_cents=amount_cents,
            target_position_bps=target_position_bps,
            account_equity_cents=account_equity_cents,
            fee_cents=fee_cents,
            tax_cents=tax_cents,
            expected_ledger_revision=expected_ledger_revision,
            expected_quantity=expected_quantity,
            expected_gross_amount_cents=expected_gross_amount_cents,
            expected_cash_delta_cents=expected_cash_delta_cents,
        )
        self._validate_request_id(client_request_id)
        self._validate_occurred_at(occurred_at)

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_idempotent_transaction(
                    conn,
                    client_request_id,
                    request,
                )
                if existing is not None:
                    conn.commit()
                    return existing

                expected_values = (
                    expected_ledger_revision,
                    expected_quantity,
                    expected_gross_amount_cents,
                    expected_cash_delta_cents,
                )
                provided_expected_count = sum(
                    value is not None for value in expected_values
                )
                if provided_expected_count not in {0, 4}:
                    raise InvalidTradeError(
                        "expected preview binding requires all four expected fields"
                    )
                account = self._get_account(conn, account_id)
                self._require_active(account)
                preview = self._preview_trade(
                    conn,
                    account_id=account_id,
                    business_type=business_type,
                    trade_date=trade_date,
                    symbol=symbol,
                    price_micros=price_micros,
                    quantity=quantity,
                    amount_cents=amount_cents,
                    target_position_bps=target_position_bps,
                    account_equity_cents=account_equity_cents,
                    fee_cents=fee_cents,
                    tax_cents=tax_cents,
                )
                if provided_expected_count == 4 and (
                    preview.ledger_revision != expected_ledger_revision
                    or preview.quantity != expected_quantity
                    or preview.gross_amount_cents
                    != expected_gross_amount_cents
                    or preview.cash_delta_cents != expected_cash_delta_cents
                ):
                    raise StaleTradePreviewError(
                        "trade preview is stale; preview the trade again"
                    )
                self._rebuild_positions(conn, account_id, trade_date)
                if preview.business_type == "BUY":
                    cash_before = self._cash_balance(
                        conn,
                        account_id,
                        as_of_date=trade_date,
                    )
                    if cash_before + preview.cash_delta_cents < 0:
                        raise InsufficientCashError(
                            "trade exceeds the account cash balance"
                        )

                cursor = conn.execute(
                    """
                    INSERT INTO portfolio_transactions (
                        account_id, client_request_id, business_type, status,
                        trade_date, occurred_at, symbol, quantity,
                        price_micros, gross_amount_cents, cash_delta_cents,
                        fee_cents, tax_cents, target_position_bps,
                        source, metadata_json
                    ) VALUES (
                        ?, ?, ?, 'POSTED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'MANUAL', ?
                    )
                    """,
                    (
                        account_id,
                        client_request_id,
                        preview.business_type,
                        preview.trade_date,
                        occurred_at,
                        preview.symbol,
                        preview.quantity,
                        preview.price_micros,
                        preview.gross_amount_cents,
                        preview.cash_delta_cents,
                        preview.fee_cents,
                        preview.tax_cents,
                        preview.target_position_bps,
                        self._encode_request(request),
                    ),
                )
                latest_date = self._latest_account_trade_date(conn, account_id)
                self._rebuild_positions(conn, account_id, latest_date)
                self._validate_cash_sequence(conn, account)
                transaction = self._get_transaction(
                    conn,
                    int(cursor.lastrowid),
                )
                conn.commit()
                return transaction
            except Exception:
                conn.rollback()
                raise

    def post_cash_flow(
        self,
        *,
        account_id: int,
        client_request_id: str,
        business_type: str,
        trade_date: str,
        occurred_at: str,
        amount_cents: int,
        expected_ledger_revision: int,
        note: str | None = None,
    ) -> CashFlowResult:
        """Append an external deposit or withdrawal to an active account."""
        self._require_positive_integer(account_id, "account_id")
        self._validate_request_id(client_request_id)
        self._validate_cash_flow_date(trade_date)
        self._validate_occurred_at(occurred_at)
        self._validate_cash_flow_datetime(trade_date, occurred_at)
        self._require_positive_integer(amount_cents, "amount_cents")
        self._require_nonnegative_integer(
            expected_ledger_revision,
            "expected_ledger_revision",
        )
        if business_type not in {"DEPOSIT", "WITHDRAWAL"}:
            raise InvalidTradeError(
                "business_type must be DEPOSIT or WITHDRAWAL"
            )
        normalized_note = self._normalize_note(note)
        request: RequestPayload = {
            "operation": "POST_CASH_FLOW",
            "account_id": account_id,
            "client_request_id": client_request_id,
            "business_type": business_type,
            "trade_date": trade_date,
            "occurred_at": occurred_at,
            "amount_cents": amount_cents,
            "expected_ledger_revision": expected_ledger_revision,
            "note": normalized_note,
        }

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_idempotent_transaction(
                    conn,
                    client_request_id,
                    request,
                )
                if existing is not None:
                    cash_after = self._cash_balance(conn, account_id)
                    revision = self._account_ledger_revision(conn, account_id)
                    conn.commit()
                    return CashFlowResult(
                        transaction=existing,
                        cash_before_cents=(
                            cash_after - existing.cash_delta_cents
                        ),
                        cash_after_cents=cash_after,
                        ledger_revision=revision,
                    )

                account = self._get_account(conn, account_id)
                self._require_active(account)
                latest_snapshot_date = self._latest_snapshot_date(
                    conn,
                    account_id,
                )
                if (
                    latest_snapshot_date is not None
                    and trade_date <= latest_snapshot_date
                ):
                    raise InvalidTradeError(
                        "cash flow date must be after the latest settled performance snapshot"
                    )
                current_revision = self._account_ledger_revision(
                    conn,
                    account_id,
                )
                if current_revision != expected_ledger_revision:
                    raise StaleAccountRevisionError(
                        "account ledger changed; refresh before posting cash flow"
                    )

                cash_before = self._cash_balance(conn, account_id)
                cash_delta = (
                    amount_cents
                    if business_type == "DEPOSIT"
                    else -amount_cents
                )
                cursor = conn.execute(
                    """
                    INSERT INTO portfolio_transactions (
                        account_id, client_request_id, business_type, status,
                        trade_date, occurred_at, gross_amount_cents,
                        cash_delta_cents, source, note, metadata_json
                    ) VALUES (
                        ?, ?, ?, 'POSTED', ?, ?, ?, ?, 'MANUAL', ?, ?
                    )
                    """,
                    (
                        account_id,
                        client_request_id,
                        business_type,
                        trade_date,
                        occurred_at,
                        amount_cents,
                        cash_delta,
                        normalized_note,
                        self._encode_request(request),
                    ),
                )
                self._validate_cash_sequence(conn, account)
                transaction = self._get_transaction(
                    conn,
                    int(cursor.lastrowid),
                )
                cash_after = self._cash_balance(conn, account_id)
                revision = self._account_ledger_revision(conn, account_id)
                conn.commit()
                return CashFlowResult(
                    transaction=transaction,
                    cash_before_cents=cash_before,
                    cash_after_cents=cash_after,
                    ledger_revision=revision,
                )
            except Exception:
                conn.rollback()
                raise

    def refresh_positions(self, account_id: int, as_of_date: str) -> None:
        self._require_positive_integer(account_id, "account_id")
        self._validate_trade_date(as_of_date)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._get_account(conn, account_id)
                latest_date = self._latest_ledger_trade_date(conn, account_id)
                if latest_date is not None and as_of_date < latest_date:
                    raise InvalidTradeError(
                        "as_of_date cannot precede the latest ledger trade_date"
                    )
                self._rebuild_positions(conn, account_id, as_of_date)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def reverse_transaction(
        self,
        *,
        account_id: int,
        transaction_id: int,
        client_request_id: str,
        trade_date: str,
        occurred_at: str,
    ) -> Transaction:
        self._require_positive_integer(account_id, "account_id")
        self._require_positive_integer(transaction_id, "transaction_id")
        self._validate_request_id(client_request_id)
        self._validate_trade_date(trade_date)
        self._validate_occurred_at(occurred_at)
        request: RequestPayload = {
            "operation": "REVERSE",
            "account_id": account_id,
            "transaction_id": transaction_id,
            "client_request_id": client_request_id,
            "trade_date": trade_date,
            "occurred_at": occurred_at,
        }

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_idempotent_transaction(
                    conn,
                    client_request_id,
                    request,
                )
                if existing is not None:
                    if existing.business_type != "REVERSAL":
                        raise IdempotencyConflictError(
                            "request id belongs to a non-reversal transaction"
                        )
                    conn.commit()
                    return existing

                account = self._get_account(conn, account_id)
                self._require_active(account)
                original_row = conn.execute(
                    """
                    SELECT *
                    FROM portfolio_transactions
                    WHERE id = ? AND account_id = ?
                    """,
                    (transaction_id, account_id),
                ).fetchone()
                if original_row is None:
                    raise TransactionNotReversibleError(
                        "original transaction does not exist in this account"
                    )
                if original_row["business_type"] == "REVERSAL":
                    raise TransactionNotReversibleError(
                        "a reversal transaction cannot be reversed"
                    )
                already_reversed = conn.execute(
                    """
                    SELECT 1
                    FROM portfolio_transactions
                    WHERE reversal_of = ?
                    LIMIT 1
                    """,
                    (transaction_id,),
                ).fetchone()
                if already_reversed is not None:
                    raise TransactionNotReversibleError(
                        "transaction has already been reversed"
                    )
                if trade_date < str(original_row["trade_date"]):
                    raise InvalidTradeError(
                        "reversal date cannot precede the original trade"
                    )

                cursor = conn.execute(
                    """
                    INSERT INTO portfolio_transactions (
                        account_id, client_request_id, business_type, status,
                        trade_date, occurred_at, cash_delta_cents,
                        source, reversal_of, metadata_json
                    ) VALUES (
                        ?, ?, 'REVERSAL', 'POSTED', ?, ?, ?,
                        'MANUAL', ?, ?
                    )
                    """,
                    (
                        account_id,
                        client_request_id,
                        trade_date,
                        occurred_at,
                        -int(original_row["cash_delta_cents"]),
                        transaction_id,
                        self._encode_request(request),
                    ),
                )
                latest_date = self._latest_account_trade_date(conn, account_id)
                self._rebuild_positions(conn, account_id, latest_date)
                affected_accounts = {account.id}
                if (
                    original_row["business_type"] == "TRANSFER"
                    and original_row["counterparty_account_id"] is not None
                ):
                    affected_accounts.add(
                        int(original_row["counterparty_account_id"])
                    )
                for affected_account_id in affected_accounts:
                    affected_account = self._get_account(
                        conn,
                        affected_account_id,
                    )
                    self._validate_cash_sequence(conn, affected_account)
                reversal = self._get_transaction(conn, int(cursor.lastrowid))
                conn.commit()
                return reversal
            except Exception:
                conn.rollback()
                raise

    def _preview_trade(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: int,
        business_type: str,
        trade_date: str,
        symbol: str,
        price_micros: int,
        quantity: int | None,
        amount_cents: int | None,
        target_position_bps: int | None,
        account_equity_cents: int | None,
        fee_cents: int,
        tax_cents: int,
    ) -> TradePreview:
        self._require_positive_integer(account_id, "account_id")
        if business_type not in {"BUY", "SELL"}:
            raise InvalidTradeError("business_type must be BUY or SELL")
        self._validate_trade_date(trade_date)
        self._validate_symbol(symbol)
        self._require_positive_integer(price_micros, "price_micros")
        if price_micros > MAX_PRICE_MICROS:
            raise InvalidTradeError("price_micros exceeds the ledger limit")
        self._require_nonnegative_integer(fee_cents, "fee_cents")
        self._require_nonnegative_integer(tax_cents, "tax_cents")
        sizing_count = sum(
            value is not None
            for value in (quantity, amount_cents, target_position_bps)
        )
        if sizing_count != 1:
            raise InvalidTradeError(
                "exactly one of quantity, amount_cents, "
                "or target_position_bps is required"
            )

        current_quantity, available_quantity = (
            self._position_quantities_as_of(
                conn,
                account_id=account_id,
                symbol=symbol,
                trade_date=trade_date,
            )
        )

        resolved_quantity: int
        if quantity is not None:
            self._require_positive_integer(quantity, "quantity")
            if business_type == "BUY" and quantity % 100 != 0:
                raise InvalidTradeError(
                    "explicit BUY quantity must be a 100-share board lot"
                )
            resolved_quantity = quantity
        elif amount_cents is not None:
            self._require_positive_integer(amount_cents, "amount_cents")
            resolved_quantity = amount_cents * 10_000 // price_micros
        else:
            if target_position_bps is None:
                raise InvalidTradeError("target_position_bps is required")
            self._require_positive_integer(
                target_position_bps,
                "target_position_bps",
            )
            if target_position_bps > 10_000:
                raise InvalidTradeError("target_position_bps cannot exceed 10000")
            if account_equity_cents is None:
                raise InvalidTradeError(
                    "account_equity_cents is required for target sizing"
                )
            self._require_positive_integer(
                account_equity_cents,
                "account_equity_cents",
            )
            target_value = self._round_half_up(
                account_equity_cents * target_position_bps,
                10_000,
            )
            current_value = self._gross_amount(
                price_micros,
                current_quantity,
            )
            value_delta = (
                target_value - current_value
                if business_type == "BUY"
                else current_value - target_value
            )
            if value_delta <= 0:
                raise InvalidTradeError(
                    "target position does not require this trade direction"
                )
            resolved_quantity = value_delta * 10_000 // price_micros

        if business_type == "BUY":
            if quantity is None:
                resolved_quantity = resolved_quantity // 100 * 100
            if resolved_quantity <= 0:
                raise InvalidTradeError(
                    "BUY size is below one 100-share board lot"
                )
        elif resolved_quantity <= 0:
            raise InvalidTradeError("SELL size must resolve to at least one share")
        if resolved_quantity > MAX_QUANTITY:
            raise InvalidTradeError("quantity exceeds the ledger limit")

        if business_type == "SELL" and resolved_quantity > available_quantity:
            raise InsufficientPositionError(
                "SELL quantity exceeds T+1 available_quantity"
            )

        gross_amount_cents = self._gross_amount(
            price_micros,
            resolved_quantity,
        )
        if business_type == "BUY":
            cash_delta_cents = -(
                gross_amount_cents + fee_cents + tax_cents
            )
        else:
            cash_delta_cents = gross_amount_cents - fee_cents - tax_cents
            if cash_delta_cents <= 0:
                raise InvalidTradeError("SELL fees and tax exceed sale proceeds")

        return TradePreview(
            account_id=account_id,
            business_type=business_type,
            trade_date=trade_date,
            symbol=symbol,
            quantity=resolved_quantity,
            price_micros=price_micros,
            gross_amount_cents=gross_amount_cents,
            cash_delta_cents=cash_delta_cents,
            fee_cents=fee_cents,
            tax_cents=tax_cents,
            target_position_bps=target_position_bps,
            ledger_revision=self._account_ledger_revision(conn, account_id),
        )

    @staticmethod
    def _account_ledger_revision(
        conn: sqlite3.Connection,
        account_id: int,
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(transaction_row.id), 0)
            FROM portfolio_transactions AS transaction_row
            WHERE transaction_row.account_id = ?
               OR transaction_row.counterparty_account_id = ?
               OR EXISTS (
                    SELECT 1
                    FROM portfolio_transactions AS original
                    WHERE original.id = transaction_row.reversal_of
                      AND original.counterparty_account_id = ?
               )
            """,
            (account_id, account_id, account_id),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _account_ledger_revision_as_of(
        conn: sqlite3.Connection,
        account_id: int,
        as_of_date: str,
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(transaction_row.id), 0)
            FROM portfolio_transactions AS transaction_row
            WHERE transaction_row.status = 'POSTED'
              AND (
                    (
                        (
                            transaction_row.account_id = ?
                            OR transaction_row.counterparty_account_id = ?
                        )
                        AND transaction_row.trade_date <= ?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM portfolio_transactions AS original
                        WHERE original.id = transaction_row.reversal_of
                          AND original.status = 'POSTED'
                          AND original.trade_date <= ?
                          AND (
                                original.account_id = ?
                                OR original.counterparty_account_id = ?
                          )
                    )
              )
            """,
            (
                account_id,
                account_id,
                as_of_date,
                as_of_date,
                account_id,
                account_id,
            ),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _position_quantities_as_of(
        conn: sqlite3.Connection,
        *,
        account_id: int,
        symbol: str,
        trade_date: str,
    ) -> tuple[int, int]:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(
                    CASE
                        WHEN transaction_row.business_type IN (
                            'BUY', 'CORPORATE_ACTION'
                        )
                        THEN transaction_row.quantity
                        WHEN transaction_row.business_type = 'SELL'
                        THEN -transaction_row.quantity
                        ELSE 0
                    END
                ), 0) AS current_quantity,
                COALESCE(SUM(
                    CASE
                        WHEN transaction_row.business_type IN (
                            'BUY', 'CORPORATE_ACTION'
                        )
                         AND transaction_row.trade_date < ?
                        THEN transaction_row.quantity
                        WHEN transaction_row.business_type = 'SELL'
                        THEN -transaction_row.quantity
                        ELSE 0
                    END
                ), 0) AS available_quantity
            FROM portfolio_transactions AS transaction_row
            WHERE transaction_row.account_id = ?
              AND transaction_row.symbol = ?
              AND transaction_row.status = 'POSTED'
              AND transaction_row.business_type IN (
                  'BUY', 'SELL', 'CORPORATE_ACTION'
              )
              AND transaction_row.trade_date <= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM portfolio_transactions AS reversal
                  WHERE reversal.reversal_of = transaction_row.id
                    AND reversal.status = 'POSTED'
              )
            """,
            (trade_date, account_id, symbol, trade_date),
        ).fetchone()
        current_quantity = int(row["current_quantity"])
        available_quantity = int(row["available_quantity"])
        if (
            current_quantity < 0
            or available_quantity < 0
            or available_quantity > current_quantity
        ):
            raise InsufficientPositionError(
                "immutable ledger contains an invalid position sequence"
            )
        return current_quantity, available_quantity

    def _project_position_states(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        as_of_date: str,
    ) -> dict[str, _PositionState]:
        self._validate_trade_date(as_of_date)
        rows = conn.execute(
            """
            SELECT transaction_row.*
            FROM portfolio_transactions AS transaction_row
            WHERE transaction_row.account_id = ?
              AND transaction_row.status = 'POSTED'
              AND transaction_row.business_type IN (
                  'BUY', 'SELL', 'CORPORATE_ACTION'
              )
              AND transaction_row.trade_date <= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM portfolio_transactions AS reversal
                  WHERE reversal.reversal_of = transaction_row.id
                    AND reversal.status = 'POSTED'
              )
            ORDER BY transaction_row.trade_date,
                     transaction_row.occurred_at,
                     transaction_row.id
            """,
            (account_id, as_of_date),
        ).fetchall()
        states: dict[str, _PositionState] = {}
        for row in rows:
            symbol = str(row["symbol"])
            business_type = str(row["business_type"])
            trade_date = str(row["trade_date"])
            transaction_id = int(row["id"])
            quantity = int(row["quantity"])
            state = states.get(symbol)

            if business_type == "BUY":
                buy_cost = (
                    int(row["gross_amount_cents"])
                    + int(row["fee_cents"])
                    + int(row["tax_cents"])
                )
                if state is None:
                    state = _PositionState(
                        symbol=symbol,
                        revision=1,
                        quantity=0,
                        cost_basis_cents=0,
                        avg_cost_micros=0,
                        realized_pnl_cents=0,
                        first_buy_date=trade_date,
                        last_trade_date=trade_date,
                        status="OPEN",
                        last_transaction_id=transaction_id,
                    )
                    states[symbol] = state
                elif state.status == "CLOSED":
                    state.revision += 1
                    state.realized_pnl_cents = 0
                    state.first_buy_date = trade_date
                    state.lots.clear()
                state.quantity += quantity
                state.cost_basis_cents += buy_cost
                state.avg_cost_micros = self._round_half_up(
                    state.cost_basis_cents * 10_000,
                    state.quantity,
                )
                state.last_trade_date = trade_date
                state.last_transaction_id = transaction_id
                state.status = "OPEN"
                state.lots.append(_Lot(trade_date=trade_date, quantity=quantity))
                continue

            if state is None or state.status != "OPEN":
                raise InsufficientPositionError(
                    f"{symbol} has no open position for transaction {transaction_id}"
                )
            if business_type == "SELL":
                if quantity > state.quantity:
                    raise InsufficientPositionError(
                        f"{symbol} sale exceeds total position"
                    )
                available = sum(
                    lot.quantity
                    for lot in state.lots
                    if lot.trade_date < trade_date
                )
                if quantity > available:
                    raise InsufficientPositionError(
                        f"{symbol} sale exceeds T+1 available shares"
                    )
                self._consume_available_lots(state.lots, quantity, trade_date)
                old_quantity = state.quantity
                if quantity == old_quantity:
                    released_cost = state.cost_basis_cents
                else:
                    released_cost = self._round_half_up(
                        state.cost_basis_cents * quantity,
                        old_quantity,
                    )
                net_proceeds = (
                    int(row["gross_amount_cents"])
                    - int(row["fee_cents"])
                    - int(row["tax_cents"])
                )
                state.realized_pnl_cents += net_proceeds - released_cost
                state.quantity -= quantity
                state.cost_basis_cents -= released_cost
                state.last_trade_date = trade_date
                state.last_transaction_id = transaction_id
                if state.quantity == 0:
                    state.cost_basis_cents = 0
                    state.avg_cost_micros = 0
                    state.status = "CLOSED"
                    state.lots.clear()
                else:
                    state.avg_cost_micros = self._round_half_up(
                        state.cost_basis_cents * 10_000,
                        state.quantity,
                    )
                continue

            state.quantity += quantity
            state.avg_cost_micros = self._round_half_up(
                state.cost_basis_cents * 10_000,
                state.quantity,
            )
            state.last_trade_date = trade_date
            state.last_transaction_id = transaction_id
            state.lots.append(_Lot(trade_date=trade_date, quantity=quantity))

        return states

    @staticmethod
    def _projected_position(
        account_id: int,
        state: _PositionState,
        as_of_date: str,
    ) -> ProjectedPosition:
        available_quantity = (
            sum(
                lot.quantity
                for lot in state.lots
                if lot.trade_date < as_of_date
            )
            if state.status == "OPEN"
            else 0
        )
        return ProjectedPosition(
            account_id=account_id,
            symbol=state.symbol,
            position_revision=state.revision,
            quantity=state.quantity,
            available_quantity=available_quantity,
            cost_basis_cents=state.cost_basis_cents,
            avg_cost_micros=state.avg_cost_micros,
            realized_pnl_cents=state.realized_pnl_cents,
            first_buy_date=state.first_buy_date,
            last_trade_date=state.last_trade_date,
            status=state.status,
            last_transaction_id=state.last_transaction_id,
        )

    def _rebuild_positions(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        as_of_date: str,
    ) -> None:
        states = self._project_position_states(conn, account_id, as_of_date)
        conn.execute(
            "DELETE FROM portfolio_positions WHERE account_id = ?",
            (account_id,),
        )
        for symbol in sorted(states):
            state = states[symbol]
            projection = self._projected_position(
                account_id,
                state,
                as_of_date,
            )
            conn.execute(
                """
                INSERT INTO portfolio_positions (
                    account_id, symbol, position_revision, quantity,
                    available_quantity, cost_basis_cents, avg_cost_micros,
                    realized_pnl_cents, first_buy_date, last_trade_date,
                    status, last_transaction_id, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
                )
                """,
                (
                    account_id,
                    projection.symbol,
                    projection.position_revision,
                    projection.quantity,
                    projection.available_quantity,
                    projection.cost_basis_cents,
                    projection.avg_cost_micros,
                    projection.realized_pnl_cents,
                    projection.first_buy_date,
                    projection.last_trade_date,
                    projection.status,
                    projection.last_transaction_id,
                ),
            )

    @staticmethod
    def _consume_available_lots(
        lots: list[_Lot],
        quantity: int,
        sell_date: str,
    ) -> None:
        remaining = quantity
        for lot in lots:
            if remaining == 0:
                break
            if lot.trade_date >= sell_date or lot.quantity == 0:
                continue
            consumed = min(lot.quantity, remaining)
            lot.quantity -= consumed
            remaining -= consumed
        if remaining:
            raise InsufficientPositionError(
                "sale could not be allocated to T+1 available lots"
            )

    def _cash_balance(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        *,
        as_of_date: str | None = None,
    ) -> int:
        account = self._get_account(conn, account_id)
        cash = account.opening_cash_cents
        for row in self._effective_cash_rows(conn, account_id, as_of_date):
            if row["business_type"] == "TRANSFER":
                gross = int(row["gross_amount_cents"])
                if int(row["account_id"]) == account_id:
                    cash -= gross
                else:
                    cash += gross
            elif int(row["account_id"]) == account_id:
                cash += int(row["cash_delta_cents"])
        return cash

    def _validate_cash_sequence(
        self,
        conn: sqlite3.Connection,
        account: Account,
    ) -> None:
        cash = account.opening_cash_cents
        for row in self._effective_cash_rows(conn, account.id):
            if row["business_type"] == "TRANSFER":
                gross = int(row["gross_amount_cents"])
                cash += -gross if int(row["account_id"]) == account.id else gross
            elif int(row["account_id"]) == account.id:
                cash += int(row["cash_delta_cents"])
            if cash < 0:
                raise InsufficientCashError(
                    "ledger sequence would make the cash balance negative"
                )

    @staticmethod
    def _effective_cash_rows(
        conn: sqlite3.Connection,
        account_id: int,
        as_of_date: str | None = None,
    ) -> list[sqlite3.Row]:
        date_clause = (
            "AND transaction_row.trade_date <= ?" if as_of_date is not None else ""
        )
        parameters: tuple[int, int] | tuple[int, int, str]
        if as_of_date is None:
            parameters = (account_id, account_id)
        else:
            parameters = (account_id, account_id, as_of_date)
        return conn.execute(
            f"""
            SELECT transaction_row.*
            FROM portfolio_transactions AS transaction_row
            WHERE (
                transaction_row.account_id = ?
                OR transaction_row.counterparty_account_id = ?
            )
              AND transaction_row.status = 'POSTED'
              AND transaction_row.business_type <> 'REVERSAL'
              {date_clause}
              AND NOT EXISTS (
                  SELECT 1
                  FROM portfolio_transactions AS reversal
                  WHERE reversal.reversal_of = transaction_row.id
                    AND reversal.status = 'POSTED'
              )
            ORDER BY transaction_row.trade_date,
                     transaction_row.occurred_at,
                     transaction_row.id
            """,
            parameters,
        ).fetchall()

    @staticmethod
    def _latest_account_trade_date(
        conn: sqlite3.Connection,
        account_id: int,
    ) -> str:
        row = conn.execute(
            """
            SELECT MAX(trade_date)
            FROM portfolio_transactions
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None or row[0] is None:
            raise InvalidTradeError("account has no transactions to rebuild")
        return str(row[0])

    @staticmethod
    def _latest_ledger_trade_date(
        conn: sqlite3.Connection,
        account_id: int,
    ) -> str | None:
        row = conn.execute(
            """
            SELECT MAX(trade_date)
            FROM portfolio_transactions AS transaction_row
            WHERE transaction_row.account_id = ?
               OR transaction_row.counterparty_account_id = ?
               OR EXISTS (
                    SELECT 1
                    FROM portfolio_transactions AS original
                    WHERE original.id = transaction_row.reversal_of
                      AND original.counterparty_account_id = ?
               )
            """,
            (account_id, account_id, account_id),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    @staticmethod
    def _latest_snapshot_date(
        conn: sqlite3.Connection,
        account_id: int,
    ) -> str | None:
        row = conn.execute(
            """
            SELECT MAX(snapshot_date)
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def _find_idempotent_transaction(
        self,
        conn: sqlite3.Connection,
        client_request_id: str,
        request: RequestPayload,
    ) -> Transaction | None:
        row = conn.execute(
            """
            SELECT *
            FROM portfolio_transactions
            WHERE client_request_id = ?
            """,
            (client_request_id,),
        ).fetchone()
        if row is None:
            return None
        stored_request = self._decode_request(row["metadata_json"])
        if stored_request != request:
            raise IdempotencyConflictError(
                "client_request_id was already used with a different payload"
            )
        return self._transaction_from_row(row)

    @staticmethod
    def _encode_request(request: RequestPayload) -> str:
        return json.dumps(
            {"accounting_request": request},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_request(metadata_json: object) -> RequestPayload | None:
        if not isinstance(metadata_json, str):
            return None
        try:
            decoded = json.loads(metadata_json)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(decoded, dict):
            return None
        payload = decoded.get("accounting_request")
        if not isinstance(payload, dict):
            return None
        normalized: RequestPayload = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                return None
            if value is not None and not isinstance(value, (str, int)):
                return None
            normalized[key] = value
        return normalized

    @staticmethod
    def _trade_request_payload(
        *,
        account_id: int,
        client_request_id: str,
        business_type: str,
        trade_date: str,
        occurred_at: str,
        symbol: str,
        price_micros: int,
        quantity: int | None,
        amount_cents: int | None,
        target_position_bps: int | None,
        account_equity_cents: int | None,
        fee_cents: int,
        tax_cents: int,
        expected_ledger_revision: int | None,
        expected_quantity: int | None,
        expected_gross_amount_cents: int | None,
        expected_cash_delta_cents: int | None,
    ) -> RequestPayload:
        return {
            "operation": "POST_TRADE",
            "account_id": account_id,
            "client_request_id": client_request_id,
            "business_type": business_type,
            "trade_date": trade_date,
            "occurred_at": occurred_at,
            "symbol": symbol,
            "price_micros": price_micros,
            "quantity": quantity,
            "amount_cents": amount_cents,
            "target_position_bps": target_position_bps,
            "account_equity_cents": account_equity_cents,
            "fee_cents": fee_cents,
            "tax_cents": tax_cents,
            "expected_ledger_revision": expected_ledger_revision,
            "expected_quantity": expected_quantity,
            "expected_gross_amount_cents": expected_gross_amount_cents,
            "expected_cash_delta_cents": expected_cash_delta_cents,
        }

    def _get_account(
        self,
        conn: sqlite3.Connection,
        account_id: int,
    ) -> Account:
        row = conn.execute(
            "SELECT * FROM portfolio_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise AccountNotFoundError(f"portfolio account {account_id} not found")
        return self._account_from_row(row)

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> Account:
        return Account(
            id=int(row["id"]),
            name=str(row["name"]),
            broker_name=(
                str(row["broker_name"])
                if row["broker_name"] is not None
                else None
            ),
            account_type=str(row["account_type"]),
            currency=str(row["currency"]),
            opening_cash_cents=int(row["opening_cash_cents"]),
            status=str(row["status"]),
            is_default=bool(row["is_default"]),
        )

    def _get_transaction(
        self,
        conn: sqlite3.Connection,
        transaction_id: int,
    ) -> Transaction:
        row = conn.execute(
            "SELECT * FROM portfolio_transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        if row is None:
            raise PortfolioAccountingError(
                f"transaction {transaction_id} was not persisted"
            )
        return self._transaction_from_row(row)

    @staticmethod
    def _transaction_from_row(row: sqlite3.Row) -> Transaction:
        return Transaction(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            client_request_id=str(row["client_request_id"]),
            business_type=str(row["business_type"]),
            trade_date=str(row["trade_date"]),
            occurred_at=str(row["occurred_at"]),
            symbol=str(row["symbol"]) if row["symbol"] is not None else None,
            quantity=int(row["quantity"]) if row["quantity"] is not None else None,
            price_micros=(
                int(row["price_micros"])
                if row["price_micros"] is not None
                else None
            ),
            gross_amount_cents=(
                int(row["gross_amount_cents"])
                if row["gross_amount_cents"] is not None
                else None
            ),
            cash_delta_cents=int(row["cash_delta_cents"]),
            fee_cents=int(row["fee_cents"]),
            tax_cents=int(row["tax_cents"]),
            target_position_bps=(
                int(row["target_position_bps"])
                if row["target_position_bps"] is not None
                else None
            ),
            reversal_of=(
                int(row["reversal_of"])
                if row["reversal_of"] is not None
                else None
            ),
            note=str(row["note"]) if row["note"] is not None else None,
        )

    @staticmethod
    def _require_active(account: Account) -> None:
        if account.status != "ACTIVE":
            raise ArchivedAccountError(
                f"portfolio account {account.id} is archived"
            )
        if account.account_type == "MARGIN":
            raise InvalidTradeError(
                "MARGIN accounts require a credit model and are not supported"
            )

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if (
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isascii()
            or not symbol.isdigit()
        ):
            raise InvalidTradeError("symbol must contain exactly six digits")

    @staticmethod
    def _validate_trade_date(value: str) -> None:
        if not isinstance(value, str):
            raise InvalidTradeError("trade_date must be an ISO date")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidTradeError("trade_date must be an ISO date") from exc
        if parsed.isoformat() != value:
            raise InvalidTradeError("trade_date must be an ISO date")

    @classmethod
    def _validate_cash_flow_date(cls, value: str) -> None:
        cls._validate_trade_date(value)
        parsed = date.fromisoformat(value)
        if parsed > date.today():
            raise InvalidTradeError("cash flow date cannot be in the future")
        if parsed.weekday() >= 5:
            raise InvalidTradeError("cash flow date must be a weekday")

    @staticmethod
    def _validate_occurred_at(value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise InvalidTradeError("occurred_at must be an ISO datetime")
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise InvalidTradeError("occurred_at must be an ISO datetime") from exc

    @staticmethod
    def _validate_cash_flow_datetime(
        trade_date: str,
        occurred_at: str,
    ) -> None:
        parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            local_date = parsed.date()
        else:
            local_date = parsed.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if local_date.isoformat() != trade_date:
            raise InvalidTradeError(
                "occurred_at and cash flow trade_date must be the same date"
            )

    @staticmethod
    def _normalize_note(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidTradeError("note must be text")
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 500:
            raise InvalidTradeError("note cannot exceed 500 characters")
        return normalized

    @staticmethod
    def _validate_request_id(value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise InvalidTradeError("client_request_id must not be empty")

    @staticmethod
    def _require_positive_integer(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise InvalidTradeError(f"{field_name} must be a positive integer")

    @staticmethod
    def _require_nonnegative_integer(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidTradeError(
                f"{field_name} must be a nonnegative integer"
            )

    @staticmethod
    def _round_half_up(numerator: int, denominator: int) -> int:
        if denominator <= 0:
            raise PortfolioAccountingError("rounding denominator must be positive")
        return (numerator * 2 + denominator) // (denominator * 2)

    @classmethod
    def _gross_amount(cls, price_micros: int, quantity: int) -> int:
        return cls._round_half_up(price_micros * quantity, 10_000)
