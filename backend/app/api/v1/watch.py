"""低吸长期跟踪行动箱 API。"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.v1.schemas import ApiResponse
from quant_engine.watch.engine import DB_PATH, WatchEngine
from quant_engine.watch.rules import (
    LowBuyRules,
    load_active_rules,
    rule_config_rows,
    save_rule_config,
)
from quant_engine.watch.scanner import (
    ScanBusyError,
    ScanValidationError,
    WatchHistoricalScanner,
)


router = APIRouter(prefix="/watch", tags=["低吸跟踪"])
WATCH_DB_PATH: Path = DB_PATH
_SCHEMA_READY_PATHS: set[Path] = set()
logger = logging.getLogger(__name__)


class WatchEventUpdate(BaseModel):
    """确认或延后事件。"""

    action: Literal["acknowledge", "snooze"] | None = None
    status: str | None = Field(None, description="兼容前端：acknowledged 或 snoozed")
    snoozed_until: str | None = Field(None, description="延后至 YYYY-MM-DD")

    @model_validator(mode="after")
    def validate_snooze_date(self):
        if self.status:
            normalized = self.status.strip().lower()
            status_actions = {"acknowledged": "acknowledge", "snoozed": "snooze"}
            if normalized not in status_actions:
                raise ValueError("status 只能是 acknowledged 或 snoozed")
            status_action = status_actions[normalized]
            if self.action and self.action != status_action:
                raise ValueError("action 与 status 冲突")
            self.action = status_action
        if not self.action:
            raise ValueError("必须提供 action 或 status")
        if self.snoozed_until:
            try:
                self.snoozed_until = date.fromisoformat(self.snoozed_until[:10]).isoformat()
            except ValueError as exc:
                raise ValueError("snoozed_until 必须是 YYYY-MM-DD") from exc
        return self


class WatchRuleConfigUpdate(BaseModel):
    """保存规则草稿或保存并启用。"""

    config: dict[str, Any]
    activate: bool = False


class WatchScanRequest(BaseModel):
    """历史机会遍历请求。"""

    start_date: str
    end_date: str
    scope: Literal["all_groups", "monitored_groups", "pools"]
    pool_ids: list[str] = Field(default_factory=list)
    rule_version: int | None = Field(None, ge=1)


def _connect() -> sqlite3.Connection:
    db_path = Path(WATCH_DB_PATH)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    if db_path not in _SCHEMA_READY_PATHS:
        WatchEngine(db_path).ensure_schema(conn)
        _SCHEMA_READY_PATHS.add(db_path)
    return conn


def _json_value(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _snapshot_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["factors"] = _json_value(result.get("factors"), {})
    # 兼容早期前端命名，同时保留清晰的 watch_state/reason 契约。
    result["state"] = result.get("watch_state")
    result["why_now"] = result.get("reason")
    return result


def _event_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["payload"] = _json_value(result.get("payload"), {})
    return result


def _scan_snapshot_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["pool_ids"] = _json_value(result.get("pool_ids"), [])
    result["factors"] = _json_value(result.get("factors"), {})
    result["state"] = result.get("watch_state")
    result["why_now"] = result.get("reason")
    result["source"] = "historical_scan"
    return result


def _historical_action_events(
    conn: sqlite3.Connection,
    *,
    trade_date: str | None,
    pool_id: str | None,
    event_type: str | None,
    symbol: str | None,
    existing_rows: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """用最新完成的历史扫描状态补齐指定日期行动箱。

    扫描结果不是可确认的正式提醒，因此不伪造 event_id，前端仅作历史回看。
    持续 TRIGGERED 表示冷却期状态，需展示但不重复发送提醒。
    """
    if not trade_date:
        return []
    if not WatchEngine._table_exists(conn, "watch_scan_runs") or not WatchEngine._table_exists(
        conn, "watch_scan_results"
    ):
        return []

    scan_where = [
        "r.status='COMPLETED'",
        "COALESCE(r.origin, 'MANUAL')='MANUAL'",
        "EXISTS (SELECT 1 FROM watch_scan_results s "
        "WHERE s.scan_id=r.scan_id AND s.trade_date=?)",
    ]
    scan_params: list[Any] = [trade_date]
    if pool_id:
        scan_where[-1] = scan_where[-1][:-1] + (
            " AND EXISTS (SELECT 1 FROM json_each(s.pool_ids) WHERE value=?))"
        )
        scan_params.append(pool_id)
    scan = conn.execute(
        "SELECT r.scan_id FROM watch_scan_runs r WHERE "
        + " AND ".join(scan_where)
        + " ORDER BY COALESCE(r.completed_at, r.created_at) DESC, r.created_at DESC LIMIT 1",
        scan_params,
    ).fetchone()
    if scan is None:
        return []

    where = [
        "scan_id=?",
        "trade_date=?",
        "watch_state IN ('IN_ZONE', 'TRIGGERED', 'INVALIDATED', 'UNKNOWN')",
    ]
    params: list[Any] = [str(scan[0]), trade_date]
    if pool_id:
        where.append(
            "EXISTS (SELECT 1 FROM json_each(watch_scan_results.pool_ids) WHERE value=?)"
        )
        params.append(pool_id)
    if symbol:
        where.append("symbol=?")
        params.append(symbol.strip().upper())

    normalized_event_type = event_type.upper() if event_type else None
    state_for_event = {
        "IN_ZONE": "IN_ZONE",
        "TRIGGERED": "TRIGGERED",
        "INVALIDATED": "INVALIDATED",
        "UNKNOWN": "DATA_BLOCKED",
    }
    existing_keys = {
        (
            str(row.get("trade_date") or "")[:10],
            str(row.get("symbol") or ""),
            str(row.get("event_type") or "").upper(),
        )
        for row in existing_rows
        if str(row.get("status") or "UNREAD").upper()
        not in {"ACKNOWLEDGED", "RESOLVED"}
    }
    rows = conn.execute(
        "SELECT * FROM watch_scan_results WHERE "
        + " AND ".join(where)
        + " ORDER BY CASE watch_state WHEN 'TRIGGERED' THEN 1 "
        "WHEN 'IN_ZONE' THEN 2 WHEN 'INVALIDATED' THEN 3 ELSE 4 END, symbol",
        params,
    ).fetchall()
    payload: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        mapped_event_type = state_for_event.get(str(item.get("watch_state")), "")
        if normalized_event_type and mapped_event_type != normalized_event_type:
            continue
        key = (trade_date, str(item.get("symbol") or ""), mapped_event_type)
        if key in existing_keys:
            continue
        factors = _json_value(item.get("factors"), {})
        current_state = str(item.get("watch_state") or "")
        previous_state = str(item.get("previous_state") or "")
        is_transition = (
            previous_state != current_state
            and not (
                current_state == "IN_ZONE"
                and previous_state in {"IN_ZONE", "TRIGGERED"}
            )
        )
        event_payload = {
            key: value
            for key, value in item.items()
            if key not in {"scan_id", "factors", "created_at"}
        }
        event_payload.update(
            {
                "pool_ids": _json_value(item.get("pool_ids"), []),
                "factors": factors,
                "source": "historical_scan",
                "scan_id": str(scan[0]),
                "is_transition": is_transition,
                "why_now": item.get("reason"),
            }
        )
        payload.append(
            {
                "dedupe_key": f"scan:{scan[0]}:{trade_date}:{item.get('symbol')}:{mapped_event_type}",
                "trade_date": trade_date,
                "symbol": item.get("symbol"),
                "event_type": mapped_event_type,
                "from_state": item.get("previous_state"),
                "to_state": item.get("watch_state"),
                "severity": "HIGH" if mapped_event_type in {"TRIGGERED", "DATA_BLOCKED"} else "MEDIUM",
                "title": item.get("name") or item.get("symbol") or mapped_event_type,
                "message": item.get("reason") or item.get("data_reason") or "",
                "payload": event_payload,
                "status": "HISTORICAL",
                "created_at": item.get("created_at") or trade_date,
                "updated_at": item.get("created_at") or trade_date,
            }
        )
        if len(existing_rows) + len(payload) >= limit:
            break
    return payload


def _latest_snapshot_date(conn: sqlite3.Connection, requested: str | None) -> str | None:
    if requested:
        try:
            return date.fromisoformat(requested[:10]).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="trade_date 必须是 YYYY-MM-DD") from exc
    row = conn.execute("SELECT MAX(trade_date) FROM watch_daily_snapshot").fetchone()
    return str(row[0])[:10] if row and row[0] else None


def _next_trade_date(conn: sqlite3.Connection, base_date: str) -> str:
    if WatchEngine._table_exists(conn, "stock_daily_prices"):
        row = conn.execute(
            "SELECT MIN(substr(trade_date, 1, 10)) FROM stock_daily_prices "
            "WHERE substr(trade_date, 1, 10)>?",
            (base_date,),
        ).fetchone()
        if row and row[0]:
            return str(row[0])[:10]
    candidate = date.fromisoformat(base_date) + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


@router.get("/config", response_model=ApiResponse)
async def get_watch_config():
    with _connect() as conn:
        active_rules = load_active_rules(conn)
        conn.commit()
        versions = rule_config_rows(conn)
        active = next(
            (row for row in versions if row["version"] == active_rules.version), None
        )
        draft = next((row for row in versions if row["status"] == "DRAFT"), None)
        return ApiResponse(
            data={
                "active": active,
                "draft": draft,
                "versions": versions,
                "defaults": LowBuyRules().to_dict(),
            }
        )


@router.put("/config", response_model=ApiResponse)
async def update_watch_config(body: WatchRuleConfigUpdate):
    with _connect() as conn:
        try:
            saved = save_rule_config(conn, body.config, activate=body.activate)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        conn.commit()
        status = "ACTIVE" if body.activate else "DRAFT"
        watch_refresh = (
            WatchEngine(WATCH_DB_PATH).run() if body.activate else None
        )
        return ApiResponse(
            data={
                "version": saved.version,
                "status": status,
                "config": saved.to_dict(),
                "watch_refresh": watch_refresh,
            }
        )


def _run_watch_scan_background(db_path: Path, scan_id: str) -> None:
    try:
        WatchHistoricalScanner(db_path).run_scan(scan_id)
    except Exception:
        logger.exception("Watch 历史遍历失败: scan_id=%s", scan_id)


@router.post("/scans", response_model=ApiResponse)
async def create_watch_scan(
    body: WatchScanRequest,
    background_tasks: BackgroundTasks,
):
    scanner = WatchHistoricalScanner(WATCH_DB_PATH)
    try:
        scan = scanner.create_scan(
            start_date=body.start_date,
            end_date=body.end_date,
            scope=body.scope,
            pool_ids=body.pool_ids,
            rule_version=body.rule_version,
        )
    except ScanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ScanBusyError, sqlite3.OperationalError) as exc:
        raise HTTPException(status_code=409, detail="历史遍历忙，请稍后重试") from exc
    background_tasks.add_task(
        _run_watch_scan_background, Path(WATCH_DB_PATH), scan["scan_id"]
    )
    return ApiResponse(data=scan)


@router.get("/scans/{scan_id}", response_model=ApiResponse)
async def get_watch_scan(scan_id: str):
    try:
        scan = WatchHistoricalScanner(WATCH_DB_PATH).get_scan(scan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="历史遍历任务不存在") from exc
    return ApiResponse(data=scan)


@router.get("/scan-results", response_model=ApiResponse)
async def get_watch_scan_results(
    scan_id: str | None = None,
    trade_date: str | None = None,
    pool_id: str | None = None,
    state: str | None = None,
    data_status: str | None = None,
    symbol: str | None = None,
    limit: int = 500,
    offset: int = 0,
):
    if not 1 <= limit <= 5000:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 5000 之间")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset 不能小于 0")
    scanner = WatchHistoricalScanner(WATCH_DB_PATH)
    with sqlite3.connect(WATCH_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        scanner.ensure_schema(conn)
        target_scan_id = scan_id
        if not target_scan_id:
            if symbol:
                row = conn.execute(
                    "SELECT r.scan_id FROM watch_scan_runs r "
                    "WHERE r.status='COMPLETED' "
                    "AND EXISTS (SELECT 1 FROM watch_scan_results s "
                    "WHERE s.scan_id=r.scan_id AND s.symbol=?) "
                    "ORDER BY COALESCE(r.completed_at, r.created_at) DESC, "
                    "r.created_at DESC LIMIT 1",
                    (symbol.strip().upper(),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT scan_id FROM watch_scan_runs "
                    "WHERE COALESCE(origin, 'MANUAL')='MANUAL' "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            target_scan_id = str(row[0]) if row else None
        if not target_scan_id:
            return ApiResponse(data={"scan": None, "rows": [], "count": 0})
        run = conn.execute(
            "SELECT 1 FROM watch_scan_runs WHERE scan_id=?", (target_scan_id,)
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="历史遍历任务不存在")
        where = ["scan_id=?"]
        params: list[Any] = [target_scan_id]
        if trade_date:
            try:
                normalized_date = date.fromisoformat(trade_date[:10]).isoformat()
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail="trade_date 必须是 YYYY-MM-DD"
                ) from exc
            where.append("trade_date=?")
            params.append(normalized_date)
        if pool_id:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(watch_scan_results.pool_ids) "
                "WHERE value=?)"
            )
            params.append(pool_id)
        if state:
            normalized_state = state.upper()
            if normalized_state not in {
                "WATCHING",
                "IN_ZONE",
                "TRIGGERED",
                "INVALIDATED",
                "UNKNOWN",
            }:
                raise HTTPException(status_code=422, detail="无效 watch state")
            where.append("watch_state=?")
            params.append(normalized_state)
        if data_status:
            where.append("data_status=?")
            params.append(data_status.upper())
        if symbol:
            where.append("symbol=?")
            params.append(symbol.strip().upper())
        predicate = " AND ".join(where)
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM watch_scan_results WHERE {predicate}", params
            ).fetchone()[0]
        )
        rows = conn.execute(
            "SELECT * FROM watch_scan_results WHERE "
            + predicate
            + " ORDER BY trade_date DESC, CASE watch_state "
            "WHEN 'TRIGGERED' THEN 1 WHEN 'IN_ZONE' THEN 2 WHEN 'INVALIDATED' THEN 3 "
            "WHEN 'UNKNOWN' THEN 4 ELSE 5 END, symbol LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        payload = []
        for row in rows:
            item = dict(row)
            item["pool_ids"] = _json_value(item.get("pool_ids"), [])
            item["factors"] = _json_value(item.get("factors"), {})
            payload.append(item)
    return ApiResponse(
        data={
            "scan": scanner.get_scan(target_scan_id),
            "rows": payload,
            "count": count,
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/markers", response_model=ApiResponse)
async def get_watch_markers(
    symbol: str,
    scan_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """返回入区、确认和失效转换点；失效不是持仓卖出信号。"""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=422, detail="symbol 不能为空")

    def normalized_date(value: str | None, label: str) -> str | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"{label} 必须是 YYYY-MM-DD"
            ) from exc

    normalized_start = normalized_date(start_date, "start_date")
    normalized_end = normalized_date(end_date, "end_date")
    if normalized_start and normalized_end and normalized_start > normalized_end:
        raise HTTPException(status_code=422, detail="start_date 不能晚于 end_date")

    # Default to a bounded chart horizon; explicit windows may cover up to ten years.
    normalized_end = normalized_end or date.today().isoformat()
    normalized_start = normalized_start or (
        date.fromisoformat(normalized_end) - timedelta(days=1095)
    ).isoformat()
    if not 0 <= (date.fromisoformat(normalized_end) - date.fromisoformat(normalized_start)).days <= 3660:
        raise HTTPException(status_code=422, detail="标记日期范围必须在 0 到 3660 天之间")

    with closing(sqlite3.connect(f"{Path(WATCH_DB_PATH).resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        target_scan_id = scan_id
        if not target_scan_id:
            row = conn.execute(
                "SELECT r.scan_id FROM watch_scan_runs r "
                "WHERE r.status='COMPLETED' "
                "AND EXISTS (SELECT 1 FROM watch_scan_results s "
                "WHERE s.scan_id=r.scan_id AND s.symbol=?) "
                "ORDER BY COALESCE(r.completed_at, r.created_at) DESC, "
                "r.created_at DESC LIMIT 1",
                (normalized_symbol,),
            ).fetchone()
            target_scan_id = str(row[0]) if row else None
        if target_scan_id:
            run = conn.execute(
                "SELECT status FROM watch_scan_runs WHERE scan_id=?", (target_scan_id,)
            ).fetchone()
            if run is None:
                raise HTTPException(status_code=404, detail="历史遍历任务不存在")
            if str(run[0]) != "COMPLETED":
                raise HTTPException(status_code=409, detail="历史遍历尚未完成")
        coverage = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date) FROM watch_scan_results "
            "WHERE scan_id=? AND symbol=?", (target_scan_id, normalized_symbol),
        ).fetchone()
        columns = (
            "trade_date, symbol, name, watch_state AS marker_type, previous_state, "
            "current_price, trigger_price, invalidation_price, reason, factors, rule_version, setup_id"
        )
        # A scan is a coherent replay under its saved rule version. Keep its entire
        # coverage authoritative, and extend the timeline with official snapshots.
        timeline = (
            f"SELECT {columns}, 'historical_scan' AS source FROM watch_scan_results "
            "WHERE scan_id=:scan_id AND symbol=:symbol "
        )
        if scan_id is None:
            timeline += (
                f"UNION ALL SELECT {columns}, 'daily_snapshot' AS source "
                "FROM watch_daily_snapshot WHERE symbol=:symbol "
                "AND (:coverage_start IS NULL OR trade_date<:coverage_start "
                "OR trade_date>:coverage_end) "
            )
        params = {
            "scan_id": target_scan_id, "symbol": normalized_symbol,
            "coverage_start": coverage[0], "coverage_end": coverage[1],
            "start": normalized_start, "end": normalized_end,
        }
        # Include one predecessor so filtering the visible window cannot create
        # a false first transition at the scan/live seam.
        predecessor = conn.execute(
            "WITH timeline AS (" + timeline + ") SELECT * FROM timeline "
            "WHERE trade_date<:start ORDER BY trade_date DESC LIMIT 1", params,
        ).fetchone()
        rows = conn.execute(
            "WITH timeline AS (" + timeline + ") SELECT * FROM timeline "
            "WHERE trade_date BETWEEN :start AND :end ORDER BY trade_date", params,
        ).fetchall()
        payload = []
        previous = dict(predecessor) if predecessor else None
        for row in rows:
            item = dict(row)
            state = item["marker_type"]
            stored_previous = item["previous_state"] or "WATCHING"
            effective_previous = stored_previous
            new_setup = bool(
                previous and previous["setup_id"] and item["setup_id"]
                and previous["setup_id"] != item["setup_id"]
            )
            if previous and previous["source"] != item["source"] and not new_setup:
                effective_previous = previous["marker_type"]
            is_transition = (
                state == "IN_ZONE"
                and stored_previous not in {"IN_ZONE", "TRIGGERED"}
                and effective_previous not in {"IN_ZONE", "TRIGGERED"}
            ) or (
                state in {"TRIGGERED", "INVALIDATED"}
                and stored_previous != state and effective_previous != state
            )
            previous = item
            if is_transition:
                item["factors"] = _json_value(item.get("factors"), {})
                item["scan_id"] = target_scan_id if item["source"] == "historical_scan" else None
                payload.append(item)
    return ApiResponse(
        data={
            "scan_id": target_scan_id,
            "symbol": normalized_symbol,
            "start_date": normalized_start,
            "end_date": normalized_end,
            "scan_coverage": {"start_date": coverage[0], "end_date": coverage[1]},
            "source_policy": "explicit_scan" if scan_id else "scan_coverage_then_daily_snapshot",
            "rows": payload,
        }
    )


@router.get("/stocks/{symbol}", response_model=ApiResponse)
async def get_watch_stock_detail(symbol: str, trade_date: str | None = None):
    """聚合单股正式跟踪、历史回放和正式提醒，不相互覆盖。"""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=422, detail="symbol 不能为空")

    normalized_date: str | None = None
    if trade_date:
        try:
            normalized_date = date.fromisoformat(trade_date[:10]).isoformat()
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="trade_date 必须是 YYYY-MM-DD"
            ) from exc

    scanner = WatchHistoricalScanner(WATCH_DB_PATH)
    with _connect() as conn:
        scanner.ensure_schema(conn)
        target_date = normalized_date
        if not target_date:
            tracking_date = conn.execute(
                "SELECT MAX(trade_date) FROM watch_daily_snapshot WHERE symbol=?",
                (normalized_symbol,),
            ).fetchone()[0]
            scan_date = conn.execute(
                """
                SELECT MAX(s.trade_date)
                FROM watch_scan_results s
                JOIN watch_scan_runs r ON r.scan_id=s.scan_id
                WHERE r.status='COMPLETED' AND s.symbol=?
                """,
                (normalized_symbol,),
            ).fetchone()[0]
            available_dates = [
                str(value)[:10] for value in (tracking_date, scan_date) if value
            ]
            target_date = max(available_dates) if available_dates else None

        tracking_snapshot = None
        scan_snapshot = None
        scan_meta = None
        events: list[dict[str, Any]] = []

        if target_date:
            tracking_row = conn.execute(
                "SELECT * FROM watch_daily_snapshot WHERE trade_date=? AND symbol=?",
                (target_date, normalized_symbol),
            ).fetchone()
            if tracking_row:
                tracking_snapshot = _snapshot_row(tracking_row)
                tracking_snapshot["source"] = "tracking_snapshot"

            scan_row = conn.execute(
                """
                SELECT r.scan_id, r.start_date, r.end_date, r.rule_version AS scan_rule_version,
                       r.status AS scan_status, r.created_at AS scan_created_at,
                       r.completed_at AS scan_completed_at, s.*
                FROM watch_scan_runs r
                JOIN watch_scan_results s ON s.scan_id=r.scan_id
                WHERE r.status='COMPLETED' AND s.trade_date=? AND s.symbol=?
                ORDER BY COALESCE(r.completed_at, r.created_at) DESC, r.created_at DESC
                LIMIT 1
                """,
                (target_date, normalized_symbol),
            ).fetchone()
            if scan_row:
                scan_payload = _scan_snapshot_row(scan_row)
                scan_meta = {
                    "scan_id": scan_payload.pop("scan_id"),
                    "start_date": scan_payload.pop("start_date"),
                    "end_date": scan_payload.pop("end_date"),
                    "rule_version": scan_payload.pop("scan_rule_version"),
                    "status": scan_payload.pop("scan_status"),
                    "created_at": scan_payload.pop("scan_created_at"),
                    "completed_at": scan_payload.pop("scan_completed_at"),
                }
                scan_snapshot = scan_payload

            event_rows = conn.execute(
                """
                SELECT * FROM watch_events
                WHERE trade_date=? AND symbol=?
                ORDER BY created_at DESC
                """,
                (target_date, normalized_symbol),
            ).fetchall()
            events = [_event_row(row) for row in event_rows]

        tracking_state = (
            str(tracking_snapshot.get("watch_state")) if tracking_snapshot else None
        )
        scan_state = str(scan_snapshot.get("watch_state")) if scan_snapshot else None
        if tracking_state and scan_state:
            consistency = "MATCHED" if tracking_state == scan_state else "DIVERGED"
        elif tracking_state:
            consistency = "TRACKING_ONLY"
        elif scan_state:
            consistency = "SCAN_ONLY"
        else:
            consistency = "NO_DATA"

    return ApiResponse(
        data={
            "symbol": normalized_symbol,
            "trade_date": target_date,
            "tracking_snapshot": tracking_snapshot,
            "scan_snapshot": scan_snapshot,
            "scan": scan_meta,
            "events": events,
            "consistency": consistency,
        }
    )


@router.get("/summary", response_model=ApiResponse)
async def get_watch_summary(trade_date: str | None = None, pool_id: str | None = None):
    with _connect() as conn:
        target_date = _latest_snapshot_date(conn, trade_date)
        if not target_date:
            return ApiResponse(
                data={
                    "trade_date": None,
                    "next_trade_date": None,
                    "status_counts": {},
                    "state_counts": {},
                    "data_status_counts": {},
                    "unresolved_events": 0,
                    "unprocessed_count": 0,
                    "event_counts": {},
                    "data_trusted": False,
                }
            )
        snapshot_where = "trade_date=?"
        snapshot_params: list[object] = [target_date]
        if pool_id:
            snapshot_where += (
                " AND symbol IN ("
                "SELECT symbol FROM stock_pool_members WHERE pool_id=?"
                ")"
            )
            snapshot_params.append(pool_id)
        status_counts = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT watch_state, COUNT(*) FROM watch_daily_snapshot "
                f"WHERE {snapshot_where} GROUP BY watch_state",
                snapshot_params,
            )
        }
        data_counts = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT data_status, COUNT(*) FROM watch_daily_snapshot "
                f"WHERE {snapshot_where} GROUP BY data_status",
                snapshot_params,
            )
        }
        unresolved_where = """
            status IN ('UNREAD', 'SNOOZED')
            AND (status <> 'SNOOZED' OR snoozed_until IS NULL OR snoozed_until<=?)
        """
        unresolved_params: list[object] = [target_date]
        if pool_id:
            unresolved_where += """
                AND (symbol IS NULL OR symbol IN (
                    SELECT symbol FROM stock_pool_members WHERE pool_id=?
                ))
            """
            unresolved_params.append(pool_id)
        unresolved = int(
            conn.execute(
                f"SELECT COUNT(*) FROM watch_events WHERE {unresolved_where}",
                unresolved_params,
            ).fetchone()[0]
        )
        event_where = "trade_date=?"
        event_params: list[object] = [target_date]
        if pool_id:
            event_where += """
                AND (symbol IS NULL OR symbol IN (
                    SELECT symbol FROM stock_pool_members WHERE pool_id=?
                ))
            """
            event_params.append(pool_id)
        event_counts = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT event_type, COUNT(*) FROM watch_events "
                f"WHERE {event_where} GROUP BY event_type",
                event_params,
            )
        }
        return ApiResponse(
            data={
                "trade_date": target_date,
                "next_trade_date": _next_trade_date(conn, target_date),
                "status_counts": status_counts,
                "state_counts": status_counts,
                "data_status_counts": data_counts,
                "unresolved_events": unresolved,
                "unprocessed_count": unresolved,
                "event_counts": event_counts,
                "data_trusted": not data_counts.get("BLOCKED") and not status_counts.get("UNKNOWN"),
            }
        )


@router.get("/snapshots", response_model=ApiResponse)
async def get_watch_snapshots(
    trade_date: str | None = None,
    pool_id: str | None = None,
    state: str | None = None,
    data_status: str | None = None,
):
    with _connect() as conn:
        target_date = _latest_snapshot_date(conn, trade_date)
        if not target_date:
            return ApiResponse(data={"trade_date": None, "rows": [], "count": 0})
        where = ["trade_date=?"]
        params: list[str] = [target_date]
        if pool_id:
            where.append(
                "symbol IN (SELECT symbol FROM stock_pool_members WHERE pool_id=?)"
            )
            params.append(pool_id)
        if state:
            normalized_state = state.upper()
            if normalized_state not in {
                "WATCHING",
                "IN_ZONE",
                "TRIGGERED",
                "INVALIDATED",
                "UNKNOWN",
            }:
                raise HTTPException(status_code=422, detail="无效 watch state")
            where.append("watch_state=?")
            params.append(normalized_state)
        if data_status:
            where.append("data_status=?")
            params.append(data_status.upper())
        rows = conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE "
            + " AND ".join(where)
            + " ORDER BY CASE watch_state "
            "WHEN 'TRIGGERED' THEN 1 WHEN 'IN_ZONE' THEN 2 WHEN 'INVALIDATED' THEN 3 "
            "WHEN 'UNKNOWN' THEN 4 ELSE 5 END, symbol",
            params,
        ).fetchall()
        payload = [_snapshot_row(row) for row in rows]
        return ApiResponse(data={"trade_date": target_date, "rows": payload, "count": len(payload)})


@router.get("/events", response_model=ApiResponse)
async def get_watch_events(
    trade_date: str | None = None,
    pool_id: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    symbol: str | None = None,
    unread_only: bool = False,
    limit: int = 200,
):
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 1000 之间")
    where: list[str] = []
    params: list[object] = []
    normalized_trade_date: str | None = None
    if trade_date:
        try:
            normalized_trade_date = date.fromisoformat(trade_date[:10]).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="trade_date 必须是 YYYY-MM-DD") from exc
        where.append("trade_date=?")
        params.append(normalized_trade_date)
    if pool_id:
        where.append(
            """(symbol IS NULL OR symbol IN (
                SELECT symbol FROM stock_pool_members WHERE pool_id=?
            ))"""
        )
        params.append(pool_id)
    if unread_only:
        where.append("status='UNREAD'")
    elif status:
        normalized_status = status.upper()
        if normalized_status not in {"UNREAD", "ACKNOWLEDGED", "SNOOZED"}:
            raise HTTPException(status_code=422, detail="无效事件状态")
        where.append("status=?")
        params.append(normalized_status)
    if event_type:
        where.append("event_type=?")
        params.append(event_type.upper())
    if symbol:
        where.append("symbol=?")
        params.append(symbol.strip().upper())
    sql = "SELECT * FROM watch_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC, created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = [_event_row(row) for row in conn.execute(sql, params).fetchall()]
        if not unread_only and not status:
            rows.extend(
                _historical_action_events(
                    conn,
                    trade_date=normalized_trade_date,
                    pool_id=pool_id,
                    event_type=event_type,
                    symbol=symbol,
                    existing_rows=rows,
                    limit=limit,
                )
            )
    return ApiResponse(data={"rows": rows, "count": len(rows)})


@router.patch("/events/{event_id}", response_model=ApiResponse)
async def update_watch_event(event_id: str, body: WatchEventUpdate):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        current = conn.execute(
            "SELECT * FROM watch_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="提醒事件不存在")
        if body.action == "acknowledge":
            conn.execute(
                """
                UPDATE watch_events
                SET status='ACKNOWLEDGED', acknowledged_at=?, snoozed_until=NULL, updated_at=?
                WHERE event_id=?
                """,
                (now, now, event_id),
            )
        else:
            base_date = str(current["trade_date"])[:10]
            snoozed_until = body.snoozed_until or _next_trade_date(conn, base_date)
            conn.execute(
                """
                UPDATE watch_events
                SET status='SNOOZED', snoozed_until=?, acknowledged_at=NULL, updated_at=?
                WHERE event_id=?
                """,
                (snoozed_until, now, event_id),
            )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM watch_events WHERE event_id=?", (event_id,)
        ).fetchone()
    return ApiResponse(data={"event": _event_row(updated)})


@router.get('/mrgc-report', response_model=ApiResponse)
def get_mrgc_daily_report(trade_date: str):
    """Read the dated archive and task status without recalculating or calling AI."""
    from quant_engine.watch.mrgc_report_jobs import get_report
    try:
        return ApiResponse(data=get_report(WATCH_DB_PATH, trade_date))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        logger.error('MRGC archive read failed: %s', type(exc).__name__)
        raise HTTPException(status_code=503, detail='日报存档暂不可读，请稍后重试') from exc


class MrgcReportGenerateRequest(BaseModel):
    trade_date: str = Field(min_length=10, max_length=10)
    regenerate: bool = False


@router.post('/mrgc-report/generate', response_model=ApiResponse)
def generate_mrgc_daily_report(body: MrgcReportGenerateRequest):
    """Start or resume one historical report worker, reusing complete dated archives."""
    from quant_engine.watch.mrgc_report_jobs import start_generation
    try:
        return ApiResponse(data=start_generation(WATCH_DB_PATH, body.trade_date, body.regenerate))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (sqlite3.Error, OSError) as exc:
        logger.error('MRGC archive generation failed: %s', type(exc).__name__)
        raise HTTPException(status_code=503, detail='日报任务暂不可启动，请稍后重试') from exc
