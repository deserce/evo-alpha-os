import sqlite3
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import portfolio as portfolio_api
from portfolio.schema import configure_portfolio_connection, ensure_portfolio_schema


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "portfolio-api.db"
    ensure_portfolio_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                industry TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL,
                adjustment_mode TEXT DEFAULT 'qfq',
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE stock_daily_prices_raw (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close,
                source TEXT,
                quality_flag TEXT,
                run_id TEXT,
                updated_at TEXT,
                adjustment_mode TEXT,
                market TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            INSERT INTO stock_info VALUES ('600519', '贵州茅台', '白酒');
            INSERT INTO stock_info VALUES ('000001', '平安银行', '银行');
            INSERT INTO stock_daily_prices (symbol, trade_date, close)
            VALUES ('600519', '2026-07-23', 12.0);
            INSERT INTO stock_daily_prices_raw (
                symbol, trade_date, close, source, quality_flag,
                adjustment_mode, market
            ) VALUES (
                '600519', '2026-07-23', 12.0, 'tdx_mootdx_raw',
                'ok_unadjusted', 'none', 'SH'
            );
            """
        )
        conn.commit()
    monkeypatch.setattr(portfolio_api, "PORTFOLIO_DB_PATH", db_path)
    app = FastAPI()
    app.include_router(portfolio_api.router)
    return TestClient(app, raise_server_exceptions=False), db_path


def _create_account(
    client: TestClient,
    *,
    name: str = "主账户",
    opening_cash_cents: int = 1_000_000,
):
    response = client.post(
        "/portfolio/accounts",
        json={
            "name": name,
            "account_type": "CASH",
            "currency": "CNY",
            "opening_cash_cents": opening_cash_cents,
            "is_default": name == "主账户",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post_trade(
    client: TestClient,
    account_id: int,
    *,
    client_request_id: str,
    business_type: str = "BUY",
    trade_date: str = "2026-07-21",
    symbol: str = "600519",
    quantity: int = 100,
    price_micros: int = 10_000_000,
    fee_cents: int = 0,
    tax_cents: int = 0,
    expected: dict[str, int] | None = None,
):
    sizing_payload = {
        "account_id": account_id,
        "business_type": business_type,
        "trade_date": trade_date,
        "symbol": symbol,
        "quantity": quantity,
        "price_micros": price_micros,
        "fee_cents": fee_cents,
        "tax_cents": tax_cents,
    }
    if expected is None:
        preview = client.post("/portfolio/trades/preview", json=sizing_payload)
        if preview.status_code != 200:
            return preview
        preview_data = preview.json()
        expected = {
            "expected_ledger_revision": preview_data["ledger_revision"],
            "expected_quantity": preview_data["quantity"],
            "expected_gross_amount_cents": preview_data["gross_amount_cents"],
            "expected_cash_delta_cents": preview_data["cash_delta_cents"],
        }
    return client.post(
        "/portfolio/trades",
        json={
            "client_request_id": client_request_id,
            "occurred_at": f"{trade_date}T10:00:00+08:00",
            **sizing_payload,
            **expected,
        },
    )


def test_account_create_and_list_include_cash_summary(api_client):
    client, _ = api_client
    account = _create_account(client, opening_cash_cents=250_000)
    assert account["cash_cents"] == 250_000
    assert account["account_type"] == "CASH"

    second = client.post(
        "/portfolio/accounts",
        json={
            "name": "模拟账户",
            "account_type": "SIMULATED",
            "opening_cash_cents": 100_000,
        },
    )
    assert second.status_code == 201

    response = client.get("/portfolio/accounts")
    assert response.status_code == 200
    assert [(row["name"], row["cash_cents"]) for row in response.json()] == [
        ("主账户", 250_000),
        ("模拟账户", 100_000),
    ]

    margin = client.post(
        "/portfolio/accounts",
        json={"name": "融资账户", "account_type": "MARGIN"},
    )
    assert margin.status_code == 422

    duplicate_default = client.post(
        "/portfolio/accounts",
        json={
            "name": "重复默认账户",
            "account_type": "CASH",
            "is_default": True,
        },
    )
    assert duplicate_default.status_code == 409
    assert duplicate_default.json() == {
        "detail": "default portfolio account already exists"
    }
    assert "UNIQUE" not in duplicate_default.text


def test_cash_flow_endpoint_posts_deposit_and_withdrawal(api_client):
    client, _ = api_client
    account = _create_account(client, opening_cash_cents=100_000)
    assert account["ledger_revision"] == 0

    deposit = client.post(
        f"/portfolio/accounts/{account['id']}/cash-flows",
        json={
            "client_request_id": "api-deposit-1",
            "business_type": "DEPOSIT",
            "trade_date": "2026-07-21",
            "occurred_at": "2026-07-21T09:30:00+08:00",
            "amount_cents": 50_000,
            "expected_ledger_revision": 0,
            "note": "工资入金",
        },
    )
    assert deposit.status_code == 201, deposit.text
    deposited = deposit.json()
    assert deposited["cash_before_cents"] == 100_000
    assert deposited["cash_after_cents"] == 150_000
    assert deposited["transaction"]["business_type"] == "DEPOSIT"
    assert deposited["transaction"]["note"] == "工资入金"

    withdrawal = client.post(
        f"/portfolio/accounts/{account['id']}/cash-flows",
        json={
            "client_request_id": "api-withdrawal-1",
            "business_type": "WITHDRAWAL",
            "trade_date": "2026-07-22",
            "occurred_at": "2026-07-22T14:30:00+08:00",
            "amount_cents": 20_000,
            "expected_ledger_revision": deposited["ledger_revision"],
        },
    )
    assert withdrawal.status_code == 201, withdrawal.text
    assert withdrawal.json()["cash_after_cents"] == 130_000

    accounts = client.get("/portfolio/accounts").json()
    assert accounts[0]["cash_cents"] == 130_000
    assert accounts[0]["ledger_revision"] == withdrawal.json()["ledger_revision"]
    transactions = client.get(
        "/portfolio/transactions",
        params={"account_id": account["id"]},
    ).json()
    assert [row["business_type"] for row in transactions] == [
        "WITHDRAWAL",
        "DEPOSIT",
    ]
    assert transactions[1]["note"] == "工资入金"


def test_cash_flow_endpoint_rejects_stale_or_insufficient_requests(api_client):
    client, db_path = api_client
    account = _create_account(client, opening_cash_cents=10_000)
    first = client.post(
        f"/portfolio/accounts/{account['id']}/cash-flows",
        json={
            "client_request_id": "api-first-deposit",
            "business_type": "DEPOSIT",
            "trade_date": "2026-07-21",
            "occurred_at": "2026-07-21T09:30:00+08:00",
            "amount_cents": 1_000,
            "expected_ledger_revision": 0,
        },
    )
    assert first.status_code == 201

    stale = client.post(
        f"/portfolio/accounts/{account['id']}/cash-flows",
        json={
            "client_request_id": "api-stale-withdrawal",
            "business_type": "WITHDRAWAL",
            "trade_date": "2026-07-22",
            "occurred_at": "2026-07-22T09:30:00+08:00",
            "amount_cents": 1_000,
            "expected_ledger_revision": 0,
        },
    )
    assert stale.status_code == 409
    assert "刷新" in stale.json()["detail"]

    insufficient = client.post(
        f"/portfolio/accounts/{account['id']}/cash-flows",
        json={
            "client_request_id": "api-too-large-withdrawal",
            "business_type": "WITHDRAWAL",
            "trade_date": "2026-07-22",
            "occurred_at": "2026-07-22T09:31:00+08:00",
            "amount_cents": 11_001,
            "expected_ledger_revision": first.json()["ledger_revision"],
        },
    )
    assert insufficient.status_code == 422
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0] == 1


def test_cash_flow_endpoint_validates_payload_and_account_scope(api_client):
    client, _ = api_client
    first = _create_account(client)
    second = _create_account(client, name="第二账户")

    malformed = client.post(
        f"/portfolio/accounts/{first['id']}/cash-flows",
        json={
            "client_request_id": "api-invalid-flow",
            "business_type": "BUY",
            "trade_date": "2026-07-21",
            "occurred_at": "2026-07-21T09:30:00+08:00",
            "amount_cents": 10_000,
            "expected_ledger_revision": 0,
            "unexpected": True,
        },
    )
    assert malformed.status_code == 422

    second_flow = client.post(
        f"/portfolio/accounts/{second['id']}/cash-flows",
        json={
            "client_request_id": "api-second-deposit",
            "business_type": "DEPOSIT",
            "trade_date": "2026-07-21",
            "occurred_at": "2026-07-21T09:30:00+08:00",
            "amount_cents": 10_000,
            "expected_ledger_revision": 0,
        },
    )
    assert second_flow.status_code == 201
    accounts = client.get("/portfolio/accounts").json()
    balances = {row["id"]: row["cash_cents"] for row in accounts}
    assert balances[first["id"]] == 1_000_000
    assert balances[second["id"]] == 1_010_000


def test_preview_and_post_return_integer_accounting_units(api_client):
    client, _ = api_client
    account = _create_account(client)

    preview = client.post(
        "/portfolio/trades/preview",
        json={
            "account_id": account["id"],
            "business_type": "BUY",
            "trade_date": "2026-07-21",
            "symbol": "600519",
            "price_micros": 10_000_000,
            "amount_cents": 250_000,
            "fee_cents": 500,
            "tax_cents": 0,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "account_id": account["id"],
        "business_type": "BUY",
        "trade_date": "2026-07-21",
        "symbol": "600519",
        "quantity": 200,
        "price_micros": 10_000_000,
        "gross_amount_cents": 200_000,
        "cash_delta_cents": -200_500,
        "fee_cents": 500,
        "tax_cents": 0,
        "target_position_bps": None,
        "ledger_revision": 0,
    }

    posted = _post_trade(
        client,
        account["id"],
        client_request_id="api-buy-1",
        fee_cents=500,
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["gross_amount_cents"] == 100_000
    assert posted.json()["cash_delta_cents"] == -100_500
    assert isinstance(posted.json()["price_micros"], int)

    missing_expected = client.post(
        "/portfolio/trades",
        json={
            "account_id": account["id"],
            "client_request_id": "missing-expected-binding",
            "business_type": "BUY",
            "trade_date": "2026-07-21",
            "occurred_at": "2026-07-21T10:00:00+08:00",
            "symbol": "000001",
            "quantity": 100,
            "price_micros": 10_000_000,
            "fee_cents": 0,
            "tax_cents": 0,
        },
    )
    assert missing_expected.status_code == 422


def test_positions_join_quote_and_calculate_pnl_in_integer_units(api_client):
    client, db_path = api_client
    account = _create_account(client)
    assert _post_trade(
        client,
        account["id"],
        client_request_id="position-buy",
    ).status_code == 201
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE portfolio_positions
            SET updated_at = '2000-01-01 00:00:00'
            WHERE account_id = ?
            """,
            (account["id"],),
        )
        before = conn.execute(
            """
            SELECT *
            FROM portfolio_positions
            WHERE account_id = ?
            """,
            (account["id"],),
        ).fetchone()
        conn.commit()

    response = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "as_of_date": "2026-07-23"},
    )
    assert response.status_code == 200, response.text
    repeated = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "as_of_date": "2026-07-23"},
    )
    assert repeated.status_code == 200
    assert repeated.json() == response.json()
    assert response.json() == [
        {
            "account_id": account["id"],
            "symbol": "600519",
            "name": "贵州茅台",
            "industry": "白酒",
            "position_revision": 1,
            "quantity": 100,
            "available_quantity": 100,
            "cost_basis_cents": 100_000,
            "avg_cost_micros": 10_000_000,
            "realized_pnl_cents": 0,
            "status": "OPEN",
            "first_buy_date": "2026-07-21",
            "last_trade_date": "2026-07-21",
            "last_transaction_id": 1,
            "price_micros": 12_000_000,
            "price_trade_date": "2026-07-23",
            "price_status": "CURRENT",
            "stale": False,
            "market_value_cents": 120_000,
            "unrealized_pnl_cents": 20_000,
            "unrealized_pnl_bps": 2000,
        }
    ]
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            """
            SELECT *
            FROM portfolio_positions
            WHERE account_id = ?
            """,
            (account["id"],),
        ).fetchone()
    assert after == before
    assert after[-1] == "2000-01-01 00:00:00"


