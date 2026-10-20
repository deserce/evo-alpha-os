"""Chart API validation and completed calendar buckets."""
import asyncio
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from app.api.v1.chart_signals import complete_rows, get_chart_signals
from app.api.v1.schemas import ApiResponse


def test_forming_week_and_month_excluded():
    assert complete_rows([{"trade_date": "2026-09-29"}], "weekly", "2026-09-29") == []
    assert complete_rows([{"trade_date": "2026-09-29"}], "monthly", "2026-09-29") == []
    assert complete_rows([{"trade_date": "2026-09-30"}], "monthly", "2026-09-30")


@pytest.mark.parametrize("symbol,period,end", [("bad", "weekly", None),
    ("600001", "5min", None), ("600001", "weekly", "bad")])
def test_invalid_api_inputs(symbol, period, end):
    with pytest.raises(HTTPException) as error:
        asyncio.run(get_chart_signals(symbol, period, end, 260))
    assert error.value.status_code == 422


def test_reuses_chart_rows_and_preserves_metadata():
    async def chart(*args):
        return ApiResponse(data={"rows": [{"trade_date": "2026-09-30 11:30"}]})
    with patch("app.api.v1.chart_signals.get_stock_chart", chart), patch(
        "app.api.v1.chart_signals.calculate", return_value={"rows": [], "structure": {}}
    ) as calculate:
        data = asyncio.run(get_chart_signals("600001", "120min", "2026-09-30", 260)).data
        assert data["chart_only"] is True
        assert data["period"] == "120min"
        assert calculate.call_args.args[2][0]["trade_date"].endswith("11:30")
