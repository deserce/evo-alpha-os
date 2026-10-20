import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd

from data_job.collectors import index_data_collector as index_module
from data_job.collectors.index_data_collector import IndexDataCollector
from data_job.core.mootdx_adapter import MARKET_SH


def test_default_target_skips_exchange_holiday():
    assert index_module.latest_completed_trading_date(datetime(2026, 9, 25, 16, 0)).isoformat() == "2026-09-24"


def test_parse_eastmoney_klines_uses_reported_amount():
    collector = IndexDataCollector.__new__(IndexDataCollector)
    idx = {"symbol": "399317", "name": "国证A指"}

    df = collector._parse_eastmoney_klines(
        idx,
        [
            "2026-05-08,7540.42,7583.47,7592.73,7531.45,"
            "1475213385,3007460405679.29,0.81,0.00,0.13,2.10"
        ],
    )

    assert len(df) == 1
    row = df.iloc[0]
    assert row["index_code"] == "399317"
    assert row["trade_date"] == "2026-05-08"
    assert row["amount"] == 3007460405679.29
    assert row["amount"] != row["volume"] * row["close"]


def test_fetch_data_prefers_tdx_over_http():
    collector = IndexDataCollector.__new__(IndexDataCollector)
    collector.index_list = [
        {"code": "sh000300", "name": "沪深300", "symbol": "000300", "tdx_market": MARKET_SH}
    ]
    collector._fetch_from_eastmoney = MagicMock(side_effect=AssertionError("HTTP should not run"))
    collector._fetch_from_akshare = MagicMock(side_effect=AssertionError("AkShare should not run"))

    class FakeAdapter:
        def is_available(self):
            return True

        def get_index_kline(self, *args, **kwargs):
            return pd.DataFrame(
                {
                    "symbol": ["000300", "000300"],
                    "trade_date": ["2026-06-03", "2026-06-04"],
                    "open": [10.0, 10.5],
                    "high": [11.0, 11.5],
                    "low": [9.5, 10.0],
                    "close": [10.5, 11.0],
                    "volume": [100.0, 120.0],
                    "amount": [1000.0, 1320.0],
                    "pct_chg": [None, 4.76],
                }
            )

    with patch.object(index_module, "MootdxAdapter", return_value=FakeAdapter()):
        result = collector.fetch_data(start="2026-06-03", end="2026-06-04", max_bars=20)

    assert set(result) == {"000300"}
    assert result["000300"]["trade_date"].max() == "2026-06-04"
    collector._fetch_from_eastmoney.assert_not_called()
    collector._fetch_from_akshare.assert_not_called()


def test_fetch_data_falls_back_after_empty_tdx():
    collector = IndexDataCollector.__new__(IndexDataCollector)
    collector.index_list = [
        {"code": "sh000300", "name": "沪深300", "symbol": "000300", "tdx_market": MARKET_SH}
    ]
    collector._fetch_from_eastmoney = MagicMock(
        return_value=pd.DataFrame(
            {
                "index_code": ["000300"],
                "index_name": ["沪深300"],
                "trade_date": ["2026-06-04"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [10.5],
                "volume": [100.0],
                "amount": [1000.0],
                "change": [0.5],
                "pct_chg": [1.0],
            }
        )
    )
    collector._fetch_from_akshare = MagicMock(side_effect=AssertionError("AkShare should not run"))

    class FakeAdapter:
        def is_available(self):
            return True

        def get_index_kline(self, *args, **kwargs):
            return None

    with patch.object(index_module, "MootdxAdapter", return_value=FakeAdapter()):
        result = collector.fetch_data(start="2026-06-03", end="2026-06-04", max_bars=20)

    assert result["000300"]["trade_date"].tolist() == ["2026-06-04"]
    collector._fetch_from_eastmoney.assert_called_once()
