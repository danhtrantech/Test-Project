"""Conversational agent: Claude with tool-use over the qbai business APIs.

The agent exposes the day-to-day bookkeeping operations as tools so a user
can say "create an invoice for Acme for 20 hours of consulting at $150/hr"
or "what's open A/P?" or "ingest the receipt at /tmp/starbucks.pdf" without
remembering the CLI flags.

Requires ANTHROPIC_API_KEY. The `anthropic` SDK is imported lazily so the
rest of qbai works without it. Tool dispatch (dispatch_tool) is a pure
function of (conn, name, args) and is fully testable without network.

Safety: every tool call runs against the live DB. For writes that are hard
to undo (void, finalize, record a payment, approve a proposed JE) the
agent is instructed to confirm with the user before calling the tool.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from . import ap, ar, bank, documents, expenses, extract, ingest, payments
from .coa import list_accounts
from .ledger import (
    JournalLine, approve_proposed, get_entry, list_entries, post_journal_entry,
    void_entry,
)
from .money import format_money, to_cents
from .parties import (
    add_party, find_party, list_parties, require_party_by_name,
)
from .reports import balance_sheet, general_ledger, profit_and_loss, trial_balance


# ---------- Tool definitions (JSON schema for Anthropic tool use) ----------

TOOLS: list[dict] = [
    {
        "name": "list_accounts",
        "description": "List the chart of accounts. Optionally filter by type "
                       "(asset|liability|equity|income|expense).",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string",
                         "enum": ["asset", "liability", "equity", "income", "expense"]},
            },
        },
    },
    {
        "name": "list_parties",
        "description": "List customers and/or vendors.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["customer", "vendor"]},
            },
        },
    },
    {
        "name": "add_party",
        "description": "Add a new customer or vendor.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["customer", "vendor", "both"]},
                "email": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": ["name", "kind"],
        },
    },
    {
        "name": "create_invoice",
        "description": "Create a draft customer invoice. Add lines with "
                       "add_invoice_line, then finalize_invoice when ready.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "number": {"type": "string"},
                "memo": {"type": "string"},
            },
            "required": ["customer_name"],
        },
    },
    {
        "name": "add_invoice_line",
        "description": "Add a line to a draft invoice.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "integer"},
                "description": {"type": "string"},
                "quantity": {"type": "number"},
                "unit_price": {"type": "number",
                               "description": "In dollars, decimal (e.g. 150.00)"},
                "income_account_code": {"type": "string",
                                        "description": "e.g. '4000' or '4100'"},
            },
            "required": ["invoice_id", "quantity", "unit_price", "income_account_code"],
        },
    },
    {
        "name": "finalize_invoice",
        "description": "Finalize a draft invoice: post the journal entry and move "
                       "it to 'open'. Confirm with the user first (hard to undo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "integer"},
                "tax": {"type": "number", "description": "Tax amount in dollars"},
                "tax_account_code": {"type": "string",
                                     "description": "default 2100 Sales Tax Payable"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "list_invoices",
        "description": "List invoices. Filter by status and/or customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "enum": ["draft", "open", "paid", "voided"]},
                "customer_name": {"type": "string"},
            },
        },
    },
    {
        "name": "get_invoice",
        "description": "Get full details of one invoice (header + lines + totals).",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "integer"}},
            "required": ["invoice_id"],
        },
    },
    {
        "name": "void_invoice",
        "description": "Void an open or paid invoice (posts a reversing JE). "
                       "Confirm with user first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["invoice_id", "reason"],
        },
    },
    {
        "name": "create_bill",
        "description": "Create a draft vendor bill (A/P). Add lines with "
                       "add_bill_line, then finalize_bill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor_name": {"type": "string"},
                "bill_date": {"type": "string"},
                "due_date": {"type": "string"},
                "number": {"type": "string"},
                "memo": {"type": "string"},
            },
            "required": ["vendor_name"],
        },
    },
    {
        "name": "add_bill_line",
        "description": "Add a line to a draft bill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "integer"},
                "description": {"type": "string"},
                "quantity": {"type": "number"},
                "unit_price": {"type": "number"},
                "expense_account_code": {"type": "string"},
            },
            "required": ["bill_id", "quantity", "unit_price", "expense_account_code"],
        },
    },
    {
        "name": "finalize_bill",
        "description": "Finalize a draft bill; post the JE. Confirm first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "integer"},
                "tax": {"type": "number"},
                "tax_account_code": {"type": "string"},
            },
            "required": ["bill_id"],
        },
    },
    {
        "name": "list_bills",
        "description": "List bills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "enum": ["draft", "open", "paid", "voided"]},
                "vendor_name": {"type": "string"},
            },
        },
    },
    {
        "name": "get_bill",
        "description": "Get full details of one bill.",
        "input_schema": {
            "type": "object",
            "properties": {"bill_id": {"type": "integer"}},
            "required": ["bill_id"],
        },
    },
    {
        "name": "receive_payment",
        "description": "Record a customer payment and allocate it across invoices. "
                       "Allocation amounts in dollars must sum to the total amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "amount": {"type": "number", "description": "Total payment in dollars"},
                "payment_date": {"type": "string"},
                "allocations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "invoice_id": {"type": "integer"},
                            "amount": {"type": "number"},
                        },
                        "required": ["invoice_id", "amount"],
                    },
                },
                "memo": {"type": "string"},
            },
            "required": ["customer_name", "amount", "allocations"],
        },
    },
    {
        "name": "send_payment",
        "description": "Record a payment sent to a vendor against one or more bills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor_name": {"type": "string"},
                "amount": {"type": "number"},
                "payment_date": {"type": "string"},
                "allocations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "bill_id": {"type": "integer"},
                            "amount": {"type": "number"},
                        },
                        "required": ["bill_id", "amount"],
                    },
                },
                "memo": {"type": "string"},
            },
            "required": ["vendor_name", "amount", "allocations"],
        },
    },
    {
        "name": "record_expense",
        "description": "Record a standalone expense (direct purchase, no bill).",
        "input_schema": {
            "type": "object",
            "properties": {
                "expense_account_code": {"type": "string"},
                "amount": {"type": "number"},
                "vendor_name": {"type": "string"},
                "expense_date": {"type": "string"},
                "memo": {"type": "string"},
                "paid_from_account_code": {"type": "string",
                                           "description": "default 1010 Checking"},
            },
            "required": ["expense_account_code", "amount"],
        },
    },
    {
        "name": "ingest_document",
        "description": "Ingest a document (PDF/CSV/TXT/image) from a local path, "
                       "run extraction, and propose an accounting entry. Returns "
                       "doc_id and the proposal (which still needs human approval).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "doc_type": {"type": "string",
                             "description": "receipt|bill|customer_invoice|other"},
                "force_heuristic": {"type": "boolean",
                                    "description": "skip Claude extractor"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_documents",
        "description": "List ingested documents.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_proposed_entries",
        "description": "List proposed journal entries awaiting approval.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "approve_proposed_entry",
        "description": "Approve a proposed JE (moves it to posted).",
        "input_schema": {
            "type": "object",
            "properties": {"entry_id": {"type": "integer"}},
            "required": ["entry_id"],
        },
    },
    {
        "name": "post_manual_journal_entry",
        "description": "Post a manual balanced journal entry. Lines must sum to zero "
                       "(debits == credits). Amounts in dollars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "memo": {"type": "string"},
                "reference": {"type": "string"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_code": {"type": "string"},
                            "debit": {"type": "number", "description": "In dollars"},
                            "credit": {"type": "number", "description": "In dollars"},
                            "description": {"type": "string"},
                        },
                        "required": ["account_code"],
                    },
                },
            },
            "required": ["lines"],
        },
    },
    {
        "name": "ingest_bank_csv",
        "description": "Ingest a bank-statement CSV and propose one journal entry "
                       "per transaction. All entries sit in 'proposed' status for "
                       "the user to approve.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "cash_account_code": {"type": "string",
                                      "description": "default 1010 Checking"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "report",
        "description": "Run one of the financial reports. Returns structured results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "enum": ["trial_balance", "pnl", "balance_sheet",
                                  "general_ledger"]},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "as_of": {"type": "string"},
                "account_code": {"type": "string"},
            },
            "required": ["name"],
        },
    },
]


# ---------- Tool dispatch (pure function; fully testable offline) ----------

@dataclass
class ToolResult:
    ok: bool
    data: Any
    error: str | None = None

    def as_claude_content(self) -> str:
        """Render for the model's tool_result block. Keep compact."""
        if not self.ok:
            return json.dumps({"error": self.error}, default=str)
        return json.dumps(self.data, default=str, indent=None)[:20000]


