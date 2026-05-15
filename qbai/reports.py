"""Accounting reports. Only the trial balance lives here in the foundation
slice; P&L, balance sheet, and general ledger arrive in later slices once
A/R, A/P, and expenses land.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class TrialBalanceRow:
    code: str
    name: str
    type: str
    debit: int   # cents
    credit: int  # cents


def trial_balance(conn: sqlite3.Connection, as_of: str | None = None) -> list[TrialBalanceRow]:
    """Net debit/credit balance per account from posted entries only.

    Each account ends up as either a debit OR a credit balance (the side that
    is positive after netting), which is how trial balances are conventionally
    presented.
    """
    args: list = []
    date_clause = ""
    if as_of:
        date_clause = " AND je.entry_date <= ?"
        args.append(as_of)
    sql = f"""
        SELECT a.code, a.name, a.type,
               COALESCE(t.d, 0) AS d,
               COALESCE(t.c, 0) AS c
        FROM accounts a
        LEFT JOIN (
            SELECT jl.account_id,
                   SUM(jl.debit)  AS d,
                   SUM(jl.credit) AS c
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_entry_id
            WHERE je.status = 'posted'{date_clause}
            GROUP BY jl.account_id
        ) t ON t.account_id = a.id
        ORDER BY a.code
    """

    rows = []
    for r in conn.execute(sql, args):
        net = r["d"] - r["c"]
        if net == 0 and r["d"] == 0:
            continue  # account never touched; skip
        debit = net if net > 0 else 0
        credit = -net if net < 0 else 0
        rows.append(TrialBalanceRow(
            code=r["code"], name=r["name"], type=r["type"],
            debit=debit, credit=credit,
        ))
    return rows
