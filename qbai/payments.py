"""Customer receipts and vendor payments.

receive_payment(): customer pays us. JE: DR Cash, CR A/R (per invoice).
send_payment():    we pay vendor. JE: DR A/P (per bill), CR Cash.

Allocations must sum to the payment amount — no unallocated cash for now
(a "customer credit" feature can come later if needed).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .ap import DEFAULT_AP_ACCOUNT, apply_payment_to_bill, get_bill
from .ar import DEFAULT_AR_ACCOUNT, apply_payment_to_invoice, get_invoice
from .coa import require_account
from .db import audit
from .ledger import JournalLine, post_journal_entry
from .parties import require_party

DEFAULT_CASH_ACCOUNT = "1010"  # Checking Account


@dataclass
class Allocation:
    invoice_id: int | None = None
    bill_id: int | None = None
    amount_cents: int = 0

    def __post_init__(self) -> None:
        if (self.invoice_id is None) == (self.bill_id is None):
            raise ValueError("Allocation must reference exactly one of invoice_id / bill_id")
        if self.amount_cents <= 0:
            raise ValueError("Allocation amount must be positive")


def receive_payment(
    conn: sqlite3.Connection, *,
    party_id: int, amount_cents: int, allocations: list[Allocation],
    payment_date: str | None = None,
    cash_account_code: str = DEFAULT_CASH_ACCOUNT,
    ar_account_code: str = DEFAULT_AR_ACCOUNT,
    memo: str | None = None, reference: str | None = None,
    actor: str = "system",
) -> int:
    return _record_payment(
        conn, kind="receive", party_id=party_id, amount_cents=amount_cents,
        allocations=allocations, payment_date=payment_date,
        cash_account_code=cash_account_code, contra_account_code=ar_account_code,
        memo=memo, reference=reference, actor=actor,
    )


def send_payment(
    conn: sqlite3.Connection, *,
    party_id: int, amount_cents: int, allocations: list[Allocation],
    payment_date: str | None = None,
    cash_account_code: str = DEFAULT_CASH_ACCOUNT,
    ap_account_code: str = DEFAULT_AP_ACCOUNT,
    memo: str | None = None, reference: str | None = None,
    actor: str = "system",
) -> int:
    return _record_payment(
        conn, kind="send", party_id=party_id, amount_cents=amount_cents,
        allocations=allocations, payment_date=payment_date,
        cash_account_code=cash_account_code, contra_account_code=ap_account_code,
        memo=memo, reference=reference, actor=actor,
    )


def _record_payment(
    conn: sqlite3.Connection, *, kind: str, party_id: int, amount_cents: int,
    allocations: list[Allocation], payment_date: str | None,
    cash_account_code: str, contra_account_code: str,
    memo: str | None, reference: str | None, actor: str,
) -> int:
    if amount_cents <= 0:
        raise ValueError("Payment amount must be positive")
    if not allocations:
        raise ValueError("At least one allocation is required")
    alloc_total = sum(a.amount_cents for a in allocations)
    if alloc_total != amount_cents:
        raise ValueError(
            f"Allocations total {alloc_total} != payment amount {amount_cents}"
        )

    party = require_party(conn, party_id)
    cash_acc = require_account(conn, cash_account_code)
    contra_acc = require_account(conn, contra_account_code)
    payment_date = payment_date or _today()

    for a in allocations:
        if kind == "receive":
            if a.invoice_id is None:
                raise ValueError("receive_payment allocations must specify invoice_id")
            inv = get_invoice(conn, a.invoice_id)
            if inv is None:
                raise LookupError(f"No invoice {a.invoice_id}")
            if inv.party_id != party_id:
                raise ValueError(
                    f"Invoice {a.invoice_id} belongs to a different party "
                    f"({inv.party_name}, not {party.name})"
                )
        else:
            if a.bill_id is None:
                raise ValueError("send_payment allocations must specify bill_id")
            bill = get_bill(conn, a.bill_id)
            if bill is None:
                raise LookupError(f"No bill {a.bill_id}")
            if bill.party_id != party_id:
                raise ValueError(
                    f"Bill {a.bill_id} belongs to a different party "
                    f"({bill.party_name}, not {party.name})"
                )

    if kind == "receive":
        je_lines = [JournalLine(account_code=cash_account_code, debit=amount_cents,
                                description=f"Payment from {party.name}")]
        for a in allocations:
            je_lines.append(JournalLine(
                account_code=contra_account_code, credit=a.amount_cents,
                description=f"Applied to invoice #{a.invoice_id}",
            ))
    else:
        je_lines = []
        for a in allocations:
            je_lines.append(JournalLine(
                account_code=contra_account_code, debit=a.amount_cents,
                description=f"Paying bill #{a.bill_id}",
            ))
        je_lines.append(JournalLine(
            account_code=cash_account_code, credit=amount_cents,
            description=f"Payment to {party.name}",
        ))

    with conn:
        cur = conn.execute(
            "INSERT INTO payments(kind, party_id, payment_date, amount_cents, "
            "cash_account_id, memo, reference) VALUES (?,?,?,?,?,?,?)",
            (kind, party_id, payment_date, amount_cents, cash_acc.id, memo, reference),
        )
        payment_id = cur.lastrowid

    # JE posted in its own transaction inside post_journal_entry
    je_id = post_journal_entry(
        conn, lines=je_lines, entry_date=payment_date,
        memo=memo or f"{'Receipt' if kind == 'receive' else 'Payment'} for {party.name}",
        reference=reference, source_type="payment", source_id=payment_id,
        created_by=actor,
    )

    with conn:
        conn.execute("UPDATE payments SET posted_je_id=? WHERE id=?", (je_id, payment_id))
        for a in allocations:
            conn.execute(
                "INSERT INTO payment_allocations(payment_id, invoice_id, bill_id, amount_cents) "
                "VALUES (?,?,?,?)",
                (payment_id, a.invoice_id, a.bill_id, a.amount_cents),
            )
            if kind == "receive":
                apply_payment_to_invoice(conn, a.invoice_id, a.amount_cents)
            else:
                apply_payment_to_bill(conn, a.bill_id, a.amount_cents)
        audit(conn, actor, f"payment_{kind}", "payment", payment_id,
              f"{party.name}:{amount_cents}")
    return payment_id


def get_payment(conn: sqlite3.Connection, payment_id: int) -> dict | None:
    row = conn.execute(
        "SELECT pmt.*, p.name AS party_name FROM payments pmt "
        "JOIN parties p ON p.id = pmt.party_id WHERE pmt.id = ?",
        (payment_id,),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["allocations"] = [
        dict(r) for r in conn.execute(
            "SELECT invoice_id, bill_id, amount_cents FROM payment_allocations "
            "WHERE payment_id = ? ORDER BY id",
            (payment_id,),
        )
    ]
    return out


def list_payments(conn: sqlite3.Connection, *, kind: str | None = None,
                  party_id: int | None = None, limit: int = 100) -> list[dict]:
    sql = ("SELECT pmt.id, pmt.kind, pmt.payment_date, pmt.amount_cents, pmt.memo, "
           "p.name AS party_name FROM payments pmt "
           "JOIN parties p ON p.id = pmt.party_id")
    where = []
    args: list = []
    if kind:
        where.append("pmt.kind = ?")
        args.append(kind)
    if party_id:
        where.append("pmt.party_id = ?")
        args.append(party_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pmt.payment_date DESC, pmt.id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def _today() -> str:
    from datetime import date
    return date.today().isoformat()