def dispatch_tool(conn: sqlite3.Connection, name: str, args: dict,
                  actor: str = "agent") -> ToolResult:
    fn = _TOOL_IMPL.get(name)
    if fn is None:
        return ToolResult(False, None, f"unknown tool: {name!r}")
    try:
        return ToolResult(True, fn(conn, args, actor))
    except (ValueError, LookupError) as e:
        return ToolResult(False, None, f"{type(e).__name__}: {e}")
    except Exception as e:
        return ToolResult(False, None, f"unexpected {type(e).__name__}: {e}")


# --- individual tool implementations ---

def _t_list_accounts(conn, args, actor):
    return [
        {"code": a.code, "name": a.name, "type": a.type}
        for a in list_accounts(conn, type_=args.get("type"))
    ]


def _t_list_parties(conn, args, actor):
    return [
        {"id": p.id, "name": p.name, "kind": p.kind, "email": p.email}
        for p in list_parties(conn, kind=args.get("kind"))
    ]


def _t_add_party(conn, args, actor):
    p = add_party(conn, args["name"], args["kind"],
                  email=args.get("email"), phone=args.get("phone"), actor=actor)
    return {"id": p.id, "name": p.name, "kind": p.kind}


def _party_id_by_name(conn, name: str, kind: str):
    return require_party_by_name(conn, name, kind=kind).id


