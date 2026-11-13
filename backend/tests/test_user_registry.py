import sqlite3

import pytest

from app.core.user_registry import UserRegistry


def test_initialize_preserves_original_administrator(tmp_path):
    registry = UserRegistry(tmp_path / "users.db")
    registry.initialize(" Admin@Example.com ")
    registry.initialize("another@example.com")

    with sqlite3.connect(registry.path) as connection:
        rows = connection.execute("SELECT email, role FROM users").fetchall()
    assert rows == [("admin@example.com", "admin")]


def test_preauthorize_normalizes_and_rejects_duplicates(tmp_path):
    registry = UserRegistry(tmp_path / "users.db")
    registry.initialize("admin@example.com")
    user_id = registry.preauthorize(" Member@example.com ")
    registry.set_active(user_id, False)
    with pytest.raises(sqlite3.IntegrityError):
        registry.preauthorize("member@example.com")


def test_last_administrator_cannot_be_disabled(tmp_path):
    registry = UserRegistry(tmp_path / "users.db")
    registry.initialize("admin@example.com")
    with pytest.raises(ValueError, match="last active administrator"):
        registry.set_active(1, False)


def test_identity_binding_and_disabled_account(tmp_path):
    registry = UserRegistry(tmp_path / "users.db")
    registry.initialize("admin@example.com")
    member = registry.preauthorize("member@example.com")

    assert registry.authenticate("missing@example.com", "unknown") is None
    assert registry.authenticate("member@example.com", "member-subject")["id"] == member
    assert registry.authenticate("member@example.com", "another-subject") is None
    registry.set_active(member, False)
    assert registry.authenticate("member@example.com", "member-subject") is None


def test_group_migration_and_ownership(tmp_path):
    registry = UserRegistry(tmp_path / "users.db")
    registry.initialize("admin@example.com")
    member = registry.preauthorize("member@example.com")
    registry.add_personal_stock(1, "600519")
    favorite, = registry.groups(1)
    assert favorite["symbols"] == ["600519"]
    assert registry.groups(member)[0]["symbols"] == []
    with pytest.raises(LookupError):
        registry.change_group(member, "remove", favorite["id"], "600519")
    registry.change_group(1, "create", value="Research")
    research = registry.groups(1)[1]
    registry.change_group(1, "add", research["id"], "600519")
    registry.change_group(1, "delete", research["id"])
    assert registry.groups(1) == [favorite]
