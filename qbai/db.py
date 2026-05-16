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

CREATE TABLE IF NOT EXISTS parties (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK(kind IN ('customer','vendor','both')),
    name        TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    notes       TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, kind)
);
CREATE INDEX IF NOT EXISTS idx_parties_kind ON parties(kind);

CREATE TABLE IF NOT EXISTS invoices (
    id                  INTEGER PRIMARY KEY,
    party_id            INTEGER NOT NULL REFERENCES parties(id),
    number              TEXT,
    invoice_date        TEXT NOT NULL,
    due_date            TEXT,
    memo                TEXT,
    status              TEXT NOT NULL DEFAULT 'draft'
                          CHECK(status IN ('draft','open','paid','voided')),
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    amount_paid_cents   INTEGER NOT NULL DEFAULT 0,
    ar_account_id       INTEGER REFERENCES accounts(id),
    tax_account_id      INTEGER REFERENCES accounts(id),
    posted_je_id        INTEGER REFERENCES journal_entries(id),
    voided_je_id        INTEGER REFERENCES journal_entries(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_invoices_party  ON invoices(party_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id                INTEGER PRIMARY KEY,
    invoice_id        INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    description       TEXT,
    quantity          REAL NOT NULL DEFAULT 1,
    unit_price_cents  INTEGER NOT NULL,
    amount_cents      INTEGER NOT NULL,
    income_account_id INTEGER NOT NULL REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS bills (
    id                  INTEGER PRIMARY KEY,
    party_id            INTEGER NOT NULL REFERENCES parties(id),
    number              TEXT,
    bill_date           TEXT NOT NULL,
    due_date            TEXT,
    memo                TEXT,
    status              TEXT NOT NULL DEFAULT 'draft'
                          CHECK(status IN ('draft','open','paid','voided')),
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    amount_paid_cents   INTEGER NOT NULL DEFAULT 0,
    ap_account_id       INTEGER REFERENCES accounts(id),
    tax_account_id      INTEGER REFERENCES accounts(id),
    posted_je_id        INTEGER REFERENCES journal_entries(id),
    voided_je_id        INTEGER REFERENCES journal_entries(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bills_party  ON bills(party_id);
CREATE INDEX IF NOT EXISTS idx_bills_status ON bills(status);

CREATE TABLE IF NOT EXISTS bill_lines (
    id                 INTEGER PRIMARY KEY,
    bill_id            INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    description        TEXT,
    quantity           REAL NOT NULL DEFAULT 1,
    unit_price_cents   INTEGER NOT NULL,
    amount_cents       INTEGER NOT NULL,
    expense_account_id INTEGER NOT NULL REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS payments (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL CHECK(kind IN ('receive','send')),
    party_id         INTEGER NOT NULL REFERENCES parties(id),
    payment_date     TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    cash_account_id  INTEGER NOT NULL REFERENCES accounts(id),
    memo             TEXT,
    reference        TEXT,
    posted_je_id     INTEGER REFERENCES journal_entries(id),
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_party ON payments(party_id);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id            INTEGER PRIMARY KEY,
    payment_id    INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    invoice_id    INTEGER REFERENCES invoices(id),
    bill_id       INTEGER REFERENCES bills(id),
    amount_cents  INTEGER NOT NULL,
    CHECK((invoice_id IS NULL) <> (bill_id IS NULL))
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
