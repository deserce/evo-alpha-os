"""Cloudflare Access backed account administration."""
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Literal

import jwt
import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictBool

from app.core import test_sessions

from app.core.user_registry import UserRegistry

router = APIRouter(prefix="/users", tags=["users"])
personal_router = APIRouter(prefix="/personal-stocks", tags=["personal stocks"])
ACCESS_SETTINGS = (
    "EVOALPHA_USERS_DB", "EVOALPHA_ADMIN_EMAIL",
    "EVOALPHA_ACCESS_ISSUER", "EVOALPHA_ACCESS_AUDIENCE",
)


@lru_cache(maxsize=4)
def open_registry(path: str, admin: str) -> UserRegistry:
    store = UserRegistry(Path(path))
    store.initialize(admin)
    return store


@lru_cache(maxsize=1)
def access_keys(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", timeout=3)


def registry() -> UserRegistry:
    path = os.getenv("EVOALPHA_USERS_DB")
    admin = os.getenv("EVOALPHA_ADMIN_EMAIL")
    if not path or not admin:
        raise HTTPException(503, "User management is not configured")
    return open_registry(path, admin)


def current_user(
    request: Request, response: Response, cf_access_jwt_assertion: str | None = Header(default=None),
) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    if os.getenv("EVOALPHA_TEST_CREDENTIALS"):
        user = test_sessions.lookup(registry().path, request.cookies.get(test_sessions.COOKIE, ""))
        if not user:
            raise HTTPException(401, "Login required")
        return user
    issuer = os.getenv("EVOALPHA_ACCESS_ISSUER", "").rstrip("/")
    audience = os.getenv("EVOALPHA_ACCESS_AUDIENCE")
    if not audience or not re.fullmatch(r"https://[a-z0-9-]+\.cloudflareaccess\.com", issuer):
        raise HTTPException(503, "User management is not configured")
    if not cf_access_jwt_assertion or len(cf_access_jwt_assertion) > 8192:
        raise HTTPException(401, "Access login required")
    try:
        token = cf_access_jwt_assertion
        if jwt.get_unverified_header(token).get("alg") != "RS256":
            raise jwt.InvalidTokenError("Unexpected signing algorithm")
        key = access_keys(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], issuer=issuer, audience=audience,
            options={"require": ["exp", "iss", "aud", "sub", "email"]},
        )
        email, subject = claims["email"], claims["sub"]
        if not isinstance(email, str) or not isinstance(subject, str) or not subject:
            raise jwt.InvalidTokenError("Invalid user identity")
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid Access login") from exc
    try:
        user = registry().authenticate(email, subject)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(403, "Account unavailable") from exc
    if not user:
        raise HTTPException(403, "Account unavailable")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


class Invitation(BaseModel):
    email: str


class AccountStatus(BaseModel):
    active: StrictBool


class ProfileSettings(BaseModel):
    nickname: str = Field(max_length=32)
    landing: Literal["dashboard", "watchlist"]
    group_id: int | None = Field(default=None, gt=0)


