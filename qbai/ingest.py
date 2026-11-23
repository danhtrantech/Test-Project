"""Turn extracted document data into a proposed accounting entry.

Routing:
  doc_type = 'receipt'          -> proposed JE (DR expense, CR cash), status='proposed'
  doc_type = 'bill'             -> draft bill (status='draft') with lines
  doc_type = 'customer_invoice' -> draft invoice (status='draft') with lines
  anything else                 -> proposed JE (best-effort)

All of these need human approval before they affect the books:
  - Proposed JE: approve via `qbai propose approve ENTRY_ID`
  - Draft bill / invoice: review and run `qbai bill finalize ID` / `qbai invoice finalize ID`
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls

from . import ap, ar, documents
from .coa import require_account
from .ledger import JournalLine, post_journal_entry
from .parties import add_party, find_party


@dataclass
class ProposalResult:
    doc_id: int
    kind: str                 # 'je' | 'bill' | 'invoice'
    target_id: int            # je_id / bill_id / invoice_id
    party_id: int | None
    requires: str             # human-readable next step


def propose_from_document(
    conn: sqlite3.Connection, doc_id: int, *,
    cash_account_code: str = "1010", actor: str = "system",
) -> ProposalResult:
    doc = documents.get_document(conn, doc_id)
    if doc is None:
        raise LookupError(f"No document {doc_id}")
    if not doc.extracted_data:
        raise ValueError(f"Document {doc_id} has no extracted_data yet")

    data = doc.extracted_data
    doc_type = (data.get("doc_type") or doc.doc_type or "other").lower()

    if doc_type == "receipt":
        return _propose_receipt(conn, doc, data, cash_account_code, actor)
    if doc_type == "bill":
        return _propose_bill(conn, doc, data, actor)
    if doc_type == "customer_invoice":
        return _propose_invoice(conn, doc, data, actor)
    return _propose_generic_je(conn, doc, data, cash_account_code, actor)


def _date_or_today(data: dict) -> str:
    d = data.get("date")
    if d and isinstance(d, str) and len(d) == 10:
        return d
    return date_cls.today().isoformat()


def _valid_account_or(conn, code: str | None, default: str) -> str:
    if not code:
        return default
    try:
        require_account(conn, code)
        return code
    except LookupError:
        return default


def _ensure_party(conn, name: str | None, kind: str, actor: str):
    if not name:
        return None
    p = find_party(conn, name, kind=kind)
    if p:
        return p
    return add_party(conn, name, kind, actor=actor)


# ---- receipt -> proposed JE ----------------------------------------------

def _propose_receipt(conn, doc, data, cash_code, actor) -> ProposalResult:
    date = _date_or_today(data)
    total = int(data.get("total_cents") or 0)
    if total <= 0:
        # Fall back to subtotal + tax if total is missing or zero
        total = int(data.get("subtotal_cents") or 0) + int(data.get("tax_cents") or 0)
    if total <= 0:
        raise ValueError(
            f"Document {doc.id} has no usable total; cannot propose entry. "
            f"Try `qbai doc show {doc.id}` and post a manual JE."
        )
    paid_from = _valid_account_or(conn, cash_code, "1010")
    vendor = data.get("vendor_or_customer_name") or "Unknown"

    lines: list[JournalLine] = []
    items = data.get("line_items") or []
    debit_total = 0
    for item in items:
        amount = int(item.get("amount_cents") or 0)
        if amount <= 0:
            continue
        code = _valid_account_or(conn, item.get("suggested_account_code"), "6999")
        lines.append(JournalLine(
            account_code=code, debit=amount,
            description=(item.get("description") or "")[:200],
        ))
        debit_total += amount

    tax_cents = int(data.get("tax_cents") or 0)
    if tax_cents > 0:
        # Vendor-charged sales tax: treat as expense in simple mode.
        lines.append(JournalLine(
            account_code=_valid_account_or(conn, None, "6999"),
            debit=tax_cents, description="Sales tax",
        ))
        debit_total += tax_cents

    if debit_total != total:
        # Fill the gap with "Other Expense" so the JE balances with the receipt total.
        diff = total - debit_total
        if diff > 0:
            lines.append(JournalLine(
                account_code="6999", debit=diff,
                description=f"Unallocated from {vendor}",
            ))
        else:
            # Over-allocated; rescale the final line down. Rare, but defensive.
            overflow = -diff
            last = lines[-1]
            if last.debit > overflow:
                lines[-1] = JournalLine(
                    account_code=last.account_code,
                    debit=last.debit - overflow,
                    description=last.description,
                )
            else:
                raise ValueError(
                    f"Line items ({debit_total}) exceed total ({total}) in doc {doc.id}"
                )

    lines.append(JournalLine(
        account_code=paid_from, credit=total,
        description=f"Paid to {vendor}",
    ))
    je_id = post_journal_entry(
        conn, lines=lines, entry_date=date,
        memo=f"Receipt: {vendor}" + (f" (doc #{doc.id})" if doc.id else ""),
        reference=data.get("reference_number"),
        source_type="document", source_id=doc.id,
        created_by=actor, status="proposed",
    )
    documents.set_proposed_targets(conn, doc.id, je_id=je_id)
    return ProposalResult(
        doc_id=doc.id, kind="je", target_id=je_id, party_id=None,
        requires=f"Review and approve: `qbai propose approve {je_id}`",
    )


# ---- bill -> draft bill with lines ---------------------------------------

def _propose_bill(conn, doc, data, actor) -> ProposalResult:
    date = _date_or_today(data)
    vendor_name = data.get("vendor_or_customer_name") or f"Vendor from doc #{doc.id}"
    vendor = _ensure_party(conn, vendor_name, "vendor", actor)

    bid = ap.create_bill(
        conn, party_id=vendor.id, bill_date=date, due_date=None,
        number=data.get("reference_number"),
        memo=f"Auto-proposed from doc #{doc.id}",
        actor=actor,
    )
    items = data.get("line_items") or []
    if not items:
        raise ValueError(f"Document {doc.id}: no line items to propose a bill from")
    for item in items:
        amount = int(item.get("amount_cents") or 0)
        if amount <= 0:
            continue
        code = _valid_account_or(conn, item.get("suggested_account_code"), "6999")
        qty = float(item.get("quantity") or 1)
        unit_price = int(item.get("unit_price_cents") or amount)
        ap.add_bill_line(
            conn, bid,
            description=(item.get("description") or "")[:200],
            quantity=qty, unit_price_cents=unit_price,
            expense_account_code=code,
        )
    documents.set_proposed_targets(conn, doc.id, bill_id=bid)
    return ProposalResult(
        doc_id=doc.id, kind="bill", target_id=bid, party_id=vendor.id,
        requires=(f"Review and finalize: `qbai bill show {bid}` then "
                  f"`qbai bill finalize {bid}`"
                  + (f" --tax {data.get('tax_cents', 0)/100:.2f} --tax-account 2100"
                     if data.get("tax_cents") else "")),
    )


# ---- customer_invoice -> draft invoice -----------------------------------

def _propose_invoice(conn, doc, data, actor) -> ProposalResult:
    date = _date_or_today(data)
    customer_name = data.get("vendor_or_customer_name") or f"Customer from doc #{doc.id}"
    customer = _ensure_party(conn, customer_name, "customer", actor)

    iid = ar.create_invoice(
        conn, party_id=customer.id, invoice_date=date,
        number=data.get("reference_number"),
        memo=f"Auto-proposed from doc #{doc.id}",
        actor=actor,
    )
    items = data.get("line_items") or []
    if not items:
        raise ValueError(f"Document {doc.id}: no line items to propose an invoice from")
    for item in items:
        amount = int(item.get("amount_cents") or 0)
        if amount <= 0:
            continue
        # For invoice lines, map the suggestion to an income account if valid;
        # otherwise fall back to 4900 Other Income.
        code = item.get("suggested_account_code")
        try:
            require_account(conn, code or "")
            # Only accept income accounts; else fallback.
            row = conn.execute(
                "SELECT type FROM accounts WHERE code = ?", (code,)
            ).fetchone()
            if row["type"] != "income":
                code = "4900"
        except LookupError:
            code = "4900"
        qty = float(item.get("quantity") or 1)
        unit_price = int(item.get("unit_price_cents") or amount)
        ar.add_invoice_line(
            conn, iid,
            description=(item.get("description") or "")[:200],
            quantity=qty, unit_price_cents=unit_price,
            income_account_code=code,
        )
    documents.set_proposed_targets(conn, doc.id, invoice_id=iid)
    return ProposalResult(
        doc_id=doc.id, kind="invoice", target_id=iid, party_id=customer.id,
        requires=f"Review and finalize: `qbai invoice show {iid}` then `qbai invoice finalize {iid}`",
    )


# ---- fallback: generic JE -------------------------------------------------

def _propose_generic_je(conn, doc, data, cash_code, actor) -> ProposalResult:
    # Treat unknown docs like a receipt (safest: a cash expense the user reviews).
    return _propose_receipt(conn, doc, data, cash_code, actor)
