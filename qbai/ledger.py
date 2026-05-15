"""Double-entry ledger core.

Every journal entry is enforced to balance (sum of debits == sum of credits)
inside a single transaction. Entries can be posted directly, or staged as
'proposed' for human approval (used by the AI agent ingestion pipeline).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls

from .coa import require_account
from .db import audit


@dataclass
class JournalLine:
    account_code: str
    debit: int = 0   # cents
    credit: int = 0  # cents
    description: str | None = None

    def __post_init__(self) -> None:
        if self.debit < 0 or self.credit < 0:
            raise ValueError("Line amounts cannot be negative")
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError(
                f"Each line must be either a debit OR a credit "
                f"(account={self.account_code}, debit={self.debit}, credit={self.credit})"
            )


@dataclass
class JournalEntry:
    id: int
    entry_date: str
    memo: str | None
    reference: str | None
    source_type: str | None
    source_id: int | None
    status: str
    created_by: str
    lines: list[dict]


def _today() -> str:
    return date_cls.today().isoformat()


def post_journal_entry(
    conn: sqlite3.Connection,
    *,
    lines: list[JournalLine],
    entry_date: str | None = None,
    memo: str | None = None,
    reference: str | None = None,
    source_type: str | None = "manual",
    source_id: int | None = None,
    created_by: str = "system",
    status: str = "posted",
) -> int:
    """Post (or stage as 'proposed') a balanced journal entry. Returns entry id."""
    if status not in {"posted", "proposed"}:
        raise ValueError(f"status must be 'posted' or 'proposed', got {status!r}")
    if not lines or len(lines) < 2:
        raise ValueError("A journal entry needs at least two lines")

    total_debit = sum(l.debit for l in lines)
    total_credit = sum(l.credit for l in lines)
    if total_debit != total_credit:
        raise ValueError(
            f"Entry does not balance: debits={total_debit} credits={total_credit}"
        )
    if total_debit == 0:
        raise ValueError("Entry total is zero")

    entry_date = entry_date or _today()

    account_ids = {l.account_code: require_account(conn, l.account_code).id for l in lines}

    with conn:
        cur = conn.execute(
            """INSERT INTO journal_entries
               (entry_date, memo, reference, source_type, source_id, status, created_by,
                approved_at, approved_by)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                entry_date, memo, reference, source_type, source_id, status, created_by,
                _today() if status == "posted" else None,
                created_by if status == "posted" else None,
            ),
        )
        entry_id = cur.lastrowid
        for line in lines:
            conn.execute(
                """INSERT INTO journal_lines
                   (journal_entry_id, account_id, debit, credit, description)
                   VALUES (?,?,?,?,?)""",
                (entry_id, account_ids[line.account_code], line.debit, line.credit,
                 line.description),
            )
        audit(conn, created_by, f"je_{status}", "journal_entry", entry_id, memo)
    return entry_id


def approve_proposed(conn: sqlite3.Connection, entry_id: int, approved_by: str) -> None:
    row = conn.execute(
        "SELECT status FROM journal_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"No journal entry {entry_id}")
    if row["status"] != "proposed":
        raise ValueError(f"Entry {entry_id} is not in 'proposed' state (is {row['status']!r})")
    with conn:
        conn.execute(
            "UPDATE journal_entries SET status='posted', approved_at=?, approved_by=? WHERE id=?",
            (_today(), approved_by, entry_id),
        )
        audit(conn, approved_by, "je_approved", "journal_entry", entry_id, None)


def void_entry(conn: sqlite3.Connection, entry_id: int, reason: str, voided_by: str) -> int:
    """Void an entry by posting a mirror-image reversal. Returns reversal entry id."""
    row = conn.execute(
        "SELECT * FROM journal_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"No journal entry {entry_id}")
    if row["status"] != "posted":
        raise ValueError(f"Can only void posted entries (entry {entry_id} is {row['status']!r})")

    lines = conn.execute(
        "SELECT jl.debit, jl.credit, a.code "
        "FROM journal_lines jl JOIN accounts a ON a.id = jl.account_id "
        "WHERE jl.journal_entry_id = ?",
        (entry_id,),
    ).fetchall()

    reversal_lines = [
        JournalLine(account_code=l["code"], debit=l["credit"], credit=l["debit"],
                    description=f"Reversal of JE#{entry_id}")
        for l in lines
    ]
    rev_id = post_journal_entry(
        conn,
        lines=reversal_lines,
        memo=f"Void: {reason}",
        source_type="void",
        source_id=entry_id,
        created_by=voided_by,
    )
    # The original entry stays 'posted' — the reversal is what cancels it out.
    # voided_at / voided_by / reverses_entry_id are metadata flags that the
    # original was voided (so the UI can show a strikethrough, etc.).
    with conn:
        conn.execute(
            "UPDATE journal_entries SET voided_at=?, voided_by=?, "
            "void_reason=?, reverses_entry_id=? WHERE id=?",
            (_today(), voided_by, reason, rev_id, entry_id),
        )
        audit(conn, voided_by, "je_voided", "journal_entry", entry_id, reason)
    return rev_id


def get_entry(conn: sqlite3.Connection, entry_id: int) -> JournalEntry | None:
    row = conn.execute(
        "SELECT * FROM journal_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        return None
    lines = [dict(r) for r in conn.execute(
        "SELECT jl.debit, jl.credit, jl.description, a.code AS account_code, a.name AS account_name "
        "FROM journal_lines jl JOIN accounts a ON a.id = jl.account_id "
        "WHERE jl.journal_entry_id = ? ORDER BY jl.id",
        (entry_id,),
    )]
    return JournalEntry(
        id=row["id"], entry_date=row["entry_date"], memo=row["memo"],
        reference=row["reference"], source_type=row["source_type"],
        source_id=row["source_id"], status=row["status"], created_by=row["created_by"],
        lines=lines,
    )


def list_entries(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict]:
    sql = "SELECT id, entry_date, memo, reference, source_type, status FROM journal_entries"
    where = []
    args: list = []
    if status:
        where.append("status = ?")
        args.append(status)
    if date_from:
        where.append("entry_date >= ?")
        args.append(date_from)
    if date_to:
        where.append("entry_date <= ?")
        args.append(date_to)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY entry_date DESC, id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]
