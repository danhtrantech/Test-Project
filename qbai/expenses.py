"""Standalone expense (paid directly, no bill recorded first).

Use case: small purchases on the company credit card or out of checking
that don't go through A/P. Posts a single JE:
    DR <expense account>
    CR <cash/credit-card account>
"""

from __future__ import annotations

import sqlite3

from .coa import require_account
from .ledger import JournalLine, post_journal_entry
from .parties import find_party
from .payments import DEFAULT_CASH_ACCOUNT


def record_expense(
    conn: sqlite3.Connection, *,
    expense_account_code: str, amount_cents: int,
    paid_from_account_code: str = DEFAULT_CASH_ACCOUNT,
    expense_date: str | None = None,
    vendor_name: str | None = None,
    memo: str | None = None, reference: str | None = None,
    actor: str = "system",
) -> int:
    """Returns the journal entry id."""
    if amount_cents <= 0:
        raise ValueError("Expense amount must be positive")
    require_account(conn, expense_account_code)
    require_account(conn, paid_from_account_code)

    party = find_party(conn, vendor_name) if vendor_name else None
    full_memo = memo
    if vendor_name and not full_memo:
        full_memo = f"Expense at {vendor_name}"
    elif vendor_name and full_memo:
        full_memo = f"{full_memo} ({vendor_name})"

    return post_journal_entry(
        conn,
        lines=[
            JournalLine(account_code=expense_account_code, debit=amount_cents,
                        description=memo or vendor_name),
            JournalLine(account_code=paid_from_account_code, credit=amount_cents,
                        description=f"Paid {vendor_name}" if vendor_name else "Cash out"),
        ],
        entry_date=expense_date,
        memo=full_memo,
        reference=reference,
        source_type="expense",
        source_id=party.id if party else None,
        created_by=actor,
    )
