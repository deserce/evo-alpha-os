"""Fail-closed, account-aware exit reminders for real portfolio positions."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from portfolio.accounting import (
    AccountAsOfProjection,
    PortfolioAccountingService,
    ProjectedPosition,
)
from portfolio.schema import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    MAX_PRICE_MICROS,
    configure_portfolio_connection,
)


from portfolio.structure_exit import (
    HISTORY_ANCHOR, MAX_BARS, evaluate_position_structure,
)

LEGACY_RULE_VERSION = "portfolio-exit-v1"
DEFAULT_RULE_VERSION = "portfolio-exit-c-v2"
_RAW_SOURCE = "tdx_mootdx_raw"
_RAW_MODE = "none"
_RAW_QUALITY = "ok_unadjusted"
_TRUSTED_TECH_QUALITY_FLAGS = frozenset(
    {"ok", "legacy_ok", "repaired_turnover_local"}
)
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_PLAN_FIELDS = (
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
)


class PortfolioExitError(RuntimeError):
    """Base error for portfolio exit planning and monitoring."""


class ExitPlanNotFoundError(PortfolioExitError):
    """Raised when an exit plan or event does not exist."""


class StaleExitPlanError(PortfolioExitError):
    """Raised when a plan does not match the current position lifecycle."""


class InvalidExitPlanError(PortfolioExitError):
    """Raised when an exit-plan request is malformed."""


@dataclass(frozen=True, slots=True)
class ExitPlan:
    account_id: int
    symbol: str
    position_revision: int
    enabled: bool
    entry_transaction_id: int
    hard_stop_micros: int | None
    take_profit_1_micros: int | None
    take_profit_1_reduce_bps: int | None
    take_profit_2_micros: int | None
    take_profit_2_reduce_bps: int | None
    take_profit_3_micros: int | None
    take_profit_3_reduce_bps: int | None
    trailing_activation_bps: int | None
    trailing_distance_bps: int | None
    time_stop_trading_days: int | None
    time_stop_min_return_bps: int | None
    trend_exit_enabled: bool
    intraday_enabled: bool
    rule_version: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExitState:
    account_id: int
    symbol: str
    position_revision: int
    state: str
    highest_price_micros: int | None
    trailing_stop_micros: int | None
    last_price_micros: int | None
    last_price_date: str | None
    last_bar_time: str | None
    data_status: str
    last_transaction_id: int
    rule_version: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExitEvent:
    id: int
    account_id: int
    symbol: str
    position_revision: int
    event_type: str
    severity: str
    event_status: str
    trigger_date: str
    triggered_at: str
    bar_time: str | None
    observed_price_micros: int | None
    available_quantity: int
    recommended_quantity: int
    executable: bool
    tier: int | None
    dedupe_key: str
    payload_json: str | None
    rule_version: str
    acknowledged_at: str | None
    resolved_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExitAccountRunResult:
    account_id: int
    evaluated_positions: int
    blocked_positions: int
    created_event_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExitRunResult:
    trade_date: str
    accounts: tuple[ExitAccountRunResult, ...]

    @property
    def created_event_count(self) -> int:
        return sum(len(item.created_event_ids) for item in self.accounts)


@dataclass(frozen=True, slots=True)
class IntradayExitAccountRunResult:
    account_id: int
    evaluated_positions: int
    skipped_positions: int
    created_event_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class IntradayExitRunResult:
    bar_time: str
    accounts: tuple[IntradayExitAccountRunResult, ...]

    @property
    def created_event_count(self) -> int:
        return sum(len(item.created_event_ids) for item in self.accounts)


@dataclass(frozen=True, slots=True)
class _Technical:
    close: float
    ma10: float
    ma20: float
    macd_hist: float
    prices: tuple[dict, ...] = ()


class PortfolioExitEngine:
    """Evaluate persisted plans without placing or simulating broker orders."""

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

    def upsert_plan(
        self,
        *,
        account_id: int,
        symbol: str,
        position_revision: int,
        enabled: bool = True,
        hard_stop_micros: int | None = None,
        take_profit_1_micros: int | None = None,
        take_profit_1_reduce_bps: int | None = None,
        take_profit_2_micros: int | None = None,
        take_profit_2_reduce_bps: int | None = None,
        take_profit_3_micros: int | None = None,
        take_profit_3_reduce_bps: int | None = None,
        trailing_activation_bps: int | None = None,
        trailing_distance_bps: int | None = None,
        time_stop_trading_days: int | None = None,
        time_stop_min_return_bps: int | None = None,
        trend_exit_enabled: bool = True,
        intraday_enabled: bool = True,
        rule_version: str = DEFAULT_RULE_VERSION,
    ) -> ExitPlan:
        self._validate_identity(account_id, symbol, position_revision)
        self._require_bool(enabled, "enabled")
        self._require_bool(trend_exit_enabled, "trend_exit_enabled")
        self._require_bool(intraday_enabled, "intraday_enabled")
        self._validate_plan_values(
            hard_stop_micros=hard_stop_micros,
            take_profit_1_micros=take_profit_1_micros,
            take_profit_1_reduce_bps=take_profit_1_reduce_bps,
            take_profit_2_micros=take_profit_2_micros,
            take_profit_2_reduce_bps=take_profit_2_reduce_bps,
            take_profit_3_micros=take_profit_3_micros,
            take_profit_3_reduce_bps=take_profit_3_reduce_bps,
            trailing_activation_bps=trailing_activation_bps,
            trailing_distance_bps=trailing_distance_bps,
            time_stop_trading_days=time_stop_trading_days,
            time_stop_min_return_bps=time_stop_min_return_bps,
            rule_version=rule_version,
        )
        values = (
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
        )
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                position = conn.execute(
                    """
                    SELECT *
                    FROM portfolio_positions
                    WHERE account_id = ? AND symbol = ?
                      AND position_revision = ? AND status = 'OPEN'
                    """,
                    (account_id, symbol, position_revision),
                ).fetchone()
                if position is None:
                    raise StaleExitPlanError(
                        "exit plan must match the current OPEN position revision"
                    )
                entry_id = self._find_entry_transaction_id(
                    conn,
                    account_id,
                    symbol,
                    position_revision,
                )
                self._write_plan(
                    conn,
                    account_id=account_id,
                    symbol=symbol,
                    position_revision=position_revision,
                    entry_transaction_id=entry_id,
                    enabled=enabled,
                    values=values,
                    trend_exit_enabled=trend_exit_enabled,
                    intraday_enabled=intraday_enabled,
                    rule_version=rule_version,
                )
                row = self._get_plan_row(
                    conn,
                    account_id,
                    symbol,
                    position_revision,
                )
                conn.commit()
                return self._plan_from_row(row)
            except Exception:
                conn.rollback()
                raise

    def list_plans(
        self,
        *,
        account_id: int | None = None,
        enabled: bool | None = None,
        limit: int = 200,
    ) -> list[ExitPlan]:
        self._require_int_range(limit, "limit", 1, 500)
        clauses: list[str] = []
        parameters: list[object] = []
        if account_id is not None:
            self._require_positive_int(account_id, "account_id")
            clauses.append("account_id = ?")
            parameters.append(account_id)
        if enabled is not None:
            clauses.append("enabled = ?")
            parameters.append(int(enabled))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM portfolio_exit_plans
                {where}
                ORDER BY account_id, symbol, position_revision
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._plan_from_row(row) for row in rows]

    def list_states(
        self,
        *,
        account_id: int | None = None,
        limit: int = 200,
    ) -> list[ExitState]:
        self._require_int_range(limit, "limit", 1, 500)
        parameters: list[object] = []
        where = ""
        if account_id is not None:
            self._require_positive_int(account_id, "account_id")
            where = "WHERE account_id = ?"
            parameters.append(account_id)
        parameters.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM portfolio_exit_states
                {where}
                ORDER BY account_id, symbol, position_revision
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._state_from_row(row) for row in rows]

    def list_events(
        self,
        *,
        account_id: int | None = None,
        event_status: str | None = None,
        limit: int = 200,
    ) -> list[ExitEvent]:
        self._require_int_range(limit, "limit", 1, 500)
        clauses: list[str] = []
        parameters: list[object] = []
        if account_id is not None:
            self._require_positive_int(account_id, "account_id")
            clauses.append("account_id = ?")
            parameters.append(account_id)
        if event_status is not None:
            normalized = event_status.strip().upper()
            if normalized not in {"OPEN", "ACKNOWLEDGED", "RESOLVED"}:
                raise InvalidExitPlanError("invalid event_status")
            clauses.append("event_status = ?")
            parameters.append(normalized)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM portfolio_exit_events
                {where}
                ORDER BY trigger_date DESC, id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def acknowledge_event(self, event_id: int) -> ExitEvent:
        return self._transition_event(event_id, "ACKNOWLEDGED")

    def resolve_event(self, event_id: int) -> ExitEvent:
        return self._transition_event(event_id, "RESOLVED")

    def run_daily(
        self,
        trade_date: str,
        *,
        account_id: int | None = None,
    ) -> ExitRunResult:
        self._validate_date(trade_date)
        if account_id is not None:
            self._require_positive_int(account_id, "account_id")
            account_ids = (account_id,)
        else:
            account_ids = tuple(
                account.id
                for account in self._accounting.list_accounts(
                    include_archived=False
                )
            )
        results = tuple(
            self._run_account(trade_date, item_id)
            for item_id in account_ids
        )
        return ExitRunResult(trade_date=trade_date, accounts=results)

    def run_intraday_bars(
        self,
        bar_time: str | datetime,
        prices_micros_by_symbol: Mapping[str, int],
        *,
        account_ids: Iterable[int] | None = None,
        observed_at: str | datetime | None = None,
    ) -> IntradayExitRunResult:
        bar = self._validate_intraday_bar_time(bar_time)
        observed = self._parse_shanghai_time(
            observed_at or datetime.now(_SHANGHAI_TZ),
            "observed_at",
        )
        if bar > observed:
            raise InvalidExitPlanError("intraday bar_time cannot be in the future")
        prices = self._validate_intraday_prices(prices_micros_by_symbol)
        if account_ids is None:
            resolved_account_ids = tuple(
                account.id
                for account in self._accounting.list_accounts(
                    include_archived=False
                )
            )
        else:
            if isinstance(account_ids, (str, bytes)):
                raise InvalidExitPlanError("account_ids must contain integers")
            values = tuple(account_ids)
            for account_id in values:
                self._require_positive_int(account_id, "account_id")
            if len(set(values)) != len(values):
                raise InvalidExitPlanError("account_ids must not contain duplicates")
            resolved_account_ids = tuple(sorted(values))
        canonical_bar_time = bar.isoformat()
        accounts = tuple(
            self._run_intraday_account(
                bar,
                canonical_bar_time,
                prices,
                account_id,
            )
            for account_id in resolved_account_ids
        )
        return IntradayExitRunResult(
            bar_time=canonical_bar_time,
            accounts=accounts,
        )

    def _run_intraday_account(
        self,
        bar: datetime,
        canonical_bar_time: str,
        prices: dict[str, int],
        account_id: int,
    ) -> IntradayExitAccountRunResult:
        trade_date = bar.date().isoformat()
        projection = self._accounting.project_account_as_of(
            account_id,
            trade_date,
        )
        positions = {
            position.symbol: position
            for position in projection.positions
            if (
                position.status == "OPEN"
                and position.quantity > 0
                and position.symbol in prices
            )
        }
        symbols = tuple(sorted(positions))
        with closing(self._connect()) as conn:
            eligible_keys = self._load_intraday_plan_keys(
                conn,
                account_id,
                symbols,
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                created_event_ids: list[int] = []
                evaluated = 0
                skipped = 0
                for symbol in symbols:
                    position = positions[symbol]
                    key = (symbol, position.position_revision)
                    if key not in eligible_keys:
                        continue
                    if not self._projection_position_is_current(conn, position):
                        skipped += 1
                        continue
                    row = conn.execute(
                        """
                        SELECT *
                        FROM portfolio_exit_plans
                        WHERE account_id = ? AND symbol = ?
                          AND position_revision = ?
                          AND enabled = 1 AND intraday_enabled = 1
                        """,
                        (account_id, symbol, position.position_revision),
                    ).fetchone()
                    if row is None or not self._plan_entry_is_current(conn, row):
                        skipped += 1
                        continue
                    previous = conn.execute(
                        """
                        SELECT *
                        FROM portfolio_exit_states
                        WHERE account_id = ? AND symbol = ?
                          AND position_revision = ?
                        """,
                        (account_id, symbol, position.position_revision),
                    ).fetchone()
                    if self._intraday_bar_is_not_newer(
                        conn,
                        position,
                        previous,
                        canonical_bar_time,
                    ):
                        skipped += 1
                        continue
                    plan = self._plan_from_row(row)
                    conn.execute("SAVEPOINT portfolio_intraday_position")
                    try:
                        event_ids = self._evaluate_intraday_position(
                            conn,
                            trade_date=trade_date,
                            bar_time=canonical_bar_time,
                            position=position,
                            plan=plan,
                            price=prices[symbol],
                            previous=previous,
                        )
                    except (StaleExitPlanError, sqlite3.IntegrityError):
                        conn.execute(
                            "ROLLBACK TO SAVEPOINT portfolio_intraday_position"
                        )
                        conn.execute(
                            "RELEASE SAVEPOINT portfolio_intraday_position"
                        )
                        skipped += 1
                        continue
                    conn.execute("RELEASE SAVEPOINT portfolio_intraday_position")
                    evaluated += 1
                    created_event_ids.extend(event_ids)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return IntradayExitAccountRunResult(
            account_id=account_id,
            evaluated_positions=evaluated,
            skipped_positions=skipped,
            created_event_ids=tuple(created_event_ids),
        )

    @staticmethod
    def _load_intraday_plan_keys(
        conn: sqlite3.Connection,
        account_id: int,
        symbols: tuple[str, ...],
    ) -> set[tuple[str, int]]:
        if not symbols:
            return set()
        placeholders = ",".join("?" for _ in symbols)
        rows = conn.execute(
            f"""
            SELECT symbol, position_revision
            FROM portfolio_exit_plans
            WHERE account_id = ?
              AND symbol IN ({placeholders})
              AND enabled = 1 AND intraday_enabled = 1
            """,
            (account_id, *symbols),
        ).fetchall()
        return {
            (str(row["symbol"]), int(row["position_revision"]))
            for row in rows
        }

    def _evaluate_intraday_position(
        self,
        conn: sqlite3.Connection,
        *,
        trade_date: str,
        bar_time: str,
        position: ProjectedPosition,
        plan: ExitPlan,
        price: int,
        previous: sqlite3.Row | None,
    ) -> list[int]:
        old_highest = (
            int(previous["highest_price_micros"])
            if previous is not None
            and previous["highest_price_micros"] is not None
            else None
        )
        highest = max(price, old_highest or price)
        old_trailing = (
            int(previous["trailing_stop_micros"])
            if previous is not None
            and previous["trailing_stop_micros"] is not None
            else None
        )
        trailing = old_trailing
        if (
            plan.trailing_activation_bps is not None
            and plan.trailing_distance_bps is not None
            and highest * 10_000
            >= position.avg_cost_micros
            * (10_000 + plan.trailing_activation_bps)
        ):
            candidate = self._round_half_up(
                highest * (10_000 - plan.trailing_distance_bps),
                10_000,
            )
            trailing = max(old_trailing or 0, candidate)

        created: list[int] = []
        state: str | None = None
        signal: tuple[str, dict[str, object]] | None = None
        if plan.hard_stop_micros is not None and price <= plan.hard_stop_micros:
            signal = (
                "HARD_STOP_TRIGGERED",
                {"hard_stop_micros": plan.hard_stop_micros},
            )
        elif trailing is not None and price <= trailing:
            signal = (
                "TRAILING_STOP_TRIGGERED",
                {
                    "trailing_stop_micros": trailing,
                    "highest_price_micros": highest,
                },
            )
        if signal is not None:
            event_id = self._insert_intraday_event(
                conn,
                trade_date=trade_date,
                bar_time=bar_time,
                position=position,
                plan=plan,
                event_type=signal[0],
                severity="CRITICAL",
                observed_price=price,
                recommended=position.available_quantity,
                executable=position.available_quantity > 0,
                tier=None,
                payload=signal[1],
            )
            if event_id is not None:
                created.append(event_id)
            state = "EXIT_READY"
        else:
            lifecycle_base, lifecycle_sold = self._lifecycle_quantities(
                conn,
                position,
                plan,
                trade_date,
            )
            recommended_total = 0
            cumulative_bps = 0
            reduced = False
            for tier, threshold, reduce_bps in self._take_profit_rules(plan):
                cumulative_bps += reduce_bps
                existing = self._take_profit_event(conn, position, tier)
                if existing is not None:
                    recommended_total += int(existing["recommended_quantity"])
                    continue
                if price < threshold:
                    continue
                target = self._round_half_up(
                    lifecycle_base * cumulative_bps,
                    10_000,
                )
                fulfilled = max(lifecycle_sold, recommended_total)
                recommended = min(
                    position.available_quantity,
                    max(0, target - fulfilled),
                )
                if recommended <= 0:
                    continue
                event_id = self._insert_intraday_event(
                    conn,
                    trade_date=trade_date,
                    bar_time=bar_time,
                    position=position,
                    plan=plan,
                    event_type="TAKE_PROFIT_TRIGGERED",
                    severity="WARNING",
                    observed_price=price,
                    recommended=recommended,
                    executable=True,
                    tier=tier,
                    payload={
                        "threshold_micros": threshold,
                        "reduce_bps": reduce_bps,
                    },
                )
                recommended_total += recommended
                if event_id is not None:
                    created.append(event_id)
                    reduced = True
            if reduced:
                state = "REDUCE_READY"
        if previous is not None:
            conn.execute(
                """
                UPDATE portfolio_exit_states
                SET state = CASE WHEN last_price_micros IS NOT NULL
                                 THEN COALESCE(?, state) ELSE state END,
                    data_status = CASE WHEN ? IS NOT NULL AND last_price_micros IS NOT NULL
                                       THEN 'OK' ELSE data_status END,
                    highest_price_micros = ?,
                    trailing_stop_micros = ?,
                    last_bar_time = ?,
                    last_transaction_id = ?,
                    rule_version = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ? AND symbol = ?
                  AND position_revision = ?
                """,
                (
                    state,
                    state,
                    highest,
                    trailing,
                    bar_time,
                    position.last_transaction_id,
                    plan.rule_version,
                    position.account_id,
                    position.symbol,
                    position.position_revision,
                ),
            )
        return created

    def _insert_intraday_event(
        self,
        conn: sqlite3.Connection,
        *,
        trade_date: str,
        bar_time: str,
        position: ProjectedPosition,
        plan: ExitPlan,
        event_type: str,
        severity: str,
        observed_price: int,
        recommended: int,
        executable: bool,
        tier: int | None,
        payload: dict[str, object],
    ) -> int | None:
        dedupe_key = self._dedupe_key(position, event_type, tier)
        if event_type != "TAKE_PROFIT_TRIGGERED":
            dedupe_key = (
                f"{dedupe_key}:intraday_5min:"
                f"available{position.available_quantity}:"
                f"recommended{recommended}:executable{int(executable)}"
            )
        event_payload = dict(payload)
        event_payload["source"] = "intraday_5min"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO portfolio_exit_events (
                account_id, symbol, position_revision, event_type,
                severity, event_status, trigger_date, triggered_at,
                bar_time, observed_price_micros, available_quantity,
                recommended_quantity, executable, tier, dedupe_key,
                payload_json, rule_version, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP
            )
            """,
            (
                position.account_id,
                position.symbol,
                position.position_revision,
                event_type,
                severity,
                trade_date,
                bar_time,
                bar_time,
                observed_price,
                position.available_quantity,
                recommended,
                int(executable),
                tier,
                dedupe_key,
                json.dumps(
                    event_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                plan.rule_version,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return int(cursor.lastrowid)

    @staticmethod
    def _intraday_bar_is_not_newer(
        conn: sqlite3.Connection,
        position: ProjectedPosition,
        previous: sqlite3.Row | None,
        bar_time: str,
    ) -> bool:
        candidate = PortfolioExitEngine._parse_shanghai_time(
            bar_time,
            "bar_time",
        )
        checkpoints: list[datetime] = []
        if previous is not None and previous["last_bar_time"] is not None:
            checkpoints.append(
                PortfolioExitEngine._parse_shanghai_time(
                    str(previous["last_bar_time"]),
                    "last_bar_time",
                )
            )
        event_row = conn.execute(
            """
            SELECT MAX(bar_time)
            FROM portfolio_exit_events
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
              AND json_extract(payload_json, '$.source') = 'intraday_5min'
            """,
            (
                position.account_id,
                position.symbol,
                position.position_revision,
            ),
        ).fetchone()
        if event_row and event_row[0] is not None:
            checkpoints.append(
                PortfolioExitEngine._parse_shanghai_time(
                    str(event_row[0]),
                    "event.bar_time",
                )
            )
        return bool(checkpoints and candidate <= max(checkpoints))

    @staticmethod
    def _validate_intraday_prices(
        prices: Mapping[str, int],
    ) -> dict[str, int]:
        if not isinstance(prices, Mapping):
            raise InvalidExitPlanError(
                "prices_micros_by_symbol must be a mapping"
            )
        if len(prices) > 50:
            raise InvalidExitPlanError("intraday batch is limited to 50 symbols")
        result: dict[str, int] = {}
        for symbol, price in prices.items():
            if (
                not isinstance(symbol, str)
                or len(symbol) != 6
                or not symbol.isdigit()
            ):
                raise InvalidExitPlanError(
                    "intraday symbol must contain exactly six digits"
                )
            PortfolioExitEngine._require_int_range(
                price,
                f"price_micros[{symbol}]",
                1,
                MAX_PRICE_MICROS,
            )
            result[symbol] = price
        return result

    @staticmethod
    def _validate_intraday_bar_time(value: str | datetime) -> datetime:
        parsed = PortfolioExitEngine._parse_shanghai_time(value, "bar_time")
        if (
            parsed.second != 0
            or parsed.microsecond != 0
            or parsed.minute % 5 != 0
        ):
            raise InvalidExitPlanError(
                "bar_time must be an exact completed 5-minute boundary"
            )
        local_time = parsed.time().replace(tzinfo=None)
        in_morning = time(9, 35) <= local_time <= time(11, 30)
        in_afternoon = time(13, 5) <= local_time <= time(15, 0)
        if not (in_morning or in_afternoon):
            raise InvalidExitPlanError(
                "bar_time must be within an A-share trading session"
            )
        return parsed

    @staticmethod
    def _parse_shanghai_time(
        value: str | datetime,
        field: str,
    ) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise InvalidExitPlanError(
                    f"{field} must be an ISO timestamp"
                ) from exc
        else:
            raise InvalidExitPlanError(f"{field} must be an ISO timestamp")
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(hours=8):
            raise InvalidExitPlanError(f"{field} must use Asia/Shanghai timezone")
        return parsed.astimezone(_SHANGHAI_TZ)

    def _run_account(
        self,
        trade_date: str,
        account_id: int,
    ) -> ExitAccountRunResult:
        projection = self._accounting.project_account_as_of(
            account_id,
            trade_date,
        )
        open_positions = {
            position.symbol: position
            for position in projection.positions
            if position.status == "OPEN" and position.quantity > 0
        }
        symbols = tuple(sorted(open_positions))
        with closing(self._connect()) as conn:
            raw_prices = self._load_raw_prices(conn, symbols, trade_date)
            technical = self._load_technicals(conn, symbols, trade_date)
            calendar = self._load_calendar(
                conn,
                min(
                    (position.first_buy_date for position in open_positions.values()),
                    default=trade_date,
                ),
                trade_date,
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._reject_out_of_order(
                    conn,
                    account_id,
                    symbols,
                    trade_date,
                )
                stable_positions = {
                    symbol: position
                    for symbol, position in open_positions.items()
                    if self._projection_position_is_current(conn, position)
                }
                if len(stable_positions) == len(open_positions):
                    self._disable_stale_plans(conn, projection, trade_date)
                plans = self._ensure_open_position_plans(
                    conn,
                    projection,
                    stable_positions,
                    trade_date,
                )
                event_ids: list[int] = []
                blocked = len(open_positions) - len(stable_positions)
                for symbol in symbols:
                    position = stable_positions.get(symbol)
                    if position is None:
                        continue
                    plan = plans.get((symbol, position.position_revision))
                    if plan is None:
                        blocked += 1
                        continue
                    if not plan.enabled:
                        continue
                    conn.execute("SAVEPOINT portfolio_exit_position")
                    try:
                        created, is_blocked = self._evaluate_position(
                            conn,
                            trade_date=trade_date,
                            position=position,
                            plan=plan,
                            raw_price=raw_prices.get(symbol),
                            technical=technical.get(symbol),
                            calendar=calendar,
                        )
                    except (StaleExitPlanError, sqlite3.IntegrityError):
                        conn.execute(
                            "ROLLBACK TO SAVEPOINT portfolio_exit_position"
                        )
                        conn.execute(
                            "RELEASE SAVEPOINT portfolio_exit_position"
                        )
                        blocked += 1
                        continue
                    conn.execute("RELEASE SAVEPOINT portfolio_exit_position")
                    event_ids.extend(created)
                    blocked += int(is_blocked)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return ExitAccountRunResult(
            account_id=account_id,
            evaluated_positions=len(symbols),
            blocked_positions=blocked,
            created_event_ids=tuple(event_ids),
        )

    def _ensure_open_position_plans(
        self,
        conn: sqlite3.Connection,
        projection: AccountAsOfProjection,
        positions: dict[str, ProjectedPosition],
        trade_date: str,
    ) -> dict[tuple[str, int], ExitPlan]:
        result: dict[tuple[str, int], ExitPlan] = {}
        for symbol in sorted(positions):
            position = positions[symbol]
            row = conn.execute(
                """
                SELECT * FROM portfolio_exit_plans
                WHERE account_id = ? AND symbol = ? AND position_revision = ?
                """,
                (
                    projection.account.id,
                    symbol,
                    position.position_revision,
                ),
            ).fetchone()
            if row is None:
                try:
                    entry_id = self._find_entry_transaction_id(
                        conn,
                        projection.account.id,
                        symbol,
                        position.position_revision,
                    )
                except StaleExitPlanError:
                    continue
                self._write_plan(
                    conn,
                    account_id=projection.account.id,
                    symbol=symbol,
                    position_revision=position.position_revision,
                    entry_transaction_id=entry_id,
                    enabled=True,
                    values=(None,) * len(_PLAN_FIELDS),
                    trend_exit_enabled=True,
                    intraday_enabled=True,
                    rule_version=DEFAULT_RULE_VERSION,
                )
                row = self._get_plan_row(
                    conn,
                    projection.account.id,
                    symbol,
                    position.position_revision,
                )
            elif not self._plan_entry_is_current(conn, row):
                repaired = self._repair_plan_entry(
                    conn,
                    row=row,
                    trade_date=trade_date,
                )
                if repaired is None:
                    continue
                row = repaired
            if str(row["rule_version"]) == LEGACY_RULE_VERSION:
                for event_type in ("TREND_EXIT_WATCH", "TREND_EXIT_TRIGGERED"):
                    self._resolve_event_type(conn, position, event_type, self._event_time(trade_date))
                conn.execute(
                    "UPDATE portfolio_exit_plans SET rule_version=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE account_id=? AND symbol=? AND position_revision=?",
                    (DEFAULT_RULE_VERSION, projection.account.id, symbol, position.position_revision),
                )
                row = self._get_plan_row(conn, projection.account.id, symbol, position.position_revision)
            result[(symbol, position.position_revision)] = self._plan_from_row(row)
        return result

    @staticmethod
    def _projection_position_is_current(
        conn: sqlite3.Connection,
        position: ProjectedPosition,
    ) -> bool:
        row = conn.execute(
            """
            SELECT position_revision, quantity, status, last_transaction_id
            FROM portfolio_positions
            WHERE account_id = ? AND symbol = ?
            """,
            (position.account_id, position.symbol),
        ).fetchone()
        return bool(
            row is not None
            and str(row["status"]) == "OPEN"
            and int(row["position_revision"]) == position.position_revision
            and int(row["quantity"]) == position.quantity
            and int(row["last_transaction_id"]) == position.last_transaction_id
        )

    @staticmethod
    def _reject_out_of_order(
        conn: sqlite3.Connection,
        account_id: int,
        symbols: tuple[str, ...],
        trade_date: str,
    ) -> None:
        if not symbols:
            return
        placeholders = ",".join("?" for _ in symbols)
        row = conn.execute(
            f"""
            SELECT MAX(last_price_date)
            FROM portfolio_exit_states
            WHERE account_id = ?
              AND symbol IN ({placeholders})
              AND last_price_date IS NOT NULL
            """,
            (account_id, *symbols),
        ).fetchone()
        latest = str(row[0]) if row and row[0] is not None else None
        if latest is not None and trade_date < latest:
            raise InvalidExitPlanError(
                "trade_date cannot be earlier than persisted exit state"
            )

    @staticmethod
    def _plan_entry_is_current(
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        return conn.execute(
            """
            SELECT 1
            FROM portfolio_transactions AS entry
            JOIN portfolio_positions AS position
              ON position.account_id = entry.account_id
             AND position.symbol = entry.symbol
             AND position.position_revision = ?
             AND position.status = 'OPEN'
            WHERE entry.id = ?
              AND entry.account_id = ?
              AND entry.symbol = ?
              AND entry.status = 'POSTED'
              AND entry.business_type = 'BUY'
              AND entry.trade_date = position.first_buy_date
              AND NOT EXISTS (
                  SELECT 1
                  FROM portfolio_transactions AS reversal
                  WHERE reversal.account_id = entry.account_id
                    AND reversal.status = 'POSTED'
                    AND reversal.business_type = 'REVERSAL'
                    AND reversal.reversal_of = entry.id
              )
            """,
            (
                int(row["position_revision"]),
                int(row["entry_transaction_id"]),
                int(row["account_id"]),
                str(row["symbol"]),
            ),
        ).fetchone() is not None

    def _repair_plan_entry(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        trade_date: str,
    ) -> sqlite3.Row | None:
        account_id = int(row["account_id"])
        symbol = str(row["symbol"])
        revision = int(row["position_revision"])
        self._resolve_plan_events(
            conn,
            account_id,
            symbol,
            revision,
            self._event_time(trade_date),
        )
        conn.execute(
            """
            UPDATE portfolio_exit_plans
            SET enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
            """,
            (account_id, symbol, revision),
        )
        has_history = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM portfolio_exit_events
                WHERE account_id = ? AND symbol = ? AND position_revision = ?
            )
            """,
            (account_id, symbol, revision),
        ).fetchone()[0]
        if has_history:
            return None
        try:
            entry_id = self._find_entry_transaction_id(
                conn,
                account_id,
                symbol,
                revision,
            )
        except StaleExitPlanError:
            return None
        values = tuple(row[field] for field in _PLAN_FIELDS)
        conn.execute(
            """
            DELETE FROM portfolio_exit_states
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
            """,
            (account_id, symbol, revision),
        )
        conn.execute(
            """
            DELETE FROM portfolio_exit_plans
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
            """,
            (account_id, symbol, revision),
        )
        self._write_plan(
            conn,
            account_id=account_id,
            symbol=symbol,
            position_revision=revision,
            entry_transaction_id=entry_id,
            enabled=bool(row["enabled"]),
            values=values,
            trend_exit_enabled=bool(row["trend_exit_enabled"]),
            intraday_enabled=bool(row["intraday_enabled"]),
            rule_version=str(row["rule_version"]),
        )
        return self._get_plan_row(conn, account_id, symbol, revision)

    def _disable_stale_plans(
        self,
        conn: sqlite3.Connection,
        projection: AccountAsOfProjection,
        trade_date: str,
    ) -> None:
        active = {
            (position.symbol, position.position_revision)
            for position in projection.positions
            if position.status == "OPEN" and position.quantity > 0
        }
        rows = conn.execute(
            """
            SELECT symbol, position_revision
            FROM portfolio_exit_plans
            WHERE account_id = ? AND enabled = 1
            """,
            (projection.account.id,),
        ).fetchall()
        for row in rows:
            key = (str(row["symbol"]), int(row["position_revision"]))
            if key in active:
                continue
            self._resolve_plan_events(
                conn,
                projection.account.id,
                key[0],
                key[1],
                self._event_time(trade_date),
            )
            conn.execute(
                """
                UPDATE portfolio_exit_plans
                SET enabled = 0, updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ? AND symbol = ? AND position_revision = ?
                """,
                (projection.account.id, key[0], key[1]),
            )

    def _evaluate_position(
        self,
        conn: sqlite3.Connection,
        *,
        trade_date: str,
        position: ProjectedPosition,
        plan: ExitPlan,
        raw_price: int | None,
        technical: _Technical | None,
        calendar: tuple[str, ...],
    ) -> tuple[list[int], bool]:
        previous = conn.execute(
            """
            SELECT * FROM portfolio_exit_states
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
            """,
            (position.account_id, position.symbol, position.position_revision),
        ).fetchone()
        was_blocked = conn.execute(
            "SELECT 1 FROM portfolio_exit_events WHERE account_id=? AND symbol=? "
            "AND position_revision=? AND event_type='EXIT_DATA_BLOCKED' "
            "AND event_status IN ('OPEN','ACKNOWLEDGED') LIMIT 1",
            (position.account_id, position.symbol, position.position_revision),
        ).fetchone() is not None
        if raw_price is None:
            previous_highest = (
                int(previous["highest_price_micros"])
                if previous is not None
                and previous["highest_price_micros"] is not None
                else None
            )
            previous_trailing = (
                int(previous["trailing_stop_micros"])
                if previous is not None
                and previous["trailing_stop_micros"] is not None
                else None
            )
            previous_price = (
                int(previous["last_price_micros"])
                if previous is not None
                and previous["last_price_micros"] is not None
                else None
            )
            previous_price_date = (
                str(previous["last_price_date"])
                if previous is not None
                and previous["last_price_date"] is not None
                else None
            )
            self._upsert_state(
                conn,
                position=position,
                plan=plan,
                state="DATA_BLOCKED",
                highest=previous_highest,
                trailing=previous_trailing,
                price=previous_price,
                trade_date=previous_price_date,
                data_status=(
                    "STALE" if previous_price_date is not None else "MISSING"
                ),
            )
            event_id = None
            if not was_blocked:
                event_id = self._insert_event(
                    conn,
                    trade_date=trade_date,
                    position=position,
                    plan=plan,
                    event_type="EXIT_DATA_BLOCKED",
                    severity="WARNING",
                    observed_price=None,
                    recommended=0,
                    executable=False,
                    tier=None,
                    payload={"reason": "trusted raw close unavailable"},
                )
            return ([event_id] if event_id is not None else [], True)

        structure = None
        if plan.trend_exit_enabled and technical is not None and technical.prices:
            try:
                structure = evaluate_position_structure(
                    list(technical.prices), entry_date=position.first_buy_date,
                )
            except (ValueError, TypeError, OverflowError):
                structure = None
        created: list[int] = []
        if was_blocked and (not plan.trend_exit_enabled or
                            (structure is not None and structure["data_status"] == "OK")):
            self._resolve_event_type(
                conn,
                position,
                "EXIT_DATA_BLOCKED",
                self._event_time(trade_date),
            )
            recovered_id = self._insert_event(
                conn,
                trade_date=trade_date,
                position=position,
                plan=plan,
                event_type="EXIT_DATA_RECOVERED",
                severity="INFO",
                observed_price=raw_price,
                recommended=0,
                executable=False,
                tier=None,
                payload={"price_source": _RAW_SOURCE},
            )
            if recovered_id is not None:
                created.append(recovered_id)

        old_highest = (
            int(previous["highest_price_micros"])
            if previous is not None
            and previous["highest_price_micros"] is not None
            else None
        )
        highest = max(raw_price, old_highest or raw_price)
        old_trailing = (
            int(previous["trailing_stop_micros"])
            if previous is not None
            and previous["trailing_stop_micros"] is not None
            else None
        )
        trailing = old_trailing
        if (
            plan.trailing_activation_bps is not None
            and plan.trailing_distance_bps is not None
            and highest * 10_000
            >= position.avg_cost_micros
            * (10_000 + plan.trailing_activation_bps)
        ):
            candidate = self._round_half_up(
                highest * (10_000 - plan.trailing_distance_bps),
                10_000,
            )
            trailing = max(old_trailing or 0, candidate)

        if structure is not None and structure["data_status"] == "OK":
            obsolete = []
            if not structure["exit"]:
                obsolete.append("TREND_EXIT_TRIGGERED")
            if not structure["warning"] or structure["exit"]:
                obsolete.append("TREND_EXIT_WATCH")
            for event_type in obsolete:
                old_events = conn.execute(
                    "SELECT id, payload_json, triggered_at, acknowledged_at FROM portfolio_exit_events "
                    "WHERE account_id=? AND symbol=? AND position_revision=? AND event_type=? "
                    "AND event_status IN ('OPEN','ACKNOWLEDGED') LIMIT 1500",
                    (position.account_id, position.symbol, position.position_revision, event_type),
                ).fetchall()
                for event in old_events:
                    if json.loads(str(event["payload_json"])).get("strategy") != "C":
                        continue
                    resolved = self._latest_utc_timestamp(
                        self._event_time(trade_date), str(event["triggered_at"]),
                        str(event["acknowledged_at"]) if event["acknowledged_at"] else None,
                    )
                    conn.execute("UPDATE portfolio_exit_events SET event_status='RESOLVED', "
                                 "resolved_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 (resolved, int(event["id"])))
        exit_signal: tuple[str, str, dict[str, object]] | None = None
        if plan.hard_stop_micros is not None and raw_price <= plan.hard_stop_micros:
            exit_signal = (
                "HARD_STOP_TRIGGERED",
                "CRITICAL",
                {"hard_stop_micros": plan.hard_stop_micros},
            )
        elif trailing is not None and raw_price <= trailing:
            exit_signal = (
                "TRAILING_STOP_TRIGGERED",
                "CRITICAL",
                {"trailing_stop_micros": trailing, "highest_price_micros": highest},
            )
        elif self._time_stop_triggered(
            position,
            plan,
            raw_price,
            calendar,
            trade_date,
        ):
            exit_signal = (
                "TIME_STOP_TRIGGERED",
                "WARNING",
                {
                    "time_stop_trading_days": plan.time_stop_trading_days,
                    "time_stop_min_return_bps": plan.time_stop_min_return_bps,
                },
            )
        elif (
            structure is not None and structure["exit"]
        ):
            exit_signal = (
                "TREND_EXIT_TRIGGERED",
                "WARNING",
                structure,
            )

        state = "NORMAL"
        if exit_signal is not None:
            event_id = self._insert_event(
                conn,
                trade_date=trade_date,
                position=position,
                plan=plan,
                event_type=exit_signal[0],
                severity=exit_signal[1],
                observed_price=raw_price,
                recommended=position.available_quantity,
                executable=position.available_quantity > 0,
                tier=None,
                payload=exit_signal[2],
            )
            if event_id is not None:
                created.append(event_id)
            state = "EXIT_READY"
        else:
            lifecycle_base, lifecycle_sold = self._lifecycle_quantities(
                conn,
                position,
                plan,
                trade_date,
            )
            recommended_total = 0
            cumulative_bps = 0
            take_profit_created = False
            for tier, threshold, reduce_bps in self._take_profit_rules(plan):
                cumulative_bps += reduce_bps
                existing = self._take_profit_event(
                    conn,
                    position,
                    tier,
                )
                if existing is not None:
                    recommended_total += int(existing["recommended_quantity"])
                    continue
                if raw_price < threshold:
                    continue
                cumulative_target = self._round_half_up(
                    lifecycle_base * cumulative_bps,
                    10_000,
                )
                fulfilled = max(lifecycle_sold, recommended_total)
                recommended = min(
                    position.available_quantity,
                    max(0, cumulative_target - fulfilled),
                )
                if recommended <= 0:
                    continue
                event_id = self._insert_event(
                    conn,
                    trade_date=trade_date,
                    position=position,
                    plan=plan,
                    event_type="TAKE_PROFIT_TRIGGERED",
                    severity="WARNING",
                    observed_price=raw_price,
                    recommended=recommended,
                    executable=recommended > 0,
                    tier=tier,
                    payload={
                        "threshold_micros": threshold,
                        "reduce_bps": reduce_bps,
                    },
                )
                recommended_total += recommended
                if event_id is not None:
                    created.append(event_id)
                    take_profit_created = True
            if take_profit_created or recommended_total > 0:
                state = "REDUCE_READY"
            elif (
                structure is not None and structure["warning"]
            ):
                event_id = self._insert_event(
                    conn,
                    trade_date=trade_date,
                    position=position,
                    plan=plan,
                    event_type="TREND_EXIT_WATCH",
                    severity="INFO",
                    observed_price=raw_price,
                    recommended=0,
                    executable=False,
                    tier=None,
                    payload=structure,
                )
                if event_id is not None:
                    created.append(event_id)
                state = "EXIT_WATCH"

        c_blocked = (plan.trend_exit_enabled and
                     (structure is None or structure["data_status"] != "OK"))
        if c_blocked:
            if state == "NORMAL":
                state = "DATA_BLOCKED"
            if not was_blocked:
                blocked_id = self._insert_event(
                    conn, trade_date=trade_date, position=position, plan=plan,
                    event_type="EXIT_DATA_BLOCKED", severity="WARNING",
                    observed_price=raw_price, recommended=0, executable=False, tier=None,
                    payload={"strategy": "C", "reason": "策略C可信日K或建仓历史缺失",
                             "data_status": structure["data_status"]
                             if structure is not None else "MISSING_OR_STALE"},
                )
                if blocked_id is not None:
                    created.append(blocked_id)
        self._upsert_state(
            conn,
            position=position,
            plan=plan,
            state=state,
            highest=highest,
            trailing=trailing,
            price=raw_price,
            trade_date=trade_date,
            data_status="STALE" if state == "DATA_BLOCKED" else "OK",
        )
        return created, c_blocked

    def _upsert_state(
        self,
        conn: sqlite3.Connection,
        *,
        position: ProjectedPosition,
        plan: ExitPlan,
        state: str,
        highest: int | None,
        trailing: int | None,
        price: int | None,
        trade_date: str | None,
        data_status: str,
    ) -> None:
        bar_time = self._bar_time(trade_date) if trade_date is not None else None
        conn.execute(
            """
            INSERT INTO portfolio_exit_states (
                account_id, symbol, position_revision, state,
                highest_price_micros, trailing_stop_micros,
                last_price_micros, last_price_date, last_bar_time,
                data_status, last_transaction_id, rule_version,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id, symbol, position_revision) DO UPDATE SET
                state = excluded.state,
                highest_price_micros = excluded.highest_price_micros,
                trailing_stop_micros = excluded.trailing_stop_micros,
                last_price_micros = excluded.last_price_micros,
                last_price_date = excluded.last_price_date,
                last_bar_time = excluded.last_bar_time,
                data_status = excluded.data_status,
                last_transaction_id = excluded.last_transaction_id,
                rule_version = excluded.rule_version,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                position.account_id,
                position.symbol,
                position.position_revision,
                state,
                highest,
                trailing,
                price,
                trade_date,
                bar_time,
                data_status,
                position.last_transaction_id,
                plan.rule_version,
            ),
        )

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        trade_date: str,
        position: ProjectedPosition,
        plan: ExitPlan,
        event_type: str,
        severity: str,
        observed_price: int | None,
        recommended: int,
        executable: bool,
        tier: int | None,
        payload: dict[str, object],
    ) -> int | None:
        dedupe_key = self._dedupe_key(position, event_type, tier)
        if event_type in {"EXIT_DATA_BLOCKED", "EXIT_DATA_RECOVERED"}:
            episode = conn.execute(
                """
                SELECT COUNT(*)
                FROM portfolio_exit_events
                WHERE account_id = ? AND symbol = ? AND position_revision = ?
                  AND event_type = ?
                """,
                (
                    position.account_id,
                    position.symbol,
                    position.position_revision,
                    event_type,
                ),
            ).fetchone()[0]
            dedupe_key = f"{dedupe_key}:episode{int(episode) + 1}"
        elif event_type in {
            "HARD_STOP_TRIGGERED",
            "TRAILING_STOP_TRIGGERED",
            "TIME_STOP_TRIGGERED",
            "TREND_EXIT_TRIGGERED",
        }:
            dedupe_key = (
                f"{dedupe_key}:available{position.available_quantity}:"
                f"recommended{recommended}:executable{int(executable)}"
            )
        if event_type in {"TREND_EXIT_WATCH", "TREND_EXIT_TRIGGERED"}:
            dedupe_key = f"{dedupe_key}:rule{plan.rule_version}:strategyC"
            target_state = "EXIT_READY" if event_type == "TREND_EXIT_TRIGGERED" else "EXIT_WATCH"
            prior = conn.execute(
                "SELECT state, rule_version FROM portfolio_exit_states "
                "WHERE account_id=? AND symbol=? AND position_revision=?",
                (position.account_id, position.symbol, position.position_revision),
            ).fetchone()
            latest = conn.execute(
                "SELECT dedupe_key FROM portfolio_exit_events WHERE account_id=? "
                "AND symbol=? AND position_revision=? AND event_type=? AND rule_version=? "
                "ORDER BY id DESC LIMIT 1",
                (position.account_id, position.symbol, position.position_revision,
                 event_type, plan.rule_version),
            ).fetchone()
            if (prior is not None and str(prior["state"]) == target_state
                and str(prior["rule_version"]) == plan.rule_version and latest is not None
                and str(latest["dedupe_key"]).startswith(dedupe_key + ":phase")):
                dedupe_key = str(latest["dedupe_key"])
            else:
                dedupe_key = f"{dedupe_key}:phase{trade_date}"
        daily_payload = dict(payload)
        daily_payload["source"] = "daily"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO portfolio_exit_events (
                account_id, symbol, position_revision, event_type,
                severity, event_status, trigger_date, triggered_at,
                bar_time, observed_price_micros, available_quantity,
                recommended_quantity, executable, tier, dedupe_key,
                payload_json, rule_version, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP
            )
            """,
            (
                position.account_id,
                position.symbol,
                position.position_revision,
                event_type,
                severity,
                trade_date,
                self._event_time(trade_date),
                self._bar_time(trade_date),
                observed_price,
                position.available_quantity,
                recommended,
                int(executable),
                tier,
                dedupe_key,
                json.dumps(
                    daily_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                plan.rule_version,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return int(cursor.lastrowid)

    @staticmethod
    def _lifecycle_quantities(
        conn: sqlite3.Connection,
        position: ProjectedPosition,
        plan: ExitPlan,
        trade_date: str,
    ) -> tuple[int, int]:
        rows = conn.execute(
            """
            SELECT id, business_type, quantity
            FROM portfolio_transactions AS transaction_row
            WHERE account_id = ? AND symbol = ?
              AND trade_date <= ?
              AND status = 'POSTED'
              AND business_type IN ('BUY', 'SELL', 'CORPORATE_ACTION')
              AND NOT EXISTS (
                  SELECT 1
                  FROM portfolio_transactions AS reversal
                  WHERE reversal.account_id = transaction_row.account_id
                    AND reversal.status = 'POSTED'
                    AND reversal.business_type = 'REVERSAL'
                    AND reversal.reversal_of = transaction_row.id
              )
            ORDER BY trade_date, occurred_at, id
            """,
            (
                position.account_id,
                position.symbol,
                trade_date,
            ),
        ).fetchall()
        started = False
        base = 0
        sold = 0
        for row in rows:
            if int(row["id"]) == plan.entry_transaction_id:
                started = True
            if not started:
                continue
            quantity = int(row["quantity"])
            business_type = str(row["business_type"])
            if business_type == "BUY":
                base += quantity
            elif business_type == "SELL":
                sold += quantity
            elif quantity > 0:
                base += quantity
        if base <= 0 or base - sold != position.quantity:
            raise StaleExitPlanError(
                "could not derive current position lifecycle quantities"
            )
        return base, sold

    @staticmethod
    def _take_profit_event(
        conn: sqlite3.Connection,
        position: ProjectedPosition,
        tier: int,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT recommended_quantity
            FROM portfolio_exit_events
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
              AND event_type = 'TAKE_PROFIT_TRIGGERED' AND tier = ?
            ORDER BY id
            LIMIT 1
            """,
            (
                position.account_id,
                position.symbol,
                position.position_revision,
                tier,
            ),
        ).fetchone()

    @staticmethod
    def _dedupe_key(
        position: ProjectedPosition,
        event_type: str,
        tier: int | None,
    ) -> str:
        suffix = str(tier) if tier is not None else "0"
        return (
            f"{position.account_id}:{position.symbol}:"
            f"r{position.position_revision}:{event_type}:{suffix}"
        )

    def _transition_event(self, event_id: int, target: str) -> ExitEvent:
        self._require_positive_int(event_id, "event_id")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM portfolio_exit_events WHERE id = ?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    raise ExitPlanNotFoundError("exit event does not exist")
                current = str(row["event_status"])
                if current == target:
                    conn.commit()
                    return self._event_from_row(row)
                if current == "RESOLVED" or (
                    current == "ACKNOWLEDGED" and target == "ACKNOWLEDGED"
                ):
                    raise InvalidExitPlanError("invalid event status transition")
                timestamp = self._transition_time(row)
                if target == "ACKNOWLEDGED":
                    conn.execute(
                        """
                        UPDATE portfolio_exit_events
                        SET event_status = 'ACKNOWLEDGED',
                            acknowledged_at = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (timestamp, event_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE portfolio_exit_events
                        SET event_status = 'RESOLVED',
                            resolved_at = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (timestamp, event_id),
                    )
                updated = conn.execute(
                    "SELECT * FROM portfolio_exit_events WHERE id = ?",
                    (event_id,),
                ).fetchone()
                conn.commit()
                return self._event_from_row(updated)
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _transition_time(row: sqlite3.Row) -> str:
        return PortfolioExitEngine._latest_utc_timestamp(
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            str(row["triggered_at"]),
            (
                str(row["acknowledged_at"])
                if row["acknowledged_at"]
                else None
            ),
        )

    @staticmethod
    def _latest_utc_timestamp(*values: str | None) -> str:
        parsed: list[datetime] = []
        for value in values:
            if not value:
                continue
            normalized = value.strip().replace("Z", "+00:00")
            try:
                item = datetime.fromisoformat(normalized)
            except ValueError as exc:
                raise InvalidExitPlanError(
                    "exit event contains an invalid timestamp"
                ) from exc
            if item.tzinfo is None:
                item = item.replace(tzinfo=timezone.utc)
            parsed.append(item.astimezone(timezone.utc))
        if not parsed:
            raise InvalidExitPlanError("exit event timestamp is missing")
        return max(parsed).replace(microsecond=0).isoformat()

    def _resolve_plan_events(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        symbol: str,
        revision: int,
        timestamp: str,
    ) -> None:
        rows = conn.execute(
            """
            SELECT id, triggered_at, acknowledged_at
            FROM portfolio_exit_events
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
              AND event_status IN ('OPEN', 'ACKNOWLEDGED')
            """,
            (account_id, symbol, revision),
        ).fetchall()
        for row in rows:
            resolved = self._latest_utc_timestamp(
                timestamp,
                str(row["triggered_at"]),
                (
                    str(row["acknowledged_at"])
                    if row["acknowledged_at"]
                    else None
                ),
            )
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET event_status = 'RESOLVED', resolved_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (resolved, int(row["id"])),
            )

    def _resolve_event_type(
        self,
        conn: sqlite3.Connection,
        position: ProjectedPosition,
        event_type: str,
        timestamp: str,
    ) -> None:
        rows = conn.execute(
            """
            SELECT id, triggered_at, acknowledged_at
            FROM portfolio_exit_events
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
              AND event_type = ?
              AND event_status IN ('OPEN', 'ACKNOWLEDGED')
            """,
            (
                position.account_id,
                position.symbol,
                position.position_revision,
                event_type,
            ),
        ).fetchall()
        for row in rows:
            resolved = self._latest_utc_timestamp(
                timestamp,
                str(row["triggered_at"]),
                (
                    str(row["acknowledged_at"])
                    if row["acknowledged_at"]
                    else None
                ),
            )
            conn.execute(
                """
                UPDATE portfolio_exit_events
                SET event_status = 'RESOLVED', resolved_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (resolved, int(row["id"])),
            )

    @staticmethod
    def _load_raw_prices(
        conn: sqlite3.Connection,
        symbols: tuple[str, ...],
        trade_date: str,
    ) -> dict[str, int]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        try:
            rows = conn.execute(
                f"""
                SELECT symbol, close
                FROM stock_daily_prices_raw
                WHERE trade_date = ?
                  AND symbol IN ({placeholders})
                  AND source = ?
                  AND adjustment_mode = ?
                  AND quality_flag = ?
                """,
                (trade_date, *symbols, _RAW_SOURCE, _RAW_MODE, _RAW_QUALITY),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise
        result: dict[str, int] = {}
        for row in rows:
            price = PortfolioExitEngine._price_to_micros(row["close"])
            if price is not None:
                result[str(row["symbol"])] = price
        return result

    @staticmethod
    def _load_technicals(
        conn: sqlite3.Connection,
        symbols: tuple[str, ...],
        trade_date: str,
    ) -> dict[str, _Technical]:
        if not symbols:
            return {}
        columns = {str(row["name"]) for row in conn.execute(
            "PRAGMA table_info(stock_daily_prices)"
        ).fetchall()}
        required = {"symbol", "trade_date", "open", "high", "low", "close", "volume",
                    "adjustment_mode", "quality_flag"}
        if not required.issubset(columns):
            return {}
        result: dict[str, _Technical] = {}
        for symbol in symbols:
            rows = conn.execute(
                "SELECT substr(trade_date,1,10) AS trade_date, open, high, low, close, volume, "
                "quality_flag FROM stock_daily_prices WHERE symbol=? AND trade_date>=? "
                "AND trade_date<=? AND adjustment_mode='qfq' ORDER BY trade_date LIMIT ?",
                (symbol, HISTORY_ANCHOR, trade_date, MAX_BARS+1),
            ).fetchall()
            if (not rows or len(rows) > MAX_BARS or rows[-1]["trade_date"] != trade_date
                or any(str(row["quality_flag"]) not in _TRUSTED_TECH_QUALITY_FLAGS
                       for row in rows)):
                continue
            prices = tuple({key: row[key] for key in
                            ("trade_date", "open", "high", "low", "close", "volume")}
                           for row in rows)
            try:
                closes = [float(row["close"]) for row in rows]
                technical = PortfolioExitEngine._technical(closes)
            except (ValueError, TypeError, OverflowError):
                continue
            if technical is not None:
                result[symbol] = _Technical(
                    technical.close, technical.ma10, technical.ma20,
                    technical.macd_hist, prices,
                )
        return result

    @staticmethod
    def _load_calendar(
        conn: sqlite3.Connection,
        start_date: str,
        end_date: str,
    ) -> tuple[str, ...]:
        try:
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
            if "no such table" in str(exc).lower():
                return ()
            raise
        return tuple(str(row["trade_date"]) for row in rows)

    @staticmethod
    def _technical(closes: list[float]) -> _Technical | None:
        if len(closes) < 20:
            return None
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        ema12 = PortfolioExitEngine._ema(closes, 12)
        ema26 = PortfolioExitEngine._ema(closes, 26)
        dif = [fast - slow for fast, slow in zip(ema12, ema26)]
        dea = PortfolioExitEngine._ema(dif, 9)
        return _Technical(
            close=closes[-1],
            ma10=ma10,
            ma20=ma20,
            macd_hist=(dif[-1] - dea[-1]) * 2,
        )

    @staticmethod
    def _ema(values: list[float], period: int) -> list[float]:
        alpha = 2 / (period + 1)
        result = [values[0]]
        for value in values[1:]:
            result.append(alpha * value + (1 - alpha) * result[-1])
        return result

    @staticmethod
    def _technical_payload(technical: _Technical) -> dict[str, object]:
        return {
            "qfq_close": technical.close,
            "ma10": technical.ma10,
            "ma20": technical.ma20,
            "macd_hist": technical.macd_hist,
        }

    @staticmethod
    def _take_profit_rules(plan: ExitPlan) -> Iterable[tuple[int, int, int]]:
        for tier in (1, 2, 3):
            threshold = getattr(plan, f"take_profit_{tier}_micros")
            reduce_bps = getattr(plan, f"take_profit_{tier}_reduce_bps")
            if threshold is not None and reduce_bps is not None:
                yield tier, threshold, reduce_bps

    @staticmethod
    def _time_stop_triggered(
        position: ProjectedPosition,
        plan: ExitPlan,
        raw_price: int,
        calendar: tuple[str, ...],
        trade_date: str,
    ) -> bool:
        if (
            plan.time_stop_trading_days is None
            or plan.time_stop_min_return_bps is None
        ):
            return False
        held_days = sum(
            position.first_buy_date <= item <= trade_date for item in calendar
        )
        if held_days < plan.time_stop_trading_days:
            return False
        return (
            raw_price * 10_000
            <= position.avg_cost_micros
            * (10_000 + plan.time_stop_min_return_bps)
        )

    @staticmethod
    def _price_to_micros(value: object) -> int | None:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if not decimal_value.is_finite() or decimal_value <= 0:
            return None
        micros = int(
            (decimal_value * Decimal(1_000_000)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        return micros if 1 <= micros <= MAX_PRICE_MICROS else None

    @staticmethod
    def _round_half_up(numerator: int, denominator: int) -> int:
        return (numerator + denominator // 2) // denominator

    @staticmethod
    def _find_entry_transaction_id(
        conn: sqlite3.Connection,
        account_id: int,
        symbol: str,
        target_revision: int,
    ) -> int:
        rows = conn.execute(
            """
            SELECT id, business_type, quantity
            FROM portfolio_transactions AS transaction_row
            WHERE account_id = ? AND symbol = ? AND status = 'POSTED'
              AND business_type IN ('BUY', 'SELL', 'CORPORATE_ACTION')
              AND NOT EXISTS (
                  SELECT 1 FROM portfolio_transactions AS reversal
                  WHERE reversal.reversal_of = transaction_row.id
                    AND reversal.status = 'POSTED'
              )
            ORDER BY trade_date, occurred_at, id
            """,
            (account_id, symbol),
        ).fetchall()
        revision = 0
        quantity = 0
        entry_id: int | None = None
        for row in rows:
            business_type = str(row["business_type"])
            change = int(row["quantity"])
            if business_type == "BUY":
                if quantity == 0:
                    revision += 1
                    entry_id = int(row["id"])
                quantity += change
            elif business_type == "SELL":
                quantity -= change
            else:
                quantity += change
            if quantity < 0:
                raise StaleExitPlanError("invalid ledger position sequence")
            if revision == target_revision and entry_id is not None:
                if quantity > 0:
                    return entry_id
        raise StaleExitPlanError("could not find effective BUY entry for revision")

    @staticmethod
    def _write_plan(
        conn: sqlite3.Connection,
        *,
        account_id: int,
        symbol: str,
        position_revision: int,
        entry_transaction_id: int,
        enabled: bool,
        values: tuple[int | None, ...],
        trend_exit_enabled: bool,
        intraday_enabled: bool,
        rule_version: str,
    ) -> None:
        assignments = ", ".join(f"{field} = excluded.{field}" for field in _PLAN_FIELDS)
        columns = ", ".join(_PLAN_FIELDS)
        placeholders = ", ".join("?" for _ in _PLAN_FIELDS)
        conn.execute(
            f"""
            INSERT INTO portfolio_exit_plans (
                account_id, symbol, position_revision, enabled,
                entry_transaction_id, {columns},
                trend_exit_enabled, intraday_enabled, rule_version,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, {placeholders}, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id, symbol, position_revision) DO UPDATE SET
                enabled = excluded.enabled,
                {assignments},
                trend_exit_enabled = excluded.trend_exit_enabled,
                intraday_enabled = excluded.intraday_enabled,
                rule_version = excluded.rule_version,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                account_id,
                symbol,
                position_revision,
                int(enabled),
                entry_transaction_id,
                *values,
                int(trend_exit_enabled),
                int(intraday_enabled),
                rule_version,
            ),
        )

    @staticmethod
    def _get_plan_row(
        conn: sqlite3.Connection,
        account_id: int,
        symbol: str,
        revision: int,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM portfolio_exit_plans
            WHERE account_id = ? AND symbol = ? AND position_revision = ?
            """,
            (account_id, symbol, revision),
        ).fetchone()
        if row is None:
            raise ExitPlanNotFoundError("exit plan does not exist")
        return row

    @staticmethod
    def _validate_plan_values(**values: object) -> None:
        rule_version = values.pop("rule_version")
        if not isinstance(rule_version, str) or not rule_version.strip():
            raise InvalidExitPlanError("rule_version must be non-empty")
        price_fields = (
            "hard_stop_micros",
            "take_profit_1_micros",
            "take_profit_2_micros",
            "take_profit_3_micros",
        )
        for field in price_fields:
            value = values[field]
            if value is not None:
                PortfolioExitEngine._require_int_range(
                    value, field, 1, MAX_PRICE_MICROS
                )
        for tier in (1, 2, 3):
            price = values[f"take_profit_{tier}_micros"]
            bps = values[f"take_profit_{tier}_reduce_bps"]
            if (price is None) != (bps is None):
                raise InvalidExitPlanError(
                    f"take-profit tier {tier} requires price and bps"
                )
            if bps is not None:
                PortfolioExitEngine._require_int_range(
                    bps, f"take_profit_{tier}_reduce_bps", 1, 10_000
                )
        prices = [
            values[f"take_profit_{tier}_micros"]
            for tier in (1, 2, 3)
            if values[f"take_profit_{tier}_micros"] is not None
        ]
        if prices != sorted(prices) or len(prices) != len(set(prices)):
            raise InvalidExitPlanError("take-profit prices must increase")
        bps_total = sum(
            int(values[f"take_profit_{tier}_reduce_bps"] or 0)
            for tier in (1, 2, 3)
        )
        if bps_total > 10_000:
            raise InvalidExitPlanError("take-profit bps total exceeds 10000")
        for first, second, label in (
            (
                "trailing_activation_bps",
                "trailing_distance_bps",
                "trailing",
            ),
            (
                "time_stop_trading_days",
                "time_stop_min_return_bps",
                "time stop",
            ),
        ):
            if (values[first] is None) != (values[second] is None):
                raise InvalidExitPlanError(f"{label} requires both fields")
        if values["trailing_activation_bps"] is not None:
            PortfolioExitEngine._require_int_range(
                values["trailing_activation_bps"],
                "trailing_activation_bps",
                1,
                100_000,
            )
            PortfolioExitEngine._require_int_range(
                values["trailing_distance_bps"],
                "trailing_distance_bps",
                1,
                10_000,
            )
        if values["time_stop_trading_days"] is not None:
            PortfolioExitEngine._require_int_range(
                values["time_stop_trading_days"],
                "time_stop_trading_days",
                1,
                10_000,
            )
            PortfolioExitEngine._require_int_range(
                values["time_stop_min_return_bps"],
                "time_stop_min_return_bps",
                -10_000,
                100_000,
            )

    @staticmethod
    def _validate_identity(account_id: int, symbol: str, revision: int) -> None:
        PortfolioExitEngine._require_positive_int(account_id, "account_id")
        PortfolioExitEngine._require_positive_int(revision, "position_revision")
        if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit():
            raise InvalidExitPlanError("symbol must contain exactly six digits")

    @staticmethod
    def _require_positive_int(value: object, field: str) -> None:
        PortfolioExitEngine._require_int_range(value, field, 1, 2**63 - 1)

    @staticmethod
    def _require_bool(value: object, field: str) -> None:
        if type(value) is not bool:
            raise InvalidExitPlanError(f"{field} must be a boolean")

    @staticmethod
    def _require_int_range(
        value: object,
        field: str,
        minimum: int,
        maximum: int,
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise InvalidExitPlanError(
                f"{field} must be an integer between {minimum} and {maximum}"
            )

    @staticmethod
    def _validate_date(value: str) -> None:
        if not isinstance(value, str):
            raise InvalidExitPlanError("trade_date must be YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidExitPlanError("trade_date must be YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise InvalidExitPlanError("trade_date must be YYYY-MM-DD")

    @staticmethod
    def _event_time(trade_date: str) -> str:
        return f"{trade_date}T15:00:00+08:00"

    @staticmethod
    def _bar_time(trade_date: str | None) -> str | None:
        return f"{trade_date}T15:00:00+08:00" if trade_date else None

    @staticmethod
    def _plan_from_row(row: sqlite3.Row) -> ExitPlan:
        return ExitPlan(
            account_id=int(row["account_id"]),
            symbol=str(row["symbol"]),
            position_revision=int(row["position_revision"]),
            enabled=bool(row["enabled"]),
            entry_transaction_id=int(row["entry_transaction_id"]),
            hard_stop_micros=PortfolioExitEngine._optional_int(row, "hard_stop_micros"),
            take_profit_1_micros=PortfolioExitEngine._optional_int(row, "take_profit_1_micros"),
            take_profit_1_reduce_bps=PortfolioExitEngine._optional_int(
                row, "take_profit_1_reduce_bps"
            ),
            take_profit_2_micros=PortfolioExitEngine._optional_int(row, "take_profit_2_micros"),
            take_profit_2_reduce_bps=PortfolioExitEngine._optional_int(
                row, "take_profit_2_reduce_bps"
            ),
            take_profit_3_micros=PortfolioExitEngine._optional_int(row, "take_profit_3_micros"),
            take_profit_3_reduce_bps=PortfolioExitEngine._optional_int(
                row, "take_profit_3_reduce_bps"
            ),
            trailing_activation_bps=PortfolioExitEngine._optional_int(
                row, "trailing_activation_bps"
            ),
            trailing_distance_bps=PortfolioExitEngine._optional_int(row, "trailing_distance_bps"),
            time_stop_trading_days=PortfolioExitEngine._optional_int(row, "time_stop_trading_days"),
            time_stop_min_return_bps=PortfolioExitEngine._optional_int(
                row, "time_stop_min_return_bps"
            ),
            trend_exit_enabled=bool(row["trend_exit_enabled"]),
            intraday_enabled=bool(row["intraday_enabled"]),
            rule_version=str(row["rule_version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> ExitState:
        return ExitState(
            account_id=int(row["account_id"]),
            symbol=str(row["symbol"]),
            position_revision=int(row["position_revision"]),
            state=str(row["state"]),
            highest_price_micros=PortfolioExitEngine._optional_int(row, "highest_price_micros"),
            trailing_stop_micros=PortfolioExitEngine._optional_int(row, "trailing_stop_micros"),
            last_price_micros=PortfolioExitEngine._optional_int(row, "last_price_micros"),
            last_price_date=str(row["last_price_date"]) if row["last_price_date"] else None,
            last_bar_time=str(row["last_bar_time"]) if row["last_bar_time"] else None,
            data_status=str(row["data_status"]),
            last_transaction_id=int(row["last_transaction_id"]),
            rule_version=str(row["rule_version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ExitEvent:
        return ExitEvent(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            symbol=str(row["symbol"]),
            position_revision=int(row["position_revision"]),
            event_type=str(row["event_type"]),
            severity=str(row["severity"]),
            event_status=str(row["event_status"]),
            trigger_date=str(row["trigger_date"]),
            triggered_at=str(row["triggered_at"]),
            bar_time=str(row["bar_time"]) if row["bar_time"] else None,
            observed_price_micros=PortfolioExitEngine._optional_int(row, "observed_price_micros"),
            available_quantity=int(row["available_quantity"]),
            recommended_quantity=int(row["recommended_quantity"]),
            executable=bool(row["executable"]),
            tier=PortfolioExitEngine._optional_int(row, "tier"),
            dedupe_key=str(row["dedupe_key"]),
            payload_json=str(row["payload_json"]) if row["payload_json"] else None,
            rule_version=str(row["rule_version"]),
            acknowledged_at=str(row["acknowledged_at"]) if row["acknowledged_at"] else None,
            resolved_at=str(row["resolved_at"]) if row["resolved_at"] else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _optional_int(row: sqlite3.Row, column: str) -> int | None:
        value = row[column]
        return int(value) if value is not None else None
