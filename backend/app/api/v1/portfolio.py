"""Portfolio account, trade, position, and ledger API."""

from __future__ import annotations

import sqlite3
import json
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Request, status
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from portfolio.accounting import (
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
from portfolio.performance import (
    AccountDailySnapshot,
    AccountSnapshotResult,
    InvalidSnapshotDateError,
    PortfolioPerformanceService,
    SnapshotAccountError,
    SnapshotConsistencyError,
)
from portfolio.exit_engine import (
    ExitPlanNotFoundError,
    InvalidExitPlanError,
    PortfolioExitEngine,
    StaleExitPlanError,
)
from portfolio.schema import DEFAULT_BUSY_TIMEOUT_MS, configure_portfolio_connection
from portfolio.import_service import PortfolioImportService


router = APIRouter(prefix="/portfolio", tags=["Portfolio"])
PORTFOLIO_DB_PATH: Path = Path(settings.LOCAL_DB_PATH)


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountCreate(_StrictRequest):
    name: str = Field(min_length=1, max_length=100)
    broker_name: str | None = Field(default=None, max_length=100)
    account_type: Literal["CASH", "SIMULATED"] = "CASH"
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    opening_cash_cents: int = Field(default=0, ge=0, strict=True)
    is_default: bool = False


class TradePreviewRequest(_StrictRequest):
    account_id: int = Field(gt=0, strict=True)
    business_type: Literal["BUY", "SELL"]
    trade_date: date
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    price_micros: int = Field(gt=0, strict=True)
    quantity: int | None = Field(default=None, gt=0, strict=True)
    amount_cents: int | None = Field(default=None, gt=0, strict=True)
    target_position_bps: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        strict=True,
    )
    account_equity_cents: int | None = Field(default=None, gt=0, strict=True)
    fee_cents: int = Field(default=0, ge=0, strict=True)
    tax_cents: int = Field(default=0, ge=0, strict=True)


class TradeCreate(TradePreviewRequest):
    client_request_id: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    expected_ledger_revision: int = Field(ge=0, strict=True)
    expected_quantity: int = Field(gt=0, strict=True)
    expected_gross_amount_cents: int = Field(gt=0, strict=True)
    expected_cash_delta_cents: int = Field(strict=True)


class TransactionReverse(_StrictRequest):
    account_id: int = Field(gt=0, strict=True)
    client_request_id: str = Field(min_length=1, max_length=200)
    trade_date: date
    occurred_at: datetime


class CashFlowCreate(_StrictRequest):
    client_request_id: str = Field(min_length=1, max_length=200)
    business_type: Literal["DEPOSIT", "WITHDRAWAL"]
    trade_date: date
    occurred_at: datetime
    amount_cents: int = Field(
        gt=0,
        le=9_000_000_000_000_000,
        strict=True,
    )
    expected_ledger_revision: int = Field(ge=0, strict=True)
    note: str | None = Field(default=None, max_length=500)


class ExitPlanUpdate(_StrictRequest):
    position_revision: int = Field(gt=0, strict=True)
    enabled: bool = Field(default=True, strict=True)
    hard_stop_micros: int | None = Field(default=None, gt=0, strict=True)
    take_profit_1_micros: int | None = Field(default=None, gt=0, strict=True)
    take_profit_1_reduce_bps: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        strict=True,
    )
    take_profit_2_micros: int | None = Field(default=None, gt=0, strict=True)
    take_profit_2_reduce_bps: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        strict=True,
    )
    take_profit_3_micros: int | None = Field(default=None, gt=0, strict=True)
    take_profit_3_reduce_bps: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        strict=True,
    )
    trailing_activation_bps: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
        strict=True,
    )
    trailing_distance_bps: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        strict=True,
    )
    time_stop_trading_days: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        strict=True,
    )
    time_stop_min_return_bps: int | None = Field(
        default=None,
        ge=-10_000,
        le=100_000,
        strict=True,
    )
    trend_exit_enabled: bool = Field(default=True, strict=True)
    intraday_enabled: bool = Field(default=True, strict=True)


class ExitDailyEvaluationRequest(_StrictRequest):
    trade_date: date


