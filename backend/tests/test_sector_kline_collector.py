from datetime import date

import pandas as pd
import pytest
import requests

from data_job.collectors import sector_kline_collector as sector_module
from data_job.collectors.sector_kline_collector import (
    SectorKlineCollector,
    normalize_eastmoney_volume,
    validate_synthetic_sector_frame,
)


def test_sector_collector_health_check_uses_direct_network(monkeypatch):
    captured = {}

    def fake_base_init(self, **kwargs):
        captured.update(kwargs)
        self.session = requests.Session()

    monkeypatch.setattr(sector_module.BaseCollector, "__init__", fake_base_init)
    monkeypatch.setattr(sector_module, "get_engine", lambda: object())

    collector = SectorKlineCollector()

    assert captured["enable_anti_ban"] is False
    assert collector.session.trust_env is False
    assert collector.session.proxies == {}


def test_synthetic_sector_validation_accepts_continuous_rows():
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-08-06", "2026-08-07"],
            "open": [1000.0, 1010.0],
            "close": [1010.0, 999.9],
            "high": [1010.0, 1010.0],
            "low": [1000.0, 999.9],
            "pct_chg": [1.0, -1.0],
        }
    )

    ok, reason = validate_synthetic_sector_frame(frame, previous_close=1000.0)

    assert ok
    assert reason == ""


def test_synthetic_sector_validation_rejects_absurd_member_contamination():
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-08-07"],
            "open": [1000.0],
            "close": [14000.0],
            "high": [14000.0],
            "low": [1000.0],
            "pct_chg": [1300.0],
        }
    )

    ok, reason = validate_synthetic_sector_frame(frame, previous_close=1000.0)

    assert not ok
    assert "pct_chg_out_of_range" in reason


def test_synthetic_sector_validation_rejects_wrong_anchor_scale():
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-08-07"],
            "open": [10000.0],
            "close": [10100.0],
            "high": [10100.0],
            "low": [10000.0],
            "pct_chg": [1.0],
        }
    )

    ok, reason = validate_synthetic_sector_frame(frame, previous_close=1000.0)

    assert not ok
    assert "pct_chg_mismatch" in reason


def test_eastmoney_volume_is_normalized_from_lots_to_shares():
    normalized = normalize_eastmoney_volume(pd.Series([1, 2.5, None, "bad"]))

    assert normalized.tolist() == [100.0, 250.0, 0.0, 0.0]


def test_non_eastmoney_source_is_rejected_before_database_write():
    collector = object.__new__(SectorKlineCollector)
    frame = pd.DataFrame(
        {
            "日期": ["2026-08-25"],
            "开盘": [100.0],
            "收盘": [101.0],
            "最高": [102.0],
            "最低": [99.0],
            "成交量": [100.0],
        }
    )

    assert collector.save_data(frame, "行业A", source="synthetic_stock_map") is False


def test_eastmoney_empty_does_not_write_synthetic_fallback(monkeypatch):
    collector = object.__new__(SectorKlineCollector)
    row = pd.Series(
        {
            "sector_name": "银行",
            "sector_code": "BK0475",
            "source": "eastmoney_stock_plate",
        }
    )
    calls = {}

    def fake_fetch(sector_code, start_date, end_date):
        calls["fetch"] = (sector_code, start_date, end_date)
        return pd.DataFrame()

    monkeypatch.setattr(collector, "_fetch_eastmoney_kline", fake_fetch)
    monkeypatch.setattr(
        collector,
        "_calculate_synthetic_sector_data",
        lambda *args, **kwargs: pytest.fail("synthetic fallback must not run"),
    )
    monkeypatch.setattr(
        collector,
        "_save_synthetic_data",
        lambda *args, **kwargs: pytest.fail("synthetic fallback must not write"),
    )

    ok = collector._save_eastmoney_or_synthetic_data(
        row,
        "20260706",
        "2026-07-10",
        "20260710",
    )

    assert not ok
    assert calls["fetch"] == ("BK0475", "20260706", "20260710")


def test_eastmoney_success_skips_synthetic_fallback(monkeypatch):
    collector = object.__new__(SectorKlineCollector)
    row = pd.Series(
        {
            "sector_name": "银行",
            "sector_code": "BK0475",
            "source": "eastmoney_stock_plate",
        }
    )
    calls = {}
    eastmoney_df = pd.DataFrame(
        {
            "trade_date": ["2026-07-10"],
            "open": [100.0],
            "close": [101.0],
            "high": [101.0],
            "low": [100.0],
            "volume": [1000.0],
            "amount": [10000.0],
            "pct_chg": [1.0],
        }
    )

    def fake_fetch(sector_code, start_date, end_date):
        calls["fetch"] = (sector_code, start_date, end_date)
        return eastmoney_df

    def fake_save_data(df, sector_name, end_date, source):
        calls["save_data"] = (sector_name, end_date, source, len(df))
        return True

    def fail_calculate(*args, **kwargs):
        raise AssertionError("Synthetic fallback should not run when Eastmoney succeeds")

    monkeypatch.setattr(collector, "_fetch_eastmoney_kline", fake_fetch)
    monkeypatch.setattr(collector, "save_data", fake_save_data)
    monkeypatch.setattr(collector, "_calculate_synthetic_sector_data", fail_calculate)

    ok = collector._save_eastmoney_or_synthetic_data(
        row,
        "20260706",
        "2026-07-10",
        "20260710",
    )

    assert ok
    assert calls["fetch"] == ("BK0475", "20260706", "20260710")
    assert calls["save_data"] == (
        "银行",
        "2026-07-10",
        "eastmoney_stock_plate",
        1,
    )


