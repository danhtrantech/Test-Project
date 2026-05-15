"""Chart of accounts. A pragmatic default set covering the accounts most small
businesses need; users can add/disable accounts via the CLI later.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

DEFAULT_ACCOUNTS: list[tuple[str, str, str]] = [
    # (code, name, type)
    ("1000", "Cash", "asset"),
    ("1010", "Checking Account", "asset"),
    ("1020", "Savings Account", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("1200", "Inventory", "asset"),
    ("1300", "Prepaid Expenses", "asset"),
    ("1500", "Fixed Assets", "asset"),
    ("1510", "Accumulated Depreciation", "asset"),

    ("2000", "Accounts Payable", "liability"),
    ("2100", "Sales Tax Payable", "liability"),
    ("2200", "Payroll Liabilities", "liability"),
    ("2300", "Credit Card Payable", "liability"),
    ("2500", "Loans Payable", "liability"),

    ("3000", "Owner's Equity", "equity"),
    ("3100", "Retained Earnings", "equity"),
    ("3200", "Owner Draws", "equity"),

    ("4000", "Sales Income", "income"),
    ("4100", "Service Income", "income"),
    ("4900", "Other Income", "income"),

    ("5000", "Cost of Goods Sold", "expense"),
    ("6000", "Rent Expense", "expense"),
    ("6100", "Utilities Expense", "expense"),
    ("6200", "Office Supplies", "expense"),
    ("6300", "Meals & Entertainment", "expense"),
    ("6400", "Travel Expense", "expense"),
    ("6500", "Professional Fees", "expense"),
    ("6600", "Wages & Salaries", "expense"),
    ("6700", "Payroll Taxes", "expense"),
    ("6800", "Insurance Expense", "expense"),
    ("6850", "Software & Subscriptions", "expense"),
    ("6900", "Bank Fees", "expense"),
    ("6950", "Depreciation Expense", "expense"),
    ("6999", "Other Expense", "expense"),
]


@dataclass
class Account:
    id: int
    code: str
    name: str
    type: str
    is_active: bool


def seed_chart_of_accounts(conn: sqlite3.Connection) -> int:
    """Idempotent: inserts any missing default accounts. Returns count added."""
    added = 0
    for code, name, type_ in DEFAULT_ACCOUNTS:
        cur = conn.execute("SELECT 1 FROM accounts WHERE code = ?", (code,))
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO accounts(code, name, type) VALUES (?,?,?)",
                (code, name, type_),
            )
            added += 1
    conn.commit()
    return added


def get_account(conn: sqlite3.Connection, code: str) -> Account | None:
    row = conn.execute(
        "SELECT id, code, name, type, is_active FROM accounts WHERE code = ?",
        (code,),
    ).fetchone()
    if row is None:
        return None
    return Account(row["id"], row["code"], row["name"], row["type"], bool(row["is_active"]))


def require_account(conn: sqlite3.Connection, code: str) -> Account:
    acct = get_account(conn, code)
    if acct is None:
        raise LookupError(f"Unknown account code: {code!r}")
    if not acct.is_active:
        raise LookupError(f"Account {code} is inactive")
    return acct


def list_accounts(conn: sqlite3.Connection, type_: str | None = None,
                  include_inactive: bool = False) -> list[Account]:
    sql = "SELECT id, code, name, type, is_active FROM accounts"
    where = []
    args: list = []
    if type_:
        where.append("type = ?")
        args.append(type_)
    if not include_inactive:
        where.append("is_active = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY code"
    return [Account(r["id"], r["code"], r["name"], r["type"], bool(r["is_active"]))
            for r in conn.execute(sql, args)]


def add_account(conn: sqlite3.Connection, code: str, name: str, type_: str) -> Account:
    if type_ not in {"asset", "liability", "equity", "income", "expense"}:
        raise ValueError(f"Invalid account type: {type_!r}")
    conn.execute("INSERT INTO accounts(code, name, type) VALUES (?,?,?)", (code, name, type_))
    conn.commit()
    return require_account(conn, code)
