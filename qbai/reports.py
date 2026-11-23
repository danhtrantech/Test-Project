"""Accounting reports: trial balance, P&L, balance sheet, general ledger.

All reports look only at `status='posted'` journal entries so AI-proposed
entries don't leak into financials until approved.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class TrialBalanceRow:
    code: str
    name: str
    type: str
    debit: int   # cents
    credit: int  # cents


def _account_totals(
    conn: sqlite3.Connection, *, date_from: str | None = None,
    date_to: str | None = None,
) -> list[sqlite3.Row]:
    """Per-account summed debits + credits from posted entries over a window.

    Returns one row per account (even accounts with no activity) with columns:
      code, name, type, d (debit total), c (credit total).
    """
    args: list = []
    where = ["je.status = 'posted'"]
    if date_from:
        where.append("je.entry_date >= ?")
        args.append(date_from)
    if date_to:
        where.append("je.entry_date <= ?")
        args.append(date_to)
    where_clause = " AND ".join(where)
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
            WHERE {where_clause}
            GROUP BY jl.account_id
        ) t ON t.account_id = a.id
        ORDER BY a.code
    """
    return conn.execute(sql, args).fetchall()


# ---- Trial Balance -------------------------------------------------------

def trial_balance(conn: sqlite3.Connection, as_of: str | None = None) -> list[TrialBalanceRow]:
    """Net debit/credit balance per account. Each account rolls up to either
    a debit or a credit balance (the side that is positive after netting)."""
    rows = []
    for r in _account_totals(conn, date_to=as_of):
        net = r["d"] - r["c"]
        if net == 0 and r["d"] == 0:
            continue
        debit = net if net > 0 else 0
        credit = -net if net < 0 else 0
        rows.append(TrialBalanceRow(
            code=r["code"], name=r["name"], type=r["type"],
            debit=debit, credit=credit,
        ))
    return rows


# ---- Profit & Loss -------------------------------------------------------

@dataclass
class PnLRow:
    code: str
    name: str
    amount: int  # cents, natural-sign for the account type


@dataclass
class ProfitLoss:
    period_start: str | None
    period_end: str | None
    income: list[PnLRow] = field(default_factory=list)
    expenses: list[PnLRow] = field(default_factory=list)
    total_income: int = 0
    total_expenses: int = 0
    net_income: int = 0


def profit_and_loss(
    conn: sqlite3.Connection, *,
    date_from: str | None = None, date_to: str | None = None,
) -> ProfitLoss:
    """Income and expense activity over a period. Net income = income - expenses.

    For income accounts, natural sign is credit-positive (so amount = C - D).
    For expense accounts, natural sign is debit-positive (so amount = D - C).
    """
    pnl = ProfitLoss(period_start=date_from, period_end=date_to)
    for r in _account_totals(conn, date_from=date_from, date_to=date_to):
        d, c = r["d"], r["c"]
        if d == 0 and c == 0:
            continue
        if r["type"] == "income":
            amount = c - d
            if amount != 0:
                pnl.income.append(PnLRow(r["code"], r["name"], amount))
                pnl.total_income += amount
        elif r["type"] == "expense":
            amount = d - c
            if amount != 0:
                pnl.expenses.append(PnLRow(r["code"], r["name"], amount))
                pnl.total_expenses += amount
    pnl.net_income = pnl.total_income - pnl.total_expenses
    return pnl


# ---- Balance Sheet -------------------------------------------------------

@dataclass
class BalanceSheetSection:
    rows: list[PnLRow] = field(default_factory=list)
    total: int = 0


@dataclass
class BalanceSheet:
    as_of: str | None
    assets: BalanceSheetSection = field(default_factory=BalanceSheetSection)
    liabilities: BalanceSheetSection = field(default_factory=BalanceSheetSection)
    equity: BalanceSheetSection = field(default_factory=BalanceSheetSection)
    net_income_to_date: int = 0           # included inside equity.total
    total_liabilities_and_equity: int = 0