@router.get("/me")
def me(response: Response, user: dict = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {**user, **registry().profile(user["id"])}


@router.patch("/me")
def update_profile(body: ProfileSettings, request: Request, user: dict = Depends(current_user)):
    if os.getenv("EVOALPHA_TEST_CREDENTIALS"):
        require_test_origin(request)
    try:
        registry().save_profile(user["id"], body.nickname.strip(), body.landing, body.group_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"saved": True}


@router.get("")
def list_users(_: dict = Depends(require_admin)):
    return registry().list_users()


_access_sync_lock = Lock()


def sync_invited_users(store: UserRegistry) -> None:
    """Reconcile the dedicated Access policy with enabled ordinary users."""
    url = os.getenv("EVOALPHA_INVITED_POLICY_URL")
    if (
        not url
        or str(store.path.resolve()) != os.getenv("EVOALPHA_ACCESS_SYNC_DB")
        or os.getenv("EVOALPHA_TEST_CREDENTIALS")
    ):
        return
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not token or not re.fullmatch(
        r"https://api\.cloudflare\.com/client/v4/accounts/[0-9a-f]{32}"
        r"/access/apps/[0-9a-f-]{36}/policies/[0-9a-f-]{36}", url
    ):
        raise HTTPException(503, "站内变更已保存，Cloudflare 同步配置不完整")
    with _access_sync_lock:
        emails = [
            user["email"] for user in store.list_users()
            if user["role"] == "user" and user["active"]
        ]
        payload = {
            "name": "EvoAlpha invited users",
            "decision": "allow",
            "precedence": 2,
            "include": [
                {"email": {"email": email}}
                for email in (emails or ["no-invited-users@evoalpha.invalid"])
            ],
        }
        try:
            response = requests.put(
                url, headers={"Authorization": f"Bearer {token}"},
                json=payload, timeout=8, allow_redirects=False,
            )
            if response.ok and response.json().get("success"):
                return
        except (requests.RequestException, ValueError):
            pass
    raise HTTPException(502, "站内变更已保存，Cloudflare 同步失败，请点击重新同步")


@router.post("/sync-access")
def retry_access_sync(_: dict = Depends(require_admin)):
    sync_invited_users(registry())
    return {"synced": True}


@router.post("", status_code=201)
def invite(body: Invitation, _: dict = Depends(require_admin)):
    try:
        store = registry()
        user_id = store.preauthorize(body.email)
    except ValueError as exc:
        raise HTTPException(422, "Invalid email") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Email already registered") from exc

    sync_invited_users(store)
    return {"id": user_id}


@router.patch("/{user_id}")
def set_status(user_id: int, body: AccountStatus, _: dict = Depends(require_admin)):
    try:
        registry().set_active(user_id, body.active)
    except LookupError as exc:
        raise HTTPException(404, "Unknown account") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    sync_invited_users(registry())
    return {"active": body.active}


def enforce_business_access(request: Request, response: Response) -> None:
    """Preserve single-user deployments until application Access is configured."""
    if not any(os.getenv(name) for name in ACCESS_SETTINGS):
        return
    user = current_user(request, response, request.headers.get("cf-access-jwt-assertion"))
    path = request.url.path.removeprefix("/api/v1/")
    if path.startswith("portfolio/") and os.getenv("EVOALPHA_TEST_CREDENTIALS"):
        if user["role"] != "admin" or user["email"] != os.getenv("EVOALPHA_ADMIN_EMAIL"):
            raise HTTPException(403, "Portfolio is restricted to its owner")
    if user["role"] == "admin":
        return
    shared = (
        path.startswith(("watch/", "stock/", "research/", "market/", "sector/", "index/", "etf/"))
        or path in {
            "quant/dates", "quant/lifecycle", "quant/signals", "quant/trade-plans",
            "quant/industry-strength", "quant/industry-stocks",
            "quant/sector-rps-heatmap", "quant/sector-chart",
            "chart-signals", "quant/dashboard",
            "quant/strategy/results", "quant/strategy/meta",
        }
    )
    if request.method != "GET" or not shared:
        raise HTTPException(403, "Administrator access required")


def stock_symbol(symbol: str) -> str:
    if not re.fullmatch(r"[0-9]{6}", symbol):
        raise HTTPException(422, "Expected a six-digit stock code")
    return symbol


@personal_router.get("")
def personal_stocks(user: dict = Depends(current_user)):
    return registry().personal_stocks(user["id"])


@personal_router.put("/{symbol}")
def add_personal_stock(symbol: str, user: dict = Depends(current_user)):
    try:
        registry().add_personal_stock(user["id"], stock_symbol(symbol))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"saved": True}


@personal_router.delete("/{symbol}")
def remove_personal_stock(symbol: str, user: dict = Depends(current_user)):
    registry().remove_personal_stock(user["id"], stock_symbol(symbol))
    return {"saved": True}

