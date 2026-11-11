"""Read-only, bounded access to the shared research catalogue."""
from datetime import date
import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.v1.schemas import ApiResponse
from research.repository import Repository


router = APIRouter(prefix="/research", tags=["研报中心"])
logger = logging.getLogger(__name__)


def _validate_dates(date_from: str, date_to: str) -> None:
    for value in (date_from, date_to):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(status_code=422, detail="日期必须为有效的 YYYY-MM-DD") from None
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="开始日期不得晚于结束日期")


def _unavailable(error: Exception) -> HTTPException:
    # Do not expose database paths or raw SQLite diagnostics to clients or logs.
    logger.warning("Research catalogue unavailable (%s)", type(error).__name__)
    return HTTPException(status_code=503, detail="研报库暂时不可用，请稍后重试")


@router.get("/reports", response_model=ApiResponse)
def list_reports(
    q: str = Query("", max_length=120),
    symbol: str = Query("", pattern=r"^(?:[0-9]{6})?$"),
    report_type: Literal["", "company", "industry", "unknown"] = "",
    industry: str = Query("", max_length=120),
    org: str = Query("", max_length=120),
    date_from: str = Query("", pattern=r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2})?$"),
    date_to: str = Query("", pattern=r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2})?$"),
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(30, ge=1, le=100),
) -> ApiResponse:
    """List metadata without downloading documents or triggering collection."""
    _validate_dates(date_from, date_to)
    try:
        result = Repository().list_reports(
            q=q.strip(), symbol=symbol, report_type=report_type,
            industry=industry.strip(), org=org.strip(), date_from=date_from,
            date_to=date_to, page=page, page_size=page_size,
        )
    except (sqlite3.Error, OSError) as error:
        raise _unavailable(error) from None
    return ApiResponse(data=result)


@router.get("/reports/{report_id}", response_model=ApiResponse)
def get_report(
    report_id: str = Path(..., min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$"),
) -> ApiResponse:
    """Read one catalogue item by its source-qualified identifier."""
    try:
        report = Repository().get_report(report_id)
    except (sqlite3.Error, OSError) as error:
        raise _unavailable(error) from None
    if report is None:
        raise HTTPException(status_code=404, detail="未找到该研报")
    return ApiResponse(data=report)


@router.get("/facets", response_model=ApiResponse)
def get_facets() -> ApiResponse:
    """Return bounded filter choices and catalogue coverage."""
    try:
        result = Repository().facets()
    except (sqlite3.Error, OSError) as error:
        raise _unavailable(error) from None
    return ApiResponse(data=result)