def test_positions_use_market_expected_date_and_batch_metadata_queries(
    api_client,
    monkeypatch,
):
    client, db_path = api_client
    account = _create_account(client)
    assert _post_trade(
        client,
        account["id"],
        client_request_id="batch-price-buy",
        symbol="600519",
    ).status_code == 201
    assert _post_trade(
        client,
        account["id"],
        client_request_id="batch-missing-buy",
        symbol="000001",
    ).status_code == 201

    traced_statements: list[str] = []

    def traced_connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        configure_portfolio_connection(conn)
        conn.set_trace_callback(traced_statements.append)
        return conn

    monkeypatch.setattr(portfolio_api, "_connect_read", traced_connect)
    response = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "as_of_date": "2026-07-25"},
    )
    assert response.status_code == 200, response.text
    rows = {row["symbol"]: row for row in response.json()}
    assert rows["600519"]["price_status"] == "CURRENT"
    assert rows["600519"]["stale"] is False
    assert rows["600519"]["price_trade_date"] == "2026-07-23"
    assert rows["000001"]["price_status"] == "MISSING"

    metadata_selects = [
        statement
        for statement in traced_statements
        if statement.lstrip().upper().startswith("SELECT")
        and (
            "stock_info" in statement
            or "stock_daily_prices" in statement
        )
    ]
    assert len(metadata_selects) <= 2