@contextmanager
def _map_errors():
    try:
        yield
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StaleTradePreviewError as exc:
        raise HTTPException(
            status_code=409,
            detail="Portfolio 已发生变化，请重新预览后再确认记账",
        ) from exc
    except StaleAccountRevisionError as exc:
        raise HTTPException(
            status_code=409,
            detail="账户账本已变化，请刷新后重新确认资金操作",
        ) from exc
    except SnapshotConsistencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidSnapshotDateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SnapshotAccountError as exc:
        error_status = (
            404
            if str(exc) == "portfolio account does not exist"
            else 422
        )
        raise HTTPException(status_code=error_status, detail=str(exc)) from exc
    except ExitPlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StaleExitPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (
        ArchivedAccountError,
        InsufficientCashError,
        InsufficientPositionError,
        InvalidExitPlanError,
        InvalidTradeError,
        TransactionNotReversibleError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        if "portfolio_accounts.is_default" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="default portfolio account already exists",
            ) from exc
        raise HTTPException(
            status_code=422,
            detail="portfolio data violates integrity constraints",
        ) from exc
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="portfolio database is busy; retry later",
            ) from exc
        raise


def _service() -> PortfolioAccountingService:
    return PortfolioAccountingService(PORTFOLIO_DB_PATH)


def _performance_service() -> PortfolioPerformanceService:
    return PortfolioPerformanceService(PORTFOLIO_DB_PATH)


def _exit_service() -> PortfolioExitEngine:
    return PortfolioExitEngine(PORTFOLIO_DB_PATH)


def _connect_read() -> sqlite3.Connection:
    conn = sqlite3.connect(
        PORTFOLIO_DB_PATH,
        timeout=DEFAULT_BUSY_TIMEOUT_MS / 1_000,
    )
    conn.row_factory = sqlite3.Row
    configure_portfolio_connection(conn)
    return conn


def _account_payload(
    account,
    cash_cents: int,
    ledger_revision: int,
) -> dict[str, object]:
    return {
        "id": account.id,
        "name": account.name,
        "broker_name": account.broker_name,
        "account_type": account.account_type,
        "currency": account.currency,
        "opening_cash_cents": account.opening_cash_cents,
        "status": account.status,
        "is_default": account.is_default,
        "cash_cents": cash_cents,
        "ledger_revision": ledger_revision,
    }


def _preview_kwargs(request: TradePreviewRequest) -> dict[str, object]:
    return {
        "account_id": request.account_id,
        "business_type": request.business_type,
        "trade_date": request.trade_date.isoformat(),
        "symbol": request.symbol,
        "price_micros": request.price_micros,
        "quantity": request.quantity,
        "amount_cents": request.amount_cents,
        "target_position_bps": request.target_position_bps,
        "account_equity_cents": request.account_equity_cents,
        "fee_cents": request.fee_cents,
        "tax_cents": request.tax_cents,
    }


