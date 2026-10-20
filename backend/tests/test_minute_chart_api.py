"""Minute chart API contracts without live network or production database writes."""
import asyncio
from contextlib import closing
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.v1 import market as market_api
from app.api.v1 import quant as quant_api
from app.api.v1 import stock as stock_api


def _payload(period, available=True):
    bars = [{
        "trade_date": f"2026-09-30 {time}",
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
        "volume": 1000.0, "amount": 10500.0, "pct_chg": 0.5,
    } for time in ("11:30", "15:00")] if available else []
    return {
        "period": period, "source": "fixture_minute", "adjustment": "none",
        "volume_unit": "shares", "status": "ready" if bars else "unavailable",
        "ready": bool(bars), "message": "完成柱" if bars else "指定日期分钟行情不可用",
        "bars": bars, "source_count": len(bars), "cache_hit": False,
    }


@pytest.mark.parametrize("period", ["30min", "120min"])
def test_stock_minutes_preserve_timestamp_and_use_only_minute_indicators(monkeypatch, period):
    monkeypatch.setattr(stock_api, "get_minute_chart", lambda *args: _payload(period))
    monkeypatch.setattr(stock_api, "fetch_one", lambda *args: pytest.fail("minute queried daily DB"))
    data = asyncio.run(stock_api.get_stock_chart(
        "600519", period=period, end_date="2026-09-30", limit=30,
    )).data
    assert data["dates"] == ["2026-09-30 11:30", "2026-09-30 15:00"]
    assert len(data["macd"]["diff"]) == len(data["kdj"]["k"]) == 2
    assert not {"rps_short", "rps_long", "funds"}.intersection(data)
    assert data["adjustment"] == "none" and data["source_count"] == 2


def test_stock_unavailable_does_not_read_daily_fallback(monkeypatch):
    monkeypatch.setattr(stock_api, "get_minute_chart", lambda *args: _payload("30min", False))
    monkeypatch.setattr(stock_api, "fetch_one", lambda *args: pytest.fail("daily fallback"))
    data = asyncio.run(stock_api.get_stock_chart("600519", period="30min", limit=30)).data
    assert data["status"] == "unavailable" and data["dates"] == [] and data["message"]


@pytest.mark.parametrize("code, expected_market", [("000001", 1), ("399006", 0), ("399317", 0)])
def test_index_minutes_use_explicit_exchange_and_minute_moving_averages(monkeypatch, code, expected_market):
    calls = []
    def load(*args):
        calls.append(args)
        return _payload(args[2])
    monkeypatch.setattr(market_api, "get_minute_chart", load)
    data = asyncio.run(market_api.get_market_index_kline(
        code, days=250, date="2026-09-30", period="120min",
    )).data
    assert calls[0] == ("index", code, "120min", "2026-09-30", 250, expected_market)
    assert data["items"][0]["tradeDate"].endswith("11:30")
    assert {"ma20", "ma50", "ma120", "ma250"}.issubset(data["items"][0])
    assert data["period"] == "120min" and data["cache_hit"] is False


def _sector_db(tmp_path, monkeypatch, source="eastmoney_stock_plate"):
    path = tmp_path / "minute-sector.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE sector_list(sector_name TEXT, sector_code TEXT, sector_type TEXT, source TEXT)")
        conn.execute("INSERT INTO sector_list VALUES (?,?,?,?)", ("行业A", "BK1234", "Industry", source))
        conn.commit()
    monkeypatch.setattr(quant_api, "DB_PATH", path)


def test_sector_minutes_use_verified_bk_instead_of_local_daily_mode(tmp_path, monkeypatch):
    _sector_db(tmp_path, monkeypatch)
    calls = []
    def load(*args, **kwargs):
        calls.append((args, kwargs))
        return _payload(args[2])
    monkeypatch.setattr(quant_api, "get_minute_chart", load)
    data = asyncio.run(quant_api.get_sector_chart(
        "行业A", trade_date="2026-09-30", limit=30, mode="local", period="30min",
    )).data
    assert calls == [(("sector", "BK1234", "30min", "2026-09-30", 30), {"sector_type": "Industry"})]
    assert data["mode"] == "vendor" and "非本地等权合成" in data["source_label"]
    assert data["dates"][1] == "2026-09-30 15:00"
    assert not {"rps_short", "rps_long"}.intersection(data)
    assert len(data["macd"]["hist"]) == 2


@pytest.mark.parametrize("source", ["unknown", "eastmoney_stock_plate"])
def test_sector_missing_or_unverified_mapping_never_loads_daily_or_network(tmp_path, monkeypatch, source):
    _sector_db(tmp_path, monkeypatch, source)
    monkeypatch.setattr(quant_api, "get_minute_chart", lambda *a, **kw: pytest.fail("unverified network"))
    name = "行业A" if source == "unknown" else "不存在行业"
    data = asyncio.run(quant_api.get_sector_chart(name, limit=30, period="120min")).data
    assert data["status"] == "unavailable" and data["dates"] == [] and data["message"]


@pytest.mark.parametrize("api_call", [
    lambda: stock_api.get_stock_chart("600519", period="invalid", limit=30),
    lambda: market_api.get_market_index_kline("000001", period="invalid"),
    lambda: quant_api.get_sector_chart("行业A", period="invalid", limit=30),
])
def test_chart_apis_reject_unsupported_period(api_call):
    with pytest.raises(HTTPException) as error:
        asyncio.run(api_call())
    assert error.value.status_code == 400


@pytest.mark.parametrize("api_call", [
    lambda: stock_api.get_stock_chart("600519", period="30min", end_date="not-a-date", limit=30),
    lambda: market_api.get_market_index_kline("000001", days=30, date="not-a-date", period="30min"),
])
def test_minute_apis_reject_invalid_explicit_dates_before_network(api_call):
    with pytest.raises(HTTPException) as error:
        asyncio.run(api_call())
    assert error.value.status_code == 400


def test_sector_invalid_date_has_http_validation_error(tmp_path, monkeypatch):
    _sector_db(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as error:
        asyncio.run(quant_api.get_sector_chart(
            "行业A", trade_date="not-a-date", limit=30, period="120min",
        ))
    assert error.value.status_code == 400