def _t_create_invoice(conn, args, actor):
    pid = _party_id_by_name(conn, args["customer_name"], "customer")
    iid = ar.create_invoice(
        conn, party_id=pid, invoice_date=args.get("invoice_date"),
        due_date=args.get("due_date"), number=args.get("number"),
        memo=args.get("memo"), actor=actor,
    )
    return {"invoice_id": iid, "status": "draft"}


def _t_add_invoice_line(conn, args, actor):
    lid = ar.add_invoice_line(
        conn, args["invoice_id"], description=args.get("description"),
        quantity=float(args["quantity"]),
        unit_price_cents=to_cents(args["unit_price"]),
        income_account_code=args["income_account_code"],
    )
    return {"line_id": lid}


def _t_finalize_invoice(conn, args, actor):
    je_id = ar.finalize_invoice(
        conn, args["invoice_id"],
        tax_cents=to_cents(args["tax"]) if args.get("tax") else 0,
        tax_account_code=args.get("tax_account_code") or ar.DEFAULT_TAX_ACCOUNT,
        actor=actor,
    )
    return {"journal_entry_id": je_id, "status": "open"}


def _t_list_invoices(conn, args, actor):
    pid = None
    if args.get("customer_name"):
        p = find_party(conn, args["customer_name"], kind="customer")
        if p is None:
            return []
        pid = p.id
    return ar.list_invoices(conn, status=args.get("status"), party_id=pid)


def _t_get_invoice(conn, args, actor):
    inv = ar.get_invoice(conn, args["invoice_id"])
    if inv is None:
        raise LookupError(f"invoice {args['invoice_id']}")
    return {
        "id": inv.id, "status": inv.status, "customer": inv.party_name,
        "invoice_date": inv.invoice_date, "due_date": inv.due_date,
        "number": inv.number, "memo": inv.memo,
        "subtotal": inv.subtotal_cents / 100,
        "tax": inv.tax_cents / 100,
        "total": inv.total_cents / 100,
        "paid": inv.amount_paid_cents / 100,
        "balance": (inv.total_cents - inv.amount_paid_cents) / 100,
        "lines": [
            {"description": l.description, "quantity": l.quantity,
             "unit_price": l.unit_price_cents / 100,
             "amount": l.amount_cents / 100,
             "account": l.income_account_code}
            for l in inv.lines
        ],
    }


