import asyncio
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.api.v1 import stock as stock_api
from data_job.collectors import finance_summary_collector, stock_enrichment_collector


DETAILED_FINANCE_SCHEMA = """
CREATE TABLE stock_finance_summary (
    code TEXT,
    name TEXT,
    report_date TEXT,
    report_type TEXT,
    report_date_name TEXT,
    notice_date TEXT,
    update_date TEXT,
    source TEXT,
    fetched_at TEXT,
    eps REAL,
    net_profit_up REAL,
    revenue_up REAL,
    roe REAL,
    total_revenue REAL,
    net_profit REAL,
    deduct_net_profit REAL,
    deduct_net_profit_up REAL,
    revenue_qoq REAL,
    net_profit_qoq REAL,
    deduct_net_profit_qoq REAL,
    bvps REAL,
    operating_cashflow_per_share REAL,
    gross_margin REAL,
    roe_diluted REAL,
    debt_ratio REAL,
    current_ratio REAL,
    quick_ratio REAL,
    net_margin REAL,
    total_revenue_single REAL,
    net_profit_single REAL,
    deduct_net_profit_single REAL,
    eps_single REAL,
    roe_single REAL,
    operating_cashflow_per_share_single REAL,
    gross_margin_single REAL,
    net_margin_single REAL,
    total_revenue_single_yoy REAL,
    net_profit_single_yoy REAL,
    deduct_net_profit_single_yoy REAL,
    total_revenue_single_qoq REAL,
    net_profit_single_qoq REAL,
    deduct_net_profit_single_qoq REAL,
    PRIMARY KEY (code, report_date)
);
"""


def insert_finance_row(conn, report_date, **values):
    row = {
        "code": "688766",
        "name": "普冉股份",
        "report_date": report_date,
        "report_type": {
            "03-31": "Q1",
            "06-30": "H1",
            "09-30": "Q3",
            "12-31": "ANNUAL",
        }[report_date[5:]],
        "report_date_name": None,
        "notice_date": report_date,
        "update_date": report_date,
        "source": "eastmoney_f10",
        "fetched_at": f"{report_date} 18:00:00",
    }
    row.update(values)
    columns = ", ".join(row)
    placeholders = ", ".join(f":{column}" for column in row)
    conn.execute(
        f"INSERT INTO stock_finance_summary ({columns}) VALUES ({placeholders})",
        row,
    )


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_info (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            market_cap REAL,
            pe REAL,
            pb REAL,
            list_date TEXT
        );
        CREATE TABLE stock_valuation_daily (
            code TEXT,
            name TEXT,
            trade_date TEXT,
            pe_ttm REAL,
            pb REAL,
            total_mv REAL,
            circ_mv REAL,
            turnover REAL,
            limit_up_price REAL,
            limit_down_price REAL
        );
        INSERT INTO stock_info VALUES ('688766', '普冉股份', '半导体', 120.0, 30.1, 4.2, '2021-08-23');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def enrichment_db(tmp_path, monkeypatch):
    db_path = tmp_path / "stock_enrichment.db"
    make_db(db_path)
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(stock_api, "engine", test_engine)
    return db_path


def test_stock_enrichment_returns_empty_cache(enrichment_db):
    data = asyncio.run(stock_api.get_stock_enrichment("688766")).data

    assert data["symbol"] == "688766"
    assert data["profile"]["info"]["name"] == "普冉股份"
    assert data["profile"]["valuation"]["pe_ttm"] == 30.1
    assert data["profile"]["valuation"]["pb"] == 4.2
    assert data["announcements"] == []
    assert any(row["status"] == "empty" for row in data["freshness"])


def test_stock_enrichment_returns_cached_sections(enrichment_db):
    stock_api._ensure_enrichment_tables()
    with sqlite3.connect(enrichment_db) as conn:
        conn.execute(
            """
            INSERT INTO stock_valuation_daily (
                code, name, trade_date, pe_ttm, pb, total_mv, circ_mv,
                turnover, limit_up_price, limit_down_price
            )
            VALUES ('688766', '普冉股份', '2026-07-03', 28.5, 3.2, 10000000000, 8000000000, 2.3, 55.0, 45.0)
            """
        )
        conn.execute(
            """
            INSERT INTO stock_f10_sections
            VALUES ('688766', '公司概况', '2026-07-03 16:00:00', 'mootdx', '公司概况全文', '公司概况摘要', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO stock_announcements_cninfo
            VALUES ('688766', 'a1', '年度报告', '定期报告', '2026-04-30', 'https://example.com/a1.pdf', 'cninfo_direct', '2026-07-03 16:00:00', '{}')
            """
        )
        conn.commit()

    data = asyncio.run(stock_api.get_stock_enrichment("688766")).data

    assert data["profile"]["valuation"]["pe_ttm"] == 28.5
    assert data["f10_sections"][0]["content_digest"] == "公司概况摘要"
    assert data["announcements"][0]["title"] == "年度报告"
    assert any(row["table"] == "stock_f10_sections" and row["row_count"] == 1 for row in data["freshness"])