def _bulk_universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sector_name": ["银行", "证券", "保险"],
            "sector_code": ["BK0475", "BK0476", "BK0477"],
            "sector_type": ["Industry"] * 3,
            "source": ["eastmoney_direct"] * 3,
        }
    )


def _bulk_row(code: str, name: str, *, f124: str = "2026-08-25 15:00:00") -> dict:
    return {
        "f12": code,
        "f14": name,
        "f2": "101.00",
        "f3": "1.00",
        "f5": "1000",
        "f6": "100000",
        "f15": "102.00",
        "f16": "99.00",
        "f17": "100.00",
        "f18": "100.00",
        "f124": f124,
    }


def test_target_only_bulk_fetch_paginates_and_never_calls_single_sector(monkeypatch):
    collector = object.__new__(SectorKlineCollector)
    universe = _bulk_universe()
    responses = [
        {
            "data": {
                "total": 3,
                "diff": [
                    _bulk_row("BK0475", "银行"),
                    _bulk_row("BK0476", "证券"),
                ],
            }
        },
        {"data": {"total": 3, "diff": [_bulk_row("BK0477", "保险")]}},
    ]
    calls = []

    def fake_get_json(url, params):
        calls.append((url, params.copy()))
        return responses[len(calls) - 1]

    monkeypatch.setattr(collector, "_eastmoney_get_json", fake_get_json)
    monkeypatch.setattr(
        collector,
        "_fetch_eastmoney_kline",
        lambda *args, **kwargs: pytest.fail("target_only must not request one sector at a time"),
    )

    frame = collector._fetch_eastmoney_bulk_snapshot(date(2026, 8, 25), universe)

    assert len(calls) == 2
    assert all(call[0].endswith("/api/qt/clist/get") for call in calls)
    assert all(
        call[1]["fields"]
        == "f12,f14,f2,f3,f5,f6,f15,f16,f17,f18,f124"
        for call in calls
    )
    assert frame["sector_code"].tolist() == ["BK0475", "BK0476", "BK0477"]
    assert frame["volume"].tolist() == [100000.0] * 3


def test_bulk_snapshot_rejects_intraday_stale_future_and_mixed_dates():
    collector = object.__new__(SectorKlineCollector)
    universe = _bulk_universe()
    good = pd.DataFrame([_bulk_row("BK0475", "银行")])

    for timestamp in (
        "2026-08-25 14:59:59",
        "2026-08-24 15:00:00",
        "2026-08-26 15:00:00",
    ):
        frame = good.copy()
        frame.loc[0, "f124"] = timestamp
        ok, reason = collector._validate_eastmoney_bulk_snapshot(
            collector._normalize_eastmoney_bulk_rows(frame.to_dict("records")),
            universe,
            date(2026, 8, 25),
            min_coverage=1 / 3,
        )
        assert not ok
        assert "f124" in reason


def test_bulk_snapshot_rejects_duplicates_unknown_codes_and_low_coverage():
    collector = object.__new__(SectorKlineCollector)
    universe = _bulk_universe()
    duplicate = pd.DataFrame(
        [
            _bulk_row("BK0475", "银行"),
            _bulk_row("BK0475", "银行"),
            _bulk_row("BK0476", "证券"),
        ]
    )
    normalized = collector._normalize_eastmoney_bulk_rows(duplicate.to_dict("records"))
    ok, reason = collector._validate_eastmoney_bulk_snapshot(
        normalized, universe, date(2026, 8, 25), min_coverage=1 / 3
    )
    assert not ok
    assert "duplicate" in reason

    unknown = pd.DataFrame(
        [_bulk_row("BK0475", "银行"), _bulk_row("BK9999", "未知")]
    )
    normalized = collector._normalize_eastmoney_bulk_rows(unknown.to_dict("records"))
    ok, reason = collector._validate_eastmoney_bulk_snapshot(
        normalized, universe, date(2026, 8, 25), min_coverage=1 / 3
    )
    assert not ok
    assert "universe" in reason

    low_coverage = pd.DataFrame([_bulk_row("BK0475", "银行")])
    normalized = collector._normalize_eastmoney_bulk_rows(low_coverage.to_dict("records"))
    ok, reason = collector._validate_eastmoney_bulk_snapshot(
        normalized, universe, date(2026, 8, 25)
    )
    assert not ok
    assert "coverage" in reason


