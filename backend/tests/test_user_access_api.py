from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import users


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    issuer = "https://evoalpha-test.cloudflareaccess.com"
    for name, value in zip(
        users.ACCESS_SETTINGS, (str(tmp_path / "users.db"), "admin@example.com", issuer, "aud"),
    ):
        monkeypatch.setenv(name, value)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(users, "access_keys", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    ))
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(users.personal_router, prefix="/api/v1")
    business = APIRouter(dependencies=[Depends(users.enforce_business_access)])

    @business.get("/quant/sector-rps-heatmap")
    @business.get("/quant/industry-stocks")
    @business.get("/quant/sector-chart")
    @business.post("/quant/sector-rps-heatmap")
    @business.get("/watch/summary")
    @business.post("/stock/pools")
    @business.get("/quant/ai/brief")
    @business.get("/portfolio/accounts")
    def business_handler():
        return {"ok": True}

    app.include_router(business, prefix="/api/v1")

    def token(name, **overrides):
        claims = {
            "iss": issuer, "aud": "aud", "sub": name, "email": f"{name}@example.com",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            **overrides,
        }
        return {"Cf-Access-Jwt-Assertion": jwt.encode(claims, key, algorithm="RS256")}

    with TestClient(app, base_url="http://testserver/api/v1/") as client:
        yield client, token


def test_access_requires_valid_audience_and_registered_account(accounts):
    client, token = accounts
    assert client.get("users/me").status_code == 401
    assert client.get("users/me", headers=token("admin", aud="wrong")).status_code == 401
    assert client.get("users/me", headers=token("unknown")).status_code == 403
    assert client.get("users/me", headers=token("admin")).json()["role"] == "admin"


def test_shared_watchlist_and_administrator_boundary(accounts):
    client, token = accounts
    assert client.post("users", json={"email": "member@example.com"},
                       headers=token("admin")).status_code == 201
    member = token("member")
    assert client.get("watch/summary", headers=member).status_code == 200
    assert client.post("stock/pools", headers=member).status_code == 403
    assert client.get("portfolio/accounts", headers=member).status_code == 403
    assert client.get("quant/ai/brief", headers=member).status_code == 403
    assert client.get("quant/ai/brief", headers=token("admin")).status_code == 200
    assert client.get("users", headers=member).status_code == 403
    assert client.post("stock/pools", headers=token("admin")).status_code == 200


def test_personal_stock_isolation_and_account_revocation(accounts):
    client, token = accounts
    admin = token("admin")
    member_id = client.post("users", json={"email": "member@example.com"}, headers=admin).json()["id"]
    member = token("member")
    assert client.put("personal-stocks/600519", headers=admin).status_code == 200
    assert client.put("personal-stocks/000001", headers=member).status_code == 200
    assert client.put("personal-stocks/invalid", headers=member).status_code == 422
    assert client.delete("personal-stocks/600519", headers=member).status_code == 200
    assert client.get("personal-stocks", headers=admin).json() == ["600519"]
    result = client.get("personal-stocks", headers=member)
    assert result.json() == ["000001"]
    assert result.headers["cache-control"] == "private, no-store"
    assert client.patch(f"users/{member_id}", json={"active": False}, headers=admin).status_code == 200
    assert client.get("personal-stocks", headers=member).status_code == 403
    assert client.get("watch/summary", headers=member).status_code == 403


def test_personal_hint_scope(accounts):
    client, token = accounts
    admin = token("admin")
    assert client.post("users", json={"email": "member@example.com"}, headers=admin).status_code == 201
    group = client.get("personal-stocks/groups", headers=admin).json()[0]
    path = f"personal-stocks/groups/{group['id']}/hints?trade_date=2026-11-12"
    assert client.get(path, headers=admin).json() == []
    assert client.get(path, headers=token("member")).status_code == 404

@pytest.mark.parametrize("path", [
    "quant/sector-rps-heatmap", "quant/industry-stocks", "quant/sector-chart",
])
def test_member_can_read_shared_sector_data(accounts, path):
    client, token = accounts
    assert client.post("users", json={"email": "member@example.com"},
                       headers=token("admin")).status_code == 201
    assert client.get(path).status_code == 401
    assert client.get(path, headers=token("member")).status_code == 200
    assert client.post("quant/sector-rps-heatmap", headers=token("member")).status_code == 403


def test_profile_is_private_and_group_preference_clears_on_delete(accounts):
    client, token = accounts
    admin = token("admin")
    member = token("member")
    assert client.post("users", json={"email": "member@example.com"}, headers=admin).status_code == 201
    store = users.registry()
    member_id = client.get("users/me", headers=member).json()["id"]
    admin_group = store.groups(1)[0]["id"]
    store.change_group(member_id, "create", value="Research")
    group = store.groups(member_id)[1]["id"]

    settings = {"nickname": "  研究员  ", "landing": "watchlist", "group_id": admin_group}
    assert client.patch("users/me", json=settings, headers=member).status_code == 422
    settings["group_id"] = group
    assert client.patch("users/me", json=settings, headers=member).status_code == 200
    profile = client.get("users/me", headers=member).json()
    assert (profile["nickname"], profile["landing"], profile["group_id"]) == ("研究员", "watchlist", group)
    assert client.get("users/me", headers=admin).json()["nickname"] == ""
    store.change_group(member_id, "delete", group)
    assert client.get("users/me", headers=member).json()["group_id"] is None
