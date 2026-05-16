"""Accounts Receivable: customer invoices.

Lifecycle:
    draft   - editable, lines can be added/removed, no JE yet
    open    - finalized; a balanced JE has been posted to the ledger
              (DR A/R, CR Income lines, CR Sales Tax Payable if tax)
    paid    - all of total_cents has been allocated by payments
    voided  - reversed via a mirror-image JE; original stays in books

The invoice is the business-level object the AI agent will create; the JE is
its accounting consequence. Both are linked so we can drill from one to the
other.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .coa import require_account
from .db import audit
from .ledger import JournalLine, post_journal_entry
from .parties import require_party

DEFAULT_AR_ACCOUNT = "1100"          # Accounts Receivable
DEFAULT_TAX_ACCOUNT = "2100"         # Sales Tax Payable


@dataclass
class InvoiceLine:
    id: int
    description: str | None
    quantity: float
    unit_price_cents: int
    amount_cents: int
    income_account_code: str


@dataclass
class Invoice:
    id: int
    party_id: int
    party_name: str
    number: str | None
    invoice_date: str
    due_date: str | None
    memo: str | None
    status: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    amount_paid_cents: int
    posted_je_id: int | None
    voided_je_id: int | None
    lines: list[InvoiceLine]


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def create_invoice(
    conn: sqlite3.Connection,
    *,
    party_id: int,
    invoice_date: str | None = None,
    due_date: str | None = None,
    number: str | None = None,
    memo: str | None = None,
    ar_account_code: str = DEFAULT_AR_ACCOUNT,
    actor: str = "system",
) -> int:
    require_party(conn, party_id)
    ar_acc = require_account(conn, ar_account_code)
    with conn:
        cur = conn.execute(
            "INSERT INTO invoices(party_id, number, invoice_date, due_date, memo, "
            "ar_account_id) VALUES (?,?,?,?,?,?)",
            (party_id, number, invoice_date or _today(), due_date, memo, ar_acc.id),
        )
        inv_id = cur.lastrowid
        audit(conn, actor, "invoice_created", "invoice", inv_id, memo)
    return inv_id


def add_invoice_line(
    conn: sqlite3.Connection, invoice_id: int, *,
    description: str | None, quantity: float, unit_price_cents: int,
    income_account_code: str,
) -> int:
    _require_draft(conn, invoice_id)
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if unit_price_cents < 0:
        raise ValueError("unit price cannot be negative")
    acc = require_account(conn, income_account_code)
    amount_cents = round(quantity * unit_price_cents)
    with conn:
        cur = conn.execute(
            "INSERT INTO invoice_lines(invoice_id, description, quantity, "
            "unit_price_cents, amount_cents, income_account_id) VALUES (?,?,?,?,?,?)",
            (invoice_id, description, quantity, unit_price_cents, amount_cents, acc.id),
        )
        _recompute_invoice_totals(conn, invoice_id)
    return cur.lastrowid


def finalize_invoice(
    conn: sqlite3.Connection, invoice_id: int, *,
    tax_cents: int = 0, tax_account_code: str = DEFAULT_TAX_ACCOUNT,
    actor: str = "system",
) -> int:
    """Post the JE for this invoice and move it to 'open'. Returns JE id."""
    inv = _row(conn, invoice_id)
    if inv["status"] != "draft":
        raise ValueError(f"Invoice {invoice_id} is not in draft (is {inv['status']!r})")
    line_rows = conn.execute(
        "SELECT il.*, a.code AS acc_code FROM invoice_lines il "
        "JOIN accounts a ON a.id = il.income_account_id WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchall()
    if not line_rows:
        raise ValueError(f"Invoice {invoice_id} has no lines")
    if tax_cents < 0:
        raise ValueError("tax_cents cannot be negative")

    subtotal = sum(l["amount_cents"] for l in line_rows)
    total = subtotal + tax_cents

    ar_code = conn.execute(
        "SELECT code FROM accounts WHERE id = ?", (inv["ar_account_id"],)
    ).fetchone()["code"]

    tax_acc_id = None
    je_lines: list[JournalLine] = [JournalLine(account_code=ar_code, debit=total,
                                                description=f"Invoice #{invoice_id}")]
    # Credit each income account line by line (preserves traceability per income type).
    for l in line_rows:
        je_lines.append(JournalLine(
            account_code=l["acc_code"], credit=l["amount_cents"],
            description=l["description"],
        ))
    if tax_cents > 0:
        tax_acc = require_account(conn, tax_account_code)
        tax_acc_id = tax_acc.id
        je_lines.append(JournalLine(
            account_code=tax_account_code, credit=tax_cents, description="Sales tax",
        ))

    je_id = post_journal_entry(
        conn, lines=je_lines, entry_date=inv["invoice_date"],
        memo=f"Invoice #{invoice_id}" + (f": {inv['memo']}" if inv["memo"] else ""),
        reference=inv["number"], source_type="invoice", source_id=invoice_id,
        created_by=actor,
    )
    with conn:
        conn.execute(
            "UPDATE invoices SET status='open', subtotal_cents=?, tax_cents=?, "
            "total_cents=?, tax_account_id=?, posted_je_id=? WHERE id=?",
            (subtotal, tax_cents, total, tax_acc_id, je_id, invoice_id),
        )
        audit(conn, actor, "invoice_finalized", "invoice", invoice_id, f"je={je_id}")
    return je_id


def void_invoice(conn: sqlite3.Connection, invoice_id: int, *, reason: str,
                 actor: str = "system") -> int:
    """Void an open or paid invoice by reversing its JE."""
    from .ledger import void_entry  # local import to avoid cycle at module load

    inv = _row(conn, invoice_id)
    if inv["status"] not in {"open", "paid"}:
        raise ValueError(f"Cannot void invoice in {inv['status']!r} state")
    if inv["posted_je_id"] is None:
        raise ValueError(f"Invoice {invoice_id} has no posted JE to reverse")
    rev_id = void_entry(conn, inv["posted_je_id"], reason=reason, voided_by=actor)
    with conn:
        conn.execute(
            "UPDATE invoices SET status='voided', voided_je_id=? WHERE id=?",
            (rev_id, invoice_id),
        )
        audit(conn, actor, "invoice_voided", "invoice", invoice_id, reason)
    return rev_id


def apply_payment_to_invoice(
    conn: sqlite3.Connection, invoice_id: int, amount_cents: int,
) -> None:
    """Internal: called by payments module when allocating cash. Updates
    amount_paid and flips to 'paid' once fully covered."""
    inv = _row(conn, invoice_id)
    if inv["status"] not in {"open", "paid"}:
        raise ValueError(f"Cannot apply payment to invoice in {inv['status']!r} state")
    new_paid = inv["amount_paid_cents"] + amount_cents
    if new_paid > inv["total_cents"]:
        raise ValueError(
            f"Payment of {amount_cents} would overpay invoice {invoice_id} "
            f"(total={inv['total_cents']}, already paid={inv['amount_paid_cents']})"
        )
    new_status = "paid" if new_paid == inv["total_cents"] else "open"
    conn.execute(
        "UPDATE invoices SET amount_paid_cents=?, status=? WHERE id=?",
        (new_paid, new_status, invoice_id),
    )


def get_invoice(conn: sqlite3.Connection, invoice_id: int) -> Invoice | None:
    row = conn.execute(
        "SELECT i.*, p.name AS party_name FROM invoices i "
        "JOIN parties p ON p.id = i.party_id WHERE i.id = ?",
        (invoice_id,),
    ).fetchone()
    if row is None:
        return None
    lines = [
        InvoiceLine(
            id=l["id"], description=l["description"], quantity=l["quantity"],
            unit_price_cents=l["unit_price_cents"], amount_cents=l["amount_cents"],
            income_account_code=l["acc_code"],
        )
        for l in conn.execute(
            "SELECT il.*, a.code AS acc_code FROM invoice_lines il "
            "JOIN accounts a ON a.id = il.income_account_id "
            "WHERE invoice_id = ? ORDER BY il.id", (invoice_id,),
        )
    ]
    return Invoice(
        id=row["id"], party_id=row["party_id"], party_name=row["party_name"],
        number=row["number"], invoice_date=row["invoice_date"], due_date=row["due_date"],
        memo=row["memo"], status=row["status"], subtotal_cents=row["subtotal_cents"],
        tax_cents=row["tax_cents"], total_cents=row["total_cents"],
        amount_paid_cents=row["amount_paid_cents"], posted_je_id=row["posted_je_id"],
        voided_je_id=row["voided_je_id"], lines=lines,
    )


def list_invoices(
    conn: sqlite3.Connection, *, status: str | None = None,
    party_id: int | None = None, limit: int = 100,
) -> list[dict]:
    sql = ("SELECT i.id, i.number, i.invoice_date, i.due_date, i.status, "
           "i.total_cents, i.amount_paid_cents, p.name AS party_name "
           "FROM invoices i JOIN parties p ON p.id = i.party_id")
    where = []
    args: list = []
    if status:
        where.append("i.status = ?")
        args.append(status)
    if party_id:
        where.append("i.party_id = ?")
        args.append(party_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i.invoice_date DESC, i.id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def open_invoices_for_party(conn: sqlite3.Connection, party_id: int) -> list[dict]:
    """Used by the payment-receive flow to show what's owed."""
    rows = conn.execute(
        "SELECT id, number, invoice_date, total_cents, amount_paid_cents, "
        "(total_cents - amount_paid_cents) AS balance_cents "
        "FROM invoices WHERE party_id = ? AND status = 'open' "
        "ORDER BY invoice_date, id",
        (party_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _row(conn: sqlite3.Connection, invoice_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        raise LookupError(f"No invoice {invoice_id}")
    return row


def _require_draft(conn: sqlite3.Connection, invoice_id: int) -> None:
    inv = _row(conn, invoice_id)
    if inv["status"] != "draft":
        raise ValueError(f"Invoice {invoice_id} is not editable (status={inv['status']!r})")


def _recompute_invoice_totals(conn: sqlite3.Connection, invoice_id: int) -> None:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM invoice_lines WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()
    subtotal = row["s"]
    conn.execute(
        "UPDATE invoices SET subtotal_cents=?, total_cents=? WHERE id=?",
        (subtotal, subtotal, invoice_id),  # tax stays 0 until finalize
    )