def test_positions_quote_lookup_does_not_scan_market_history(api_client, monkeypatch):
    client, db_path = api_client
    account = _create_account(client)
    assert _post_trade(
        client, account["id"], client_request_id="bounded-quote-buy"
    ).status_code == 201
    statements: list[str] = []

    def traced_connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        configure_portfolio_connection(conn)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(portfolio_api, "_connect_read", traced_connect)
    response = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "as_of_date": "2026-07-23"},
    )
    assert response.status_code == 200, response.text
    position_query = next(
        statement for statement in statements
        if "FROM portfolio_positions AS position_row" in statement
    )
    with sqlite3.connect(db_path) as conn:
        plan = conn.execute("EXPLAIN QUERY PLAN " + position_query).fetchall()
    details = [str(step[3]) for step in plan]
    assert not any("SCAN stock_daily_prices" in detail for detail in details), details
    assert any("symbol=? AND trade_date<?" in detail for detail in details), details


@pytest.mark.parametrize("position_status", ["OPEN", "CLOSED"])
def test_positions_quote_lookup_preserves_history_null_and_status(
    api_client, position_status
):
    client, db_path = api_client
    account = _create_account(client)
    assert _post_trade(
        client, account["id"], client_request_id="historical-quote-buy"
    ).status_code == 201
    if position_status == "CLOSED":
        assert _post_trade(
            client, account["id"], client_request_id="historical-quote-sell",
            business_type="SELL", trade_date="2026-07-22",
        ).status_code == 201
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO stock_daily_prices (symbol, trade_date, close) VALUES (?, ?, ?)",
            [
                ("600519", "2026-07-24", None),
                ("600519", "2026-07-27", 15.0),
                ("000001", "2026-07-24", 20.0),
            ],
        )
    response = client.get(
        "/portfolio/positions",
        params={
            "account_id": account["id"], "as_of_date": "2026-07-25",
            "status": position_status,
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["status"] == position_status
    assert rows[0]["price_trade_date"] == "2026-07-23"
    assert rows[0]["price_micros"] == 12_000_000
    assert rows[0]["price_status"] == "STALE"
    assert rows[0]["stale"] is True


def test_missing_quote_returns_null_valuation_instead_of_fake_price(api_client):
    client, _ = api_client
    account = _create_account(client)
    posted = _post_trade(
        client,
        account["id"],
        client_request_id="missing-price-buy",
        symbol="000001",
    )
    assert posted.status_code == 201

    response = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "as_of_date": "2026-07-23"},
    )
    row = response.json()[0]
    assert row["symbol"] == "000001"
    assert row["name"] == "平安银行"
    assert row["price_micros"] is None
    assert row["market_value_cents"] is None
    assert row["unrealized_pnl_cents"] is None
    assert row["unrealized_pnl_bps"] is None
    assert row["price_status"] == "MISSING"
    assert row["stale"] is True