def test_bulk_snapshot_rejects_invalid_ohlc_and_pct_semantics():
    collector = object.__new__(SectorKlineCollector)
    universe = _bulk_universe()
    invalid = _bulk_row("BK0475", "银行")
    invalid["f15"] = "98.00"
    normalized = collector._normalize_eastmoney_bulk_rows([invalid])

    ok, reason = collector._validate_eastmoney_bulk_snapshot(
        normalized, universe, date(2026, 8, 25), min_coverage=1 / 3
    )

    assert not ok
    assert "ohlc" in reason


def test_target_only_bulk_failure_is_fail_closed_and_does_not_save_partial(monkeypatch):
    collector = object.__new__(SectorKlineCollector)
    collector.table_name = "sector_vendor_daily"
    collector._eastmoney_last_error = ""
    universe = _bulk_universe()
    saves = []

    monkeypatch.setattr(collector, "_fetch_eastmoney_bulk_snapshot", lambda *args: pd.DataFrame())
    monkeypatch.setattr(
        collector,
        "_save_eastmoney_bulk_snapshot",
        lambda *args, **kwargs: saves.append(True),
    )

    frame = collector._fetch_eastmoney_bulk_snapshot(date(2026, 8, 25), universe)
    assert frame.empty
    assert saves == []


def test_bulk_save_is_single_transaction_and_rerun_is_idempotent(monkeypatch):
    collector = object.__new__(SectorKlineCollector)
    collector.table_name = "sector_vendor_daily"
    calls = []
    written = []

    class FakeConnection:
        def execute(self, statement, params=None):
            calls.append((str(statement), params))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeEngine:
        def begin(self):
            calls.append(("begin", None))
            return FakeConnection()

    collector.engine = FakeEngine()
    monkeypatch.setattr(
        pd.DataFrame,
        "to_sql",
        lambda self, *args, **kwargs: (
            calls.append(("to_sql", len(self))),
            written.append(self.copy()),
        ),
    )

    frame = collector._normalize_eastmoney_bulk_rows(
        [_bulk_row("BK0475", "银行"), _bulk_row("BK0476", "证券"), _bulk_row("BK0477", "保险")]
    )
    assert collector._save_eastmoney_bulk_snapshot(frame, date(2026, 8, 25))

    assert [entry[0] for entry in calls] == ["begin", "DELETE FROM sector_vendor_daily\n                WHERE trade_date = :trade_date", "to_sql"]
    assert calls[-1][1] == 3
    assert written[0]["volume"].tolist() == [100000.0] * 3
    assert written[0]["source"].unique().tolist() == ["eastmoney_bulk_snapshot"]
    assert written[0]["quality_flag"].unique().tolist() == ["ok"]


def test_bulk_save_rechecks_cached_coverage_before_opening_transaction():
    collector = object.__new__(SectorKlineCollector)
    collector.table_name = "sector_vendor_daily"
    collector.engine = pytest.fail
    partial = collector._normalize_eastmoney_bulk_rows([_bulk_row("BK0475", "银行")])

    assert not collector._save_eastmoney_bulk_snapshot(
        partial,
        date(2026, 8, 25),
        universe=_bulk_universe(),
    )


def test_target_only_run_uses_cached_bulk_once_and_has_no_sector_fallback(monkeypatch):
    collector = object.__new__(SectorKlineCollector)
    collector._eastmoney_last_error = ""
    events = []
    universe = _bulk_universe()
    bulk_frame = collector._normalize_eastmoney_bulk_rows(
        [
            _bulk_row("BK0475", "银行"),
            _bulk_row("BK0476", "证券"),
            _bulk_row("BK0477", "保险"),
        ]
    )

    monkeypatch.setattr(collector, "log_collection_start", lambda: events.append("start"))
    monkeypatch.setattr(collector, "_health_check", lambda: None)
    monkeypatch.setattr(collector, "_init_table", lambda: events.append("init"))
    monkeypatch.setattr(collector, "_load_existing_sector_list", lambda: universe)
    monkeypatch.setattr(
        collector,
        "_fetch_eastmoney_bulk_snapshot",
        lambda target, cached: (events.append(("bulk", target, len(cached))), bulk_frame)[1],
    )
    monkeypatch.setattr(
        collector,
        "_save_eastmoney_bulk_snapshot",
        lambda frame, target, **kwargs: (
            events.append(("save", target, len(frame))),
            True,
        )[1],
    )
    monkeypatch.setattr(
        collector,
        "_save_eastmoney_or_synthetic_data",
        lambda *args, **kwargs: pytest.fail("target_only must not fall back to per-sector writes"),
    )
    monkeypatch.setattr(
        collector,
        "log_collection_end",
        lambda success, message: events.append(("end", success, message)),
    )

    collector.run(end="2026-08-25", target_only=True)

    assert [event[0] for event in events if isinstance(event, tuple)] == [
        "bulk",
        "save",
        "end",
    ]
    assert events[-1][1] is True
