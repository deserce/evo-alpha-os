"""Bounded, versioned screenshot import drafts, separate from the immutable ledger."""

import sqlite3


def ensure_import_schema(conn: sqlite3.Connection) -> None:
    """Create v1 tables; caller owns transaction and connection lifetime."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_import_drafts (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
            account_id INTEGER NOT NULL REFERENCES portfolio_accounts(id),
            version INTEGER NOT NULL CHECK(version > 0),
            ledger_revision INTEGER NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            preview_json TEXT NOT NULL CHECK(json_valid(preview_json)),
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            committed_at INTEGER,
            result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_portfolio_import_expiry
        ON portfolio_import_drafts(expires_at) WHERE committed_at IS NULL
    """)
