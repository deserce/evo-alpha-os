"""Deterministic daily performance snapshots built from the portfolio ledger."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio.accounting import (
    AccountAsOfProjection,
    PortfolioAccountingService,
    ProjectedPosition,
)
from portfolio.schema import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    configure_portfolio_connection,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MICRO_WEIGHT = 1_000_000
_PPB = 1_000_000_000
_RAW_SOURCE = "tdx_mootdx_raw"
_RAW_QUALITY_FLAGS = ("ok_unadjusted",)
_MAX_REBUILD_DATES = 500


class PortfolioPerformanceError(RuntimeError):
    """Base error for portfolio performance snapshot operations."""


class InvalidSnapshotDateError(PortfolioPerformanceError):
    """Raised when a date is malformed or is not an observed trading day."""


class SnapshotAccountError(PortfolioPerformanceError):
    """Raised when an account cannot safely receive derived snapshots."""


class SnapshotConsistencyError(PortfolioPerformanceError):
    """Raised when aggregate state is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class PositionDailySnapshot:
    account_id: int
    snapshot_date: str
    symbol: str
    position_revision: int
    quantity: int
    available_quantity: int
    cost_basis_cents: int
    realized_pnl_cents: int
    price_micros: int | None
    price_date: str | None
    market_value_cents: int | None
    unrealized_pnl_cents: int | None
    price_status: str
    last_transaction_id: int


@dataclass(frozen=True, slots=True)
class AccountDailySnapshot:
    account_id: int
    snapshot_date: str
    ledger_revision: int
    cash_cents: int
    market_value_cents: int | None
    total_equity_cents: int | None
    net_external_flow_cents: int
    weighted_external_flow_micros: int
    pnl_cents: int | None
    return_ppb: int | None
    realized_pnl_cents: int
    unrealized_pnl_cents: int | None
    position_count: int
    unpriced_position_count: int
    price_status: str
    expected_price_date: str
    performance_status: str
    return_base_date: str | None


@dataclass(frozen=True, slots=True)
class AccountSnapshotResult:
    account: AccountDailySnapshot
    positions: tuple[PositionDailySnapshot, ...]


@dataclass(frozen=True, slots=True)
class PeriodPerformanceSummary:
    period: str
    start_date: str | None
    end_date: str | None
    status: str
    trading_day_count: int
    net_external_flow_cents: int | None
    pnl_cents: int | None
    return_ppb: int | None
    end_equity_cents: int | None
    end_realized_pnl_cents: int | None
    end_unrealized_pnl_cents: int | None


