"""Password sessions for the loopback-only login test server."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

COOKIE = "evoalpha_test_session"


@lru_cache(maxsize=1)
def credentials():
    return json.loads(Path(os.environ["EVOALPHA_TEST_CREDENTIALS"]).read_text())


def verify(email: str, password: str) -> bool:
    account = credentials().get(email, {})
    salt = bytes.fromhex(account.get("salt", "00" * 16))
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    return hmac.compare_digest(digest, account.get("digest", "0" * 128))


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(path: Path, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS local_login_sessions (
                digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL
            )
        """)
        db.execute("DELETE FROM local_login_sessions WHERE user_id = ?", (user_id,))
        db.execute("INSERT INTO local_login_sessions VALUES (?, ?)", (digest(token), user_id))
    return token


def lookup(path: Path, token: str) -> dict | None:
    if len(token) != 43:
        return None
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'local_login_sessions'").fetchone():
            return None
        row = db.execute("""
            SELECT u.id, u.email, u.role FROM local_login_sessions s JOIN users u ON s.user_id = u.id
            WHERE s.digest = ? AND u.active = 1
        """, (digest(token),)).fetchone()
        return dict(row) if row else None


def revoke(path: Path, token: str) -> None:
    if lookup(path, token):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DELETE FROM local_login_sessions WHERE digest = ?", (digest(token),))