def test_transactions_list_idempotency_conflict_and_reverse(api_client):
    client, _ = api_client
    account = _create_account(client)
    buy_preview = client.post(
        "/portfolio/trades/preview",
        json={
            "account_id": account["id"],
            "business_type": "BUY",
            "trade_date": "2026-07-21",
            "symbol": "600519",
            "quantity": 100,
            "price_micros": 10_000_000,
            "fee_cents": 0,
            "tax_cents": 0,
        },
    ).json()
    buy_expected = {
        "expected_ledger_revision": buy_preview["ledger_revision"],
        "expected_quantity": buy_preview["quantity"],
        "expected_gross_amount_cents": buy_preview["gross_amount_cents"],
        "expected_cash_delta_cents": buy_preview["cash_delta_cents"],
    }
    buy = _post_trade(
        client,
        account["id"],
        client_request_id="list-buy",
        expected=buy_expected,
    )
    assert buy.status_code == 201
    sell = _post_trade(
        client,
        account["id"],
        client_request_id="list-sell",
        business_type="SELL",
        trade_date="2026-07-22",
        quantity=40,
        price_micros=12_000_000,
    )
    assert sell.status_code == 201

    retry = _post_trade(
        client,
        account["id"],
        client_request_id="list-buy",
        expected=buy_expected,
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == buy.json()["id"]
    conflict = _post_trade(
        client,
        account["id"],
        client_request_id="list-buy",
        fee_cents=1,
        expected=buy_expected,
    )
    assert conflict.status_code == 409

    listed = client.get(
        "/portfolio/transactions",
        params={"account_id": account["id"], "limit": 10},
    )
    assert listed.status_code == 200
    assert [row["client_request_id"] for row in listed.json()] == [
        "list-sell",
        "list-buy",
    ]

    reversed_response = client.post(
        f"/portfolio/transactions/{sell.json()['id']}/reverse",
        json={
            "account_id": account["id"],
            "client_request_id": "reverse-list-sell",
            "trade_date": "2026-07-23",
            "occurred_at": "2026-07-23T10:00:00+08:00",
        },
    )
    assert reversed_response.status_code == 201, reversed_response.text
    assert reversed_response.json()["reversal_of"] == sell.json()["id"]


def test_trade_confirmation_detects_related_revision_but_not_other_accounts(
    api_client,
):
    client, db_path = api_client
    account = _create_account(client)
    other = _create_account(client, name="其他账户")
    request = {
        "account_id": account["id"],
        "business_type": "BUY",
        "trade_date": "2026-07-22",
        "symbol": "600519",
        "quantity": 100,
        "price_micros": 10_000_000,
        "fee_cents": 0,
        "tax_cents": 0,
    }
    preview = client.post("/portfolio/trades/preview", json=request)
    assert preview.status_code == 200
    preview_data = preview.json()
    old_expected = {
        "expected_ledger_revision": preview_data["ledger_revision"],
        "expected_quantity": preview_data["quantity"],
        "expected_gross_amount_cents": preview_data["gross_amount_cents"],
        "expected_cash_delta_cents": preview_data["cash_delta_cents"],
    }

    assert _post_trade(
        client,
        other["id"],
        client_request_id="unrelated-api-buy",
        trade_date="2026-07-21",
        symbol="000001",
    ).status_code == 201
    unaffected = _post_trade(
        client,
        account["id"],
        client_request_id="unaffected-confirm",
        trade_date="2026-07-22",
        expected=old_expected,
    )
    assert unaffected.status_code == 201, unaffected.text

    stale_preview = client.post(
        "/portfolio/trades/preview",
        json={**request, "symbol": "000001"},
    ).json()
    stale_expected = {
        "expected_ledger_revision": stale_preview["ledger_revision"],
        "expected_quantity": stale_preview["quantity"],
        "expected_gross_amount_cents": stale_preview["gross_amount_cents"],
        "expected_cash_delta_cents": stale_preview["cash_delta_cents"],
    }
    assert _post_trade(
        client,
        account["id"],
        client_request_id="related-api-buy",
        trade_date="2026-07-22",
        symbol="600519",
    ).status_code == 201

    stale = _post_trade(
        client,
        account["id"],
        client_request_id="stale-api-confirm",
        trade_date="2026-07-22",
        symbol="000001",
        expected=stale_expected,
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "detail": "Portfolio 已发生变化，请重新预览后再确认记账"
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM portfolio_transactions
            WHERE client_request_id = 'stale-api-confirm'
            """
        ).fetchone()[0] == 0


def test_next_day_sell_preview_succeeds_without_positions_get(api_client):
    client, _ = api_client
    account = _create_account(client)
    assert _post_trade(
        client,
        account["id"],
        client_request_id="preview-ledger-buy",
        trade_date="2026-07-21",
    ).status_code == 201

    preview = client.post(
        "/portfolio/trades/preview",
        json={
            "account_id": account["id"],
            "business_type": "SELL",
            "trade_date": "2026-07-22",
            "symbol": "600519",
            "price_micros": 11_000_000,
            "quantity": 100,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["quantity"] == 100


def test_business_errors_map_to_422_and_query_limit_is_bounded(api_client):
    client, _ = api_client
    account = _create_account(client, opening_cash_cents=100_000)

    too_expensive = _post_trade(
        client,
        account["id"],
        client_request_id="api-too-expensive",
        quantity=200,
    )
    assert too_expensive.status_code == 422

    assert _post_trade(
        client,
        account["id"],
        client_request_id="api-valid-buy",
        price_micros=5_000_000,
    ).status_code == 201
    same_day_sell = _post_trade(
        client,
        account["id"],
        client_request_id="api-t1-sell",
        business_type="SELL",
        quantity=1,
        price_micros=5_000_000,
    )
    assert same_day_sell.status_code == 422

    historical = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "as_of_date": "2026-07-20"},
    )
    assert historical.status_code == 422

    assert client.get(
        "/portfolio/transactions",
        params={"account_id": account["id"], "limit": 501},
    ).status_code == 422


def test_performance_post_get_and_summary_serialize_complete_snapshot(api_client):
    client, db_path = api_client
    account = _create_account(client)
    assert _post_trade(
        client,
        account["id"],
        client_request_id="performance-buy",
    ).status_code == 201

    built = client.post(
        f"/portfolio/accounts/{account['id']}"
        "/performance/snapshots/2026-07-23"
    )

    assert built.status_code == 201, built.text
    payload = built.json()
    assert payload["account"]["account_id"] == account["id"]
    assert payload["account"]["snapshot_date"] == "2026-07-23"
    assert payload["account"]["price_status"] == "COMPLETE"
    assert payload["account"]["performance_status"] == "INITIAL"
    assert payload["account"]["market_value_cents"] == 120_000
    assert payload["account"]["return_ppb"] == 0
    assert payload["positions"] == [
        {
            "account_id": account["id"],
            "snapshot_date": "2026-07-23",
            "symbol": "600519",
            "position_revision": 1,
            "quantity": 100,
            "available_quantity": 100,
            "cost_basis_cents": 100_000,
            "realized_pnl_cents": 0,
            "price_micros": 12_000_000,
            "price_date": "2026-07-23",
            "market_value_cents": 120_000,
            "unrealized_pnl_cents": 20_000,
            "price_status": "CURRENT",
            "last_transaction_id": 1,
        }
    ]

    curve = client.get(
        f"/portfolio/accounts/{account['id']}/performance/snapshots"
    )
    assert curve.status_code == 200
    assert curve.json() == [payload["account"]]

    summary = client.get(
        f"/portfolio/accounts/{account['id']}/performance/summary",
        params={"as_of_date": "2026-07-23"},
    )
    assert summary.status_code == 200, summary.text
    assert tuple(summary.json()) == ("DAY", "WEEK", "MONTH", "YEAR")
    assert summary.json()["DAY"] == {
        "period": "DAY",
        "start_date": "2026-07-23",
        "end_date": "2026-07-23",
        "status": "INITIAL",
        "trading_day_count": 1,
        "net_external_flow_cents": 0,
        "pnl_cents": 0,
        "return_ppb": 0,
        "end_equity_cents": 1_020_000,
        "end_realized_pnl_cents": 0,
        "end_unrealized_pnl_cents": 20_000,
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM portfolio_account_daily_snapshots
            WHERE account_id = ?
            """,
            (account["id"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM portfolio_position_daily_snapshots
            WHERE account_id = ?
            """,
            (account["id"],),
        ).fetchone()[0] == 1


def test_performance_partial_snapshot_preserves_null_financial_values(api_client):
    client, _ = api_client
    account = _create_account(client)
    assert _post_trade(
        client,
        account["id"],
        client_request_id="performance-missing-price",
        symbol="000001",
    ).status_code == 201

    response = client.post(
        f"/portfolio/accounts/{account['id']}"
        "/performance/snapshots/2026-07-23"
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["account"]["price_status"] == "PARTIAL"
    assert payload["account"]["performance_status"] == "UNAVAILABLE"
    assert payload["account"]["market_value_cents"] is None
    assert payload["account"]["total_equity_cents"] is None
    assert payload["account"]["pnl_cents"] is None
    assert payload["account"]["return_ppb"] is None
    assert payload["positions"][0]["price_status"] == "MISSING"
    assert payload["positions"][0]["price_micros"] is None
    assert payload["positions"][0]["market_value_cents"] is None


def test_performance_empty_gets_do_not_create_snapshots(api_client):
    client, db_path = api_client
    account = _create_account(client)

    curve = client.get(
        f"/portfolio/accounts/{account['id']}/performance/snapshots"
    )
    summary = client.get(
        f"/portfolio/accounts/{account['id']}/performance/summary"
    )

    assert curve.status_code == 200
    assert curve.json() == []
    assert summary.status_code == 200
    assert all(
        row == {
            "period": period,
            "start_date": None,
            "end_date": None,
            "status": "EMPTY",
            "trading_day_count": 0,
            "net_external_flow_cents": None,
            "pnl_cents": None,
            "return_ppb": None,
            "end_equity_cents": None,
            "end_realized_pnl_cents": None,
            "end_unrealized_pnl_cents": None,
        }
        for period, row in summary.json().items()
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_account_daily_snapshots"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_position_daily_snapshots"
        ).fetchone()[0] == 0


def test_performance_curve_defaults_to_latest_400_and_caps_limit(api_client):
    client, db_path = api_client
    account = _create_account(client)
    first_date = date(2024, 1, 1)
    rows = [
        (
            account["id"],
            (first_date + timedelta(days=index)).isoformat(),
        )
        for index in range(405)
    ]
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.executemany(
            """
            INSERT INTO portfolio_account_daily_snapshots (
                account_id, snapshot_date, ledger_revision, cash_cents,
                market_value_cents, total_equity_cents,
                net_external_flow_cents, weighted_external_flow_micros,
                pnl_cents, return_ppb, realized_pnl_cents,
                unrealized_pnl_cents, position_count,
                unpriced_position_count, price_status,
                expected_price_date, performance_status, return_base_date
            ) VALUES (
                ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                'COMPLETE', ?, 'INITIAL', NULL
            )
            """,
            [(account_id, item_date, item_date) for account_id, item_date in rows],
        )
        conn.commit()

    default_curve = client.get(
        f"/portfolio/accounts/{account['id']}/performance/snapshots"
    )
    latest_two = client.get(
        f"/portfolio/accounts/{account['id']}/performance/snapshots",
        params={"limit": 2},
    )

    assert default_curve.status_code == 200
    assert len(default_curve.json()) == 400
    assert default_curve.json()[0]["snapshot_date"] == rows[5][1]
    assert [row["snapshot_date"] for row in latest_two.json()] == [
        rows[-2][1],
        rows[-1][1],
    ]
    assert client.get(
        f"/portfolio/accounts/{account['id']}/performance/snapshots",
        params={"limit": 1501},
    ).status_code == 422


def test_performance_validation_and_domain_errors_are_mapped(
    api_client,
    monkeypatch,
):
    client, db_path = api_client
    account = _create_account(client)

    assert client.post(
        f"/portfolio/accounts/{account['id']}"
        "/performance/snapshots/not-a-date"
    ).status_code == 422
    assert client.get(
        f"/portfolio/accounts/{account['id']}/performance/summary",
        params={"as_of_date": "2026-02-30"},
    ).status_code == 422
    assert client.get(
        f"/portfolio/accounts/{account['id']}/performance/snapshots",
        params={"start_date": "2026-07-24", "end_date": "2026-07-23"},
    ).status_code == 422
    assert client.get(
        "/portfolio/accounts/999999/performance/summary"
    ).status_code == 404

    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            INSERT INTO stock_daily_prices (symbol, trade_date, close)
            VALUES ('600519', '2026-07-22', 11.0)
            """
        )
        conn.commit()
    assert client.post(
        f"/portfolio/accounts/{account['id']}"
        "/performance/snapshots/2026-07-23"
    ).status_code == 201
    out_of_order = client.post(
        f"/portfolio/accounts/{account['id']}"
        "/performance/snapshots/2026-07-22"
    )
    assert out_of_order.status_code == 409
    assert "later snapshot" in out_of_order.json()["detail"]

    archived = _create_account(client, name="归档账户")
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.execute(
            """
            UPDATE portfolio_accounts
            SET status = 'ARCHIVED', archived_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (archived["id"],),
        )
        conn.commit()
    archived_build = client.post(
        f"/portfolio/accounts/{archived['id']}"
        "/performance/snapshots/2026-07-23"
    )
    assert archived_build.status_code == 422

    class LockedPerformanceService:
        def build_account_snapshot(self, account_id, snapshot_date):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        portfolio_api,
        "_performance_service",
        LockedPerformanceService,
    )
    busy = client.post(
        f"/portfolio/accounts/{account['id']}"
        "/performance/snapshots/2026-07-23"
    )
    assert busy.status_code == 503
    assert busy.json() == {
        "detail": "portfolio database is busy; retry later"
    }
    assert "sqlite" not in busy.text.lower()


def test_exit_plan_api_is_strict_and_rejects_stale_revision(api_client):
    client, _ = api_client
    account = _create_account(client)
    trade = _post_trade(
        client,
        account["id"],
        client_request_id="exit-plan-buy",
    )
    assert trade.status_code == 201, trade.text
    position = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "status": "OPEN"},
    ).json()[0]
    url = (
        f"/portfolio/accounts/{account['id']}/exit-plans/"
        f"{position['symbol']}"
    )
    payload = {
        "position_revision": position["position_revision"],
        "enabled": True,
        "hard_stop_micros": 9_000_000,
        "take_profit_1_micros": 12_000_000,
        "take_profit_1_reduce_bps": 5_000,
        "trailing_activation_bps": 1_000,
        "trailing_distance_bps": 500,
        "time_stop_trading_days": 20,
        "time_stop_min_return_bps": 300,
        "trend_exit_enabled": True,
        "intraday_enabled": True,
    }

    response = client.put(url, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == account["id"]
    assert response.json()["symbol"] == "600519"
    assert response.json()["hard_stop_micros"] == 9_000_000
    assert client.get(
        f"/portfolio/accounts/{account['id']}/exit-plans"
    ).json() == [response.json()]

    extra = client.put(url, json={**payload, "unknown_rule": True})
    assert extra.status_code == 422

    stale = client.put(
        url,
        json={**payload, "position_revision": position["position_revision"] + 1},
    )
    assert stale.status_code == 409

    missing_account = client.get(
        "/portfolio/accounts/999999/exit-plans"
    )
    assert missing_account.status_code == 404


def test_exit_get_endpoints_are_read_only_and_account_scoped(api_client):
    client, db_path = api_client
    first = _create_account(client)
    second = _create_account(client, name="第二账户")
    for account, symbol, request_id in (
        (first, "600519", "exit-read-first"),
        (second, "000001", "exit-read-second"),
    ):
        trade = _post_trade(
            client,
            account["id"],
            client_request_id=request_id,
            symbol=symbol,
        )
        assert trade.status_code == 201, trade.text
        position = client.get(
            "/portfolio/positions",
            params={"account_id": account["id"], "status": "OPEN"},
        ).json()[0]
        response = client.put(
            f"/portfolio/accounts/{account['id']}/exit-plans/{symbol}",
            json={
                "position_revision": position["position_revision"],
                "hard_stop_micros": 9_000_000,
            },
        )
        assert response.status_code == 200, response.text

    with sqlite3.connect(db_path) as conn:
        before = conn.total_changes
        before_counts = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "portfolio_exit_plans",
                "portfolio_exit_states",
                "portfolio_exit_events",
            )
        )

    plans = client.get(
        f"/portfolio/accounts/{first['id']}/exit-plans"
    )
    states = client.get(
        f"/portfolio/accounts/{first['id']}/exit-states"
    )
    events = client.get(
        f"/portfolio/accounts/{first['id']}/exit-events",
        params={"status": "ALL"},
    )
    assert plans.status_code == states.status_code == events.status_code == 200
    assert [row["symbol"] for row in plans.json()] == ["600519"]
    assert states.json() == []
    assert events.json() == []

    with sqlite3.connect(db_path) as conn:
        assert conn.total_changes == before
        assert tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "portfolio_exit_plans",
                "portfolio_exit_states",
                "portfolio_exit_events",
            )
        ) == before_counts


def test_exit_daily_evaluation_and_event_transitions(api_client):
    client, _ = api_client
    account = _create_account(client)
    trade = _post_trade(
        client,
        account["id"],
        client_request_id="exit-daily-buy",
    )
    assert trade.status_code == 201, trade.text
    position = client.get(
        "/portfolio/positions",
        params={"account_id": account["id"], "status": "OPEN"},
    ).json()[0]
    plan = client.put(
        f"/portfolio/accounts/{account['id']}/exit-plans/600519",
        json={
            "position_revision": position["position_revision"],
            "hard_stop_micros": 13_000_000,
            "trend_exit_enabled": False,
        },
    )
    assert plan.status_code == 200, plan.text

    evaluated = client.post(
        f"/portfolio/accounts/{account['id']}/exit-evaluate-daily",
        json={"trade_date": "2026-07-23"},
    )
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["created_event_count"] == 1
    assert evaluated.json()["accounts"][0]["account_id"] == account["id"]

    open_events = client.get(
        f"/portfolio/accounts/{account['id']}/exit-events",
        params={"status": "OPEN"},
    )
    assert open_events.status_code == 200
    event = open_events.json()[0]
    assert event["event_type"] == "HARD_STOP_TRIGGERED"
    assert event["observed_price_micros"] == 12_000_000

    acknowledged = client.post(
        f"/portfolio/exit-events/{event['id']}/acknowledge"
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["event_status"] == "ACKNOWLEDGED"
    resolved = client.post(
        f"/portfolio/exit-events/{event['id']}/resolve"
    )
    assert resolved.status_code == 200
    assert resolved.json()["event_status"] == "RESOLVED"


def test_exit_api_maps_invalid_missing_and_busy_errors(api_client, monkeypatch):
    client, _ = api_client
    account = _create_account(client)

    invalid_status = client.get(
        f"/portfolio/accounts/{account['id']}/exit-events",
        params={"status": "INVALID"},
    )
    assert invalid_status.status_code == 422
    assert client.post(
        "/portfolio/exit-events/999999/acknowledge"
    ).status_code == 404

    class LockedExitService:
        def list_plans(self, *, account_id):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        portfolio_api,
        "_exit_service",
        lambda: LockedExitService(),
        raising=False,
    )
    busy = client.get(
        f"/portfolio/accounts/{account['id']}/exit-plans"
    )
    assert busy.status_code == 503
    assert busy.json() == {
        "detail": "portfolio database is busy; retry later"
    }


def test_import_preview_commit_and_replay(api_client):
    client, db_path = api_client
    account = _create_account(client)
    # API fixtures contain one observed date; derivation may be pending, booking remains atomic.
    payload = {'account_id': account['id'], 'client_request_id': 'api-batch', 'rows': [
        {'row_id':'1','business_type':'BUY','symbol':'600519','trade_date':'2026-07-22',
         'trade_time':'10:00','price_micros':10000000,'quantity':100,'fee_cents':100}]}
    preview = client.post('/portfolio/imports/preview', json=payload)
    assert preview.status_code == 200, preview.text
    draft = preview.json()
    response = client.post('/portfolio/imports/commit', json={'draft_id':draft['draft_id'],
        'version':1,'client_request_id':'api-batch'})
    assert response.status_code == 200, response.text
    assert response.json()['booked_count'] == 1
    retry = client.post('/portfolio/imports/commit', json={'draft_id':draft['draft_id'],
        'version':1,'client_request_id':'api-batch'})
    assert retry.status_code == 200 and retry.json()['duplicate_replay']


def test_import_recognition_resolves_name_without_posting(api_client, monkeypatch):
    client, db_path = api_client
    account = _create_account(client)
    import portfolio.screenshot_parser as parser
    monkeypatch.setattr(parser, 'recognize_images', lambda images, year: {
        'rows':[{'row_id':'a','name':'贵州茅台','symbol':None}],
        'warnings':[], 'source_hashes':['a'*64]})
    result = client.post('/portfolio/imports/recognize', data={'account_id':str(account['id'])},
                         files=[('images',('shot.png',b'small image','image/png'))])
    assert result.status_code == 200, result.text
    assert result.json()['rows'][0]['symbol']=='600519'
    with sqlite3.connect(db_path) as conn:
        assert conn.execute('SELECT count(*) FROM portfolio_transactions').fetchone()[0] == 0


def test_import_rejects_incomplete_and_oversized_upload(api_client):
    client, _ = api_client
    account = _create_account(client)
    result = client.post('/portfolio/imports/preview', json={
        'account_id':account['id'],'client_request_id':'bad','rows':[{'business_type':'BUY'}]})
    assert result.status_code == 422
    result = client.post('/portfolio/imports/recognize', content=b'x',
                         headers={'content-length':str(35*1024*1024)})
    assert result.status_code == 413


def test_statement_names_order_and_cross_month_reversal(api_client):
    client, _ = api_client
    account = _create_account(client)
    buy = _post_trade(client, account['id'], client_request_id='statement-buy',
                      trade_date='2025-12-31')
    assert buy.status_code == 201, buy.text
    reversal = client.post(
        f"/portfolio/transactions/{buy.json()['id']}/reverse",
        json={'account_id': account['id'], 'client_request_id': 'statement-reverse',
              'trade_date': '2026-01-02', 'occurred_at': '2026-01-02T10:00:00+08:00'},
    )
    assert reversal.status_code == 201, reversal.text
    response = client.get('/portfolio/statement', params={'account_id': account['id']})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['truncated'] is False and data['limit'] == 10000
    assert [row['business_type'] for row in data['transactions']] == ['REVERSAL', 'BUY']
    assert data['transactions'][1]['name'] == '贵州茅台'
    assert data['transactions'][1]['is_reversed'] is True
    assert data['transactions'][0]['is_reversed'] is False
    assert client.get('/portfolio/statement', params={'account_id': account['id']}).json() == data


def test_statement_empty_account_isolation_and_validation(api_client):
    client, _ = api_client
    first = _create_account(client)
    second = _create_account(client, name='第二账户')
    assert _post_trade(client, second['id'], client_request_id='statement-other').status_code == 201
    assert client.get('/portfolio/statement', params={'account_id': first['id']}).json() == {
        'transactions': [], 'truncated': False, 'limit': 10000,
    }
    assert client.get('/portfolio/statement', params={'account_id': 999999}).status_code == 404
    for invalid in (0, -1, 'invalid'):
        assert client.get('/portfolio/statement', params={'account_id': invalid}).status_code == 422
    assert client.get('/portfolio/statement').status_code == 422


def test_statement_caps_history_and_reports_truncation(api_client):
    client, db_path = api_client
    account = _create_account(client)
    with sqlite3.connect(db_path) as conn:
        configure_portfolio_connection(conn)
        conn.executemany(
            """
            INSERT INTO portfolio_transactions (
                account_id, client_request_id, business_type, trade_date,
                occurred_at, gross_amount_cents, cash_delta_cents, source
            ) VALUES (?, ?, 'DEPOSIT', '2026-07-21',
                      '2026-07-21T10:00:00+08:00', 1, 1, 'MANUAL')
            """,
            [(account['id'], f'statement-cap-{index}') for index in range(10001)],
        )
        conn.commit()
    response = client.get('/portfolio/statement', params={'account_id': account['id']})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['truncated'] is True and len(data['transactions']) == data['limit'] == 10000
    assert data['transactions'][0]['client_request_id'] == 'statement-cap-10000'
    assert data['transactions'][-1]['client_request_id'] == 'statement-cap-1'


def test_statement_reversal_lookup_uses_index_and_read_closes_connection(api_client, monkeypatch):
    client, db_path = api_client
    account = _create_account(client)
    connections = []
    def tracked_connect():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        connections.append(conn)
        return conn

    monkeypatch.setattr(portfolio_api, '_connect_read', tracked_connect)
    response = client.get('/portfolio/statement', params={'account_id': account['id']})
    assert response.status_code == 200
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')
    with sqlite3.connect(db_path) as conn:
        plan = conn.execute(
            """EXPLAIN QUERY PLAN
            SELECT EXISTS (
                SELECT 1 FROM portfolio_transactions AS reversal
                WHERE reversal.reversal_of = transaction_row.id
                  AND reversal.business_type = 'REVERSAL'
            ) FROM portfolio_transactions AS transaction_row
            """
        ).fetchall()
        assert any('uq_portfolio_transactions_reversal_of' in row[3] for row in plan)
        assert conn.execute('SELECT COUNT(*) FROM portfolio_transactions').fetchone()[0] == 0