def _t_void_invoice(conn, args, actor):
    rev = ar.void_invoice(conn, args["invoice_id"], reason=args["reason"], actor=actor)
    return {"reversing_journal_entry_id": rev}


def _t_create_bill(conn, args, actor):
    pid = _party_id_by_name(conn, args["vendor_name"], "vendor")
    bid = ap.create_bill(
        conn, party_id=pid, bill_date=args.get("bill_date"),
        due_date=args.get("due_date"), number=args.get("number"),
        memo=args.get("memo"), actor=actor,
    )
    return {"bill_id": bid, "status": "draft"}


def _t_add_bill_line(conn, args, actor):
    lid = ap.add_bill_line(
        conn, args["bill_id"], description=args.get("description"),
        quantity=float(args["quantity"]),
        unit_price_cents=to_cents(args["unit_price"]),
        expense_account_code=args["expense_account_code"],
    )
    return {"line_id": lid}


def _t_finalize_bill(conn, args, actor):
    je_id = ap.finalize_bill(
        conn, args["bill_id"],
        tax_cents=to_cents(args["tax"]) if args.get("tax") else 0,
        tax_account_code=args.get("tax_account_code"),
        actor=actor,
    )
    return {"journal_entry_id": je_id, "status": "open"}


def _t_list_bills(conn, args, actor):
    pid = None
    if args.get("vendor_name"):
        p = find_party(conn, args["vendor_name"], kind="vendor")
        if p is None:
            return []
        pid = p.id
    return ap.list_bills(conn, status=args.get("status"), party_id=pid)


def _t_get_bill(conn, args, actor):
    b = ap.get_bill(conn, args["bill_id"])
    if b is None:
        raise LookupError(f"bill {args['bill_id']}")
    return {
        "id": b.id, "status": b.status, "vendor": b.party_name,
        "bill_date": b.bill_date, "due_date": b.due_date, "number": b.number,
        "subtotal": b.subtotal_cents / 100,
        "tax": b.tax_cents / 100, "total": b.total_cents / 100,
        "paid": b.amount_paid_cents / 100,
        "balance": (b.total_cents - b.amount_paid_cents) / 100,
        "lines": [
            {"description": l.description, "quantity": l.quantity,
             "unit_price": l.unit_price_cents / 100,
             "amount": l.amount_cents / 100,
             "account": l.expense_account_code}
            for l in b.lines
        ],
    }


def _t_receive_payment(conn, args, actor):
    pid = _party_id_by_name(conn, args["customer_name"], "customer")
    allocs = [
        payments.Allocation(invoice_id=a["invoice_id"],
                            amount_cents=to_cents(a["amount"]))
        for a in args["allocations"]
    ]
    payment_id = payments.receive_payment(
        conn, party_id=pid, amount_cents=to_cents(args["amount"]),
        allocations=allocs, payment_date=args.get("payment_date"),
        memo=args.get("memo"), actor=actor,
    )
    return {"payment_id": payment_id}


def _t_send_payment(conn, args, actor):
    pid = _party_id_by_name(conn, args["vendor_name"], "vendor")
    allocs = [
        payments.Allocation(bill_id=a["bill_id"],
                            amount_cents=to_cents(a["amount"]))
        for a in args["allocations"]
    ]
    payment_id = payments.send_payment(
        conn, party_id=pid, amount_cents=to_cents(args["amount"]),
        allocations=allocs, payment_date=args.get("payment_date"),
        memo=args.get("memo"), actor=actor,
    )
    return {"payment_id": payment_id}


def _t_record_expense(conn, args, actor):
    je_id = expenses.record_expense(
        conn, expense_account_code=args["expense_account_code"],
        amount_cents=to_cents(args["amount"]),
        paid_from_account_code=args.get("paid_from_account_code") or "1010",
        expense_date=args.get("expense_date"),
        vendor_name=args.get("vendor_name"),
        memo=args.get("memo"), actor=actor,
    )
    return {"journal_entry_id": je_id}