def _price_micros(close_value: object) -> int | None:
    if close_value is None:
        return None
    try:
        close = Decimal(str(close_value))
    except ArithmeticError:
        return None
    if not close.is_finite() or close <= 0:
        return None
    return int(
        (close * Decimal(1_000_000)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _round_ratio(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    absolute = abs(numerator)
    return sign * ((absolute * 2 + denominator) // (denominator * 2))


def _position_payload(
    row: sqlite3.Row,
    *,
    expected_market_date: str | None,
) -> dict[str, object]:
    symbol = str(row["symbol"])
    price_micros = _price_micros(row["quote_close"])
    price_trade_date = (
        str(row["quote_trade_date"])
        if row["quote_trade_date"] is not None
        else None
    )
    if price_micros is None:
        price_status = "MISSING"
        stale = True
        market_value_cents = None
        unrealized_pnl_cents = None
        unrealized_pnl_bps = None
    else:
        price_status = (
            "CURRENT"
            if price_trade_date == expected_market_date
            else "STALE"
        )
        stale = price_status != "CURRENT"
        market_value_cents = _round_ratio(
            price_micros * int(row["quantity"]),
            10_000,
        )
        unrealized_pnl_cents = (
            market_value_cents - int(row["cost_basis_cents"])
        )
        cost_basis_cents = int(row["cost_basis_cents"])
        unrealized_pnl_bps = (
            _round_ratio(unrealized_pnl_cents * 10_000, cost_basis_cents)
            if cost_basis_cents > 0
            else 0
        )
    return {
        "account_id": int(row["account_id"]),
        "symbol": symbol,
        "name": str(row["stock_name"]) if row["stock_name"] else None,
        "industry": (
            str(row["stock_industry"]) if row["stock_industry"] else None
        ),
        "position_revision": int(row["position_revision"]),
        "quantity": int(row["quantity"]),
        "available_quantity": int(row["derived_available_quantity"]),
        "cost_basis_cents": int(row["cost_basis_cents"]),
        "avg_cost_micros": int(row["avg_cost_micros"]),
        "realized_pnl_cents": int(row["realized_pnl_cents"]),
        "status": str(row["status"]),
        "first_buy_date": str(row["first_buy_date"]),
        "last_trade_date": str(row["last_trade_date"]),
        "last_transaction_id": int(row["last_transaction_id"]),
        "price_micros": price_micros,
        "price_trade_date": price_trade_date,
        "price_status": price_status,
        "stale": stale,
        "market_value_cents": market_value_cents,
        "unrealized_pnl_cents": unrealized_pnl_cents,
        "unrealized_pnl_bps": unrealized_pnl_bps,
    }


def _transaction_payload(row: sqlite3.Row) -> dict[str, object]:
    integer_or_none = (
        "counterparty_account_id",
        "quantity",
        "price_micros",
        "gross_amount_cents",
        "target_position_bps",
        "reversal_of",
    )
    result: dict[str, object] = {
        "id": int(row["id"]),
        "account_id": int(row["account_id"]),
        "client_request_id": str(row["client_request_id"]),
        "business_type": str(row["business_type"]),
        "status": str(row["status"]),
        "trade_date": str(row["trade_date"]),
        "occurred_at": str(row["occurred_at"]),
        "symbol": str(row["symbol"]) if row["symbol"] is not None else None,
        "cash_delta_cents": int(row["cash_delta_cents"]),
        "fee_cents": int(row["fee_cents"]),
        "tax_cents": int(row["tax_cents"]),
        "source": str(row["source"]),
        "note": str(row["note"]) if row["note"] is not None else None,
    }
    for column in integer_or_none:
        result[column] = (
            int(row[column]) if row[column] is not None else None
        )
    return result


def _account_snapshot_payload(
    snapshot: AccountDailySnapshot,
) -> dict[str, object]:
    return asdict(snapshot)


def _snapshot_result_payload(
    result: AccountSnapshotResult,
) -> dict[str, object]:
    return {
        "account": _account_snapshot_payload(result.account),
        "positions": [asdict(position) for position in result.positions],
    }


@router.get("/accounts")
def list_accounts() -> list[dict[str, object]]:
    with _map_errors():
        service = _service()
        result = [
            _account_payload(
                account,
                service.get_account_summary(account.id).cash_cents,
                service.ledger_revision(account.id),
            )
            for account in service.list_accounts()
        ]
        with closing(_connect_read()) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for account in result:
                account["baseline_date"] = None
                account["derived_data_pending"] = False
                if "data_reconciliation_audits" in tables:
                    row = conn.execute("SELECT details_json FROM data_reconciliation_audits "
                        "WHERE account_id=? ORDER BY created_at DESC LIMIT 1", (account["id"],)).fetchone()
                    if row:
                        details = json.loads(row[0])
                        account["baseline_date"] = details.get("baseline_date")
                        account["net_contributions_cents"] = details.get("actual_cumulative_net_contributions_cents")
                if "portfolio_import_drafts" in tables:
                    row = conn.execute("SELECT result_json FROM portfolio_import_drafts WHERE account_id=? "
                        "AND committed_at IS NOT NULL ORDER BY committed_at DESC,rowid DESC LIMIT 1",
                        (account["id"],)).fetchone()
                    if row:
                        receipt = json.loads(row[0])
                        account["derived_data_pending"] = receipt.get("performance_status") != "COMPLETE"
        return result


@router.get("/statement")
def get_statement(account_id: int = Query(gt=0)) -> dict[str, object]:
    """Read bounded account history with reversal flags from the entire ledger."""
    history_limit = 10_000
    with _map_errors():
        _service().get_account_summary(account_id)
        with closing(_connect_read()) as conn:
            rows = conn.execute(
                """
                SELECT transaction_row.*, stock_info.name AS stock_name,
                       EXISTS (
                           SELECT 1 FROM portfolio_transactions AS reversal
                           WHERE reversal.reversal_of = transaction_row.id
                             AND reversal.business_type = 'REVERSAL'
                       ) AS is_reversed
                FROM portfolio_transactions AS transaction_row
                LEFT JOIN stock_info ON stock_info.symbol = transaction_row.symbol
                WHERE transaction_row.account_id = ?
                   OR transaction_row.counterparty_account_id = ?
                   OR EXISTS (
                       SELECT 1 FROM portfolio_transactions AS original
                       WHERE original.id = transaction_row.reversal_of
                         AND original.counterparty_account_id = ?
                   )
                ORDER BY transaction_row.trade_date DESC,
                         transaction_row.occurred_at DESC, transaction_row.id DESC
                LIMIT ?
                """,
                (account_id, account_id, account_id, history_limit + 1),
            ).fetchall()
            transactions = [
                {
                    **_transaction_payload(row),
                    "name": row["stock_name"] or row["symbol"],
                    "is_reversed": bool(row["is_reversed"]),
                }
                for row in rows[:history_limit]
            ]
            return {
                "transactions": transactions,
                "truncated": len(rows) > history_limit,
                "limit": history_limit,
            }


@router.post(
    "/accounts",
    status_code=status.HTTP_201_CREATED,
)
def create_account(request: AccountCreate) -> dict[str, object]:
    with _map_errors():
        service = _service()
        account = service.create_account(
            name=request.name,
            broker_name=request.broker_name,
            account_type=request.account_type,
            currency=request.currency,
            opening_cash_cents=request.opening_cash_cents,
            is_default=request.is_default,
        )
        return _account_payload(
            account,
            service.get_account_summary(account.id).cash_cents,
            service.ledger_revision(account.id),
        )


@router.post(
    "/accounts/{account_id}/cash-flows",
    status_code=status.HTTP_201_CREATED,
)
def post_cash_flow(
    request: CashFlowCreate,
    account_id: int = ApiPath(gt=0),
) -> dict[str, object]:
    with _map_errors():
        result = _service().post_cash_flow(
            account_id=account_id,
            client_request_id=request.client_request_id,
            business_type=request.business_type,
            trade_date=request.trade_date.isoformat(),
            occurred_at=request.occurred_at.isoformat(),
            amount_cents=request.amount_cents,
            expected_ledger_revision=request.expected_ledger_revision,
            note=request.note,
        )
        return asdict(result)


@router.post(
    "/accounts/{account_id}/performance/snapshots/{snapshot_date}",
    status_code=status.HTTP_201_CREATED,
)
def build_performance_snapshot(
    account_id: int = ApiPath(gt=0),
    snapshot_date: date = ApiPath(),
) -> dict[str, object]:
    with _map_errors():
        result = _performance_service().build_account_snapshot(
            account_id,
            snapshot_date.isoformat(),
        )
        return _snapshot_result_payload(result)


@router.get("/accounts/{account_id}/performance/snapshots")
def list_performance_snapshots(
    account_id: int = ApiPath(gt=0),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=400, ge=1, le=1_500),
) -> list[dict[str, object]]:
    with _map_errors():
        snapshots = _performance_service().get_account_snapshots(
            account_id,
            start_date=(
                start_date.isoformat() if start_date is not None else None
            ),
            end_date=end_date.isoformat() if end_date is not None else None,
        )
        return [
            _account_snapshot_payload(snapshot)
            for snapshot in snapshots[-limit:]
        ]


@router.get("/accounts/{account_id}/performance/summary")
def get_performance_summary(
    account_id: int = ApiPath(gt=0),
    as_of_date: date | None = Query(default=None),
) -> dict[str, dict[str, object]]:
    with _map_errors():
        summaries = _performance_service().summarize_account_periods(
            account_id,
            as_of_date=(
                as_of_date.isoformat() if as_of_date is not None else None
            ),
        )
        return {
            period: asdict(summary)
            for period, summary in summaries.items()
        }


@router.get("/accounts/{account_id}/exit-plans")
def list_exit_plans(
    account_id: int = ApiPath(gt=0),
) -> list[dict[str, object]]:
    with _map_errors():
        _service().get_account_summary(account_id)
        return [
            asdict(plan)
            for plan in _exit_service().list_plans(account_id=account_id)
        ]


@router.put("/accounts/{account_id}/exit-plans/{symbol}")
def update_exit_plan(
    request: ExitPlanUpdate,
    account_id: int = ApiPath(gt=0),
    symbol: str = ApiPath(pattern=r"^[0-9]{6}$"),
) -> dict[str, object]:
    with _map_errors():
        _service().get_account_summary(account_id)
        plan = _exit_service().upsert_plan(
            account_id=account_id,
            symbol=symbol,
            **request.model_dump(),
        )
        return asdict(plan)


@router.get("/accounts/{account_id}/exit-states")
def list_exit_states(
    account_id: int = ApiPath(gt=0),
) -> list[dict[str, object]]:
    with _map_errors():
        _service().get_account_summary(account_id)
        return [
            asdict(state)
            for state in _exit_service().list_states(account_id=account_id)
        ]


@router.get("/accounts/{account_id}/exit-events")
def list_exit_events(
    account_id: int = ApiPath(gt=0),
    event_status: Literal[
        "ALL",
        "OPEN",
        "ACKNOWLEDGED",
        "RESOLVED",
    ] = Query(default="ALL", alias="status"),
) -> list[dict[str, object]]:
    with _map_errors():
        _service().get_account_summary(account_id)
        return [
            asdict(event)
            for event in _exit_service().list_events(
                account_id=account_id,
                event_status=None if event_status == "ALL" else event_status,
            )
        ]


@router.post("/exit-events/{event_id}/acknowledge")
def acknowledge_exit_event(
    event_id: int = ApiPath(gt=0),
) -> dict[str, object]:
    with _map_errors():
        return asdict(_exit_service().acknowledge_event(event_id))


@router.post("/exit-events/{event_id}/resolve")
def resolve_exit_event(
    event_id: int = ApiPath(gt=0),
) -> dict[str, object]:
    with _map_errors():
        return asdict(_exit_service().resolve_event(event_id))


@router.post("/accounts/{account_id}/exit-evaluate-daily")
def evaluate_exit_daily(
    request: ExitDailyEvaluationRequest,
    account_id: int = ApiPath(gt=0),
) -> dict[str, object]:
    with _map_errors():
        _service().get_account_summary(account_id)
        result = _exit_service().run_daily(
            request.trade_date.isoformat(),
            account_id=account_id,
        )
        return {
            **asdict(result),
            "created_event_count": result.created_event_count,
        }


@router.post("/trades/preview")
def preview_trade(request: TradePreviewRequest) -> dict[str, object]:
    with _map_errors():
        preview = _service().preview_trade(**_preview_kwargs(request))
        return asdict(preview)


@router.post(
    "/trades",
    status_code=status.HTTP_201_CREATED,
)
def post_trade(request: TradeCreate) -> dict[str, object]:
    with _map_errors():
        transaction = _service().post_trade(
            **_preview_kwargs(request),
            client_request_id=request.client_request_id,
            occurred_at=request.occurred_at.isoformat(),
            expected_ledger_revision=request.expected_ledger_revision,
            expected_quantity=request.expected_quantity,
            expected_gross_amount_cents=request.expected_gross_amount_cents,
            expected_cash_delta_cents=request.expected_cash_delta_cents,
        )
        return asdict(transaction)


@router.get("/positions")
def list_positions(
    account_id: int = Query(gt=0),
    as_of_date: date | None = Query(default=None),
    position_status: Literal["ALL", "OPEN", "CLOSED"] = Query(
        default="ALL",
        alias="status",
    ),
) -> list[dict[str, object]]:
    requested_date = as_of_date or date.today()
    requested_iso = requested_date.isoformat()
    with _map_errors():
        _service().get_account_summary(account_id)
        where_status = ""
        with closing(_connect_read()) as conn:
            latest_ledger_row = conn.execute(
                """
                SELECT MAX(transaction_row.trade_date)
                FROM portfolio_transactions AS transaction_row
                WHERE transaction_row.account_id = ?
                  AND transaction_row.status = 'POSTED'
                  AND transaction_row.business_type IN (
                      'BUY', 'SELL', 'CORPORATE_ACTION'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM portfolio_transactions AS reversal
                      WHERE reversal.reversal_of = transaction_row.id
                        AND reversal.status = 'POSTED'
                  )
                """,
                (account_id,),
            ).fetchone()
            latest_ledger_date = (
                str(latest_ledger_row[0])
                if latest_ledger_row and latest_ledger_row[0] is not None
                else None
            )
            if (
                latest_ledger_date is not None
                and requested_iso < latest_ledger_date
            ):
                raise InvalidTradeError(
                    "as_of_date cannot precede the latest position ledger trade_date"
                )

            expected_market_row = conn.execute(
                """
                SELECT MAX(trade_date)
                FROM stock_daily_prices
                WHERE trade_date <= ? AND close IS NOT NULL
                """,
                (requested_iso,),
            ).fetchone()
            expected_market_date = (
                str(expected_market_row[0])
                if expected_market_row and expected_market_row[0] is not None
                else None
            )
            parameters: list[object] = [
                account_id,
                requested_iso,
                requested_iso,
                requested_iso,
                account_id,
            ]
            if position_status != "ALL":
                where_status = "AND position_row.status = ?"
                parameters.append(position_status)
            rows = conn.execute(
                f"""
                WITH effective_transactions AS (
                    SELECT transaction_row.*
                    FROM portfolio_transactions AS transaction_row
                    WHERE transaction_row.account_id = ?
                      AND transaction_row.trade_date <= ?
                      AND transaction_row.status = 'POSTED'
                      AND transaction_row.business_type IN (
                          'BUY', 'SELL', 'CORPORATE_ACTION'
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM portfolio_transactions AS reversal
                          WHERE reversal.reversal_of = transaction_row.id
                            AND reversal.status = 'POSTED'
                      )
                ),
                derived_quantities AS (
                    SELECT
                        symbol,
                        SUM(
                            CASE
                                WHEN business_type IN (
                                    'BUY', 'CORPORATE_ACTION'
                                )
                                 AND trade_date < ?
                                THEN quantity
                                WHEN business_type = 'SELL'
                                THEN -quantity
                                ELSE 0
                            END
                        ) AS available_quantity
                    FROM effective_transactions
                    GROUP BY symbol
                )
                SELECT
                    position_row.*,
                    COALESCE(
                        derived_quantities.available_quantity,
                        0
                    ) AS derived_available_quantity,
                    stock_info.name AS stock_name,
                    stock_info.industry AS stock_industry,
                    quote.trade_date AS quote_trade_date,
                    quote.close AS quote_close
                FROM portfolio_positions AS position_row
                LEFT JOIN derived_quantities
                  ON derived_quantities.symbol = position_row.symbol
                LEFT JOIN stock_info
                  ON stock_info.symbol = position_row.symbol
                LEFT JOIN stock_daily_prices AS quote
                  ON quote.symbol = position_row.symbol
                 AND quote.trade_date = (
                     SELECT quote_date.trade_date
                     FROM stock_daily_prices AS quote_date
                     WHERE quote_date.symbol = position_row.symbol
                       AND quote_date.trade_date <= ?
                       AND quote_date.close IS NOT NULL
                     ORDER BY quote_date.trade_date DESC
                     LIMIT 1
                 )
                WHERE position_row.account_id = ?
                  {where_status}
                ORDER BY position_row.symbol
                """,
                parameters,
            ).fetchall()
            return [
                _position_payload(
                    row,
                    expected_market_date=expected_market_date,
                )
                for row in rows
            ]


@router.get("/transactions")
def list_transactions(
    account_id: int = Query(gt=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, object]]:
    with _map_errors():
        _service().get_account_summary(account_id)
        with closing(_connect_read()) as conn:
            rows = conn.execute(
                """
                SELECT transaction_row.*
                FROM portfolio_transactions AS transaction_row
                WHERE transaction_row.account_id = ?
                   OR transaction_row.counterparty_account_id = ?
                   OR EXISTS (
                        SELECT 1
                        FROM portfolio_transactions AS original
                        WHERE original.id = transaction_row.reversal_of
                          AND original.counterparty_account_id = ?
                   )
                ORDER BY trade_date DESC, occurred_at DESC, id DESC
                LIMIT ?
                """,
                (account_id, account_id, account_id, limit),
            ).fetchall()
            return [_transaction_payload(row) for row in rows]


@router.post(
    "/transactions/{transaction_id}/reverse",
    status_code=status.HTTP_201_CREATED,
)
def reverse_transaction(
    transaction_id: int,
    request: TransactionReverse,
) -> dict[str, object]:
    if transaction_id <= 0:
        raise HTTPException(status_code=422, detail="transaction_id must be positive")
    with _map_errors():
        transaction = _service().reverse_transaction(
            account_id=request.account_id,
            transaction_id=transaction_id,
            client_request_id=request.client_request_id,
            trade_date=request.trade_date.isoformat(),
            occurred_at=request.occurred_at.isoformat(),
        )
        return asdict(transaction)


class ImportPreviewRequest(_StrictRequest):
    account_id: int = Field(gt=0, strict=True)
    rows: list[dict[str, object]] = Field(min_length=1, max_length=200)
    source_hashes: list[str] = Field(default_factory=list, max_length=8)
    client_request_id: str = Field(min_length=1, max_length=100)
    replace_transaction_ids: list[int] = Field(default_factory=list, max_length=200)


class ImportCommitRequest(_StrictRequest):
    draft_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: int = Field(gt=0, strict=True)
    client_request_id: str = Field(min_length=1, max_length=100)


@router.post('/imports/recognize')
async def recognize_import(request: Request) -> dict[str, object]:
    """Local-only OCR. Enforce upload limits before multipart spooling starts."""
    from portfolio.screenshot_parser import recognize_images

    length = request.headers.get('content-length')
    if length is None or not length.isdigit():
        raise HTTPException(status_code=411, detail='上传需要明确的文件大小')
    if int(length) > 34 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='截图总大小不能超过32MB')
    with _map_errors():
        async with request.form(max_files=8, max_fields=3) as form:
            account_id = int(str(form.get('account_id', '0')))
            account = await run_in_threadpool(_service().get_account_summary, account_id)
            if account.account.status != 'ACTIVE':
                raise ValueError('归档账户不能导入')
            files = form.getlist('images')
            if not 1 <= len(files) <= 8 or any(not isinstance(file, UploadFile) for file in files):
                raise ValueError('请选择1至8张截图')
            images = []
            total = 0
            for file in files:
                image = await file.read(8 * 1024 * 1024 + 1)
                total += len(image)
                if len(image) > 8 * 1024 * 1024 or total > 32 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail='单张限8MB，总大小限32MB')
                images.append(image)
            raw_year = form.get('reference_year')
            year = int(str(raw_year)) if raw_year not in (None, '') else None
            result = await run_in_threadpool(recognize_images, images, year)
        # Exact local name matching only; ambiguous names stay editable.
        with closing(_connect_read()) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='stock_info'").fetchone():
                names = {str(row['name']) for row in result['rows'] if row.get('name')}
                mapping = {}
                for name in names:
                    matches = conn.execute('SELECT symbol FROM stock_info WHERE name=? LIMIT 2',
                                           (name,)).fetchall()
                    if len(matches) == 1:
                        mapping[name] = matches[0][0]
                for row in result['rows']:
                    if not row.get('symbol') and row.get('name') in mapping:
                        row['symbol'] = mapping[row['name']]
        return result


@router.post('/imports/preview')
def preview_import(request: ImportPreviewRequest) -> dict[str, object]:
    with _map_errors():
        return PortfolioImportService(PORTFOLIO_DB_PATH).preview(**request.model_dump())


@router.post('/imports/commit')
def commit_import(request: ImportCommitRequest) -> dict[str, object]:
    with _map_errors():
        service = PortfolioImportService(PORTFOLIO_DB_PATH)
        result = service.commit(**request.model_dump())
        if result['performance_status'] != 'COMPLETE':
            refreshed = service.refresh_after_commit(request.draft_id)
            refreshed['duplicate_replay'] = result['duplicate_replay']
            return refreshed
        return result
