"""Isolated account API for EvoAlpha's local login test environment."""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import api_v1_router, watch
from app.api.v1.users import registry
from app.core.config import settings
from pathlib import Path

if not all(os.getenv(key) for key in (
    "EVOALPHA_TEST_CREDENTIALS", "EVOALPHA_USERS_DB", "EVOALPHA_ADMIN_EMAIL",
)):
    raise RuntimeError("Login test environment requires a separate account configuration")

registry()
watch.WATCH_DB_PATH = Path(settings.LOCAL_DB_PATH)
app = FastAPI()
app.include_router(api_v1_router)


@app.middleware("http")
async def restrict_test_writes(request: Request, call_next):
    path = request.url.path
    if request.method not in {"GET", "HEAD"} and not (
        path.startswith("/api/v1/users/") or path.startswith("/api/v1/personal-stocks/")
        or path == "/api/v1/users"
        or path == "/api/v1/stock/pools" or path.startswith("/api/v1/stock/pools/")
    ):
        return JSONResponse({"detail": "Shared local data is read-only in this test server"}, 403)
    return await call_next(request)
