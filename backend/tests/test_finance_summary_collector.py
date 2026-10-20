from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from data_job.collectors.finance_summary_collector import FinanceSummaryCollector
from data_job.core.direct_market_sources import EastmoneyDirectClient


class FakeF10Client:
    def __init__(self, cumulative: list[dict[str, Any]], quarterly: list[dict[str, Any]]) -> None:
        self.cumulative = cumulative
        self.quarterly = quarterly
        self.calls: list[str] = []

    def stock_f10_main_finance(self, symbol: str) -> dict[str, list[dict[str, Any]]]:
        self.calls.append(symbol)
        return {"cumulative": self.cumulative, "quarterly": self.quarterly}


def make_collector(client: FakeF10Client | None = None) -> FinanceSummaryCollector:
    collector = FinanceSummaryCollector.__new__(FinanceSummaryCollector)
    collector.engine = create_engine("sqlite://")
    collector.table_name = "stock_finance_summary"
    collector.request_timeout = 5
    collector.f10_client = client or FakeF10Client([], [])
    return collector


@pytest.mark.parametrize(
    ("symbol", "expected_filter"),
    [
        ("600519", '(SECUCODE="600519.SH")'),
        ("000001.SZ", '(SECUCODE="000001.SZ")'),
        ("920001", '(SECUCODE="920001.BJ")'),
    ],
)
def test_stock_f10_main_finance_uses_two_bounded_requests(
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
    expected_filter: str,
) -> None:
    client = EastmoneyDirectClient(min_interval=0, timeout=3)
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_em_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((url, params))
        return {"result": {"data": [{"REPORT_DATE": "2025-12-31"}]}}

    monkeypatch.setattr(client, "em_get", fake_em_get)

    result = client.stock_f10_main_finance(symbol)

    assert len(calls) == 2
    assert [call[1]["type"] for call in calls] == [
        "RPT_F10_FINANCE_MAINFINADATA",
        "RPT_F10_QTR_MAINFINADATA",
    ]
    assert [call[1]["sty"] for call in calls] == ["APP_F10_MAINFINADATA", "ALL"]
    assert all(call[1]["filter"] == expected_filter for call in calls)
    assert all(call[1]["p"] == 1 and call[1]["ps"] <= 200 for call in calls)
    assert result == {
        "cumulative": [{"REPORT_DATE": "2025-12-31"}],
        "quarterly": [{"REPORT_DATE": "2025-12-31"}],
    }
    assert client.retries <= 1


def test_eastmoney_request_retries_once_and_passes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = EastmoneyDirectClient(min_interval=0, timeout=7, retries=1)
    timeouts: list[int] = []

    def fake_get(_url: str, *, params: dict[str, Any], timeout: int):
        timeouts.append(timeout)
        raise TimeoutError("timeout")

    monkeypatch.setattr(client.session, "get", fake_get)
    monkeypatch.setattr("data_job.core.direct_market_sources.time.sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="Eastmoney request failed"):
        client.em_get("https://example.test", {})

    assert timeouts == [7, 7]


def test_backfill_symbol_merges_cumulative_and_real_quarter_rows() -> None:
    cumulative = [
        {
            "SECUCODE": "002821.SZ",
            "SECURITY_NAME_ABBR": "凯莱英",
            "REPORT_DATE": "2025-12-31 00:00:00",
            "REPORT_TYPE": "年报",
            "REPORT_DATE_NAME": "2025年报",
            "NOTICE_DATE": "2026-03-28 00:00:00",
            "UPDATE_DATE": "2026-03-28 09:30:00",
            "TOTALOPERATEREVE": 10_000_000_000,
            "PARENTNETPROFIT": 0,
            "KCFJCXSYJLR": 800_000_000,
            "TOTALOPERATEREVETZ": 12.5,
            "YYZSRGDHBZC": 2.1,
            "PARENTNETPROFITTZ": -3.5,
            "NETPROFITRPHBZC": -1.2,
            "KCFJCXSYJLRTZ": 9.5,
            "KFJLRGDHBZC": 4.2,
            "EPSJB": 1.23,
            "ROEJQ": 8.6,
            "XSMLL": 43.2,
            "XSJLL": 10.4,
            "MGJYXJJE": 2.3,
            "BPS": 15.6,
            "ZCFZL": 38.1,
            "LD": 2.2,
            "SD": 1.9,
        }
    ]
    quarterly = [
        {
            "SECUCODE": "002821.SZ",
            "REPORT_DATE": "2025-12-31 00:00:00",
            "TOTALOPERATEREVE": 3_000_000_000,
            "PARENTNETPROFIT": -100_000_000,
            "DEDU_PARENT_PROFIT": 250_000_000,
            "TOTALOPERATEREVETZ": 7.5,
            "YYZSRGDHBZC": 2.5,
            "PARENTNETPROFITTZ": -12.0,
            "NETPROFITRPHBZC": -4.0,
            "DPNP_YOY_RATIO": 6.0,
            "KFJLRGDHBZC": 1.5,
            "EPSJB": -0.12,
            "ROE_DILUTED": -0.8,
            "GROSS_PROFIT_RATIO": 41.0,
            "NET_PROFIT_RATIO": -3.3,
            "PER_NETCASH": 0.7,
        }
    ]
    client = FakeF10Client(cumulative, quarterly)
    collector = make_collector(client)

    result = collector.backfill_symbol("002821", years=6)

    assert client.calls == ["002821"]
    assert result["updated"] == 1
    with collector.engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM stock_finance_summary WHERE code='002821'")
        ).mappings().one()

    assert str(row["report_date"]) == "2025-12-31"
    assert row["report_type"] == "年报"
    assert row["report_date_name"] == "2025年报"
    assert row["total_revenue"] == 10_000_000_000
    assert row["net_profit"] == 0
    assert row["deduct_net_profit"] == 800_000_000
    assert row["revenue_up"] == 12.5
    assert row["net_profit_up"] == -3.5
    assert row["deduct_net_profit_up"] == 9.5
    assert row["revenue_qoq"] == 2.1
    assert row["net_profit_qoq"] == -1.2
    assert row["deduct_net_profit_qoq"] == 4.2
    assert row["total_revenue_single"] == 3_000_000_000
    assert row["net_profit_single"] == -100_000_000
    assert row["deduct_net_profit_single"] == 250_000_000
    assert row["total_revenue_single_yoy"] == 7.5
    assert row["net_profit_single_yoy"] == -12.0
    assert row["deduct_net_profit_single_yoy"] == 6.0
    assert row["total_revenue_single_qoq"] == 2.5
    assert row["net_profit_single_qoq"] == -4.0
    assert row["deduct_net_profit_single_qoq"] == 1.5
    assert row["gross_margin"] == 43.2
    assert row["gross_margin_single"] == 41.0
    assert row["operating_cashflow_per_share"] == 2.3
    assert row["operating_cashflow_per_share_single"] == 0.7
    assert row["bvps"] == 15.6
    assert row["debt_ratio"] == 38.1
    assert row["current_ratio"] == 2.2
    assert row["quick_ratio"] == 1.9
    assert row["source"] == "eastmoney_f10"
    assert row["fetched_at"] is not None


