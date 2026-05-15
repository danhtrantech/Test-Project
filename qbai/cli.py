"""qbai CLI. Run with `python -m qbai.cli <command> ...`

Foundation-slice commands:
    init                          # create the DB and seed the chart of accounts
    accounts list [--type T]      # list chart of accounts
    accounts add CODE NAME TYPE   # add an account
    je post --date D --memo M --line "CODE:debit:AMOUNT[:desc]" ...
    je list [--status S]
    je show ENTRY_ID
    je void ENTRY_ID --reason "..."
    trial-balance [--as-of D]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .coa import add_account, list_accounts, seed_chart_of_accounts
from .db import get_connection, init_db
from .ledger import (
    JournalLine, get_entry, list_entries, post_journal_entry, void_entry,
)
from .money import format_money, to_cents
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
