"""Customers and vendors (collectively, 'parties').

A party can be a customer (we invoice them), a vendor (they bill us), or both.
Names are unique within a kind, so 'Acme Corp' can exist as a customer and a
vendor independently if needed (kind='both' avoids that ambiguity).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .db import audit

VALID_KINDS = {"customer", "vendor", "both"}


@dataclass
class Party:
    id: int
    kind: str
    name: str
    email: str | None
    phone: str | None
    address: str | None
    is_active: bool


def _row_to_party(row: sqlite3.Row) -> Party:
    return Party(
        id=row["id"], kind=row["kind"], name=row["name"],
        email=row["email"], phone=row["phone"], address=row["address"],
        is_active=bool(row["is_active"]),
    )


def add_party(
    conn: sqlite3.Connection, name: str, kind: str,
    *, email: str | None = None, phone: str | None = None,
    address: str | None = None, notes: str | None = None,
    actor: str = "system",
) -> Party:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if not name.strip():
        raise ValueError("Party name cannot be empty")
    with conn:
        cur = conn.execute(
            "INSERT INTO parties(kind, name, email, phone, address, notes) "
            "VALUES (?,?,?,?,?,?)",
            (kind, name.strip(), email, phone, address, notes),
        )
        pid = cur.lastrowid
        audit(conn, actor, "party_added", "party", pid, f"{kind}:{name}")
    return require_party(conn, pid)


def get_party(conn: sqlite3.Connection, party_id: int) -> Party | None:
    row = conn.execute("SELECT * FROM parties WHERE id = ?", (party_id,)).fetchone()
    return _row_to_party(row) if row else None


def require_party(conn: sqlite3.Connection, party_id: int) -> Party:
    p = get_party(conn, party_id)
    if p is None:
        raise LookupError(f"No party {party_id}")
    return p


def find_party(conn: sqlite3.Connection, name: str, kind: str | None = None) -> Party | None:
    """Look up by name; if kind given, narrow to that kind (or 'both')."""
    if kind:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
        row = conn.execute(
            "SELECT * FROM parties WHERE name = ? AND kind IN (?, 'both') "
            "AND is_active = 1 ORDER BY id LIMIT 1",
            (name.strip(), kind),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM parties WHERE name = ? AND is_active = 1 ORDER BY id LIMIT 1",
            (name.strip(),),
        ).fetchone()
    return _row_to_party(row) if row else None


def require_party_by_name(conn: sqlite3.Connection, name: str, kind: str | None = None) -> Party:
    p = find_party(conn, name, kind)
    if p is None:
        kind_part = f" ({kind})" if kind else ""
        raise LookupError(f"No active party named {name!r}{kind_part}")
    return p


def list_parties(
    conn: sqlite3.Connection, *, kind: str | None = None, include_inactive: bool = False,
) -> list[Party]:
    sql = "SELECT * FROM parties"
    where = []
    args: list = []
    if kind:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
        where.append("(kind = ? OR kind = 'both')")
        args.append(kind)
    if not include_inactive:
        where.append("is_active = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    return [_row_to_party(r) for r in conn.execute(sql, args)]
