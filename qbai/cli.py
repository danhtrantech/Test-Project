"""qbai CLI. Run with `python -m qbai.cli <command> ...`

See README.md for the full command reference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import agent as agent_mod
from . import ap, ar, bank, documents, expenses, extract, ingest, payments
from .coa import add_account, list_accounts, seed_chart_of_accounts
from .db import get_connection, init_db
from .ledger import (
    JournalLine, approve_proposed, get_entry, list_entries, post_journal_entry, void_entry,
)
from .money import format_money, to_cents
from .parties import add_party, list_parties, require_party_by_name
from .reports import balance_sheet, general_ledger, profit_and_loss, trial_balance


def _conn(args) -> "sqlite3.Connection":  # type: ignore[name-defined]
    return get_connection(args.db)


def cmd_init(args) -> int:
    conn = _conn(args)
    init_db(conn)
    added = seed_chart_of_accounts(conn)
    print(f"Initialized DB at {Path(args.db).resolve() if args.db else '(default)'}")
    print(f"Seeded chart of accounts ({added} new accounts).")
    return 0


def cmd_accounts_list(args) -> int:
    conn = _conn(args)
    rows = list_accounts(conn, type_=args.type)
    if not rows:
        print("(no accounts)")
        return 0
    print(f"{'CODE':<8} {'TYPE':<10} {'NAME'}")
    for a in rows:
        print(f"{a.code:<8} {a.type:<10} {a.name}")
    return 0


def cmd_accounts_add(args) -> int:
    conn = _conn(args)
    a = add_account(conn, args.code, args.name, args.type)
    print(f"Added {a.code} {a.name} ({a.type})")
    return 0


def _parse_line(spec: str) -> JournalLine:
    """Parse a CLI line spec like '6200:debit:42.50:Office paper'."""
    parts = spec.split(":", 3)
    if len(parts) < 3:
        raise ValueError(
            f"Bad line spec {spec!r}. Expected 'CODE:debit|credit:AMOUNT[:description]'"
        )
    code, side, amount = parts[0], parts[1].lower().strip(), parts[2]
    desc = parts[3] if len(parts) == 4 else None
    cents = to_cents(amount)
    if side == "debit":
        return JournalLine(account_code=code, debit=cents, description=desc)
    if side == "credit":
        return JournalLine(account_code=code, credit=cents, description=desc)
    raise ValueError(f"Side must be 'debit' or 'credit', got {side!r}")


def cmd_je_post(args) -> int:
    conn = _conn(args)
    lines = [_parse_line(s) for s in args.line]
    eid = post_journal_entry(
        conn,
        lines=lines,
        entry_date=args.date,
        memo=args.memo,
        reference=args.reference,
        created_by=args.user or "cli",
    )
    print(f"Posted journal entry #{eid}")
    return 0


def cmd_je_list(args) -> int:
    conn = _conn(args)
    rows = list_entries(conn, status=args.status, date_from=args.date_from,
                        date_to=args.date_to, limit=args.limit)
    if not rows:
        print("(no entries)")
        return 0
    print(f"{'ID':<6} {'DATE':<12} {'STATUS':<10} {'SOURCE':<12} MEMO")
    for r in rows:
        print(f"{r['id']:<6} {r['entry_date']:<12} {r['status']:<10} "
              f"{(r['source_type'] or ''):<12} {r['memo'] or ''}")
    return 0


def cmd_je_show(args) -> int:
    conn = _conn(args)
    e = get_entry(conn, args.entry_id)
    if e is None:
        print(f"No entry #{args.entry_id}", file=sys.stderr)
        return 1
    print(f"JE #{e.id} | {e.entry_date} | {e.status} | by {e.created_by}")
    if e.memo:
        print(f"Memo: {e.memo}")
    if e.reference:
        print(f"Ref:  {e.reference}")
    print()
    print(f"  {'ACCOUNT':<8} {'NAME':<28} {'DEBIT':>12} {'CREDIT':>12}  DESCRIPTION")
    for l in e.lines:
        print(f"  {l['account_code']:<8} {l['account_name'][:28]:<28} "
              f"{format_money(l['debit']):>12} {format_money(l['credit']):>12}  "
              f"{l['description'] or ''}")
    return 0


def cmd_je_void(args) -> int:
    conn = _conn(args)
    rev = void_entry(conn, args.entry_id, reason=args.reason, voided_by=args.user or "cli")
    print(f"Voided JE #{args.entry_id} via reversing entry #{rev}")
    return 0


# ---- Parties --------------------------------------------------------------

def cmd_parties_add(args) -> int:
    conn = _conn(args)
    p = add_party(conn, args.name, args.kind, email=args.email, phone=args.phone,
                  address=args.address, actor=args.user or "cli")
    print(f"Added {p.kind} #{p.id} {p.name}")
    return 0


def cmd_parties_list(args) -> int:
    conn = _conn(args)
    rows = list_parties(conn, kind=args.kind)
    if not rows:
        print("(no parties)")
        return 0
    print(f"{'ID':<5} {'KIND':<10} {'NAME':<30} EMAIL")
    for p in rows:
        print(f"{p.id:<5} {p.kind:<10} {p.name[:30]:<30} {p.email or ''}")
    return 0


# ---- Invoices -------------------------------------------------------------

def cmd_invoice_create(args) -> int:
    conn = _conn(args)
    party = require_party_by_name(conn, args.party, kind="customer")
    iid = ar.create_invoice(
        conn, party_id=party.id, invoice_date=args.date, due_date=args.due,
        number=args.number, memo=args.memo, actor=args.user or "cli",
    )
    print(f"Created invoice #{iid} (draft) for {party.name}")
    return 0


def cmd_invoice_add_line(args) -> int:
    conn = _conn(args)
    line_id = ar.add_invoice_line(
        conn, args.invoice_id, description=args.description,
        quantity=args.qty, unit_price_cents=to_cents(args.unit_price),
        income_account_code=args.account,
    )
    print(f"Added line #{line_id} to invoice #{args.invoice_id}")
    return 0


def cmd_invoice_finalize(args) -> int:
    conn = _conn(args)
    je_id = ar.finalize_invoice(
        conn, args.invoice_id,
        tax_cents=to_cents(args.tax) if args.tax else 0,
        tax_account_code=args.tax_account or ar.DEFAULT_TAX_ACCOUNT,
        actor=args.user or "cli",
    )
    print(f"Finalized invoice #{args.invoice_id}; posted JE #{je_id}")
    return 0


def cmd_invoice_list(args) -> int:
    conn = _conn(args)
    rows = ar.list_invoices(conn, status=args.status)
    if not rows:
        print("(no invoices)")
        return 0
    print(f"{'ID':<5} {'DATE':<12} {'STATUS':<8} {'CUSTOMER':<25} {'TOTAL':>12} {'PAID':>12}")
    for r in rows:
        print(f"{r['id']:<5} {r['invoice_date']:<12} {r['status']:<8} "
              f"{r['party_name'][:25]:<25} "
              f"{format_money(r['total_cents']):>12} "
              f"{format_money(r['amount_paid_cents']):>12}")
    return 0


def cmd_invoice_show(args) -> int:
    conn = _conn(args)
    inv = ar.get_invoice(conn, args.invoice_id)
    if inv is None:
        print(f"No invoice #{args.invoice_id}", file=sys.stderr)
        return 1
    print(f"Invoice #{inv.id} | {inv.status} | {inv.party_name}")
    print(f"Date: {inv.invoice_date}  Due: {inv.due_date or '-'}  "
          f"Number: {inv.number or '-'}")
    if inv.memo:
        print(f"Memo: {inv.memo}")
    print()
    print(f"  {'DESCRIPTION':<35} {'QTY':>6} {'UNIT':>12} {'AMOUNT':>12}  ACCOUNT")
    for l in inv.lines:
        print(f"  {(l.description or '')[:35]:<35} {l.quantity:>6.2f} "
              f"{format_money(l.unit_price_cents):>12} "
              f"{format_money(l.amount_cents):>12}  {l.income_account_code}")
    print(f"\n  {'Subtotal':<60} {format_money(inv.subtotal_cents):>12}")
    if inv.tax_cents:
        print(f"  {'Tax':<60} {format_money(inv.tax_cents):>12}")
    print(f"  {'TOTAL':<60} {format_money(inv.total_cents):>12}")
    print(f"  {'Paid':<60} {format_money(inv.amount_paid_cents):>12}")
    print(f"  {'Balance':<60} "
          f"{format_money(inv.total_cents - inv.amount_paid_cents):>12}")
    if inv.posted_je_id:
        print(f"\nPosted JE: #{inv.posted_je_id}")
    if inv.voided_je_id:
        print(f"Voided via JE: #{inv.voided_je_id}")
    return 0


def cmd_invoice_void(args) -> int:
    conn = _conn(args)
    rev = ar.void_invoice(conn, args.invoice_id, reason=args.reason,
                          actor=args.user or "cli")
    print(f"Voided invoice #{args.invoice_id} via reversing JE #{rev}")
    return 0


# ---- Bills ----------------------------------------------------------------

def cmd_bill_create(args) -> int:
    conn = _conn(args)
    party = require_party_by_name(conn, args.party, kind="vendor")
    bid = ap.create_bill(
        conn, party_id=party.id, bill_date=args.date, due_date=args.due,
        number=args.number, memo=args.memo, actor=args.user or "cli",
    )
    print(f"Created bill #{bid} (draft) from {party.name}")
    return 0


def cmd_bill_add_line(args) -> int:
    conn = _conn(args)
    line_id = ap.add_bill_line(
        conn, args.bill_id, description=args.description,
        quantity=args.qty, unit_price_cents=to_cents(args.unit_price),
        expense_account_code=args.account,
    )
    print(f"Added line #{line_id} to bill #{args.bill_id}")
    return 0


def cmd_bill_finalize(args) -> int:
    conn = _conn(args)
    je_id = ap.finalize_bill(
        conn, args.bill_id,
        tax_cents=to_cents(args.tax) if args.tax else 0,
        tax_account_code=args.tax_account, actor=args.user or "cli",
    )
    print(f"Finalized bill #{args.bill_id}; posted JE #{je_id}")
    return 0


def cmd_bill_list(args) -> int:
    conn = _conn(args)
    rows = ap.list_bills(conn, status=args.status)
    if not rows:
        print("(no bills)")
        return 0
    print(f"{'ID':<5} {'DATE':<12} {'STATUS':<8} {'VENDOR':<25} {'TOTAL':>12} {'PAID':>12}")
    for r in rows:
        print(f"{r['id']:<5} {r['bill_date']:<12} {r['status']:<8} "
              f"{r['party_name'][:25]:<25} "
              f"{format_money(r['total_cents']):>12} "
              f"{format_money(r['amount_paid_cents']):>12}")
    return 0


def cmd_bill_show(args) -> int:
    conn = _conn(args)
    bill = ap.get_bill(conn, args.bill_id)
    if bill is None:
        print(f"No bill #{args.bill_id}", file=sys.stderr)
        return 1
    print(f"Bill #{bill.id} | {bill.status} | {bill.party_name}")
    print(f"Date: {bill.bill_date}  Due: {bill.due_date or '-'}  "
          f"Number: {bill.number or '-'}")
    if bill.memo:
        print(f"Memo: {bill.memo}")
    print()
    print(f"  {'DESCRIPTION':<35} {'QTY':>6} {'UNIT':>12} {'AMOUNT':>12}  ACCOUNT")
    for l in bill.lines:
        print(f"  {(l.description or '')[:35]:<35} {l.quantity:>6.2f} "
              f"{format_money(l.unit_price_cents):>12} "
              f"{format_money(l.amount_cents):>12}  {l.expense_account_code}")
    print(f"\n  {'Subtotal':<60} {format_money(bill.subtotal_cents):>12}")
    if bill.tax_cents:
        print(f"  {'Tax':<60} {format_money(bill.tax_cents):>12}")
    print(f"  {'TOTAL':<60} {format_money(bill.total_cents):>12}")
    print(f"  {'Paid':<60} {format_money(bill.amount_paid_cents):>12}")
    print(f"  {'Balance':<60} "
          f"{format_money(bill.total_cents - bill.amount_paid_cents):>12}")
    return 0


def cmd_bill_void(args) -> int:
    conn = _conn(args)
    rev = ap.void_bill(conn, args.bill_id, reason=args.reason, actor=args.user or "cli")
    print(f"Voided bill #{args.bill_id} via reversing JE #{rev}")
    return 0


# ---- Payments -------------------------------------------------------------

def _parse_alloc(spec: str, *, doc_kind: str) -> payments.Allocation:
    """Parse 'INVOICE_ID:AMOUNT' (or BILL_ID for sends)."""
    parts = spec.split(":")
    if len(parts) != 2:
        raise ValueError(f"Bad allocation {spec!r}. Expected 'DOC_ID:AMOUNT'")
    doc_id = int(parts[0])
    amt = to_cents(parts[1])
    if doc_kind == "invoice":
        return payments.Allocation(invoice_id=doc_id, amount_cents=amt)
    return payments.Allocation(bill_id=doc_id, amount_cents=amt)


def cmd_payment_receive(args) -> int:
    conn = _conn(args)
    party = require_party_by_name(conn, args.party, kind="customer")
    allocs = [_parse_alloc(s, doc_kind="invoice") for s in args.invoice]
    total = to_cents(args.amount)
    pid = payments.receive_payment(
        conn, party_id=party.id, amount_cents=total, allocations=allocs,
        payment_date=args.date,
        cash_account_code=args.cash_account or payments.DEFAULT_CASH_ACCOUNT,
        memo=args.memo, reference=args.reference, actor=args.user or "cli",
    )
    print(f"Recorded receipt #{pid} from {party.name} for {format_money(total)}")
    return 0


def cmd_payment_send(args) -> int:
    conn = _conn(args)
    party = require_party_by_name(conn, args.party, kind="vendor")
    allocs = [_parse_alloc(s, doc_kind="bill") for s in args.bill]
    total = to_cents(args.amount)
    pid = payments.send_payment(
        conn, party_id=party.id, amount_cents=total, allocations=allocs,
        payment_date=args.date,
        cash_account_code=args.cash_account or payments.DEFAULT_CASH_ACCOUNT,
        memo=args.memo, reference=args.reference, actor=args.user or "cli",
    )
    print(f"Recorded payment #{pid} to {party.name} for {format_money(total)}")
    return 0


def cmd_payment_list(args) -> int:
    conn = _conn(args)
    rows = payments.list_payments(conn, kind=args.kind)
    if not rows:
        print("(no payments)")
        return 0
    print(f"{'ID':<5} {'KIND':<8} {'DATE':<12} {'PARTY':<25} {'AMOUNT':>12}  MEMO")
    for r in rows:
        print(f"{r['id']:<5} {r['kind']:<8} {r['payment_date']:<12} "
              f"{r['party_name'][:25]:<25} "
              f"{format_money(r['amount_cents']):>12}  {r['memo'] or ''}")
    return 0


# ---- Standalone expense ---------------------------------------------------

def cmd_expense_add(args) -> int:
    conn = _conn(args)
    je_id = expenses.record_expense(
        conn,
        expense_account_code=args.account,
        amount_cents=to_cents(args.amount),
        paid_from_account_code=args.cash_account or payments.DEFAULT_CASH_ACCOUNT,
        expense_date=args.date,
        vendor_name=args.vendor,
        memo=args.memo,
        reference=args.reference,
        actor=args.user or "cli",
    )
    print(f"Recorded expense; posted JE #{je_id}")
    return 0


# ---- Documents + ingestion -----------------------------------------------

def cmd_doc_ingest(args) -> int:
    conn = _conn(args)
    doc_id = documents.ingest_file(
        conn, args.path, doc_type=args.type, db_path=args.db, actor=args.user or "cli",
    )
    doc = documents.get_document(conn, doc_id)
    print(f"Ingested document #{doc_id}: {doc.original_name} "
          f"({doc.mime_type or 'unknown'}, {doc.size_bytes} bytes)")
    if doc.extraction_status == "failed":
        print(f"  Text extraction failed: {doc.extraction_error}")
    if not args.extract:
        print(f"Run `qbai doc extract {doc_id}` to run Claude extraction and propose an entry.")
        return 0
    return cmd_doc_extract(_fake(args, doc_id=doc_id, auto_propose=args.propose))


def cmd_doc_extract(args) -> int:
    conn = _conn(args)
    doc = documents.get_document(conn, args.doc_id)
    if doc is None:
        print(f"No document #{args.doc_id}", file=sys.stderr)
        return 1
    coa = [{"code": a.code, "name": a.name, "type": a.type}
           for a in list_accounts(conn)]
    is_image = (doc.mime_type or "").startswith("image/")
    if is_image and not args.heuristic:
        try:
            result = extract.extract_image_with_claude(
                doc.stored_path, chart_of_accounts=coa, doc_type_hint=doc.doc_type,
            )
        except RuntimeError as e:
            print(f"Image extraction needs Claude Vision: {e}", file=sys.stderr)
            return 1
    else:
        if doc.extracted_text is None:
            print(f"Document #{doc.id} has no extracted text ({doc.extraction_error}); "
                  "cannot run text extraction.", file=sys.stderr)
            return 1
        result = extract.extract(
            doc.extracted_text, chart_of_accounts=coa,
            doc_type_hint=doc.doc_type, force_heuristic=args.heuristic,
        )
    documents.set_extracted_data(
        conn, doc.id, result.data, extractor=result.extractor,
        doc_type=result.data.get("doc_type"),
    )
    print(f"Extracted doc #{doc.id} with {result.extractor}" +
          (f" ({result.model})" if result.model else "") +
          f"; doc_type={result.data.get('doc_type')}, "
          f"total={format_money(int(result.data.get('total_cents') or 0))}")
    if args.auto_propose:
        return cmd_doc_propose(_fake(args, doc_id=doc.id))
    print(f"Run `qbai doc propose {doc.id}` to stage a proposed entry.")
    return 0


def cmd_doc_propose(args) -> int:
    conn = _conn(args)
    res = ingest.propose_from_document(
        conn, args.doc_id,
        cash_account_code=getattr(args, "cash_account", None) or "1010",
        actor=getattr(args, "user", None) or "cli",
    )
    print(f"Proposed {res.kind} #{res.target_id} from doc #{res.doc_id}")
    print(f"Next step: {res.requires}")
    return 0


def cmd_doc_list(args) -> int:
    conn = _conn(args)
    rows = documents.list_documents(conn, status=args.status)
    if not rows:
        print("(no documents)")
        return 0
    print(f"{'ID':<5} {'NAME':<30} {'MIME':<24} {'STATUS':<10} {'TYPE':<16} PROPOSED")
    for r in rows:
        proposed = []
        if r["proposed_je_id"]:
            proposed.append(f"JE#{r['proposed_je_id']}")
        if r["proposed_bill_id"]:
            proposed.append(f"bill#{r['proposed_bill_id']}")
        if r["proposed_invoice_id"]:
            proposed.append(f"invoice#{r['proposed_invoice_id']}")
        print(f"{r['id']:<5} {r['original_name'][:30]:<30} "
              f"{(r['mime_type'] or '')[:24]:<24} "
              f"{r['extraction_status']:<10} {(r['doc_type'] or '')[:16]:<16} "
              f"{', '.join(proposed) or '-'}")
    return 0


def cmd_doc_show(args) -> int:
    conn = _conn(args)
    doc = documents.get_document(conn, args.doc_id)
    if doc is None:
        print(f"No document #{args.doc_id}", file=sys.stderr)
        return 1
    print(f"Document #{doc.id}: {doc.original_name}")
    print(f"  Stored:      {doc.stored_path}")
    print(f"  sha256:      {doc.sha256}")
    print(f"  Mime:        {doc.mime_type}  ({doc.size_bytes} bytes)")
    print(f"  Doc type:    {doc.doc_type or '(not set)'}")
    print(f"  Status:      {doc.extraction_status}"
          + (f"  — {doc.extraction_error}" if doc.extraction_error else ""))
    print(f"  Extractor:   {doc.extractor or '-'}")
    proposed = []
    if doc.proposed_je_id:
        proposed.append(f"JE#{doc.proposed_je_id}")
    if doc.proposed_bill_id:
        proposed.append(f"bill#{doc.proposed_bill_id}")
    if doc.proposed_invoice_id:
        proposed.append(f"invoice#{doc.proposed_invoice_id}")
    print(f"  Proposed:    {', '.join(proposed) or '-'}")
    if doc.extracted_data:
        import json as _json
        print("\nExtracted data:")
        print(_json.dumps(doc.extracted_data, indent=2))
    elif doc.extracted_text:
        preview = doc.extracted_text[:500]
        print("\nExtracted text (first 500 chars):")
        print(preview)
    return 0


# ---- Proposed journal entries --------------------------------------------

def cmd_propose_list(args) -> int:
    conn = _conn(args)
    rows = list_entries(conn, status="proposed", limit=args.limit)
    if not rows:
        print("(no proposed entries)")
        return 0
    print(f"{'ID':<5} {'DATE':<12} {'SOURCE':<10} MEMO")
    for r in rows:
        print(f"{r['id']:<5} {r['entry_date']:<12} "
              f"{(r['source_type'] or ''):<10} {r['memo'] or ''}")
    return 0


def cmd_propose_approve(args) -> int:
    conn = _conn(args)
    approve_proposed(conn, args.entry_id, approved_by=args.user or "cli")
    print(f"Approved proposed JE #{args.entry_id}; now posted.")
    return 0


def cmd_propose_reject(args) -> int:
    conn = _conn(args)
    with conn:
        row = conn.execute(
            "SELECT status FROM journal_entries WHERE id = ?", (args.entry_id,)
        ).fetchone()
        if row is None:
            print(f"No JE #{args.entry_id}", file=sys.stderr)
            return 1
        if row["status"] != "proposed":
            print(f"JE #{args.entry_id} is not proposed (status={row['status']!r})",
                  file=sys.stderr)
            return 1
        # Delete the proposed entry and its lines (cascade via FK).
        conn.execute("DELETE FROM journal_lines WHERE journal_entry_id = ?", (args.entry_id,))
        conn.execute("DELETE FROM journal_entries WHERE id = ?", (args.entry_id,))
        # Clear the proposed_je_id on any document that pointed at it.
        conn.execute(
            "UPDATE documents SET proposed_je_id = NULL WHERE proposed_je_id = ?",
            (args.entry_id,),
        )
    print(f"Rejected proposed JE #{args.entry_id} (deleted).")
    return 0


def _fake(args, **overrides):
    """Build a lightweight Namespace that inherits --db / --user / chains calls."""
    import types
    merged = {**vars(args), **overrides}
    return types.SimpleNamespace(**merged)


def cmd_bank_ingest(args) -> int:
    conn = _conn(args)
    res = bank.ingest_bank_csv(
        conn, args.path,
        cash_account_code=args.cash_account or "1010",
        db_path=args.db, actor=args.user or "cli",
    )
    print(f"Ingested bank CSV as document #{res.doc_id}: "
          f"{res.rows_parsed} rows parsed, "
          f"{len(res.proposed_je_ids)} proposed JEs, "
          f"{len(res.skipped)} skipped")
    if res.skipped:
        for i, why in res.skipped:
            print(f"  row {i} skipped: {why}")
    print("Review with `qbai propose list` and approve/reject each.")
    return 0


# ---- Conversational agent -------------------------------------------------

def cmd_chat(args) -> int:
    conn = _conn(args)
    history: list[dict] = []
    if args.message is not None:
        return _run_chat_turn(conn, args.message, history, actor=args.user or "agent")
    # Interactive REPL
    print("qbai chat — type your questions or commands. Ctrl-D to exit.")
    while True:
        try:
            user = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user:
            continue
        rc = _run_chat_turn(conn, user, history, actor=args.user or "agent")
        if rc != 0:
            return rc


def _run_chat_turn(conn, user_message, history, actor) -> int:
    try:
        result = agent_mod.chat_once(conn, user_message, history=history, actor=actor)
    except RuntimeError as e:
        print(f"agent unavailable: {e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY and `pip install anthropic` to enable the agent.",
              file=sys.stderr)
        return 1
    history[:] = result["messages"]
    text = result["text"].strip()
    if text:
        print(text)
    return 0


def cmd_trial_balance(args) -> int:
    conn = _conn(args)
    rows = trial_balance(conn, as_of=args.as_of)
    if not rows:
        print("(trial balance is empty)")
        return 0
    print(f"Trial Balance{f' as of {args.as_of}' if args.as_of else ''}")
    print(f"{'CODE':<8} {'NAME':<28} {'TYPE':<10} {'DEBIT':>14} {'CREDIT':>14}")
    td = tc = 0
    for r in rows:
        print(f"{r.code:<8} {r.name[:28]:<28} {r.type:<10} "
              f"{format_money(r.debit):>14} {format_money(r.credit):>14}")
        td += r.debit
        tc += r.credit
    print("-" * 78)
    print(f"{'TOTAL':<48} {format_money(td):>14} {format_money(tc):>14}")
    if td != tc:
        print(f"!! Out of balance by {format_money(td - tc)}", file=sys.stderr)
        return 2
    return 0


# ---- P&L / Balance Sheet / General Ledger ---------------------------------

def _section_title(text: str, width: int = 78) -> None:
    print()
    print(text.upper())


def cmd_pnl(args) -> int:
    conn = _conn(args)
    pnl = profit_and_loss(conn, date_from=args.date_from, date_to=args.date_to)
    period = ""
    if args.date_from or args.date_to:
        period = f" | {args.date_from or '(inception)'} to {args.date_to or '(today)'}"
    print(f"Profit & Loss{period}")

    _section_title("Income")
    if not pnl.income:
        print("  (no income)")
    for r in pnl.income:
        print(f"  {r.code:<6} {r.name[:42]:<42} {format_money(r.amount):>18}")
    print("  " + "-" * 68)
    print(f"  {'Total Income':<49} {format_money(pnl.total_income):>18}")

    _section_title("Expenses")
    if not pnl.expenses:
        print("  (no expenses)")
    for r in pnl.expenses:
        print(f"  {r.code:<6} {r.name[:42]:<42} {format_money(r.amount):>18}")
    print("  " + "-" * 68)
    print(f"  {'Total Expenses':<49} {format_money(pnl.total_expenses):>18}")

    print()
    print(f"  {'NET INCOME':<49} {format_money(pnl.net_income):>18}")
    return 0


def cmd_balance_sheet(args) -> int:
    conn = _conn(args)
    bs = balance_sheet(conn, as_of=args.as_of)
    print(f"Balance Sheet{f' as of {args.as_of}' if args.as_of else ''}")

    _section_title("Assets")
    for r in bs.assets.rows:
        print(f"  {r.code:<6} {r.name[:42]:<42} {format_money(r.amount):>18}")
    print("  " + "-" * 68)
    print(f"  {'Total Assets':<49} {format_money(bs.assets.total):>18}")

    _section_title("Liabilities")
    for r in bs.liabilities.rows:
        print(f"  {r.code:<6} {r.name[:42]:<42} {format_money(r.amount):>18}")
    print("  " + "-" * 68)
    print(f"  {'Total Liabilities':<49} {format_money(bs.liabilities.total):>18}")

    _section_title("Equity")
    for r in bs.equity.rows:
        print(f"  {r.code:<6} {r.name[:42]:<42} {format_money(r.amount):>18}")
    print(f"  {'':<6} {'Current Period Net Income':<42} "
          f"{format_money(bs.net_income_to_date):>18}")
    print("  " + "-" * 68)
    print(f"  {'Total Equity':<49} {format_money(bs.equity.total):>18}")

    print()
    print(f"  {'Total Liabilities + Equity':<49} "
          f"{format_money(bs.total_liabilities_and_equity):>18}")
    diff = bs.assets.total - bs.total_liabilities_and_equity
    if diff != 0:
        print(f"!! Balance sheet out of balance by {format_money(diff)}", file=sys.stderr)
        return 2
    return 0


def cmd_gl(args) -> int:
    conn = _conn(args)
    accounts = general_ledger(
        conn, account_code=args.account,
        date_from=args.date_from, date_to=args.date_to,
    )
    if not accounts:
        print("(no activity)")
        return 0
    period = ""
    if args.date_from or args.date_to:
        period = f"  |  {args.date_from or '(inception)'} to {args.date_to or '(today)'}"
    for a in accounts:
        print(f"\n{a.code}  {a.name}  [{a.type}]{period}")
        print(f"  Opening balance                                       "
              f"{format_money(a.opening_balance):>18}")
        if not a.entries:
            print("  (no activity in window)")
        else:
            print(f"  {'DATE':<12} {'JE':<5} {'DESCRIPTION':<30} "
                  f"{'DEBIT':>12} {'CREDIT':>12} {'BALANCE':>14}")
            for e in a.entries:
                desc = (e.description or e.memo or "")[:30]
                print(f"  {e.date:<12} {e.entry_id:<5} {desc:<30} "
                      f"{format_money(e.debit):>12} "
                      f"{format_money(e.credit):>12} "
                      f"{format_money(e.balance):>14}")
        print(f"  Closing balance                                       "
              f"{format_money(a.closing_balance):>18}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qbai", description="qbai bookkeeping CLI")
    p.add_argument("--db", help="path to SQLite DB (default: data/qbai.db)")
    p.add_argument("--user", help="actor name recorded in audit log", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="initialize DB and seed CoA").set_defaults(func=cmd_init)

    accounts = sub.add_parser("accounts", help="manage chart of accounts")
    asub = accounts.add_subparsers(dest="accounts_cmd", required=True)
    al = asub.add_parser("list")
    al.add_argument("--type", choices=["asset", "liability", "equity", "income", "expense"])
    al.set_defaults(func=cmd_accounts_list)
    aa = asub.add_parser("add")
    aa.add_argument("code")
    aa.add_argument("name")
    aa.add_argument("type", choices=["asset", "liability", "equity", "income", "expense"])
    aa.set_defaults(func=cmd_accounts_add)

    je = sub.add_parser("je", help="manual journal entries")
    jsub = je.add_subparsers(dest="je_cmd", required=True)
    jp = jsub.add_parser("post")
    jp.add_argument("--date", help="YYYY-MM-DD (default: today)")
    jp.add_argument("--memo")
    jp.add_argument("--reference")
    jp.add_argument("--line", action="append", required=True,
                    help='Repeat for each line: "CODE:debit|credit:AMOUNT[:description]"')
    jp.set_defaults(func=cmd_je_post)
    jl = jsub.add_parser("list")
    jl.add_argument("--status", choices=["proposed", "posted", "voided"])
    jl.add_argument("--date-from")
    jl.add_argument("--date-to")
    jl.add_argument("--limit", type=int, default=100)
    jl.set_defaults(func=cmd_je_list)
    js = jsub.add_parser("show")
    js.add_argument("entry_id", type=int)
    js.set_defaults(func=cmd_je_show)
    jv = jsub.add_parser("void")
    jv.add_argument("entry_id", type=int)
    jv.add_argument("--reason", required=True)
    jv.set_defaults(func=cmd_je_void)

    # ---- parties ----
    parties = sub.add_parser("parties", help="customers and vendors")
    psub = parties.add_subparsers(dest="parties_cmd", required=True)
    pa = psub.add_parser("add")
    pa.add_argument("name")
    pa.add_argument("--kind", choices=["customer", "vendor", "both"], required=True)
    pa.add_argument("--email")
    pa.add_argument("--phone")
    pa.add_argument("--address")
    pa.set_defaults(func=cmd_parties_add)
    pl = psub.add_parser("list")
    pl.add_argument("--kind", choices=["customer", "vendor"])
    pl.set_defaults(func=cmd_parties_list)

    # ---- invoices ----
    inv = sub.add_parser("invoice", help="customer invoices (A/R)")
    isub = inv.add_subparsers(dest="inv_cmd", required=True)
    ic = isub.add_parser("create")
    ic.add_argument("--party", required=True)
    ic.add_argument("--date")
    ic.add_argument("--due")
    ic.add_argument("--number")
    ic.add_argument("--memo")
    ic.set_defaults(func=cmd_invoice_create)
    il = isub.add_parser("add-line")
    il.add_argument("invoice_id", type=int)
    il.add_argument("--description")
    il.add_argument("--qty", type=float, default=1.0)
    il.add_argument("--unit-price", required=True)
    il.add_argument("--account", required=True, help="income account code")
    il.set_defaults(func=cmd_invoice_add_line)
    ifin = isub.add_parser("finalize")
    ifin.add_argument("invoice_id", type=int)
    ifin.add_argument("--tax", help="tax amount (decimal dollars)")
    ifin.add_argument("--tax-account", help="account code for tax (default 2100)")
    ifin.set_defaults(func=cmd_invoice_finalize)
    ill = isub.add_parser("list")
    ill.add_argument("--status", choices=["draft", "open", "paid", "voided"])
    ill.set_defaults(func=cmd_invoice_list)
    ishow = isub.add_parser("show")
    ishow.add_argument("invoice_id", type=int)
    ishow.set_defaults(func=cmd_invoice_show)
    iv = isub.add_parser("void")
    iv.add_argument("invoice_id", type=int)
    iv.add_argument("--reason", required=True)
    iv.set_defaults(func=cmd_invoice_void)

    # ---- bills ----
    bill = sub.add_parser("bill", help="vendor bills (A/P)")
    bsub = bill.add_subparsers(dest="bill_cmd", required=True)
    bc = bsub.add_parser("create")
    bc.add_argument("--party", required=True)
    bc.add_argument("--date")
    bc.add_argument("--due")
    bc.add_argument("--number")
    bc.add_argument("--memo")
    bc.set_defaults(func=cmd_bill_create)
    bl = bsub.add_parser("add-line")
    bl.add_argument("bill_id", type=int)
    bl.add_argument("--description")
    bl.add_argument("--qty", type=float, default=1.0)
    bl.add_argument("--unit-price", required=True)
    bl.add_argument("--account", required=True, help="expense account code")
    bl.set_defaults(func=cmd_bill_add_line)
    bfin = bsub.add_parser("finalize")
    bfin.add_argument("bill_id", type=int)
    bfin.add_argument("--tax", help="tax amount (decimal dollars)")
    bfin.add_argument("--tax-account", help="account code for tax (required if --tax)")
    bfin.set_defaults(func=cmd_bill_finalize)
    bll = bsub.add_parser("list")
    bll.add_argument("--status", choices=["draft", "open", "paid", "voided"])
    bll.set_defaults(func=cmd_bill_list)
    bshow = bsub.add_parser("show")
    bshow.add_argument("bill_id", type=int)
    bshow.set_defaults(func=cmd_bill_show)
    bv = bsub.add_parser("void")
    bv.add_argument("bill_id", type=int)
    bv.add_argument("--reason", required=True)
    bv.set_defaults(func=cmd_bill_void)

    # ---- payments ----
    pmt = sub.add_parser("payment", help="customer receipts and vendor payments")
    pmsub = pmt.add_subparsers(dest="pmt_cmd", required=True)
    pr = pmsub.add_parser("receive", help="receive payment from a customer")
    pr.add_argument("--party", required=True)
    pr.add_argument("--amount", required=True)
    pr.add_argument("--date")
    pr.add_argument("--cash-account")
    pr.add_argument("--memo")
    pr.add_argument("--reference")
    pr.add_argument("--invoice", action="append", required=True,
                    help='Repeat: "INVOICE_ID:AMOUNT"')
    pr.set_defaults(func=cmd_payment_receive)
    ps = pmsub.add_parser("send", help="send payment to a vendor")
    ps.add_argument("--party", required=True)
    ps.add_argument("--amount", required=True)
    ps.add_argument("--date")
    ps.add_argument("--cash-account")
    ps.add_argument("--memo")
    ps.add_argument("--reference")
    ps.add_argument("--bill", action="append", required=True,
                    help='Repeat: "BILL_ID:AMOUNT"')
    ps.set_defaults(func=cmd_payment_send)
    pls = pmsub.add_parser("list")
    pls.add_argument("--kind", choices=["receive", "send"])
    pls.set_defaults(func=cmd_payment_list)

    # ---- standalone expense ----
    exp = sub.add_parser("expense", help="record a standalone expense (no bill)")
    esub = exp.add_subparsers(dest="exp_cmd", required=True)
    ea = esub.add_parser("add")
    ea.add_argument("--account", required=True, help="expense account code")
    ea.add_argument("--amount", required=True)
    ea.add_argument("--cash-account", help="paid from (default 1010)")
    ea.add_argument("--vendor", help="vendor name (free text or existing party)")
    ea.add_argument("--date")
    ea.add_argument("--memo")
    ea.add_argument("--reference")
    ea.set_defaults(func=cmd_expense_add)

    # ---- documents + ingestion ----
    doc = sub.add_parser("doc", help="documents and AI extraction")
    dsub = doc.add_subparsers(dest="doc_cmd", required=True)
    di = dsub.add_parser("ingest", help="upload a file; optionally extract + propose")
    di.add_argument("path")
    di.add_argument("--type", help="doc type hint (receipt|bill|customer_invoice|other)")
    di.add_argument("--extract", action="store_true",
                    help="also run extraction right after ingest")
    di.add_argument("--propose", action="store_true",
                    help="with --extract, also propose an entry")
    di.add_argument("--heuristic", action="store_true",
                    help="force the no-network heuristic extractor")
    di.set_defaults(func=cmd_doc_ingest, auto_propose=False)
    de = dsub.add_parser("extract", help="run Claude extraction on a document")
    de.add_argument("doc_id", type=int)
    de.add_argument("--heuristic", action="store_true")
    de.add_argument("--auto-propose", action="store_true", dest="auto_propose")
    de.set_defaults(func=cmd_doc_extract)
    dp = dsub.add_parser("propose", help="stage a bill/invoice/JE from extracted data")
    dp.add_argument("doc_id", type=int)
    dp.add_argument("--cash-account", help="default 1010 (Checking)")
    dp.set_defaults(func=cmd_doc_propose)
    dls = dsub.add_parser("list")
    dls.add_argument("--status", choices=["pending", "extracted", "failed"])
    dls.set_defaults(func=cmd_doc_list)
    ds = dsub.add_parser("show")
    ds.add_argument("doc_id", type=int)
    ds.set_defaults(func=cmd_doc_show)

    # ---- proposed JEs ----
    prop = sub.add_parser("propose", help="review AI-proposed journal entries")
    prsub = prop.add_subparsers(dest="prop_cmd", required=True)
    prl = prsub.add_parser("list")
    prl.add_argument("--limit", type=int, default=100)
    prl.set_defaults(func=cmd_propose_list)
    pra = prsub.add_parser("approve")
    pra.add_argument("entry_id", type=int)
    pra.set_defaults(func=cmd_propose_approve)
    prr = prsub.add_parser("reject")
    prr.add_argument("entry_id", type=int)
    prr.set_defaults(func=cmd_propose_reject)

    tb = sub.add_parser("trial-balance")
    tb.add_argument("--as-of", help="YYYY-MM-DD")
    tb.set_defaults(func=cmd_trial_balance)

    pnl = sub.add_parser("pnl", help="profit & loss over a period")
    pnl.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    pnl.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    pnl.set_defaults(func=cmd_pnl)

    bs = sub.add_parser("balance-sheet", help="assets / liabilities / equity snapshot")
    bs.add_argument("--as-of", help="YYYY-MM-DD")
    bs.set_defaults(func=cmd_balance_sheet)

    gl = sub.add_parser("gl", help="general ledger (transaction register per account)")
    gl.add_argument("--account", help="limit to one account by code")
    gl.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    gl.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    gl.set_defaults(func=cmd_gl)

    # ---- bank CSV ingest ----
    bk = sub.add_parser("bank", help="bank statement CSV ingestion")
    bksub = bk.add_subparsers(dest="bank_cmd", required=True)
    bki = bksub.add_parser("ingest")
    bki.add_argument("path", help="path to a bank statement CSV")
    bki.add_argument("--cash-account", help="default 1010 Checking")
    bki.set_defaults(func=cmd_bank_ingest)

    # ---- conversational agent ----
    ch = sub.add_parser("chat", help="talk to qbai (Claude agent)")
    ch.add_argument("message", nargs="?",
                    help="one-shot message; omit for interactive REPL")
    ch.set_defaults(func=cmd_chat)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, LookupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
