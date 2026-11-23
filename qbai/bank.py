"""Bank-statement CSV ingestion.

Takes a CSV with dated bank transactions and produces one proposed journal
entry per row. The user reviews each proposal before anything hits the books.

Expected CSV columns (header-based, case-insensitive; aliases supported):
    date        -> transaction date (any parseable form)
    description -> free-text description
    amount      -> signed decimal. Positive = money IN, negative = money OUT.
                   Or use separate 'debit' / 'credit' columns if the bank's
                   file is laid out that way.

Posting convention: this is the BANK's side of the view. "Money in" on the
bank statement is a DEBIT to our cash account; "money out" is a CREDIT.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import documents
from .coa import list_accounts
from .db import audit
from .extract import EXPENSE_KEYWORDS
from .ledger import JournalLine, post_journal_entry
from .money import to_cents


# Flexible header mapping.
DATE_KEYS = {"date", "transaction date", "posted date", "posting date", "txn date"}
DESC_KEYS = {"description", "memo", "payee", "details", "narrative", "name"}
AMOUNT_KEYS = {"amount", "transaction amount", "net"}
DEBIT_KEYS = {"debit", "withdrawal", "withdrawals", "money out", "out"}
CREDIT_KEYS = {"credit", "deposit", "deposits", "money in", "in"}


@dataclass
class CsvRow:
    date: str
    description: str
    cents: int   # signed: positive = money IN (debit to cash), negative = money OUT


@dataclass
class IngestResult:
    doc_id: int
    rows_parsed: int
    proposed_je_ids: list[int]
    skipped: list[tuple[int, str]]   # (row_number, reason)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _parse_amount(s: str | None) -> int:
    if not s:
        return 0
    s = s.strip().replace("$", "").replace(",", "")
    if not s:
        return 0
    # Handle parenthesis notation for negatives, e.g. "(42.99)"
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"bad amount: {s!r}") from e
    return to_cents(d)


DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y",
    "%Y/%m/%d", "%b %d %Y", "%d %b %Y", "%B %d %Y",
]


def _parse_date(s: str | None) -> str:
    if not s:
        raise ValueError("empty date")
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: {s!r}")


def parse_csv(path: str | Path) -> list[CsvRow]:
    """Best-effort flexible CSV parse. Raises ValueError with row context on bad data."""
    rows: list[CsvRow] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        fields = {_norm(k): k for k in reader.fieldnames}
        date_col = _first_match(fields, DATE_KEYS)
        desc_col = _first_match(fields, DESC_KEYS)
        amt_col = _first_match(fields, AMOUNT_KEYS)
        debit_col = _first_match(fields, DEBIT_KEYS)
        credit_col = _first_match(fields, CREDIT_KEYS)
        if not date_col or not desc_col:
            raise ValueError(
                f"CSV must have a date and description column. Saw: {reader.fieldnames}"
            )
        if not amt_col and not (debit_col and credit_col):
            raise ValueError(
                "CSV needs either an 'amount' column or both 'debit'/'credit' columns. "
                f"Saw: {reader.fieldnames}"
            )

        for i, raw in enumerate(reader, start=2):  # row 1 is header
            try:
                date = _parse_date(raw.get(date_col))
                desc = (raw.get(desc_col) or "").strip()
                if amt_col:
                    cents = _parse_amount(raw.get(amt_col))
                else:
                    debit = _parse_amount(raw.get(debit_col))
                    credit = _parse_amount(raw.get(credit_col))
                    # Bank-side: credit column = money in; debit column = money out.
                    cents = credit - debit
            except ValueError as e:
                raise ValueError(f"row {i}: {e}") from e
            if cents == 0 and not desc:
                continue
            rows.append(CsvRow(date=date, description=desc, cents=cents))
    return rows


def _first_match(fields: dict[str, str], keys: set[str]) -> str | None:
    for k in keys:
        if k in fields:
            return fields[k]
    return None


def _suggest_account(description: str, is_inflow: bool) -> str:
    """Pick a contra-account for a bank transaction.

    - Money in (positive):  CR an income or A/R-style account. Default 4900.
    - Money out (negative): DR an expense account. Use EXPENSE_KEYWORDS.
    """
    if is_inflow:
        low = description.lower()
        if any(w in low for w in ("payment", "deposit from", "transfer in")):
            return "1100"   # Accounts Receivable
        if any(w in low for w in ("refund",)):
            return "4900"
        return "4900"       # Other Income (safe default; user re-classifies)
    for pat, code in EXPENSE_KEYWORDS:
        if pat.search(description):
            return code
    return "6999"


def ingest_bank_csv(
    conn: sqlite3.Connection, csv_path: str | Path, *,
    cash_account_code: str = "1010", db_path: str | Path | None = None,
    actor: str = "system",
) -> IngestResult:
    """Parse the CSV, store it as a document, and post one proposed JE per
    transaction. All JEs reference the document and sit as 'proposed' until
    the user reviews via `qbai propose list / approve / reject`.
    """
    doc_id = documents.ingest_file(
        conn, csv_path, doc_type="bank_statement", db_path=db_path, actor=actor,
    )
    rows = parse_csv(csv_path)

    proposed_ids: list[int] = []
    skipped: list[tuple[int, str]] = []
    for i, row in enumerate(rows, start=1):
        if row.cents == 0:
            skipped.append((i, "zero amount"))
            continue
        is_inflow = row.cents > 0
        amount = abs(row.cents)
        contra_code = _suggest_account(row.description, is_inflow)
        memo = f"{row.description}"[:200] or f"Bank txn #{i}"
        if is_inflow:
            # DR Cash, CR contra (income or A/R)
            lines = [
                JournalLine(account_code=cash_account_code, debit=amount,
                            description=row.description[:200]),
                JournalLine(account_code=contra_code, credit=amount,
                            description=row.description[:200]),
            ]
        else:
            # DR expense, CR Cash
            lines = [
                JournalLine(account_code=contra_code, debit=amount,
                            description=row.description[:200]),
                JournalLine(account_code=cash_account_code, credit=amount,
                            description=row.description[:200]),
            ]
        je_id = post_journal_entry(
            conn, lines=lines, entry_date=row.date, memo=memo,
            reference=f"bank-row-{i}",
            source_type="document", source_id=doc_id,
            created_by=actor, status="proposed",
        )
        proposed_ids.append(je_id)

    with conn:
        audit(conn, actor, "bank_csv_ingested", "document", doc_id,
              f"{len(proposed_ids)} proposed, {len(skipped)} skipped")

    return IngestResult(
        doc_id=doc_id, rows_parsed=len(rows),
        proposed_je_ids=proposed_ids, skipped=skipped,
    )