def test_upsert_preserves_old_values_for_null_but_accepts_zero() -> None:
    client = FakeF10Client(
        [
            {
                "SECUCODE": "002821.SZ",
                "REPORT_DATE": "2025-12-31",
                "TOTALOPERATEREVE": None,
                "PARENTNETPROFIT": 0,
            }
        ],
        [],
    )
    collector = make_collector(client)
    collector._init_table()
    with collector.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO stock_finance_summary (
                    code, report_date, total_revenue, net_profit,
                    deduct_net_profit, source, fetched_at
                ) VALUES (
                    '002821', '2025-12-31', 99, 88, 77, 'legacy', '2026-01-01 00:00:00'
                )
                """
            )
        )

    collector.backfill_symbol("002821")

    with collector.engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT total_revenue, net_profit, deduct_net_profit, source
                FROM stock_finance_summary
                WHERE code='002821' AND report_date='2025-12-31'
                """
            )
        ).mappings().one()
    assert row["total_revenue"] == 99
    assert row["net_profit"] == 0
    assert row["deduct_net_profit"] == 77
    assert row["source"] == "eastmoney_f10"


def test_init_table_migrates_legacy_schema_without_replacing_rows() -> None:
    collector = make_collector()
    with collector.engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE stock_finance_summary (
                    code VARCHAR(20), report_date DATE,
                    total_revenue FLOAT, net_profit FLOAT,
                    PRIMARY KEY (code, report_date)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO stock_finance_summary
                    (code, report_date, total_revenue, net_profit)
                VALUES ('002821', '2025-12-31', 99, 0)
                """
            )
        )

    collector._init_table()

    with collector.engine.connect() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(stock_finance_summary)"))
        }
        row = conn.execute(
            text(
                """
                SELECT total_revenue, net_profit
                FROM stock_finance_summary
                WHERE code='002821' AND report_date='2025-12-31'
                """
            )
        ).mappings().one()
    assert {"report_date_name", "total_revenue_single", "source"} <= columns
    assert row == {"total_revenue": 99.0, "net_profit": 0.0}


def test_full_market_refresh_upserts_without_erasing_f10_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = make_collector()
    collector._init_table()
    with collector.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO stock_finance_summary (
                    code, name, report_date, total_revenue, net_profit,
                    net_profit_single, deduct_net_profit, source, fetched_at
                ) VALUES (
                    '002821', '凯莱英', '2025-12-31', 1, 2,
                    321, 654, 'eastmoney_f10', '2026-03-28 09:30:00'
                )
                """
            )
        )

    frame = pd.DataFrame(
        [
            {
                "code": "002821",
                "name": "凯莱英",
                "report_date": date(2025, 12, 31),
                "eps": 1.2,
                "net_profit_up": 3.4,
                "revenue_up": 5.6,
                "roe": 7.8,
                "total_revenue": 11,
                "net_profit": 22,
                "net_margin": 200.0,
            }
        ]
    )
    monkeypatch.setattr(collector, "fetch_report_dataframe", lambda _target_date: frame)

    assert collector.fetch_and_save("20251231") is True

    with collector.engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT total_revenue, net_profit, revenue_up, net_profit_up,
                       net_profit_single, deduct_net_profit, source
                FROM stock_finance_summary
                WHERE code='002821' AND report_date='2025-12-31'
                """
            )
        ).mappings().one()
    assert row["total_revenue"] == 11
    assert row["net_profit"] == 22
    assert row["revenue_up"] == 5.6
    assert row["net_profit_up"] == 3.4
    assert row["net_profit_single"] == 321
    assert row["deduct_net_profit"] == 654
    assert row["source"] == "eastmoney_f10"
