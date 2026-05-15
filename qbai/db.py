"""SQLite connection and schema for qbai.

Amounts are stored as INTEGER cents (see qbai.money). Date columns are TEXT in
ISO format (YYYY-MM-DD) so they sort lexicographically.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "qbai.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK(type IN ('asset','liability','equity','income','expense')),
    parent_id   INTEGER REFERENCES accounts(id),
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id                  INTEGER PRIMARY KEY,
    entry_date          TEXT NOT NULL,
    memo                TEXT,
    reference           TEXT,
    source_type         TEXT,
    source_id           INTEGER,
    status              TEXT NOT NULL DEFAULT 'posted'
                          CHECK(status IN ('proposed','posted','voided')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          TEXT NOT NULL DEFAULT 'system',
    approved_at         TEXT,
    approved_by         TEXT,
    voided_at           TEXT,
    voided_by           TEXT,
    void_reason         TEXT,
    reverses_entry_id   INTEGER REFERENCES journal_entries(id)
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id                  INTEGER PRIMARY KEY,
    journal_entry_id    INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id          INTEGER NOT NULL REFERENCES accounts(id),
    debit               INTEGER NOT NULL DEFAULT 0,
    credit              INTEGER NOT NULL DEFAULT 0,
    description         TEXT,
    CHECK(debit >= 0 AND credit >= 0),
    CHECK((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))
);

CREATE INDEX IF NOT EXISTS idx_jl_entry   ON journal_lines(journal_entry_id);
CREATE INDEX IF NOT EXISTS idx_jl_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_je_date    ON journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_je_status  ON journal_entries(status);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY,
    timestamp    TEXT NOT NULL DEFAULT (datetime('now')),
    actor        TEXT NOT NULL DEFAULT 'system',
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER,
    details      TEXT
);
"""


def get_connection(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def audit(conn: sqlite3.Connection, actor: str, action: str,
          entity_type: str, entity_id: int | None, details: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log(actor, action, entity_type, entity_id, details) "
        "VALUES (?,?,?,?,?)",
        (actor, action, entity_type, entity_id, details),
    )
