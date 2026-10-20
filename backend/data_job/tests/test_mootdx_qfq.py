"""
测试 mootdx K线分页与前复权计算。
"""

import importlib.util
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, ".")

from data_job.core.mootdx_adapter import MARKET_SH, MARKET_SZ, MootdxAdapter
from data_job.core.mootdx_qfq import (
    apply_mootdx_qfq,
    compute_turnover_rate,
    detect_volume_scale,
)

_script_path = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "standalone"
    / "collect_stock_kline_mootdx_qfq.py"
)
_spec = importlib.util.spec_from_file_location("collect_stock_kline_mootdx_qfq", _script_path)
_module = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(_module)
load_symbols = _module.load_symbols
seed_incremental_pct_chg = _module.seed_incremental_pct_chg
fetch_one_symbol = _module.fetch_one_symbol
init_target_table = _module.init_target_table
init_raw_target_table = _module.init_raw_target_table
save_dual_batch = _module.save_dual_batch
validate_target_tables = _module.validate_target_tables


class FakeQuotesAPI:
    """用于测试分页行为的假 mootdx API。"""

    def __init__(self, pages):
        self.pages = pages
        self.starts = []

    def bars(self, symbol="000001", frequency=9, start=0, offset=800, **kwargs):
        self.starts.append(start)
        return self.pages.get(start, pd.DataFrame())


