"""Local application account registry."""
import sqlite3
from contextlib import closing
from pathlib import Path


class UserRegistry:
    """Persist preauthorized accounts independently of the market database."""

    def __init__(self, path: Path):
        self.path = path

    def initialize(self, administrator_email: str) -> None:
        email = self.normalize_email(administrator_email)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    subject TEXT UNIQUE,
                    last_login_at TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS personal_stocks (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    symbol TEXT NOT NULL,
                    PRIMARY KEY (user_id, symbol)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id),
                    nickname TEXT NOT NULL,
                    landing TEXT NOT NULL,
                    group_id INTEGER
                )
            """)
            connection.execute("""
                INSERT INTO users (email, role)
                SELECT ?, 'admin' WHERE NOT EXISTS (SELECT 1 FROM users)
            """, (email,))

    def profile(self, user_id: int) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT nickname, landing, group_id FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else {"nickname": "", "landing": "dashboard", "group_id": None}

    def save_profile(self, user_id: int, nickname: str, landing: str, group_id: int | None):
        if group_id is not None:
            self.groups(user_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if group_id is not None and not db.execute(
                "SELECT 1 FROM personal_groups WHERE id = ? AND user_id = ?", (group_id, user_id),
            ).fetchone():
                raise ValueError("Invalid personal group")
            db.execute("""
                INSERT INTO user_profiles (user_id, nickname, landing, group_id) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname,
                    landing=excluded.landing, group_id=excluded.group_id
            """, (user_id, nickname, landing, group_id))

    @staticmethod
    def normalize_email(email: str) -> str:
        email = email.strip().lower()
        local, separator, domain = email.partition("@")
        if not separator or not local or not domain or "@" in domain or len(email) > 254:
            raise ValueError("Invalid email")
        if any(character.isspace() for character in email):
            raise ValueError("Invalid email")
        return email

    def preauthorize(self, email: str) -> int:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO users (email, role) VALUES (?, 'user')",
                (self.normalize_email(email),),
            )
            return cursor.lastrowid

    def set_active(self, user_id: int, active: bool) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT role, active FROM users WHERE id = ?", (user_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Unknown user")
            if not active and row == ("admin", 1):
                count, = connection.execute(
                    "SELECT count(*) FROM users WHERE role = 'admin' AND active = 1"
                ).fetchone()
                if count == 1:
                    raise ValueError("Cannot disable the last active administrator")
            connection.execute("UPDATE users SET active = ? WHERE id = ?", (active, user_id))

    def authenticate(self, email: str, subject: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            email = self.normalize_email(email)
            row = connection.execute("""
                SELECT id, email, role FROM users
                WHERE email = ? AND subject = ? AND active = 1
                  AND last_login_at >= datetime('now', '-5 minutes')
            """, (email, subject)).fetchone()
            if row is None:
                row = connection.execute("""
                    UPDATE users SET subject = ?, last_login_at = CURRENT_TIMESTAMP
                    WHERE email = ? AND active = 1 AND (subject IS NULL OR subject = ?)
                    RETURNING id, email, role
                """, (subject, email, subject)).fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""
                SELECT id, email, role, active, last_login_at FROM users ORDER BY id
            """)
            return [dict(row) for row in rows]

    def personal_stocks(self, user_id: int) -> list[str]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT symbol FROM personal_stocks WHERE user_id = ? ORDER BY symbol",
                (user_id,),
            )
            return [symbol for symbol, in rows]

    def add_personal_stock(self, user_id: int, symbol: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT OR IGNORE INTO personal_stocks (user_id, symbol) VALUES (?, ?)",
                (user_id, symbol),
            )

    def remove_personal_stock(self, user_id: int, symbol: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "DELETE FROM personal_stocks WHERE user_id = ? AND symbol = ?", (user_id, symbol)
            )

    def groups(self, user_id: int) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS personal_groups (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                UNIQUE(user_id, name))""")
            db.execute("""CREATE TABLE IF NOT EXISTS personal_group_members (
                group_id INTEGER NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY(group_id, symbol))""")
            db.execute("INSERT OR IGNORE INTO personal_groups(user_id,name) VALUES (?, 'Favorites')", (user_id,))
            db.execute("""INSERT OR IGNORE INTO personal_group_members
                SELECT g.id,s.symbol FROM personal_stocks s JOIN personal_groups g
                ON g.user_id=s.user_id AND g.name='Favorites' WHERE s.user_id=?""", (user_id,))
            db.execute("DELETE FROM personal_stocks WHERE user_id=?", (user_id,))
            groups = db.execute("SELECT id,name FROM personal_groups WHERE user_id=? ORDER BY id", (user_id,))
            return [{"id": group_id, "name": name, "symbols": [row[0] for row in db.execute(
                "SELECT symbol FROM personal_group_members WHERE group_id=? ORDER BY symbol", (group_id,)
            )]} for group_id,name in groups]

    def change_group(self, user_id: int, action: str, group_id: int = 0, value: str = ""):
        self.groups(user_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if action == "create":
                db.execute("INSERT INTO personal_groups(user_id,name) VALUES (?,?)", (user_id,value))
                return
            group = db.execute("SELECT name FROM personal_groups WHERE user_id=? AND id=?",
                               (user_id,group_id)).fetchone()
            if not group:
                raise LookupError("Unknown personal group")
            if action in {"rename", "delete"} and group[0] == "Favorites":
                raise ValueError("Favorites is the default group")
            if action == "rename":
                db.execute("UPDATE personal_groups SET name=? WHERE id=?", (value,group_id))
            elif action == "delete":
                db.execute("DELETE FROM personal_group_members WHERE group_id=?", (group_id,))
                db.execute("UPDATE user_profiles SET group_id=NULL WHERE user_id=? AND group_id=?",
                           (user_id, group_id))
                db.execute("DELETE FROM personal_groups WHERE id=?", (group_id,))
            elif action == "add":
                db.execute("INSERT OR IGNORE INTO personal_group_members VALUES (?,?)", (group_id,value))
            elif action == "remove":
                db.execute("DELETE FROM personal_group_members WHERE group_id=? AND symbol=?", (group_id,value))