class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


def require_test_origin(request: Request) -> None:
    if not os.getenv("EVOALPHA_TEST_CREDENTIALS"):
        raise HTTPException(404)
    if request.headers.get("origin") not in {"http://127.0.0.1:3001", "http://localhost:3001"}:
        raise HTTPException(403, "Invalid origin")


@router.post("/login", dependencies=[Depends(require_test_origin)])
def login(body: Login, response: Response):
    email = body.email.strip().lower()
    if not test_sessions.verify(email, body.password):
        raise HTTPException(401, "Invalid credentials")
    store = registry()
    user = store.authenticate(email, "local-test:" + email)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    token = test_sessions.create(store.path, user["id"])
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(test_sessions.COOKIE, token, httponly=True, samesite="strict")
    return {**user, **store.profile(user["id"])}


@router.post("/logout", dependencies=[Depends(require_test_origin)])
def logout(request: Request, response: Response):
    test_sessions.revoke(registry().path, request.cookies.get(test_sessions.COOKIE, ""))
    response.delete_cookie(test_sessions.COOKIE, httponly=True, samesite="strict")
    return {"ok": True}


class GroupChange(BaseModel):
    action: str
    group_id: int = 0
    value: str = Field(default="", max_length=40)


@personal_router.get("/groups")
def personal_groups(user: dict = Depends(current_user)):
    return registry().groups(user["id"])


@personal_router.post("/groups")
def change_personal_group(body: GroupChange, user: dict = Depends(current_user)):
    value = body.value.strip()
    if body.action not in {"create", "rename", "delete", "add", "remove"}:
        raise HTTPException(422, "Unknown group action")
    if body.action in {"create", "rename"} and not value:
        raise HTTPException(422, "Group name is required")
    if body.action in {"add", "remove"}:
        stock_symbol(value)
    try:
        registry().change_group(user["id"], body.action, body.group_id, value)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, "Group change conflicts with an existing group") from exc
    return {"saved": True}


@personal_router.get("/groups/{group_id}/stocks")
def personal_group_quotes(group_id: int, trade_date: str = "", user: dict = Depends(current_user)):
    from datetime import date
    from app.api.v1.stock import _stock_pool_members

    try:
        target = date.fromisoformat(trade_date).isoformat() if trade_date else None
    except ValueError as exc:
        raise HTTPException(422, "Invalid trading date") from exc
    group = next((item for item in registry().groups(user["id"]) if item["id"] == group_id), None)
    if group is None:
        raise HTTPException(404, "Unknown personal group")
    return _stock_pool_members("personal", target, personal_symbols=group["symbols"])


@personal_router.get("/groups/{group_id}/hints")
def personal_hints(group_id: int, trade_date: str, user: dict = Depends(current_user)):
    from contextlib import closing
    from datetime import date

    try:
        day = date.fromisoformat(trade_date).isoformat()
    except ValueError as exc:
        raise HTTPException(422, "Invalid trading date") from exc
    store = registry()
    group = next((item for item in store.groups(user["id"]) if item["id"] == group_id), None)
    if group is None:
        raise HTTPException(404, "Unknown personal group")
    import json

    with closing(sqlite3.connect(store.path)) as db:
        results = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='personal_watch_results'").fetchone():
            rows = db.execute("""SELECT r.symbol,r.result FROM personal_watch_results r
                JOIN personal_group_members m ON m.symbol=r.symbol
                WHERE m.group_id=? AND r.trade_date=? ORDER BY r.version""", (group_id, day))
            results = {symbol: json.loads(payload) for symbol, payload in rows}
    return [{"symbol": symbol, **{key: results.get(symbol, {}).get(key) for key in (
                "watch_state", "reason", "current_price", "trigger_price", "invalidation_price",
            )}}
            for symbol in group["symbols"]]
