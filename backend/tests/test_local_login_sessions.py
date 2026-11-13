import sqlite3

from app.core import test_sessions
from app.core.user_registry import UserRegistry


def test_session_lifecycle(tmp_path):
    path = tmp_path / "users.db"
    registry = UserRegistry(path)
    registry.initialize("admin@example.com")
    token = test_sessions.create(path, 1)
    assert test_sessions.lookup(path, token)["role"] == "admin"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT digest FROM local_login_sessions").fetchone()[0] != token
    test_sessions.revoke(path, token)
    assert test_sessions.lookup(path, token) is None



def test_disabled_user_loses_session(tmp_path):
    registry = UserRegistry(tmp_path / "users.db")
    registry.initialize("admin@example.com")
    member = registry.preauthorize("member@example.com")
    token = test_sessions.create(registry.path, member)
    registry.set_active(member, False)
    assert test_sessions.lookup(registry.path, token) is None


def test_relogin_revokes_previous_session(tmp_path):
    path = tmp_path / "users.db"
    UserRegistry(path).initialize("admin@example.com")
    old = test_sessions.create(path, 1)
    new = test_sessions.create(path, 1)
    assert test_sessions.lookup(path, old) is None
    assert test_sessions.lookup(path, new)["id"] == 1
