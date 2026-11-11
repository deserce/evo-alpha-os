"""HTTP contract and read-only safety of the research catalogue."""
from contextlib import closing
import hashlib
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.v1.research import router
from research.repository import Repository


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    db_path = tmp_path / "research.db"
    monkeypatch.setenv("EVOALPHA_RESEARCH_DB", str(db_path))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""CREATE TABLE reports (
            id TEXT PRIMARY KEY, title TEXT, symbol TEXT, stock_name TEXT,
            report_type TEXT, industry_name TEXT, org_name TEXT, publish_date TEXT
        )""")
        conn.executemany("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("eastmoney:a", "增长 100%", "000001", "平安银行", "company", "银行",
             "测试证券", "2026-10-20"),
            ("eastmoney:b", "盈利预测", "000001", "平安银行", "company", "银行",
             "测试证券", "2026-10-21"),
            ("eastmoney:c", "银行行业展望", "", "", "industry", "银行",
             "其他证券", "2026-10-21"),
        ])
        conn.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client, db_path


def test_pagination_stock_and_historical_cutoff(catalogue):
    client, _ = catalogue
    response = client.get("/api/v1/research/reports", params={
        "symbol": "000001", "date_to": "2026-10-20", "page_size": 1,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["id"] == "eastmoney:a"
    result = client.get("/api/v1/research/reports", params={"page_size": 1, "page": 2})
    assert result.json()["data"]["items"][0]["id"] == "eastmoney:b"


def test_shared_detail_and_facets(catalogue):
    client, _ = catalogue
    detail = client.get("/api/v1/research/reports/eastmoney:a")
    assert detail.status_code == 200
    assert detail.json()["data"]["symbol"] == "000001"
    facets = client.get("/api/v1/research/facets")
    assert facets.status_code == 200
    assert facets.json()["data"]["industries"] == ["银行"]
    assert facets.json()["data"]["total"] == 3
    assert client.get("/api/v1/research/reports/missing").status_code == 404


@pytest.mark.parametrize("params", [
    {"symbol": "1"}, {"symbol": "０００００１"}, {"report_type": "invalid"},
    {"date_from": "2026-02-30"}, {"date_to": "20261020"},
    {"date_from": "2026-10-21", "date_to": "2026-10-20"},
    {"page": 0}, {"page": 100001}, {"page_size": 101}, {"page_size": 0},
    {"q": "x" * 121}, {"industry": "x" * 121}, {"org": "x" * 121},
])
def test_invalid_filters_rejected(catalogue, params):
    client, _ = catalogue
    assert client.get("/api/v1/research/reports", params=params).status_code == 422


def test_literal_search_and_sql_injection(catalogue):
    client, _ = catalogue
    result = client.get("/api/v1/research/reports", params={"q": "%"})
    assert result.json()["data"]["total"] == 1
    result = client.get("/api/v1/research/reports", params={"q": "' OR 1=1 --"})
    assert result.json()["data"]["total"] == 0
    assert client.get("/api/v1/research/reports/invalid%27id").status_code == 422


def test_gets_do_not_modify_catalogue(catalogue):
    client, db_path = catalogue
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    for path in ("reports", "reports/eastmoney:a", "facets"):
        assert client.get("/api/v1/research/" + path).status_code == 200
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["research.db"]
    with closing(Repository(db_path)._connect()) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM reports")


def test_missing_database_is_explicit_and_never_created(catalogue, monkeypatch, tmp_path):
    client, _ = catalogue
    absent = tmp_path / "absent" / "research.db"
    monkeypatch.setenv("EVOALPHA_RESEARCH_DB", str(absent))
    for path in ("reports", "facets"):
        response = client.get("/api/v1/research/" + path)
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "not_imported"
    assert client.get("/api/v1/research/reports/missing").status_code == 404
    assert not absent.parent.exists()


@pytest.mark.parametrize("path", ["reports", "reports/eastmoney:a", "facets"])
def test_corrupt_database_returns_safe_503(catalogue, path):
    client, db_path = catalogue
    db_path.write_bytes(b"invalid sqlite file")
    response = client.get("/api/v1/research/" + path)
    assert response.status_code == 503
    assert "暂时不可用" in response.json()["detail"]
    assert str(db_path) not in response.text


def test_research_router_registered():
    from app.api.v1 import api_v1_router
    assert "/api/v1/research/reports" in {route.path for route in api_v1_router.routes}