class PortfolioPerformanceService:
    """Build immutable-ledger-derived daily snapshots using raw close prices."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        accounting_service: PortfolioAccountingService | None = None,
    ) -> None:
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
            raise TypeError("busy_timeout_ms must be an integer")
        if not 0 <= busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS:
            raise ValueError(
                f"busy_timeout_ms must be between 0 and {MAX_BUSY_TIMEOUT_MS}"
            )
        self._db_path = Path(db_path).expanduser()
        self._busy_timeout_ms = busy_timeout_ms
        self._accounting = accounting_service or PortfolioAccountingService(
            self._db_path,
            busy_timeout_ms=busy_timeout_ms,
        )
        self._raw_price_batch_query_count = 0

    @property
    def raw_price_batch_query_count(self) -> int:
        """Expose the batch-query count for diagnostics and regression tests."""
        return self._raw_price_batch_query_count

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path,
            timeout=self._busy_timeout_ms / 1_000,
        )
        try:
            conn.row_factory = sqlite3.Row
            configure_portfolio_connection(
                conn,
                busy_timeout_ms=self._busy_timeout_ms,
            )
        except Exception:
            conn.close()
            raise
        return conn

    def _connect_read_only(self) -> sqlite3.Connection:
        database_uri = f"{self._db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(
            database_uri,
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

    def build_account_snapshot(
        self,
        account_id: int,
        snapshot_date: str,
    ) -> AccountSnapshotResult:
        """Build and atomically upsert one account's snapshot for a trading day."""
        self._require_account_id(account_id)
        self._validate_iso_date(snapshot_date)
        projection = self._accounting.project_account_as_of(
            account_id,
            snapshot_date,
        )
        if projection.account.id != account_id:
            raise SnapshotConsistencyError("account projection identity mismatch")
        projections = tuple(
            position
            for position in projection.positions
            if position.status == "OPEN" and position.quantity > 0
        )
        self._validate_projections(account_id, projections)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                account_row = self._active_account(conn, account_id)
                self._require_trading_day(conn, snapshot_date)
                self._reject_out_of_order_snapshot(
                    conn,
                    account_id,
                    snapshot_date,
                )
                result = self._build_snapshot_in_transaction(
                    conn,
                    account_row=account_row,
                    account_id=account_id,
                    snapshot_date=snapshot_date,
                    projection=projection,
                    projections=projections,
                )
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def rebuild_account_snapshots(
        self,
        account_id: int,
        start_date: str,
        end_date: str,
    ) -> tuple[AccountSnapshotResult, ...]:
        """Atomically rebuild a bounded, exact-price trading-day range."""
        self._require_account_id(account_id)
        self._validate_iso_date(start_date)
        self._validate_iso_date(end_date)
        if start_date > end_date:
            raise InvalidSnapshotDateError(
                "start_date cannot be after end_date"
            )

        trading_dates = self._trading_dates_between(start_date, end_date)
        if not trading_dates or trading_dates[0] != start_date:
            raise InvalidSnapshotDateError(
                f"{start_date} is not an observed market trading day"
            )
        if trading_dates[-1] != end_date:
            raise InvalidSnapshotDateError(
                f"{end_date} is not an observed market trading day"
            )
        if len(trading_dates) > _MAX_REBUILD_DATES:
            raise InvalidSnapshotDateError(
                f"snapshot rebuild cannot exceed {_MAX_REBUILD_DATES} trading days"
            )

        prepared = []
        for snapshot_date in trading_dates:
            projection = self._accounting.project_account_as_of(
                account_id,
                snapshot_date,
            )
            if projection.account.id != account_id:
                raise SnapshotConsistencyError(
                    "account projection identity mismatch"
                )
            projections = tuple(
                position
                for position in projection.positions
                if position.status == "OPEN" and position.quantity > 0
            )
            self._validate_projections(account_id, projections)
            prepared.append((snapshot_date, projection, projections))

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                account_row = self._active_account(conn, account_id)
                self._reject_out_of_order_snapshot(conn, account_id, end_date)
                exact_prices: dict[str, dict[str, tuple[str, int]]] = {}
                for snapshot_date, projection, projections in prepared:
                    self._assert_projection_current(
                        conn,
                        account_row=account_row,
                        account_id=account_id,
                        snapshot_date=snapshot_date,
                        projection=projection,
                    )
                    prices = self._load_latest_raw_prices(
                        conn,
                        tuple(position.symbol for position in projections),
                        snapshot_date,
                    )
                    missing = tuple(
                        position.symbol
                        for position in projections
                        if position.symbol not in prices
                        or prices[position.symbol][0] != snapshot_date
                    )
                    if missing:
                        raise SnapshotConsistencyError(
                            "exact raw close is unavailable for snapshot rebuild"
                        )
                    exact_prices[snapshot_date] = prices

                conn.execute(
                    """
                    DELETE FROM portfolio_position_daily_snapshots
                    WHERE account_id = ?
                      AND snapshot_date BETWEEN ? AND ?
                    """,
                    (account_id, start_date, end_date),
                )
                conn.execute(
                    """
                    DELETE FROM portfolio_account_daily_snapshots
                    WHERE account_id = ?
                      AND snapshot_date BETWEEN ? AND ?
                    """,
                    (account_id, start_date, end_date),
                )

                results = []
                for snapshot_date, projection, projections in prepared:
                    result = self._build_snapshot_in_transaction(
                        conn,
                        account_row=account_row,
                        account_id=account_id,
                        snapshot_date=snapshot_date,
                        projection=projection,
                        projections=projections,
                        prices=exact_prices[snapshot_date],
                    )
                    if result.account.performance_status not in {
                        "INITIAL",
                        "COMPLETE",
                    }:
                        raise SnapshotConsistencyError(
                            "snapshot rebuild did not produce a continuous range"
                        )
                    results.append(result)
                conn.commit()
                return tuple(results)
            except Exception:
                conn.rollback()
                raise

    def _trading_dates_between(
        self,
        start_date: str,
        end_date: str,
    ) -> tuple[str, ...]:
        try:
            with closing(self._connect_read_only()) as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT trade_date
                    FROM stock_daily_prices
                    WHERE trade_date BETWEEN ? AND ?
                    ORDER BY trade_date
                    """,
                    (start_date, end_date),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            raise InvalidSnapshotDateError(
                "market trading calendar is unavailable"
            ) from exc
        return tuple(str(row["trade_date"]) for row in rows)

    def _assert_projection_current(
        self,
        conn: sqlite3.Connection,
        *,
        account_row: sqlite3.Row,
        account_id: int,
        snapshot_date: str,
        projection: AccountAsOfProjection,
    ) -> None:
        current_revision = self._ledger_revision_as_of(
            conn,
            account_id,
            snapshot_date,
        )
        if current_revision != projection.ledger_revision:
            raise SnapshotConsistencyError(
                "account projection is stale; retry snapshot build"
            )
        if (
            int(account_row["opening_cash_cents"])
            != projection.account.opening_cash_cents
        ):
            raise SnapshotConsistencyError(
                "account projection is stale; retry snapshot build"
            )

    def _build_snapshot_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        account_row: sqlite3.Row,
        account_id: int,
        snapshot_date: str,
        projection: AccountAsOfProjection,
        projections: tuple[ProjectedPosition, ...],
        prices: dict[str, tuple[str, int]] | None = None,
    ) -> AccountSnapshotResult:
        previous_trading_date = self._require_trading_day(
            conn,
            snapshot_date,
        )
        self._assert_projection_current(
            conn,
            account_row=account_row,
            account_id=account_id,
            snapshot_date=snapshot_date,
            projection=projection,
        )
        resolved_prices = prices
        if resolved_prices is None:
            resolved_prices = self._load_latest_raw_prices(
                conn,
                tuple(position.symbol for position in projections),
                snapshot_date,
            )
        position_snapshots = self._build_position_snapshots(
            account_id,
            snapshot_date,
            projections,
            resolved_prices,
        )
        flow_rows = self._external_flow_rows(
            conn,
            account_id,
            snapshot_date,
        )
        net_flow, weighted_flow, cumulative_flow = (
            self._flow_totals_for_account(
                flow_rows,
                snapshot_date,
                account_id,
            )
        )
        prior_row, earlier_count = self._prior_snapshot_state(
            conn,
            account_id,
            snapshot_date,
            previous_trading_date,
        )
        prior_revision_matches = (
            prior_row is None
            or previous_trading_date is None
            or int(prior_row["ledger_revision"])
            == self._ledger_revision_as_of(
                conn,
                account_id,
                previous_trading_date,
            )
        )
        account_snapshot = self._build_account_snapshot(
            account_id=account_id,
            snapshot_date=snapshot_date,
            ledger_revision=projection.ledger_revision,
            opening_cash_cents=int(account_row["opening_cash_cents"]),
            cash_cents=projection.cash_cents,
            positions=position_snapshots,
            net_external_flow_cents=net_flow,
            weighted_external_flow_micros=weighted_flow,
            cumulative_external_flow_cents=cumulative_flow,
            prior_row=prior_row,
            prior_revision_matches=prior_revision_matches,
            earlier_snapshot_count=earlier_count,
            previous_trading_date=previous_trading_date,
        )
        self._validate_aggregate(account_snapshot, position_snapshots)
        self._replace_snapshots(conn, account_snapshot, position_snapshots)
        return AccountSnapshotResult(
            account=account_snapshot,
            positions=position_snapshots,
        )

    def get_account_snapshots(
        self,
        account_id: int,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[AccountDailySnapshot, ...]:
        """Return persisted account snapshots in chronological order."""
        self._require_account_id(account_id)
        if start_date is not None:
            self._validate_iso_date(start_date)
        if end_date is not None:
            self._validate_iso_date(end_date)
        if start_date and end_date and start_date > end_date:
            raise InvalidSnapshotDateError("start_date cannot be after end_date")
        clauses = ["account_id = ?"]
        parameters: list[object] = [account_id]
        if start_date is not None:
            clauses.append("snapshot_date >= ?")
            parameters.append(start_date)
        if end_date is not None:
            clauses.append("snapshot_date <= ?")
            parameters.append(end_date)
        with closing(self._connect()) as conn:
            if conn.execute(
                "SELECT 1 FROM portfolio_accounts WHERE id = ?",
                (account_id,),
            ).fetchone() is None:
                raise SnapshotAccountError("portfolio account does not exist")
            rows = conn.execute(
                f"""
                SELECT *
                FROM portfolio_account_daily_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY snapshot_date
                """,
                parameters,
            ).fetchall()
        return tuple(self._account_from_row(row) for row in rows)

    def summarize_account_periods(
        self,
        account_id: int,
        as_of_date: str | None = None,
    ) -> dict[str, PeriodPerformanceSummary]:
        """Summarize daily snapshots into day, week, month and year periods."""
        self._require_account_id(account_id)
        if as_of_date is not None:
            self._validate_iso_date(as_of_date)

        with closing(self._connect_read_only()) as conn:
            if conn.execute(
                "SELECT 1 FROM portfolio_accounts WHERE id = ?",
                (account_id,),
            ).fetchone() is None:
                raise SnapshotAccountError("portfolio account does not exist")

            selected = conn.execute(
                """
                SELECT *
                FROM portfolio_account_daily_snapshots
                WHERE account_id = ?
                  AND (? IS NULL OR snapshot_date <= ?)
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (account_id, as_of_date, as_of_date),
            ).fetchone()
            if selected is None:
                return self._empty_period_summaries()

            selected_date = date.fromisoformat(str(selected["snapshot_date"]))
            year_start = selected_date.replace(month=1, day=1)
            first_snapshot_date = conn.execute(
                """
                SELECT MIN(snapshot_date)
                FROM portfolio_account_daily_snapshots
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()[0]
            snapshot_rows = conn.execute(
                """
                SELECT *
                FROM portfolio_account_daily_snapshots
                WHERE account_id = ?
                  AND snapshot_date BETWEEN ? AND ?
                ORDER BY snapshot_date
                """,
                (
                    account_id,
                    year_start.isoformat(),
                    selected_date.isoformat(),
                ),
            ).fetchall()
            try:
                trading_rows = conn.execute(
                    """
                    SELECT DISTINCT trade_date
                    FROM stock_daily_prices
                    WHERE trade_date BETWEEN ? AND ?
                    ORDER BY trade_date
                    """,
                    (year_start.isoformat(), selected_date.isoformat()),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                raise InvalidSnapshotDateError(
                    "market trading calendar is unavailable"
                ) from exc

        first_date = date.fromisoformat(str(first_snapshot_date))
        snapshots_by_date = {
            str(row["snapshot_date"]): row for row in snapshot_rows
        }
        trading_dates = tuple(
            date.fromisoformat(str(row["trade_date"])) for row in trading_rows
        )
        period_starts = (
            ("DAY", selected_date),
            (
                "WEEK",
                selected_date - timedelta(days=selected_date.weekday()),
            ),
            ("MONTH", selected_date.replace(day=1)),
            ("YEAR", year_start),
        )
        return {
            period: self._summarize_period(
                period=period,
                boundary_start=boundary_start,
                selected_date=selected_date,
                first_snapshot_date=first_date,
                snapshots_by_date=snapshots_by_date,
                trading_dates=trading_dates,
            )
            for period, boundary_start in period_starts
        }

    @staticmethod
    def _empty_period_summaries() -> dict[str, PeriodPerformanceSummary]:
        return {
            period: PeriodPerformanceSummary(
                period=period,
                start_date=None,
                end_date=None,
                status="EMPTY",
                trading_day_count=0,
                net_external_flow_cents=None,
                pnl_cents=None,
                return_ppb=None,
                end_equity_cents=None,
                end_realized_pnl_cents=None,
                end_unrealized_pnl_cents=None,
            )
            for period in ("DAY", "WEEK", "MONTH", "YEAR")
        }

    @classmethod
    def _summarize_period(
        cls,
        *,
        period: str,
        boundary_start: date,
        selected_date: date,
        first_snapshot_date: date,
        snapshots_by_date: dict[str, sqlite3.Row],
        trading_dates: tuple[date, ...],
    ) -> PeriodPerformanceSummary:
        effective_start = max(boundary_start, first_snapshot_date)
        expected_dates = tuple(
            item
            for item in trading_dates
            if effective_start <= item <= selected_date
        )
        rows = tuple(
            snapshots_by_date.get(item.isoformat()) for item in expected_dates
        )
        present_rows = tuple(row for row in rows if row is not None)
        unavailable = any(
            row["performance_status"] == "UNAVAILABLE" for row in present_rows
        )
        starts_from_account_baseline = first_snapshot_date >= boundary_start
        gap = (
            not expected_dates
            or expected_dates[-1] != selected_date
            or (
                starts_from_account_baseline
                and (
                    expected_dates[0] != first_snapshot_date
                    or not present_rows
                    or present_rows[0]["performance_status"] != "INITIAL"
                )
            )
            or len(present_rows) != len(expected_dates)
            or any(row["performance_status"] == "GAP" for row in present_rows)
            or any(
                row["performance_status"] == "INITIAL"
                and (
                    str(row["snapshot_date"]) != first_snapshot_date.isoformat()
                    or first_snapshot_date != effective_start
                )
                for row in present_rows
            )
            or any(
                row["performance_status"] not in {"INITIAL", "COMPLETE"}
                for row in present_rows
                if row["performance_status"] != "UNAVAILABLE"
            )
            or any(
                row["performance_status"] == "COMPLETE"
                and (row["pnl_cents"] is None or row["return_ppb"] is None)
                for row in present_rows
            )
        )
        if unavailable or gap:
            return cls._invalid_period_summary(
                period=period,
                start_date=(
                    expected_dates[0].isoformat()
                    if expected_dates
                    else effective_start.isoformat()
                ),
                end_date=selected_date.isoformat(),
                status="UNAVAILABLE" if unavailable else "GAP",
                trading_day_count=len(expected_dates),
            )

        complete_rows = tuple(row for row in rows if row is not None)
        end_row = complete_rows[-1]
        return PeriodPerformanceSummary(
            period=period,
            start_date=expected_dates[0].isoformat(),
            end_date=selected_date.isoformat(),
            status=(
                "INITIAL"
                if end_row["performance_status"] == "INITIAL"
                else "COMPLETE"
            ),
            trading_day_count=len(expected_dates),
            net_external_flow_cents=sum(
                int(row["net_external_flow_cents"]) for row in complete_rows
            ),
            pnl_cents=sum(int(row["pnl_cents"]) for row in complete_rows),
            return_ppb=cls._compound_returns_ppb(complete_rows),
            end_equity_cents=int(end_row["total_equity_cents"]),
            end_realized_pnl_cents=int(end_row["realized_pnl_cents"]),
            end_unrealized_pnl_cents=int(end_row["unrealized_pnl_cents"]),
        )

    @staticmethod
    def _invalid_period_summary(
        *,
        period: str,
        start_date: str,
        end_date: str,
        status: str,
        trading_day_count: int,
    ) -> PeriodPerformanceSummary:
        return PeriodPerformanceSummary(
            period=period,
            start_date=start_date,
            end_date=end_date,
            status=status,
            trading_day_count=trading_day_count,
            net_external_flow_cents=None,
            pnl_cents=None,
            return_ppb=None,
            end_equity_cents=None,
            end_realized_pnl_cents=None,
            end_unrealized_pnl_cents=None,
        )

    @staticmethod
    def _compound_returns_ppb(rows: tuple[sqlite3.Row, ...]) -> int:
        with localcontext() as context:
            context.prec = max(50, len(rows) * 12)
            growth = Decimal(1)
            ppb = Decimal(_PPB)
            for row in rows:
                daily_return = Decimal(int(row["return_ppb"])) / ppb
                growth *= Decimal(1) + daily_return
            return int(
                ((growth - Decimal(1)) * ppb).quantize(
                    Decimal(1),
                    rounding=ROUND_HALF_UP,
                )
            )

    @staticmethod
    def _require_account_id(account_id: int) -> None:
        if (
            isinstance(account_id, bool)
            or not isinstance(account_id, int)
            or account_id <= 0
        ):
            raise ValueError("account_id must be a positive integer")

    @staticmethod
    def _validate_iso_date(value: str) -> None:
        if not isinstance(value, str):
            raise InvalidSnapshotDateError("snapshot date must be YYYY-MM-DD")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise InvalidSnapshotDateError(
                "snapshot date must be YYYY-MM-DD"
            ) from exc
        if parsed.isoformat() != value:
            raise InvalidSnapshotDateError("snapshot date must be YYYY-MM-DD")

    @staticmethod
    def _active_account(
        conn: sqlite3.Connection,
        account_id: int,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM portfolio_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise SnapshotAccountError("portfolio account does not exist")
        if row["status"] != "ACTIVE":
            raise SnapshotAccountError("archived account cannot receive snapshots")
        return row

    @staticmethod
    def _require_trading_day(
        conn: sqlite3.Connection,
        snapshot_date: str,
    ) -> str | None:
        try:
            observed = conn.execute(
                """
                SELECT 1
                FROM stock_daily_prices
                WHERE trade_date = ?
                LIMIT 1
                """,
                (snapshot_date,),
            ).fetchone()
            previous = conn.execute(
                """
                SELECT MAX(trade_date)
                FROM stock_daily_prices
                WHERE trade_date < ?
                """,
                (snapshot_date,),
            ).fetchone()[0]
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            raise InvalidSnapshotDateError(
                "market trading calendar is unavailable"
            ) from exc
        if observed is None:
            raise InvalidSnapshotDateError(
                f"{snapshot_date} is not an observed market trading day"
            )
        return str(previous) if previous is not None else None

    @staticmethod
    def _reject_out_of_order_snapshot(
        conn: sqlite3.Connection,
        account_id: int,
        snapshot_date: str,
    ) -> None:
        later = conn.execute(
            """
            SELECT snapshot_date
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ? AND snapshot_date > ?
            ORDER BY snapshot_date
            LIMIT 1
            """,
            (account_id, snapshot_date),
        ).fetchone()
        if later is not None:
            raise SnapshotConsistencyError(
                "cannot rebuild an account date with a later snapshot present"
            )

    def _load_latest_raw_prices(
        self,
        conn: sqlite3.Connection,
        symbols: tuple[str, ...],
        snapshot_date: str,
    ) -> dict[str, tuple[str, int]]:
        if not symbols:
            return {}
        table_exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'stock_daily_prices_raw'
            """
        ).fetchone()
        if table_exists is None:
            return {}
        self._raw_price_batch_query_count += 1
        placeholders = ", ".join("?" for _ in symbols)
        quality_placeholders = ", ".join("?" for _ in _RAW_QUALITY_FLAGS)
        rows = conn.execute(
            f"""
            SELECT price.symbol, price.trade_date, price.close
            FROM stock_daily_prices_raw AS price
            JOIN (
                SELECT symbol, MAX(trade_date) AS trade_date
                FROM stock_daily_prices_raw
                WHERE symbol IN ({placeholders})
                  AND trade_date <= ?
                  AND adjustment_mode = 'none'
                  AND source = ?
                  AND quality_flag IN ({quality_placeholders})
                GROUP BY symbol
            ) AS latest
              ON latest.symbol = price.symbol
             AND latest.trade_date = price.trade_date
            WHERE price.adjustment_mode = 'none'
              AND price.source = ?
              AND price.quality_flag IN ({quality_placeholders})
            """,
            (
                *symbols,
                snapshot_date,
                _RAW_SOURCE,
                *_RAW_QUALITY_FLAGS,
                _RAW_SOURCE,
                *_RAW_QUALITY_FLAGS,
            ),
        ).fetchall()
        prices: dict[str, tuple[str, int]] = {}
        for row in rows:
            micros = self._raw_close_to_micros(row["close"])
            if micros is not None:
                prices[str(row["symbol"])] = (
                    str(row["trade_date"]),
                    micros,
                )
        return prices

    @staticmethod
    def _raw_close_to_micros(value: object) -> int | None:
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if not price.is_finite() or price <= 0:
            return None
        micros = int(
            (price * Decimal(1_000_000)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        return micros if 1 <= micros <= 10_000_000_000 else None

    @staticmethod
    def _build_position_snapshots(
        account_id: int,
        snapshot_date: str,
        projections: tuple[ProjectedPosition, ...],
        prices: dict[str, tuple[str, int]],
    ) -> tuple[PositionDailySnapshot, ...]:
        snapshots: list[PositionDailySnapshot] = []
        for projection in projections:
            raw = prices.get(projection.symbol)
            if raw is None:
                price_date = None
                price_micros = None
                market_value = None
                unrealized = None
                price_status = "MISSING"
            else:
                price_date, price_micros = raw
                market_value = (
                    price_micros * projection.quantity + 5_000
                ) // 10_000
                unrealized = market_value - projection.cost_basis_cents
                price_status = (
                    "CURRENT" if price_date == snapshot_date else "STALE"
                )
            snapshots.append(
                PositionDailySnapshot(
                    account_id=account_id,
                    snapshot_date=snapshot_date,
                    symbol=projection.symbol,
                    position_revision=projection.position_revision,
                    quantity=projection.quantity,
                    available_quantity=projection.available_quantity,
                    cost_basis_cents=projection.cost_basis_cents,
                    realized_pnl_cents=projection.realized_pnl_cents,
                    price_micros=price_micros,
                    price_date=price_date,
                    market_value_cents=market_value,
                    unrealized_pnl_cents=unrealized,
                    price_status=price_status,
                    last_transaction_id=projection.last_transaction_id,
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _external_flow_rows(
        conn: sqlite3.Connection,
        account_id: int,
        snapshot_date: str,
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT transaction_row.*
            FROM portfolio_transactions AS transaction_row
            WHERE transaction_row.status = 'POSTED'
              AND transaction_row.business_type IN (
                  'DEPOSIT', 'WITHDRAWAL', 'TRANSFER'
              )
              AND transaction_row.trade_date <= ?
              AND (
                    transaction_row.account_id = ?
                    OR transaction_row.counterparty_account_id = ?
              )
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
            (snapshot_date, account_id, account_id),
        ).fetchall()

    @classmethod
    def _flow_totals_for_account(
        cls,
        rows: list[sqlite3.Row],
        snapshot_date: str,
        account_id: int,
    ) -> tuple[int, int, int]:
        daily = 0
        weighted = 0
        cumulative = 0
        for row in rows:
            business_type = str(row["business_type"])
            amount = int(row["gross_amount_cents"])
            if business_type == "DEPOSIT":
                signed = amount
            elif business_type == "WITHDRAWAL":
                signed = -amount
            elif int(row["account_id"]) == account_id:
                signed = -amount
            else:
                signed = amount
            cumulative += signed
            if str(row["trade_date"]) != snapshot_date:
                continue
            daily += signed
            weighted += signed * cls._remaining_day_weight(
                str(row["occurred_at"]),
                snapshot_date,
            )
        return daily, weighted, cumulative

    @staticmethod
    def _remaining_day_weight(occurred_at: str, snapshot_date: str) -> int:
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SnapshotConsistencyError(
                "external flow has invalid occurred_at"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI)
        local = parsed.astimezone(_SHANGHAI)
        if local.date().isoformat() != snapshot_date:
            raise SnapshotConsistencyError(
                "external flow occurred_at does not match trade_date"
            )
        seconds_elapsed = (
            local.hour * 3_600
            + local.minute * 60
            + local.second
            + local.microsecond / 1_000_000
        )
        remaining = Decimal(str(86_400 - seconds_elapsed))
        return int(
            (remaining * _MICRO_WEIGHT / Decimal(86_400)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

    @staticmethod
    def _prior_snapshot_state(
        conn: sqlite3.Connection,
        account_id: int,
        snapshot_date: str,
        previous_trading_date: str | None,
    ) -> tuple[sqlite3.Row | None, int]:
        earlier_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM portfolio_account_daily_snapshots
                WHERE account_id = ? AND snapshot_date < ?
                """,
                (account_id, snapshot_date),
            ).fetchone()[0]
        )
        if previous_trading_date is None:
            return None, earlier_count
        row = conn.execute(
            """
            SELECT *
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ? AND snapshot_date = ?
            """,
            (account_id, previous_trading_date),
        ).fetchone()
        return row, earlier_count

    @staticmethod
    def _ledger_revision_as_of(
        conn: sqlite3.Connection,
        account_id: int,
        as_of_date: str,
    ) -> int:
        """Mirror accounting's retrospective-correction revision semantics."""
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

    @classmethod
    def _build_account_snapshot(
        cls,
        *,
        account_id: int,
        snapshot_date: str,
        ledger_revision: int,
        opening_cash_cents: int,
        cash_cents: int,
        positions: tuple[PositionDailySnapshot, ...],
        net_external_flow_cents: int,
        weighted_external_flow_micros: int,
        cumulative_external_flow_cents: int,
        prior_row: sqlite3.Row | None,
        prior_revision_matches: bool,
        earlier_snapshot_count: int,
        previous_trading_date: str | None,
    ) -> AccountDailySnapshot:
        missing_count = sum(item.price_status == "MISSING" for item in positions)
        has_stale = any(item.price_status == "STALE" for item in positions)
        total_cost = sum(item.cost_basis_cents for item in positions)
        realized = (
            cash_cents
            + total_cost
            - opening_cash_cents
            - cumulative_external_flow_cents
        )
        if missing_count:
            market_value = None
            total_equity = None
            unrealized = None
            price_status = "PARTIAL"
            pnl = None
            return_ppb = None
            performance_status = "UNAVAILABLE"
            return_base_date = None
        else:
            market_value = sum(
                int(item.market_value_cents or 0) for item in positions
            )
            total_equity = cash_cents + market_value
            unrealized = sum(
                int(item.unrealized_pnl_cents or 0) for item in positions
            )
            price_status = "STALE" if has_stale else "COMPLETE"
            prior_equity = (
                int(prior_row["total_equity_cents"])
                if prior_row is not None
                and prior_row["total_equity_cents"] is not None
                else None
            )
            if earlier_snapshot_count == 0:
                pnl = 0
                return_ppb = 0
                performance_status = "INITIAL"
                return_base_date = None
            elif (
                prior_row is None
                or prior_equity is None
                or not prior_revision_matches
                or str(prior_row["snapshot_date"]) != previous_trading_date
            ):
                pnl = None
                return_ppb = None
                performance_status = "GAP"
                return_base_date = None
            else:
                denominator_micros = (
                    prior_equity * _MICRO_WEIGHT
                    + weighted_external_flow_micros
                )
                if denominator_micros <= 0:
                    pnl = None
                    return_ppb = None
                    performance_status = "GAP"
                    return_base_date = None
                else:
                    pnl = (
                        total_equity
                        - prior_equity
                        - net_external_flow_cents
                    )
                    return_ppb = cls._round_ratio_ppb(
                        pnl,
                        denominator_micros,
                    )
                    if not -_PPB <= return_ppb <= 100 * _PPB:
                        raise SnapshotConsistencyError(
                            "daily return exceeds schema bounds"
                        )
                    performance_status = "COMPLETE"
                    return_base_date = previous_trading_date
        return AccountDailySnapshot(
            account_id=account_id,
            snapshot_date=snapshot_date,
            ledger_revision=ledger_revision,
            cash_cents=cash_cents,
            market_value_cents=market_value,
            total_equity_cents=total_equity,
            net_external_flow_cents=net_external_flow_cents,
            weighted_external_flow_micros=weighted_external_flow_micros,
            pnl_cents=pnl,
            return_ppb=return_ppb,
            realized_pnl_cents=realized,
            unrealized_pnl_cents=unrealized,
            position_count=len(positions),
            unpriced_position_count=missing_count,
            price_status=price_status,
            expected_price_date=snapshot_date,
            performance_status=performance_status,
            return_base_date=return_base_date,
        )

    @staticmethod
    def _round_ratio_ppb(
        pnl_cents: int,
        denominator_micros: int,
    ) -> int:
        return int(
            (
                Decimal(pnl_cents)
                * Decimal(_PPB)
                * Decimal(_MICRO_WEIGHT)
                / Decimal(denominator_micros)
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

    @staticmethod
    def _validate_projections(
        account_id: int,
        projections: tuple[ProjectedPosition, ...],
    ) -> None:
        symbols: set[str] = set()
        for position in projections:
            if (
                position.account_id != account_id
                or position.status != "OPEN"
                or position.quantity <= 0
                or position.available_quantity < 0
                or position.available_quantity > position.quantity
                or position.cost_basis_cents < 0
                or position.symbol in symbols
            ):
                raise SnapshotConsistencyError("invalid projected position state")
            symbols.add(position.symbol)

    @staticmethod
    def _validate_aggregate(
        account: AccountDailySnapshot,
        positions: tuple[PositionDailySnapshot, ...],
    ) -> None:
        if account.position_count != len(positions):
            raise SnapshotConsistencyError("position count mismatch")
        missing = sum(item.price_status == "MISSING" for item in positions)
        if account.unpriced_position_count != missing:
            raise SnapshotConsistencyError("unpriced position count mismatch")
        if account.price_status == "PARTIAL":
            if missing == 0 or account.total_equity_cents is not None:
                raise SnapshotConsistencyError("invalid partial aggregate")
        elif missing or account.total_equity_cents != (
            account.cash_cents + int(account.market_value_cents or 0)
        ):
            raise SnapshotConsistencyError("invalid priced aggregate")

    @staticmethod
    def _replace_snapshots(
        conn: sqlite3.Connection,
        account: AccountDailySnapshot,
        positions: tuple[PositionDailySnapshot, ...],
    ) -> None:
        conn.execute(
            """
            DELETE FROM portfolio_position_daily_snapshots
            WHERE account_id = ? AND snapshot_date = ?
            """,
            (account.account_id, account.snapshot_date),
        )
        conn.executemany(
            """
            INSERT INTO portfolio_position_daily_snapshots (
                account_id, snapshot_date, symbol, position_revision,
                quantity, available_quantity, cost_basis_cents,
                realized_pnl_cents, price_micros, price_date,
                market_value_cents, unrealized_pnl_cents, price_status,
                last_transaction_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      CURRENT_TIMESTAMP)
            """,
            [
                (
                    item.account_id,
                    item.snapshot_date,
                    item.symbol,
                    item.position_revision,
                    item.quantity,
                    item.available_quantity,
                    item.cost_basis_cents,
                    item.realized_pnl_cents,
                    item.price_micros,
                    item.price_date,
                    item.market_value_cents,
                    item.unrealized_pnl_cents,
                    item.price_status,
                    item.last_transaction_id,
                )
                for item in positions
            ],
        )
        conn.execute(
            """
            INSERT INTO portfolio_account_daily_snapshots (
                account_id, snapshot_date, ledger_revision, cash_cents,
                market_value_cents, total_equity_cents,
                net_external_flow_cents, weighted_external_flow_micros,
                pnl_cents, return_ppb, realized_pnl_cents,
                unrealized_pnl_cents, position_count,
                unpriced_position_count, price_status, expected_price_date,
                performance_status, return_base_date, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      CURRENT_TIMESTAMP)
            ON CONFLICT(account_id, snapshot_date) DO UPDATE SET
                ledger_revision = excluded.ledger_revision,
                cash_cents = excluded.cash_cents,
                market_value_cents = excluded.market_value_cents,
                total_equity_cents = excluded.total_equity_cents,
                net_external_flow_cents = excluded.net_external_flow_cents,
                weighted_external_flow_micros =
                    excluded.weighted_external_flow_micros,
                pnl_cents = excluded.pnl_cents,
                return_ppb = excluded.return_ppb,
                realized_pnl_cents = excluded.realized_pnl_cents,
                unrealized_pnl_cents = excluded.unrealized_pnl_cents,
                position_count = excluded.position_count,
                unpriced_position_count = excluded.unpriced_position_count,
                price_status = excluded.price_status,
                expected_price_date = excluded.expected_price_date,
                performance_status = excluded.performance_status,
                return_base_date = excluded.return_base_date,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                account.account_id,
                account.snapshot_date,
                account.ledger_revision,
                account.cash_cents,
                account.market_value_cents,
                account.total_equity_cents,
                account.net_external_flow_cents,
                account.weighted_external_flow_micros,
                account.pnl_cents,
                account.return_ppb,
                account.realized_pnl_cents,
                account.unrealized_pnl_cents,
                account.position_count,
                account.unpriced_position_count,
                account.price_status,
                account.expected_price_date,
                account.performance_status,
                account.return_base_date,
            ),
        )

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> AccountDailySnapshot:
        return AccountDailySnapshot(
            account_id=int(row["account_id"]),
            snapshot_date=str(row["snapshot_date"]),
            ledger_revision=int(row["ledger_revision"]),
            cash_cents=int(row["cash_cents"]),
            market_value_cents=(
                int(row["market_value_cents"])
                if row["market_value_cents"] is not None
                else None
            ),
            total_equity_cents=(
                int(row["total_equity_cents"])
                if row["total_equity_cents"] is not None
                else None
            ),
            net_external_flow_cents=int(row["net_external_flow_cents"]),
            weighted_external_flow_micros=int(
                row["weighted_external_flow_micros"]
            ),
            pnl_cents=(
                int(row["pnl_cents"]) if row["pnl_cents"] is not None else None
            ),
            return_ppb=(
                int(row["return_ppb"])
                if row["return_ppb"] is not None
                else None
            ),
            realized_pnl_cents=int(row["realized_pnl_cents"]),
            unrealized_pnl_cents=(
                int(row["unrealized_pnl_cents"])
                if row["unrealized_pnl_cents"] is not None
                else None
            ),
            position_count=int(row["position_count"]),
            unpriced_position_count=int(row["unpriced_position_count"]),
            price_status=str(row["price_status"]),
            expected_price_date=str(row["expected_price_date"]),
            performance_status=str(row["performance_status"]),
            return_base_date=(
                str(row["return_base_date"])
                if row["return_base_date"] is not None
                else None
            ),
        )