def _t_ingest_document(conn, args, actor):
    doc_id = documents.ingest_file(conn, args["path"],
                                   doc_type=args.get("doc_type"), actor=actor)
    doc = documents.get_document(conn, doc_id)
    if doc.extraction_status == "failed":
        return {"document_id": doc_id, "status": "failed",
                "error": doc.extraction_error}
    coa = [{"code": a.code, "name": a.name, "type": a.type}
           for a in list_accounts(conn)]
    is_image = (doc.mime_type or "").startswith("image/")
    if is_image and not args.get("force_heuristic"):
        result = extract.extract_image_with_claude(
            doc.stored_path, chart_of_accounts=coa, doc_type_hint=args.get("doc_type"),
        )
    else:
        result = extract.extract(
            doc.extracted_text or "", chart_of_accounts=coa,
            doc_type_hint=args.get("doc_type"),
            force_heuristic=bool(args.get("force_heuristic")),
        )
    documents.set_extracted_data(conn, doc_id, result.data,
                                 extractor=result.extractor,
                                 doc_type=result.data.get("doc_type"))
    proposal = ingest.propose_from_document(conn, doc_id, actor=actor)
    return {
        "document_id": doc_id, "extractor": result.extractor,
        "extracted": {
            "doc_type": result.data.get("doc_type"),
            "vendor_or_customer": result.data.get("vendor_or_customer_name"),
            "date": result.data.get("date"),
            "total": (result.data.get("total_cents") or 0) / 100,
        },
        "proposed": {"kind": proposal.kind, "target_id": proposal.target_id,
                     "next_step": proposal.requires},
    }


def _t_list_documents(conn, args, actor):
    return documents.list_documents(conn)


def _t_list_proposed_entries(conn, args, actor):
    return list_entries(conn, status="proposed")


def _t_approve_proposed_entry(conn, args, actor):
    approve_proposed(conn, args["entry_id"], approved_by=actor)
    return {"entry_id": args["entry_id"], "status": "posted"}


def _t_post_manual_je(conn, args, actor):
    lines = []
    for L in args["lines"]:
        debit = to_cents(L["debit"]) if L.get("debit") else 0
        credit = to_cents(L["credit"]) if L.get("credit") else 0
        lines.append(JournalLine(
            account_code=L["account_code"], debit=debit, credit=credit,
            description=L.get("description"),
        ))
    je_id = post_journal_entry(
        conn, lines=lines, entry_date=args.get("date"),
        memo=args.get("memo"), reference=args.get("reference"),
        created_by=actor,
    )
    return {"journal_entry_id": je_id}


def _t_ingest_bank_csv(conn, args, actor):
    res = bank.ingest_bank_csv(
        conn, args["path"],
        cash_account_code=args.get("cash_account_code") or "1010",
        actor=actor,
    )
    return {
        "document_id": res.doc_id,
        "rows_parsed": res.rows_parsed,
        "proposed_journal_entry_ids": res.proposed_je_ids,
        "skipped": [{"row": i, "reason": r} for i, r in res.skipped],
    }


def _t_report(conn, args, actor):
    name = args["name"]
    if name == "trial_balance":
        rows = trial_balance(conn, as_of=args.get("as_of"))
        return [{"code": r.code, "name": r.name, "type": r.type,
                 "debit": r.debit / 100, "credit": r.credit / 100} for r in rows]
    if name == "pnl":
        pnl = profit_and_loss(conn, date_from=args.get("date_from"),
                              date_to=args.get("date_to"))
        return {
            "period": {"from": pnl.period_start, "to": pnl.period_end},
            "income": [{"code": r.code, "name": r.name, "amount": r.amount / 100}
                       for r in pnl.income],
            "expenses": [{"code": r.code, "name": r.name, "amount": r.amount / 100}
                         for r in pnl.expenses],
            "total_income": pnl.total_income / 100,
            "total_expenses": pnl.total_expenses / 100,
            "net_income": pnl.net_income / 100,
        }
    if name == "balance_sheet":
        bs = balance_sheet(conn, as_of=args.get("as_of"))
        return {
            "as_of": bs.as_of,
            "assets": [{"code": r.code, "name": r.name, "amount": r.amount / 100}
                       for r in bs.assets.rows],
            "liabilities": [{"code": r.code, "name": r.name, "amount": r.amount / 100}
                            for r in bs.liabilities.rows],
            "equity": [{"code": r.code, "name": r.name, "amount": r.amount / 100}
                       for r in bs.equity.rows],
            "net_income_to_date": bs.net_income_to_date / 100,
            "total_assets": bs.assets.total / 100,
            "total_liabilities": bs.liabilities.total / 100,
            "total_equity": bs.equity.total / 100,
        }
    if name == "general_ledger":
        accounts = general_ledger(
            conn, account_code=args.get("account_code"),
            date_from=args.get("date_from"), date_to=args.get("date_to"),
        )
        return [
            {"code": a.code, "name": a.name, "type": a.type,
             "opening_balance": a.opening_balance / 100,
             "closing_balance": a.closing_balance / 100,
             "entries": [
                 {"date": e.date, "entry_id": e.entry_id, "memo": e.memo,
                  "debit": e.debit / 100, "credit": e.credit / 100,
                  "balance": e.balance / 100,
                  "description": e.description}
                 for e in a.entries
             ]}
            for a in accounts
        ]
    raise ValueError(f"unknown report: {name}")