def test_stock_enrichment_returns_single_quarter_finance_summary(enrichment_db):
    stock_api._ensure_enrichment_tables()
    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_finance_summary (
                code TEXT,
                name TEXT,
                report_date TEXT,
                eps REAL,
                net_profit_up REAL,
                revenue_up REAL,
                roe REAL,
                total_revenue REAL,
                net_profit REAL,
                net_margin REAL,
                PRIMARY KEY (code, report_date)
            );
            INSERT INTO stock_finance_summary
            VALUES ('688766', '普冉股份', '2025-03-31', 0.20, 10.0, 8.0, 3.0, 90000000, 18000000, 20.0);
            INSERT INTO stock_finance_summary
            VALUES ('688766', '普冉股份', '2025-06-30', 0.55, 12.0, 9.0, 6.0, 210000000, 52000000, 24.8);
            INSERT INTO stock_finance_summary
            VALUES ('688766', '普冉股份', '2024-06-30', 0.40, 9.0, 7.0, 5.0, 160000000, 36000000, 22.5);
            """
        )

    data = asyncio.run(stock_api.get_stock_enrichment("688766")).data
    summary = data["finance"]["summary"]
    q2 = next(row for row in summary if row["report_date"] == "2025-06-30")

    assert q2["quarter"] == 2
    assert q2["eps_single"] is None
    assert q2["total_revenue_single"] == pytest.approx(120000000)
    assert q2["net_profit_single"] == pytest.approx(34000000)
    stale_q2 = next(row for row in summary if row["report_date"] == "2024-06-30")
    assert stale_q2["eps_single"] is None
    assert stale_q2["net_profit_single"] is None

    finance_data = asyncio.run(stock_api.get_stock_finance("688766", limit=4)).data
    assert finance_data["finance"][0]["net_profit_single"] == pytest.approx(34000000)


def test_stock_finance_performance_builds_annual_and_single_quarter_series(enrichment_db):
    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(DETAILED_FINANCE_SCHEMA)
        insert_finance_row(
            conn,
            "2024-03-31",
            report_date_name="2024一季报",
            total_revenue=100.0,
            net_profit=10.0,
            deduct_net_profit=8.0,
            eps=0.10,
            roe=2.0,
            operating_cashflow_per_share=0.20,
            gross_margin=40.0,
            net_margin=10.0,
            total_revenue_single=100.0,
            net_profit_single=10.0,
            deduct_net_profit_single=8.0,
            eps_single=0.10,
            roe_single=2.0,
            operating_cashflow_per_share_single=0.20,
            gross_margin_single=40.0,
            net_margin_single=10.0,
            total_revenue_single_yoy=12.0,
            net_profit_single_yoy=15.0,
            deduct_net_profit_single_yoy=14.0,
        )
        insert_finance_row(
            conn,
            "2024-06-30",
            report_date_name="2024中报",
            total_revenue=220.0,
            net_profit=5.0,
            deduct_net_profit=8.0,
            eps=0.15,
            roe=1.0,
            operating_cashflow_per_share=0.30,
            gross_margin=35.0,
            net_margin=2.3,
            total_revenue_single=0.0,
            net_profit_single=-5.0,
            deduct_net_profit_single=0.0,
            eps_single=None,
            roe_single=-1.0,
            operating_cashflow_per_share_single=0.0,
            gross_margin_single=0.0,
            net_margin_single=-4.2,
            total_revenue_single_yoy=0.0,
            net_profit_single_yoy=-150.0,
            deduct_net_profit_single_yoy=0.0,
            total_revenue_single_qoq=-100.0,
            net_profit_single_qoq=-150.0,
            deduct_net_profit_single_qoq=-100.0,
        )
        insert_finance_row(
            conn,
            "2024-09-30",
            report_date_name="2024三三月报",
            total_revenue=350.0,
            net_profit=20.0,
            deduct_net_profit=16.0,
            eps=0.35,
            roe=4.0,
            operating_cashflow_per_share=0.50,
            gross_margin=37.0,
            net_margin=5.7,
        )
        insert_finance_row(
            conn,
            "2024-12-31",
            report_date_name="2024年报",
            total_revenue=500.0,
            net_profit=30.0,
            deduct_net_profit=24.0,
            eps=0.55,
            roe=6.0,
            operating_cashflow_per_share=0.80,
            gross_margin=38.0,
            net_margin=6.0,
            total_revenue_single=150.0,
            net_profit_single=10.0,
            deduct_net_profit_single=8.0,
            eps_single=0.20,
            roe_single=2.0,
            operating_cashflow_per_share_single=0.30,
            gross_margin_single=39.0,
            net_margin_single=6.7,
        )
        insert_finance_row(
            conn,
            "2025-03-31",
            report_date_name="2025一季报",
            total_revenue=140.0,
            net_profit=12.0,
            deduct_net_profit=10.0,
            eps=0.20,
            roe=2.2,
            operating_cashflow_per_share=0.25,
            gross_margin=39.0,
            net_margin=8.6,
            total_revenue_single=140.0,
            net_profit_single=12.0,
            deduct_net_profit_single=10.0,
        )
        insert_finance_row(
            conn,
            "2025-06-30",
            report_date_name="2025中报",
            total_revenue=310.0,
            net_profit=26.0,
            deduct_net_profit=22.0,
            eps=0.42,
            roe=4.8,
            operating_cashflow_per_share=0.55,
            gross_margin=40.0,
            net_margin=8.4,
        )

    data = asyncio.run(
        stock_api.get_stock_finance(
            "688766",
            limit=3,
            years=2,
            as_of="2025-06-30",
        )
    ).data
    performance = data["performance"]

    assert data["count"] == 3
    assert [row["report_date"] for row in data["finance"]] == [
        "2025-06-30",
        "2025-03-31",
        "2024-12-31",
    ]
    assert [row["report_date"] for row in performance["periods"]] == sorted(
        row["report_date"] for row in performance["periods"]
    )
    assert [row["report_date"] for row in performance["annual"]] == ["2024-12-31"]
    assert "2025-06-30" not in {row["report_date"] for row in performance["annual"]}

    quarters = {row["report_date"]: row for row in performance["quarters"]}
    assert quarters["2024-06-30"]["total_revenue"] == 0.0
    assert quarters["2024-06-30"]["net_profit"] == -5.0
    assert quarters["2024-06-30"]["deduct_net_profit"] == 0.0
    assert quarters["2024-06-30"]["eps"] is None
    assert quarters["2024-06-30"]["gross_margin"] == 0.0
    assert quarters["2024-06-30"]["total_revenue_yoy"] == 0.0
    assert quarters["2024-06-30"]["total_revenue_qoq"] == -100.0
    assert quarters["2024-06-30"]["net_profit_qoq"] == -150.0
    assert quarters["2024-06-30"]["value_source"] == "explicit"
    assert quarters["2024-09-30"]["total_revenue"] == pytest.approx(130.0)
    assert quarters["2024-09-30"]["net_profit"] == pytest.approx(15.0)
    assert quarters["2024-09-30"]["eps"] is None
    assert quarters["2024-09-30"]["roe"] is None
    assert quarters["2024-09-30"]["gross_margin"] is None
    assert quarters["2024-09-30"]["value_source"] == "derived"
    assert quarters["2025-06-30"]["total_revenue"] == pytest.approx(170.0)

    assert performance["available_metrics"] == [
        "total_revenue",
        "net_profit",
        "deduct_net_profit",
        "eps",
        "roe",
        "operating_cashflow_per_share",
        "gross_margin",
        "net_margin",
    ]
    assert performance["latest_report_date"] == "2025-06-30"
    assert performance["source"] == "eastmoney_f10"
    assert performance["fetched_at"] == "2025-06-30 18:00:00"
    assert performance["data_status"] == "OK"
    assert performance["as_of"] == "2025-06-30"


def test_stock_finance_missing_quarter_is_not_derived_across_gap(enrichment_db):
    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(DETAILED_FINANCE_SCHEMA)
        insert_finance_row(
            conn,
            "2024-03-31",
            total_revenue=100.0,
            net_profit=10.0,
            deduct_net_profit=8.0,
        )
        insert_finance_row(
            conn,
            "2024-09-30",
            total_revenue=360.0,
            net_profit=30.0,
            deduct_net_profit=24.0,
        )

    data = asyncio.run(
        stock_api.get_stock_finance(
            "688766",
            years=1,
            as_of="2024-12-31",
        )
    ).data
    quarters = {row["report_date"]: row for row in data["performance"]["quarters"]}

    assert quarters["2024-03-31"]["total_revenue"] == 100.0
    assert quarters["2024-09-30"]["total_revenue"] is None
    assert quarters["2024-09-30"]["net_profit"] is None
    assert quarters["2024-09-30"]["deduct_net_profit"] is None
    assert quarters["2024-09-30"]["value_source"] == "missing"
    assert data["performance"]["data_status"] == "PARTIAL"


def test_stock_finance_as_of_and_year_window_exclude_future_rows(enrichment_db):
    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(DETAILED_FINANCE_SCHEMA)
        insert_finance_row(conn, "2023-12-31", total_revenue=300.0)
        insert_finance_row(conn, "2024-12-31", total_revenue=400.0)
        insert_finance_row(conn, "2025-03-31", total_revenue=120.0)

    data = asyncio.run(
        stock_api.get_stock_finance(
            "688766",
            limit=8,
            years=1,
            as_of="2024-12-31",
        )
    ).data

    assert [row["report_date"] for row in data["performance"]["periods"]] == ["2024-12-31"]
    assert [row["report_date"] for row in data["performance"]["annual"]] == ["2024-12-31"]
    assert [row["report_date"] for row in data["finance"]] == ["2024-12-31"]
    assert data["count"] == 1


def test_stock_finance_as_of_excludes_reports_not_yet_disclosed(enrichment_db):
    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(DETAILED_FINANCE_SCHEMA)
        insert_finance_row(
            conn,
            "2024-09-30",
            notice_date="2024-10-20",
            total_revenue=300.0,
        )
        insert_finance_row(
            conn,
            "2024-12-31",
            notice_date="2025-03-30",
            total_revenue=400.0,
        )

    data = asyncio.run(
        stock_api.get_stock_finance(
            "688766",
            years=2,
            as_of="2025-02-01",
        )
    ).data

    assert [row["report_date"] for row in data["performance"]["periods"]] == [
        "2024-09-30",
    ]


def test_stock_enrichment_refresh_uses_collector_status(enrichment_db, monkeypatch):
    threadpool_calls = []

    async def fake_run_in_threadpool(function, *args, **kwargs):
        threadpool_calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    def fake_refresh(self, symbol, modules=None, trade_date=None):
        assert symbol == "688766"
        return {"profile": {"status": "ok", "rows": 1}}

    monkeypatch.setattr(stock_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(stock_enrichment_collector.StockEnrichmentCollector, "refresh_symbol", fake_refresh)

    data = asyncio.run(
        stock_api.refresh_stock_enrichment(
            "688766",
            stock_api.StockEnrichmentRefreshRequest(modules=["profile"], trade_date="2026-07-03"),
        )
    ).data

    assert data["refresh"]["profile"]["status"] == "ok"
    assert data["cache"]["symbol"] == "688766"
    assert len(threadpool_calls) == 1
    _, args, kwargs = threadpool_calls[0]
    assert args == ("688766",)
    assert kwargs == {"modules": ["profile"], "trade_date": "2026-07-03"}


def test_explicit_finance_summary_refresh_rechecks_detailed_gaps(enrichment_db, monkeypatch):
    calls = []
    closed = []

    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(DETAILED_FINANCE_SCHEMA)
        for report_date in (
            "2024-03-31",
            "2024-06-30",
            "2024-09-30",
            "2024-12-31",
            "2025-03-31",
            "2025-06-30",
            "2025-09-30",
            "2025-12-31",
        ):
            insert_finance_row(
                conn,
                report_date,
                total_revenue=100.0,
                net_profit=10.0,
            )

    class FakeFinanceSummaryCollector:
        def __init__(self):
            self.engine = None
            self.f10_client = self

        def close(self):
            closed.append(True)

        def backfill_symbol(self, symbol, *, years):
            calls.append((symbol, years, self.engine))
            return {"checked": 24, "updated": 21, "skipped": 3, "failed": []}

    collector = stock_enrichment_collector.StockEnrichmentCollector()
    collector.engine = stock_api.engine
    monkeypatch.setattr(
        finance_summary_collector,
        "FinanceSummaryCollector",
        FakeFinanceSummaryCollector,
    )

    rows = collector._refresh_finance_summary("688766", trade_date="2026-07-03")

    assert rows == 21
    assert calls == [("688766", 6, stock_api.engine)]
    assert closed == [True]


def test_finance_summary_refresh_surfaces_source_failure(enrichment_db, monkeypatch):
    closed = []

    class FailingFinanceSummaryCollector:
        def __init__(self):
            self.engine = None
            self.f10_client = self

        def close(self):
            closed.append(True)

        def backfill_symbol(self, symbol, *, years):
            assert (symbol, years) == ("688766", 6)
            return {
                "checked": 24,
                "updated": 0,
                "skipped": 24,
                "failed": [{"date": "all", "error": "source timeout"}],
            }

    collector = stock_enrichment_collector.StockEnrichmentCollector()
    collector.engine = stock_api.engine
    monkeypatch.setattr(
        finance_summary_collector,
        "FinanceSummaryCollector",
        FailingFinanceSummaryCollector,
    )

    result = collector.refresh_symbol("688766", modules=["finance_summary"])

    assert result["finance_summary"]["status"] == "failed"
    assert result["finance_summary"]["rows"] == 0
    assert "source timeout" in result["finance_summary"]["error"]
    assert closed == [True]


def test_stock_enrichment_collector_normalizes_announcement_epoch(enrichment_db, monkeypatch):
    collector = stock_enrichment_collector.StockEnrichmentCollector()
    collector.engine = stock_api.engine
    collector.ensure_tables()

    monkeypatch.setattr(
        collector.cninfo,
        "announcements",
        lambda symbol, page_num=1, page_size=50: (
            [
                {
                    "announcementId": "epoch-1",
                    "announcementTitle": "年度报告",
                    "announcementTime": 1773331200000,
                }
            ]
            if page_num == 1
            else []
        ),
    )

    rows = collector._refresh_announcements("688766", trade_date="2026-07-03")
    data = asyncio.run(stock_api.get_stock_enrichment("688766")).data

    assert rows == 1
    assert data["announcements"][0]["publish_date"] == "2026-03-13"


def test_stock_enrichment_default_modules_cover_material_tabs():
    collector = stock_enrichment_collector.StockEnrichmentCollector()

    modules = collector._normalize_modules(None)

    assert {"profile", "financial_reports", "f10", "announcements"}.issubset(modules)
    assert {"fund_flow", "dragon_tiger", "margin", "block_trades"}.issubset(modules)
    assert {"holder_num", "dividends", "lockups", "northbound_flow"}.issubset(modules)


def test_stock_enrichment_finance_snapshot_falls_back_to_summary(enrichment_db):
    stock_api._ensure_enrichment_tables()
    with sqlite3.connect(enrichment_db) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_finance_summary (
                code TEXT,
                name TEXT,
                report_date TEXT,
                eps REAL,
                net_profit_up REAL,
                revenue_up REAL,
                roe REAL,
                total_revenue REAL,
                net_profit REAL,
                net_margin REAL,
                PRIMARY KEY (code, report_date)
            );
            INSERT INTO stock_finance_summary
            VALUES ('688766', '普冉股份', '2026-03-31', 0.85, 20.0, 18.0, 7.5, 300000000, 60000000, 20.0);
            """
        )

    collector = stock_enrichment_collector.StockEnrichmentCollector()
    collector.engine = stock_api.engine
    collector._mootdx_finance = lambda _symbol: (_ for _ in ()).throw(RuntimeError("offline"))
    count = collector._refresh_finance_snapshot("688766")
    data = asyncio.run(stock_api.get_stock_enrichment("688766")).data

    assert count == 1
    assert data["finance"]["snapshot"]["source"] == "finance_summary_fallback"
    assert data["finance"]["snapshot"]["profit"] == 60000000


def test_historical_amount_gap_is_partial_even_when_latest_is_complete():
    from app.api.v1 import stock as stock_api
    result = stock_api._finance_performance([
        {'report_date': '2024-03-31', 'eps': 0.1, 'eps_single': 0.1, 'total_revenue': None, 'net_profit': None},
        {'report_date': '2025-03-31', 'eps': 0.2, 'eps_single': 0.2, 'total_revenue': 100, 'net_profit': 10, 'total_revenue_single': 100, 'net_profit_single': 10},
    ], '2026-09-30')
    assert result['data_status'] == 'PARTIAL'
