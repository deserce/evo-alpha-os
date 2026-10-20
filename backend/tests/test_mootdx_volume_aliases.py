"""Offline regression for pytdx and mootdx volume schemas."""

from unittest.mock import Mock

import pandas as pd
import pytest

from data_job.core.mootdx_adapter import MootdxAdapter, _normalize_volume_aliases


def make_adapter(frame):
    adapter = MootdxAdapter.__new__(MootdxAdapter)
    adapter.success_count = adapter.fail_count = 0
    adapter._ensure_connected = Mock(return_value=True)
    adapter._connect = Mock(return_value=True)
    adapter._fetch_bars_data = Mock(return_value=frame)
    adapter._fetch_index_bars_data = Mock(return_value=frame)
    adapter.get_xdxr_info = Mock(return_value=None)
    return adapter


def bars():
    return pd.DataFrame({
        "datetime": ["2024-01-02", "2024-01-03"],
        "open": [10.0, 11.0], "high": [10.0, 11.0],
        "low": [10.0, 11.0], "close": [10.0, 11.0],
        "amount": [10000.0, 22000.0], "vol": [1000.0, 2000.0],
    })


@pytest.mark.parametrize("kind", ["stock_qfq", "etf_raw", "index", "sector"])
@pytest.mark.parametrize("schema", ["vol", "volume", "both"])
def test_volume_aliases_preserve_source_units(kind, schema):
    frame = bars()
    if schema != "vol":
        frame["volume"] = frame["vol"]
    if schema == "volume":
        frame = frame.drop(columns="vol")
    original = frame.copy()
    adapter = make_adapter(frame)
    if kind == "index":
        result = adapter.get_index_kline("000001")
    elif kind == "sector":
        result = adapter.get_sector_kline("880001")
    else:
        result = adapter.get_stock_kline(
            "510300" if kind == "etf_raw" else "600519",
            adjust="none" if kind == "etf_raw" else "qfq",
        )
    assert result is not None
    assert result.columns.is_unique
    assert result["volume"].tolist() == [1000.0, 2000.0]
    pd.testing.assert_frame_equal(frame, original)


def test_conflicting_volume_aliases_rejected():
    frame = bars()
    frame["volume"] = frame["vol"] * 100
    adapter = make_adapter(frame)
    assert adapter.get_stock_kline("600519", adjust="none") is None
    assert adapter.success_count == 0


def test_empty_bars_return_no_data():
    adapter = make_adapter(pd.DataFrame())
    assert adapter.get_stock_kline("600519") is None
    assert adapter.last_failure_kind == "no_data"


def test_duplicate_volume_columns_are_rejected_before_numeric_conversion():
    frame = bars().rename(columns={"vol": "volume"})
    frame = pd.concat([frame, frame[["volume"]]], axis=1)
    with pytest.raises(ValueError, match="duplicate column"):
        _normalize_volume_aliases(frame)


def test_matching_null_aliases_remain_null_without_unit_guessing():
    frame = bars()
    frame.loc[0, "vol"] = float("nan")
    frame["volume"] = frame["vol"]
    result = _normalize_volume_aliases(frame)
    assert result.columns.is_unique
    assert pd.isna(result.loc[0, "volume"])
    assert result.loc[1, "volume"] == 2000.0