_TOOL_IMPL: dict[str, Callable] = {
    "list_accounts": _t_list_accounts,
    "list_parties": _t_list_parties,
    "add_party": _t_add_party,
    "create_invoice": _t_create_invoice,
    "add_invoice_line": _t_add_invoice_line,
    "finalize_invoice": _t_finalize_invoice,
    "list_invoices": _t_list_invoices,
    "get_invoice": _t_get_invoice,
    "void_invoice": _t_void_invoice,
    "create_bill": _t_create_bill,
    "add_bill_line": _t_add_bill_line,
    "finalize_bill": _t_finalize_bill,
    "list_bills": _t_list_bills,
    "get_bill": _t_get_bill,
    "receive_payment": _t_receive_payment,
    "send_payment": _t_send_payment,
    "record_expense": _t_record_expense,
    "ingest_document": _t_ingest_document,
    "ingest_bank_csv": _t_ingest_bank_csv,
    "list_documents": _t_list_documents,
    "list_proposed_entries": _t_list_proposed_entries,
    "approve_proposed_entry": _t_approve_proposed_entry,
    "post_manual_journal_entry": _t_post_manual_je,
    "report": _t_report,
}


SYSTEM_PROMPT = """You are qbai, a bookkeeping assistant that manages a user's \
double-entry books through the provided tools. Keep responses concise.

Rules:
- Use tools to look up data before answering. Do not guess balances or IDs.
- Money inputs to tools are decimal dollars (e.g. 150.00). Internally the \
system stores integer cents.
- When creating an invoice or bill, workflow is: create_invoice (draft) -> \
add_invoice_line (one call per line) -> finalize_invoice. Same for bills.
- Before taking an action that is hard to undo (finalize, void, record a \
payment, approve a proposed JE), confirm with the user unless they were \
explicit.
- For a question like "what do I owe?" use the `report` tool (balance_sheet \
or general_ledger on account 2000), or list_bills with status=open.
- When ingesting a document, present the proposed extraction and ask the \
user to confirm before approving/finalizing.
- Chart of accounts is standard. If an account code is ambiguous, call \
list_accounts first.
"""


# ---------- Chat loop ------------------------------------------------------

def chat_once(
    conn: sqlite3.Connection, user_message: str,
    *, history: list[dict] | None = None, max_iterations: int = 10,
    model: str | None = None, actor: str = "agent",
) -> dict:
    """Run one agent turn (possibly multiple tool round-trips). Returns
    {"text": final_assistant_text, "messages": full_message_history}."""
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed (pip install anthropic)") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    model = model or os.environ.get("QBAI_CLAUDE_MODEL", "claude-sonnet-5-5")
    client = anthropic.Anthropic()

    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})

    for _ in range(max_iterations):
        resp = client.messages.create(
            model=model, max_tokens=2048,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content
                           if getattr(b, "type", None) == "text")
            return {"text": text, "messages": messages}

        # Dispatch every tool_use block and feed results back.
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result = dispatch_tool(conn, block.name, dict(block.input), actor=actor)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result.as_claude_content(),
                "is_error": not result.ok,
            })
        messages.append({"role": "user", "content": tool_results})

    return {"text": "(agent exceeded max_iterations)", "messages": messages}
