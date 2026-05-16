"""Accounts Payable: vendor bills.

Lifecycle mirrors invoices but the JE is the other way around:
    open: DR Expense lines (+ DR Sales/Use Tax if applicable), CR A/P (total)

Tax handling here is simplified: we treat any vendor-charged sales tax as an
expense line; in real bookkeeping it's sometimes recoverable. Users who need
that nuance can split it onto its own line at a recoverable-tax account.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .coa import require_account
from .db import audit
from .ledger import JournalLine, post_journal_entry
from .parties import require_party

DEFAULT_AP_ACCOUNT = "2000"   # Accounts Payable


@dataclass
class BillLine:
    id: int
    description: str | None
    quantity: float
    unit_price_cents: int
    amount_cents: int
    expense_account_code: str


@dataclass
class Bill:
    id: int
    party_id: int
    party_name: str
    number: str | None
    bill_date: str
    due_date: str | None
    memo: str | None
    status: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    amount_paid_cents: int
    posted_je_id: int | None
    voided_je_id: int | None
    lines: list[BillLine]


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def create_bill(
    conn: sqlite3.Connection, *,
    party_id: int, bill_date: str | None = None, due_date: str | None = None,
    number: str | None = None, memo: str | None = None,
    ap_account_code: str = DEFAULT_AP_ACCOUNT, actor: str = "system",
) -> int:
    require_party(conn, party_id)
    ap_acc = require_account(conn, ap_account_code)
    with conn:
        cur = conn.execute(
            "INSERT INTO bills(party_id, number, bill_date, due_date, memo, ap_account_id) "
            "VALUES (?,?,?,?,?,?)",
            (party_id, number, bill_date or _today(), due_date, memo, ap_acc.id),
        )
        bid = cur.lastrowid
        audit(conn, actor, "bill_created", "bill", bid, memo)
    return bid


def add_bill_line(
    conn: sqlite3.Connection, bill_id: int, *,
    description: str | None, quantity: float, unit_price_cents: int,
    expense_account_code: str,
) -> int:
    _require_draft(conn, bill_id)
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if unit_price_cents < 0:
        raise ValueError("unit price cannot be negative")
    acc = require_account(conn, expense_account_code)
    amount_cents = round(quantity * unit_price_cents)
    with conn:
        cur = conn.execute(
            "INSERT INTO bill_lines(bill_id, description, quantity, unit_price_cents, "
            "amount_cents, expense_account_id) VALUES (?,?,?,?,?,?)",
            (bill_id, description, quantity, unit_price_cents, amount_cents, acc.id),
        )
        _recompute_bill_totals(conn, bill_id)
    return cur.lastrowid


def finalize_bill(
    conn: sqlite3.Connection, bill_id: int, *, tax_cents: int = 0,
    tax_account_code: str | None = None, actor: str = "system",
) -> int:
    """Post the JE for this bill and move it to 'open'."""
    bill = _row(conn, bill_id)
    if bill["status"] != "draft":
        raise ValueError(f"Bill {bill_id} is not in draft (is {bill['status']!r})")
    line_rows = conn.execute(
        "SELECT bl.*, a.code AS acc_code FROM bill_lines bl "
        "JOIN accounts a ON a.id = bl.expense_account_id WHERE bill_id = ?",
        (bill_id,),
    ).fetchall()
    if not line_rows:
        raise ValueError(f"Bill {bill_id} has no lines")
    if tax_cents < 0:
        raise ValueError("tax_cents cannot be negative")
    if tax_cents > 0 and tax_account_code is None:
        raise ValueError("tax_account_code is required when tax_cents > 0")

    subtotal = sum(l["amount_cents"] for l in line_rows)
    total = subtotal + tax_cents

    ap_code = conn.execute(
        "SELECT code FROM accounts WHERE id = ?", (bill["ap_account_id"],)
    ).fetchone()["code"]

    je_lines: list[JournalLine] = []
    for l in line_rows:
        je_lines.append(JournalLine(
            account_code=l["acc_code"], debit=l["amount_cents"],
            description=l["description"],
        ))
    tax_acc_id = None
    if tax_cents > 0:
        tax_acc = require_account(conn, tax_account_code)
        tax_acc_id = tax_acc.id
        je_lines.append(JournalLine(
            account_code=tax_account_code, debit=tax_cents, description="Tax on bill",
        ))
    je_lines.append(JournalLine(
        account_code=ap_code, credit=total, description=f"Bill #{bill_id}",
    ))

    je_id = post_journal_entry(
        conn, lines=je_lines, entry_date=bill["bill_date"],
        memo=f"Bill #{bill_id}" + (f": {bill['memo']}" if bill["memo"] else ""),
        reference=bill["number"], source_type="bill", source_id=bill_id, created_by=actor,
    )
    with conn:
        conn.execute(
            "UPDATE bills SET status='open', subtotal_cents=?, tax_cents=?, total_cents=?, "
            "tax_account_id=?, posted_je_id=? WHERE id=?",
            (subtotal, tax_cents, total, tax_acc_id, je_id, bill_id),
        )
        audit(conn, actor, "bill_finalized", "bill", bill_id, f"je={je_id}")
    return je_id


def void_bill(conn: sqlite3.Connection, bill_id: int, *, reason: str,
              actor: str = "system") -> int:
    from .ledger import void_entry

    bill = _row(conn, bill_id)
    if bill["status"] not in {"open", "paid"}:
        raise ValueError(f"Cannot void bill in {bill['status']!r} state")
    if bill["posted_je_id"] is None:
        raise ValueError(f"Bill {bill_id} has no posted JE to reverse")
    rev_id = void_entry(conn, bill["posted_je_id"], reason=reason, voided_by=actor)
    with conn:
        conn.execute(
            "UPDATE bills SET status='voided', voided_je_id=? WHERE id=?",
            (rev_id, bill_id),
        )
        audit(conn, actor, "bill_voided", "bill", bill_id, reason)
    return rev_id


def apply_payment_to_bill(
    conn: sqlite3.Connection, bill_id: int, amount_cents: int,
) -> None:
    bill = _row(conn, bill_id)
    if bill["status"] not in {"open", "paid"}:
        raise ValueError(f"Cannot apply payment to bill in {bill['status']!r} state")
    new_paid = bill["amount_paid_cents"] + amount_cents
    if new_paid > bill["total_cents"]:
        raise ValueError(
            f"Payment of {amount_cents} would overpay bill {bill_id} "
            f"(total={bill['total_cents']}, already paid={bill['amount_paid_cents']})"
        )
    new_status = "paid" if new_paid == bill["total_cents"] else "open"
    conn.execute(
        "UPDATE bills SET amount_paid_cents=?, status=? WHERE id=?",
        (new_paid, new_status, bill_id),
    )


def get_bill(conn: sqlite3.Connection, bill_id: int) -> Bill | None:
    row = conn.execute(
        "SELECT b.*, p.name AS party_name FROM bills b "
        "JOIN parties p ON p.id = b.party_id WHERE b.id = ?",
        (bill_id,),
    ).fetchone()
    if row is None:
        return None
    lines = [
        BillLine(
            id=l["id"], description=l["description"], quantity=l["quantity"],
            unit_price_cents=l["unit_price_cents"], amount_cents=l["amount_cents"],
            expense_account_code=l["acc_code"],
        )
        for l in conn.execute(
            "SELECT bl.*, a.code AS acc_code FROM bill_lines bl "
            "JOIN accounts a ON a.id = bl.expense_account_id "
            "WHERE bill_id = ? ORDER BY bl.id", (bill_id,),
        )
    ]
    return Bill(
        id=row["id"], party_id=row["party_id"], party_name=row["party_name"],
        number=row["number"], bill_date=row["bill_date"], due_date=row["due_date"],
        memo=row["memo"], status=row["status"], subtotal_cents=row["subtotal_cents"],
        tax_cents=row["tax_cents"], total_cents=row["total_cents"],
        amount_paid_cents=row["amount_paid_cents"], posted_je_id=row["posted_je_id"],
        voided_je_id=row["voided_je_id"], lines=lines,
    )


def list_bills(
    conn: sqlite3.Connection, *, status: str | None = None,
    party_id: int | None = None, limit: int = 100,
) -> list[dict]:
    sql = ("SELECT b.id, b.number, b.bill_date, b.due_date, b.status, b.total_cents, "
           "b.amount_paid_cents, p.name AS party_name "
           "FROM bills b JOIN parties p ON p.id = b.party_id")
    where = []
    args: list = []
    if status:
        where.append("b.status = ?")
        args.append(status)
    if party_id:
        where.append("b.party_id = ?")
        args.append(party_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY b.bill_date DESC, b.id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def open_bills_for_party(conn: sqlite3.Connection, party_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, number, bill_date, total_cents, amount_paid_cents, "
        "(total_cents - amount_paid_cents) AS balance_cents "
        "FROM bills WHERE party_id = ? AND status = 'open' "
        "ORDER BY bill_date, id",
        (party_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _row(conn: sqlite3.Connection, bill_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if row is None:
        raise LookupError(f"No bill {bill_id}")
    return row


def _require_draft(conn: sqlite3.Connection, bill_id: int) -> None:
    bill = _row(conn, bill_id)
    if bill["status"] != "draft":
        raise ValueError(f"Bill {bill_id} is not editable (status={bill['status']!r})")


def _recompute_bill_totals(conn: sqlite3.Connection, bill_id: int) -> None:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM bill_lines WHERE bill_id = ?",
        (bill_id,),
    ).fetchone()
    subtotal = row["s"]
    conn.execute(
        "UPDATE bills SET subtotal_cents=?, total_cents=? WHERE id=?",
        (subtotal, subtotal, bill_id),
    )
