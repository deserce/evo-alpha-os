from unittest.mock import Mock
import pandas as pd
import pytest
from data_job.core.mootdx_adapter import MootdxAdapter


def make_adapter(frames):
    obj = MootdxAdapter.__new__(MootdxAdapter)
    obj.api = obj.backend = obj._active_server = None
    obj.connected = False
    obj.success_count = obj.fail_count = 0
    obj.last_failure_kind = None
    obj._ensure_connected = Mock(return_value=True)
    obj._fetch_bars_data = Mock(side_effect=frames)
    obj._discard_connection = Mock()
    obj._transaction_failed = Mock(return_value=False)
    return obj


def test_recent_bars_keep_time_and_bound_request():
    obj = make_adapter([pd.DataFrame({'datetime': ['2026-10-27 11:20', '2026-10-27 11:15'], 'close': [22.5, 22.4], 'vol': [100, 90]})])
    frame = obj.get_recent_stock_bars('000792', count=3)
    assert list(frame.bar_time.dt.strftime('%H:%M')) == ['11:15', '11:20']
    assert list(frame.close) == [22.4, 22.5]
    assert list(frame.volume) == [90, 100]
    obj._fetch_bars_data.assert_called_once_with(symbol='000792', period='5min', batch_size=3, max_bars=3)


@pytest.mark.parametrize('kwargs', [{'count': 0}, {'count': 801}, {'period': 'daily'}])
def test_invalid_request(kwargs):
    obj = make_adapter([])
    with pytest.raises(ValueError):
        obj.get_recent_stock_bars('000792', **kwargs)
    obj._fetch_bars_data.assert_not_called()


def test_empty_retries_once():
    obj = make_adapter([None, None])
    obj._active_server = ('test', 7709, 'test')
    obj._mark_server_failure = Mock()
    assert obj.get_recent_stock_bars('000792') is None
    assert obj._fetch_bars_data.call_count == 2
    assert obj.last_failure_kind == 'connectivity'


def test_invalid_time_is_not_a_bar():
    obj = make_adapter([pd.DataFrame({'datetime': [None], 'close': [22]})])
    assert obj.get_recent_stock_bars('000792') is None