def balance_sheet(conn: sqlite3.Connection, as_of: str | None = None) -> BalanceSheet:
    """Snapshot as of a date. Assets = Liabilities + Equity (with
    current-period net income rolled into equity).

    This follows the identity enforced by the double-entry ledger — if the
    ledger balances, this report balances.
    """
    bs = BalanceSheet(as_of=as_of)
    net_income_td = 0
    for r in _account_totals(conn, date_to=as_of):
        d, c = r["d"], r["c"]
        if d == 0 and c == 0:
            continue
        t = r["type"]
        if t == "asset":
            amount = d - c   # debit-positive
            if amount != 0:
                bs.assets.rows.append(PnLRow(r["code"], r["name"], amount))
                bs.assets.total += amount
        elif t == "liability":
            amount = c - d   # credit-positive
            if amount != 0:
                bs.liabilities.rows.append(PnLRow(r["code"], r["name"], amount))
                bs.liabilities.total += amount
        elif t == "equity":
            amount = c - d
            if amount != 0:
                bs.equity.rows.append(PnLRow(r["code"], r["name"], amount))
                bs.equity.total += amount
        elif t == "income":
            net_income_td += (c - d)
        elif t == "expense":
            net_income_td -= (d - c)

    bs.net_income_to_date = net_income_td
    bs.equity.total += net_income_td
    bs.total_liabilities_and_equity = bs.liabilities.total + bs.equity.total
    return bs


# ---- General Ledger ------------------------------------------------------

@dataclass
class GLEntry:
    entry_id: int
    date: str
    memo: str | None
    reference: str | None
    debit: int
    credit: int
    balance: int   # running balance, natural sign for the account type
    description: str | None


@dataclass
class GLAccount:
    code: str
    name: str
    type: str
    opening_balance: int
    entries: list[GLEntry]
    closing_balance: int


def _natural_sign_delta(type_: str, debit: int, credit: int) -> int:
    if type_ in ("asset", "expense"):
        return debit - credit
    return credit - debit


def general_ledger(
    conn: sqlite3.Connection, *,
    account_code: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
) -> list[GLAccount]:
    """Per-account transaction register with running balance.

    If `account_code` is given, returns just that account. Otherwise returns
    every account that has activity in the window (or a nonzero opening
    balance). `date_from` is NOT a cutoff on the running balance — anything
    before it is summed into the opening balance.
    """
    acct_filter = ""
    acct_args: list = []
    if account_code:
        acct_filter = " AND a.code = ?"
        acct_args.append(account_code)

    # Opening balances: everything strictly before date_from.
    opening_by_code: dict[str, int] = {}
    if date_from:
        rows = _account_totals(conn, date_to=_prev_day(date_from))
        for r in rows:
            if account_code and r["code"] != account_code:
                continue
            opening_by_code[r["code"]] = _natural_sign_delta(r["type"], r["d"], r["c"])

    # Transactions in window.
    window_sql = (
        "SELECT a.code, a.name, a.type, je.id AS entry_id, je.entry_date, "
        "je.memo, je.reference, jl.debit, jl.credit, jl.description "
        "FROM accounts a "
        "JOIN journal_lines jl ON jl.account_id = a.id "
        "JOIN journal_entries je ON je.id = jl.journal_entry_id AND je.status = 'posted' "
        "WHERE 1=1" + acct_filter
    )
    args: list = list(acct_args)
    if date_from:
        window_sql += " AND je.entry_date >= ?"
        args.append(date_from)
    if date_to:
        window_sql += " AND je.entry_date <= ?"
        args.append(date_to)
    window_sql += " ORDER BY a.code, je.entry_date, je.id, jl.id"

    by_acct: dict[str, dict] = {}
    for r in conn.execute(window_sql, args):
        code = r["code"]
        acct = by_acct.setdefault(code, {
            "name": r["name"], "type": r["type"],
            "entries": [],
            "running": opening_by_code.get(code, 0),
        })
        acct["running"] += _natural_sign_delta(r["type"], r["debit"], r["credit"])
        acct["entries"].append(GLEntry(
            entry_id=r["entry_id"], date=r["entry_date"], memo=r["memo"],
            reference=r["reference"], debit=r["debit"], credit=r["credit"],
            balance=acct["running"], description=r["description"],
        ))

    # Include account_code even if it has no activity in-window.
    if account_code and account_code not in by_acct:
        row = conn.execute(
            "SELECT code, name, type FROM accounts WHERE code = ?", (account_code,)
        ).fetchone()
        if row is not None:
            by_acct[account_code] = {
                "name": row["name"], "type": row["type"],
                "entries": [], "running": opening_by_code.get(account_code, 0),
            }

    out: list[GLAccount] = []
    for code in sorted(by_acct):
        info = by_acct[code]
        opening = opening_by_code.get(code, 0)
        out.append(GLAccount(
            code=code, name=info["name"], type=info["type"],
            opening_balance=opening, entries=info["entries"],
            closing_balance=info["running"],
        ))
    return out


def _prev_day(iso_date: str) -> str:
    from datetime import date, timedelta
    y, m, d = iso_date.split("-")
    return (date(int(y), int(m), int(d)) - timedelta(days=1)).isoformat()