class TestMootdxQfq(unittest.TestCase):
    """测试前复权计算。"""

    def test_apply_mootdx_qfq_bonus_and_dividend(self):
        """送转和分红应当正确影响历史价格。"""
        raw_df = pd.DataFrame(
            {
                "symbol": ["000001", "000001", "000001"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "open": [10.0, 4.95, 5.50],
                "high": [10.0, 4.95, 5.50],
                "low": [10.0, 4.95, 5.50],
                "close": [10.0, 4.95, 5.50],
                "volume": [100.0, 100.0, 100.0],
                "amount": [1000.0, 495.0, 550.0],
            }
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 2)],
                "category": [1],
                "fenhong": [1.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "songzhuangu": [10.0],
            }
        )

        result = apply_mootdx_qfq(raw_df, xdxr_df)

        self.assertEqual(result.loc[0, "trade_date"], date(2024, 1, 1))
        self.assertAlmostEqual(result.loc[0, "close"], 4.95, places=4)
        self.assertAlmostEqual(result.loc[1, "close"], 4.95, places=4)
        self.assertAlmostEqual(result.loc[2, "close"], 5.50, places=4)
        self.assertAlmostEqual(result.loc[1, "pct_chg"], 0.0, places=6)
        self.assertEqual(result.loc[0, "adjust_type"], "qfq")

    def test_apply_mootdx_qfq_without_actions_keeps_prices(self):
        """没有除权数据时，不应改写原始价格。"""
        raw_df = pd.DataFrame(
            {
                "symbol": ["000001", "000001"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "open": [10.0, 10.5],
                "high": [10.1, 10.6],
                "low": [9.9, 10.4],
                "close": [10.0, 10.5],
                "volume": [100.0, 120.0],
                "amount": [1000.0, 1260.0],
            }
        )

        result = apply_mootdx_qfq(raw_df, pd.DataFrame())

        self.assertAlmostEqual(result.loc[0, "close"], 10.0, places=6)
        self.assertAlmostEqual(result.loc[1, "close"], 10.5, places=6)
        self.assertAlmostEqual(result.loc[1, "pct_chg"], 5.0, places=6)
        self.assertEqual(result.loc[1, "adjust_type"], "qfq")

    def test_apply_mootdx_qfq_normalizes_tdx_zero_sentinels(self):
        raw_df = pd.DataFrame(
            {
                "trade_date": [date(2026, 7, 17)],
                "open": [34.0],
                "high": [34.0],
                "low": [34.0],
                "close": [34.0],
                "volume": [5.87747175411144e-39],
                "amount": [5.87747175411144e-39],
            }
        )

        result = apply_mootdx_qfq(raw_df, pd.DataFrame())

        self.assertEqual(result.loc[0, "volume"], 0.0)
        self.assertEqual(result.loc[0, "amount"], 0.0)

    def test_incremental_pct_chg_uses_last_stored_close_when_seed_bar_is_absent(self):
        data = pd.DataFrame(
            {
                "trade_date": [date(2026, 7, 17)],
                "close": [34.0],
                "pct_chg": [None],
            }
        )

        result = seed_incremental_pct_chg(data, previous_close=34.0)

        self.assertEqual(result.iloc[0]["pct_chg"], 0.0)


class TestTurnoverRate(unittest.TestCase):
    """测试换手率计算。"""

    def _kline(self, dates, close, volume, amount):
        return pd.DataFrame(
            {
                "symbol": ["000001"] * len(dates),
                "trade_date": dates,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )

    def test_detect_volume_scale_shares(self):
        """amount ≈ volume × close → volume 已是股，乘数 1.0。"""
        df = self._kline(
            [date(2024, 1, d) for d in (2, 3, 4, 5)],
            close=[10.0, 10.0, 10.0, 10.0],
            volume=[100.0, 200.0, 150.0, 180.0],
            amount=[1000.0, 2000.0, 1500.0, 1800.0],
        )
        self.assertEqual(detect_volume_scale(df), 1.0)

    def test_detect_volume_scale_hand(self):
        """amount ≈ volume × close × 100 → volume 是手，乘数 100.0。"""
        df = self._kline(
            [date(2024, 1, d) for d in (2, 3, 4, 5)],
            close=[10.0, 10.0, 10.0, 10.0],
            volume=[100.0, 200.0, 150.0, 180.0],
            amount=[100000.0, 200000.0, 150000.0, 180000.0],
        )
        self.assertEqual(detect_volume_scale(df), 100.0)

    def test_compute_turnover_rate_uses_xdxr_baseline(self):
        """首个 xdxr 事件之前，应使用 panqianliutong 作为历史基准。"""
        raw_df = self._kline(
            [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            close=[10.0, 10.0, 10.0],
            volume=[1_000_000.0, 2_000_000.0, 3_000_000.0],  # 股
            amount=[10_000_000.0, 20_000_000.0, 30_000_000.0],
        )
        # 2024-01-03 有一次送股事件：盘前 1 亿股流通，盘后 1.1 亿股
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 3)],
                "category": [1],
                "panqianliutong": [10000.0],  # 万股 → 1 亿股
                "panhouliutong": [11000.0],
                "fenhong": [0.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "songzhuangu": [0.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        # 2024-01-02: 历史基准 = panqianliutong = 1 亿股 → 1M/100M*100 = 1.0
        # 2024-01-03: 事件日生效 panhouliutong = 1.1 亿股 → 2M/110M*100 ≈ 1.818
        # 2024-01-04: 继续用 panhouliutong → 3M/110M*100 ≈ 2.727
        self.assertAlmostEqual(rate.iloc[0], 1.0, places=4)
        self.assertAlmostEqual(rate.iloc[1], 200.0 / 110.0, places=4)
        self.assertAlmostEqual(rate.iloc[2], 300.0 / 110.0, places=4)

    def test_compute_turnover_rate_handles_volume_in_hand(self):
        """若 volume 单位是手，应自动识别并乘 100。"""
        raw_df = self._kline(
            [date(2024, 1, d) for d in (2, 3, 4)],
            close=[10.0, 10.0, 10.0],
            volume=[10_000.0, 20_000.0, 15_000.0],                # 手
            amount=[10_000_000.0, 20_000_000.0, 15_000_000.0],    # 元
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2023, 6, 1)],
                "category": [1],
                "panqianliutong": [10000.0],
                "panhouliutong": [10000.0],  # 1 亿股
                "fenhong": [0.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "songzhuangu": [0.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        # 10000 手 × 100 = 1M 股；1M/100M*100 = 1.0
        self.assertAlmostEqual(rate.iloc[0], 1.0, places=4)
        self.assertAlmostEqual(rate.iloc[1], 2.0, places=4)
        self.assertAlmostEqual(rate.iloc[2], 1.5, places=4)

    def test_compute_turnover_rate_without_xdxr_returns_nan(self):
        """无 xdxr 数据时，turnover_rate 全为 NaN，但不抛异常。"""
        raw_df = self._kline(
            [date(2024, 1, 2), date(2024, 1, 3)],
            close=[10.0, 10.5],
            volume=[100000.0, 120000.0],
            amount=[1_000_000.0, 1_260_000.0],
        )
        rate = compute_turnover_rate(raw_df, None)

        self.assertEqual(len(rate), 2)
        self.assertTrue(rate.isna().all())

    def test_compute_turnover_rate_ignores_same_day_bad_share_event(self):
        """同日多条股本事件时，应避开总股本为 0 的异常小股本。"""
        raw_df = self._kline(
            [date(2024, 10, 18), date(2024, 10, 21)],
            close=[7.75, 7.15],
            volume=[283_345_792.0, 298_567_488.0],
            amount=[2_116_446_720.0, 2_151_651_840.0],
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2018, 12, 28), date(2018, 12, 28)],
                "category": [9, 15],
                "panqianliutong": [130000.0, 0.0],
                "panhouliutong": [197210.0, 5.17],
                "qianzongguben": [130000.0, 0.0],
                "houzongguben": [197210.0, 0.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        self.assertAlmostEqual(rate.iloc[0], 14.3677, places=4)
        self.assertAlmostEqual(rate.iloc[1], 15.1396, places=4)

    def test_compute_turnover_rate_keeps_original_input_order(self):
        """输入乱序时，返回结果仍应与原始行顺序对齐。"""
        raw_df = self._kline(
            [date(2024, 1, 4), date(2024, 1, 2), date(2024, 1, 3)],
            close=[10.0, 10.0, 10.0],
            volume=[3_000_000.0, 1_000_000.0, 2_000_000.0],
            amount=[30_000_000.0, 10_000_000.0, 20_000_000.0],
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 3)],
                "category": [5],
                "panqianliutong": [10000.0],
                "panhouliutong": [11000.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        self.assertAlmostEqual(rate.iloc[0], 300.0 / 110.0, places=4)
        self.assertAlmostEqual(rate.iloc[1], 1.0, places=4)
        self.assertAlmostEqual(rate.iloc[2], 200.0 / 110.0, places=4)


class TestMootdxAdapterPagination(unittest.TestCase):
    """测试 mootdx 分页逻辑。"""

    def test_ensure_connected_does_not_probe_healthy_pytdx_connection(self):
        class HealthyPytdxAPI:
            last_transaction_failed = False

            def get_security_bars(self, *args, **kwargs):
                raise AssertionError("healthy connections must not send a probe request")

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = HealthyPytdxAPI()
        adapter.connected = True
        adapter.backend = "pytdx"

        self.assertTrue(adapter._ensure_connected())

    def test_ensure_connected_rebuilds_poisoned_pytdx_connection(self):
        class PoisonedPytdxAPI:
            last_transaction_failed = True

            def __init__(self):
                self.disconnected = False

            def disconnect(self):
                self.disconnected = True

            def get_security_bars(self, *args, **kwargs):
                raise AssertionError("poisoned connection must not be probed or reused")

        api = PoisonedPytdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "pytdx"

        with patch.object(adapter, "_connect", return_value=True) as reconnect:
            self.assertTrue(adapter._ensure_connected())

        self.assertTrue(api.disconnected)
        reconnect.assert_called_once_with()

    def test_ensure_connected_rebuilds_poisoned_mootdx_client(self):
        class PoisonedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": True})()
                self.closed = False

            def close(self):
                self.closed = True

        api = PoisonedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"

        with patch.object(adapter, "_connect", return_value=True) as reconnect:
            self.assertTrue(adapter._ensure_connected())

        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)
        reconnect.assert_called_once_with()

    def test_failed_mootdx_bars_disconnects_before_next_request(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def bars(self, **kwargs):
                self.client.last_transaction_failed = True
                return pd.DataFrame()

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("688022", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_failed_mootdx_transaction_rejects_nonempty_bars(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def bars(self, **kwargs):
                self.client.last_transaction_failed = True
                return pd.DataFrame({
                    "datetime": ["2026-09-22"],
                    "open": [114.68], "high": [125.85],
                    "low": [114.61], "close": [123.60],
                    "volume": [13012229], "amount": [1591593216],
                })

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("688022", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_failed_mootdx_xdxr_disconnects_connection(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def xdxr(self, **kwargs):
                self.client.last_transaction_failed = True
                return pd.DataFrame()

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.last_failure_kind = None

        result = adapter.get_xdxr_info("688022")

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_qfq_rejects_bars_after_xdxr_connection_failure(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = object()
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None
        bars = pd.DataFrame({
            "datetime": ["2026-09-22"],
            "open": [8.8], "high": [9.0],
            "low": [8.7], "close": [8.9],
            "volume": [1000], "amount": [8900],
        })

        def failed_xdxr(symbol):
            adapter.last_failure_kind = "connectivity"
            return None

        with patch.object(adapter, "_fetch_bars_data", return_value=bars):
            with patch.object(adapter, "get_xdxr_info", side_effect=failed_xdxr):
                result = adapter.get_stock_kline("688022", adjust="qfq")

        self.assertIsNone(result)
        self.assertEqual(adapter.fail_count, 1)
        self.assertEqual(adapter.success_count, 0)

    def test_mootdx_bars_exception_discards_connection(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def bars(self, **kwargs):
                self.client.last_transaction_failed = True
                raise ConnectionError("broken transaction")

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        with patch.object(adapter, "_connect", return_value=False):
            result = adapter.get_stock_kline("688022", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_failed_pytdx_transaction_is_classified_as_connectivity(self):
        class FailedPytdxAPI:
            last_transaction_failed = True

            def get_security_bars(self, *args, **kwargs):
                return None

            @staticmethod
            def to_df(raw):
                return pd.DataFrame(raw)

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = FailedPytdxAPI()
        adapter.connected = True
        adapter.backend = "pytdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        with patch.object(adapter, "_ensure_connected", return_value=True):
            result = adapter.get_stock_kline(
                "601696",
                start_date="2026-08-07",
                end_date="2026-08-07",
                adjust="none",
                max_bars=2,
            )

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")

    def test_symbol_market_parsing_covers_etf_prefixes(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = None

        self.assertEqual(adapter._parse_symbol("510300"), MARKET_SH)
        self.assertEqual(adapter._parse_symbol("588000"), MARKET_SH)
        self.assertEqual(adapter._parse_symbol("159915"), MARKET_SZ)
        self.assertEqual(adapter._parse_symbol("161725"), MARKET_SZ)

    def test_recent_successful_server_is_tried_first(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.SERVERS = [
            ("1.1.1.1", 7709, "first"),
            ("2.2.2.2", 7709, "cached"),
            ("3.3.3.3", 7709, "third"),
        ]

        with patch.object(
            adapter,
            "_load_server_state",
            return_value={"host": "2.2.2.2", "port": 7709},
        ):
            ordered = adapter._ordered_servers()

        self.assertEqual(ordered[0], ("2.2.2.2", 7709, "cached"))
        self.assertEqual(len(ordered), 3)

    def test_recent_verified_servers_precede_legacy_servers(self):
        self.assertEqual(
            MootdxAdapter.SERVERS[:5],
            [
                ("59.36.5.11", 7709, "安信"),
                ("117.34.114.14", 7709, "国泰君安1"),
                ("117.34.114.15", 7709, "国泰君安2"),
                ("117.34.114.16", 7709, "国泰君安3"),
                ("117.34.114.17", 7709, "国泰君安4"),
            ],
        )

    def test_stale_cached_server_only_delays_verified_fallback_once(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)

        with patch.object(
            adapter,
            "_load_server_state",
            return_value={"host": "60.12.136.250", "port": 7709},
        ):
            ordered = adapter._ordered_servers()

        self.assertEqual(ordered[0][:2], ("60.12.136.250", 7709))
        self.assertEqual(ordered[1][:2], ("59.36.5.11", 7709))

    def test_connect_prefers_mootdx_manual_before_pytdx(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.Quotes = object()

        with patch.object(adapter, "_connect_manual", return_value=True) as manual_connect:
            with patch.object(adapter, "_connect_pytdx", return_value=True) as pytdx_connect:
                with patch.object(
                    adapter, "_connect_mootdx_auto", return_value=True
                ) as auto_connect:
                    result = adapter._connect()

        self.assertTrue(result)
        manual_connect.assert_called_once_with()
        pytdx_connect.assert_not_called()
        auto_connect.assert_not_called()

    def test_connect_manual_passes_mootdx_server_as_tuple(self):
        calls = []

        class FakeAPI:
            def bars(self, **kwargs):
                return pd.DataFrame({"datetime": ["2026-09-11"], "close": [10.0]})

        class FakeQuotes:
            @staticmethod
            def factory(**kwargs):
                calls.append(kwargs)
                return FakeAPI()

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.Quotes = FakeQuotes
        adapter.SERVERS = [("59.36.5.11", 7709, "安信")]
        adapter.timeout = 5
        adapter.connected = False
        adapter.api = None
        adapter.backend = None
        adapter.current_server = None

        with patch.object(adapter, "_save_server_state") as save_state:
            result = adapter._connect_manual()

        self.assertTrue(result)
        self.assertEqual(calls[0]["server"], ("59.36.5.11", 7709))
        self.assertFalse(calls[0]["auto_retry"])
        save_state.assert_called_once_with(
            host="59.36.5.11", port=7709, name="安信", backend="mootdx"
        )

    def test_get_stock_kline_uses_start_offset_and_deduplicates(self):
        """分页应使用递增的 start，且结果不应重复。"""
        first_dates = pd.date_range("2024-01-01", periods=800, freq="D")
        second_dates = pd.to_datetime(["2026-03-10", "2026-03-10", "2026-03-11"])

        first_page = pd.DataFrame(
            {
                "datetime": first_dates,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "volume": 100.0,
                "amount": 1000.0,
            }
        )
        second_page = pd.DataFrame(
            {
                "datetime": second_dates,
                "open": 12.0,
                "high": 13.0,
                "low": 11.0,
                "close": 12.0,
                "volume": 200.0,
                "amount": 2000.0,
            }
        )

        api = FakeQuotesAPI({0: first_page, 800: second_page})

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.current_server = "test"

        with patch.object(adapter, "_ensure_connected", return_value=True):
            result = adapter.get_stock_kline(
                "600519",
                start_date="2024-01-01",
                end_date="2026-03-11",
                adjust="none",
            )

        self.assertEqual(api.starts, [0, 800])
        self.assertEqual(result["trade_date"].nunique(), len(result))
        self.assertEqual(result["trade_date"].min(), date(2024, 1, 1))
        self.assertEqual(result["trade_date"].max(), date(2026, 3, 11))

    def test_get_index_kline_uses_explicit_market(self):
        """沪市 000xxx 指数应能显式传入 market=SH。"""
        page = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-06-03", "2026-06-04"]),
                "open": [10.0, 10.5],
                "high": [11.0, 11.5],
                "low": [9.0, 9.5],
                "close": [10.5, 11.0],
                "volume": [100.0, 120.0],
                "amount": [1000.0, 1320.0],
            }
        )

        class FakePytdxAPI:
            def __init__(self):
                self.market_args = []

            def get_index_bars(self, frequency, market, code, start, request_size):
                self.market_args.append((frequency, market, code, start, request_size))
                return page.to_dict("records") if start == 0 else []

            def to_df(self, raw):
                return pd.DataFrame(raw)

        api = FakePytdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "pytdx"
        adapter.success_count = 0
        adapter.fail_count = 0

        with patch.object(adapter, "_ensure_connected", return_value=True):
            result = adapter.get_index_kline(
                "000300",
                start_date="2026-06-03",
                end_date="2026-06-04",
                market=MARKET_SH,
                max_bars=5,
            )

        self.assertEqual(api.market_args[0][1], MARKET_SH)
        self.assertEqual(result["symbol"].iloc[0], "000300")
        self.assertEqual(result["trade_date"].max(), date(2026, 6, 4))


class TestCollectorHelpers(unittest.TestCase):
    """测试采集脚本的辅助逻辑。"""

    def test_load_symbols_deduplicates_mootdx_fallback(self):
        """mootdx 股票列表回退时应去重，避免重复采集。"""
        conn = None
        fallback_df = pd.DataFrame(
            {
                "code": ["000001", "000001", "600519", "600519", "688001", "159001"],
                "name": ["A", "A", "B", "B", "C", "ETF"],
            }
        )

        class FakeAdapter:
            def is_available(self):
                return True

            def get_stock_list(self):
                return fallback_df

            def disconnect(self):
                return None

        with patch.object(_module, "MootdxAdapter", return_value=FakeAdapter()):
            symbols = load_symbols(conn, symbol=None, symbols_file=None, limit=None)

        self.assertEqual(symbols, ["000001", "600519", "688001"])


class TestRawAndQfqDualWrite(unittest.TestCase):
    """同一请求应派生并原子保存 raw 与 qfq 两套日线。"""

    class FakeAdapter:
        def __init__(self):
            self.kline_calls = 0
            self.xdxr_calls = 0
            self.last_failure_kind = None

        def get_xdxr_info(self, symbol):
            self.xdxr_calls += 1
            return pd.DataFrame(
                {
                    "trade_date": [date(2024, 1, 2)],
                    "category": [1],
                    "panqianliutong": [10_000.0],
                    "panhouliutong": [20_000.0],
                    "fenhong": [1.0],
                    "peigu": [0.0],
                    "peigujia": [0.0],
                    "songzhuangu": [10.0],
                }
            )

        def get_stock_kline(self, **kwargs):
            self.kline_calls += 1
            return pd.DataFrame(
                {
                    "symbol": ["600519", "600519", "600519"],
                    "trade_date": [
                        date(2024, 1, 1),
                        date(2024, 1, 2),
                        date(2024, 1, 3),
                    ],
                    "open": [10.0, 4.95, 5.5],
                    "high": [10.0, 4.95, 5.5],
                    "low": [10.0, 4.95, 5.5],
                    "close": [10.0, 4.95, 5.5],
                    "volume": [1_000_000.0, 2_000_000.0, 3_000_000.0],
                    "amount": [10_000_000.0, 9_900_000.0, 16_500_000.0],
                }
            )

    class FakePool:
        def __init__(self, adapter):
            self.adapter = adapter
            self.release_calls = 0

        def acquire(self):
            return self.adapter

        def release(self, adapter):
            self.release_calls += 1

    def _fetch(self):
        adapter = self.FakeAdapter()
        pool = self.FakePool(adapter)
        result = fetch_one_symbol(
            symbol="600519",
            end_date=date(2024, 1, 3),
            default_start=date(2024, 1, 1),
            last_date=None,
            lookback_days=5,
            full_refresh=False,
            pool=pool,
            max_bars=160,
        )
        return result, adapter, pool

    def test_one_adapter_fetch_returns_distinct_raw_and_qfq_prices(self):
        result, adapter, pool = self._fetch()

        self.assertEqual(result.status, "success")
        self.assertIsNotNone(result.data)
        self.assertIsNotNone(result.raw_data)
        self.assertNotEqual(
            result.data.iloc[0]["close"],
            result.raw_data.iloc[0]["close"],
        )
        self.assertAlmostEqual(result.raw_data.iloc[0]["close"], 10.0)
        self.assertEqual(
            list(result.raw_data.columns),
            [
                "symbol",
                "trade_date",
                "close",
                "source",
                "quality_flag",
                "run_id",
                "updated_at",
                "adjustment_mode",
                "market",
            ],
        )
        self.assertEqual(set(result.raw_data["source"]), {"tdx_mootdx_raw"})
        self.assertEqual(set(result.raw_data["quality_flag"]), {"ok_unadjusted"})
        self.assertEqual(set(result.raw_data["adjustment_mode"]), {"none"})
        for omitted_column in (
            "open",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
            "turnover_rate",
        ):
            self.assertNotIn(omitted_column, result.raw_data.columns)
        self.assertEqual(adapter.kline_calls, 1)
        self.assertEqual(adapter.xdxr_calls, 1)
        self.assertEqual(pool.release_calls, 1)

    def test_bse_reuses_one_unadjusted_fetch_for_compact_raw_data(self):
        adapter = self.FakeAdapter()
        pool = self.FakePool(adapter)

        result = fetch_one_symbol(
            symbol="430047",
            end_date=date(2024, 1, 3),
            default_start=date(2024, 1, 1),
            last_date=None,
            lookback_days=5,
            full_refresh=False,
            pool=pool,
            max_bars=160,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(adapter.kline_calls, 1)
        self.assertEqual(adapter.xdxr_calls, 0)
        self.assertEqual(list(result.raw_data.columns), _module.RAW_SAVE_COLUMNS)
        self.assertEqual(set(result.raw_data["source"]), {"tdx_mootdx_raw"})
        self.assertEqual(set(result.raw_data["adjustment_mode"]), {"none"})
        self.assertEqual(
            result.data["close"].tolist(),
            result.raw_data["close"].tolist(),
        )

    def test_dual_batch_saves_both_tables(self):
        result, _, _ = self._fetch()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")

        qfq_rows, raw_rows = save_dual_batch(
            conn,
            "qfq_prices",
            "raw_prices",
            [result],
        )

        self.assertEqual((qfq_rows, raw_rows), (3, 3))
        self.assertAlmostEqual(
            conn.execute(
                "SELECT close FROM qfq_prices WHERE trade_date = '2024-01-01'"
            ).fetchone()[0],
            4.95,
        )
        self.assertEqual(
            conn.execute(
                """
                SELECT close, source, quality_flag, adjustment_mode
                FROM raw_prices
                WHERE trade_date = '2024-01-01'
                """
            ).fetchone(),
            (10.0, "tdx_mootdx_raw", "ok_unadjusted", "none"),
        )
        raw_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(raw_prices)")
        ]
        self.assertEqual(raw_columns, _module.RAW_SAVE_COLUMNS)
        raw_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(raw_prices)")
        }
        self.assertIn("idx_raw_prices_date", raw_indexes)
        self.assertIn("idx_raw_prices_market_date", raw_indexes)
        self.assertNotIn("idx_raw_prices_symbol", raw_indexes)

    def test_dual_batch_rejects_source_history_from_another_symbol(self):
        result, _, _ = self._fetch()
        result.anchor_date = date(2023, 12, 29)
        result.anchor_raw_close = 112.30
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")
        conn.execute(
            "INSERT INTO raw_prices (symbol, trade_date, close) VALUES (?, ?, ?)",
            (result.symbol, "2023-12-29", 8.99),
        )

        with self.assertRaisesRegex(ValueError, "source anchor mismatch"):
            save_dual_batch(conn, "qfq_prices", "raw_prices", [result])

        self.assertEqual(conn.execute("SELECT COUNT(*) FROM qfq_prices").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_prices").fetchone()[0], 1)

    def test_dual_batch_accepts_matching_source_anchor(self):
        result, _, _ = self._fetch()
        result.anchor_date = date(2023, 12, 29)
        result.anchor_raw_close = 8.99
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")
        conn.execute(
            "INSERT INTO raw_prices (symbol, trade_date, close) VALUES (?, ?, ?)",
            (result.symbol, "2023-12-29", 8.99),
        )

        self.assertEqual(
            save_dual_batch(conn, "qfq_prices", "raw_prices", [result]),
            (3, 3),
        )

    def test_incremental_fetch_keeps_overlap_as_anchor(self):
        adapter = self.FakeAdapter()
        result = fetch_one_symbol(
            symbol="600519",
            end_date=date(2024, 1, 3),
            default_start=date(2023, 12, 30),
            last_date=date(2024, 1, 2),
            lookback_days=1,
            full_refresh=False,
            pool=self.FakePool(adapter),
            max_bars=160,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.anchor_date, date(2024, 1, 2))
        self.assertEqual(result.anchor_raw_close, 4.95)
        self.assertEqual(result.raw_data["trade_date"].tolist(), [date(2024, 1, 3)])

    def test_dual_batch_rolls_back_qfq_when_raw_write_fails(self):
        result, _, _ = self._fetch()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")
        conn.execute(
            """
            CREATE TRIGGER reject_raw_insert
            BEFORE INSERT ON raw_prices
            BEGIN
                SELECT RAISE(ABORT, 'raw write failed');
            END
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "raw write failed"):
            save_dual_batch(
                conn,
                "qfq_prices",
                "raw_prices",
                [result],
            )

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM qfq_prices").fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM raw_prices").fetchone()[0],
            0,
        )

    def test_raw_table_name_must_be_valid_and_distinct(self):
        for invalid_name in ("raw-prices", "1raw_prices", "raw prices"):
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaises(ValueError):
                    validate_target_tables("stock_daily_prices", invalid_name)

        with self.assertRaisesRegex(ValueError, "must be different"):
            validate_target_tables("stock_daily_prices", "stock_daily_prices")
        with self.assertRaisesRegex(ValueError, "must be different"):
            validate_target_tables("Stock_Daily_Prices", "stock_daily_prices")

        parser = _module.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.raw_table, "stock_daily_prices_raw")


if __name__ == "__main__":
    unittest.main()
