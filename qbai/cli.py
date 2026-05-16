"""qbai CLI. Run with `python -m qbai.cli <command> ...`

See README.md for the full command reference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ap, ar, expenses, payments
from .coa import add_account, list_accounts, seed_chart_of_accounts
from .db import get_connection, init_db
from .ledger import (
    JournalLine, get_entry, list_entries, post_journal_entry, void_entry,
)
from .money import format_money, to_cents
from .parties import add_party, list_parties, require_party_by_name
from .reports import trial_balance


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

    tb = sub.add_parser("trial-balance")
    tb.add_argument("--as-of", help="YYYY-MM-DD")
    tb.set_defaults(func=cmd_trial_balance)

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
