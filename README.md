# qbai

A standalone bookkeeping engine with a Claude-powered AI agent on top.

Goal: an AI agent that does the kind of work a QuickBooks user would do —
ingest receipts/invoices/bank statements, keep the books in proper
double-entry form, and produce the reports an accountant would expect for
verification. **Not** an exact clone of QuickBooks Advanced — that's a
multi-year proprietary platform. This is a focused, open implementation of
the core accounting workflow.

## Current status

| Module | Status |
| --- | --- |
| SQLite schema | done |
| Standard chart of accounts | done |
| Double-entry ledger with `debits == credits` enforcement | done |
| `proposed` → `approved` workflow (for AI-generated entries) | done |
| Voiding via reversing entries | done |
| Trial balance report | done |
| Audit log | done |
| Money as integer cents (no float drift) | done |
| Customers and vendors (parties) | done |
| A/R invoices (draft → open → paid/voided), auto-posts JE on finalize | done |
| A/P bills (draft → open → paid/voided), auto-posts JE on finalize | done |
| Customer receipts (`payment receive`) with multi-invoice allocation | done |
| Vendor payments (`payment send`) with multi-bill allocation | done |
| Standalone expenses (no bill, direct DR expense / CR cash) | done |
| Document ingestion (store + text extract for pdf/csv/txt; vision deferred) | done |
| Claude-powered field extraction with heuristic fallback | done |
| Propose bill / invoice / proposed JE from extracted data | done |
| Approve / reject proposed journal entries | done |
| Profit & Loss, Balance Sheet, General Ledger | next slice |
| Conversational agent (Claude tool-use loop) | next slice |
| Payroll / inventory | later slices |

## Try it

```bash
# Initialize DB and seed the chart of accounts
python -m qbai.cli init

# Accounts
python -m qbai.cli accounts list
python -m qbai.cli accounts list --type expense

# Manual journal entry
python -m qbai.cli je post \
  --date 2026-05-15 --memo "Cash sale of widgets" \
  --line "1010:debit:250.00:Deposit" \
  --line "4000:credit:250.00:Widget sale"
python -m qbai.cli je show 1
python -m qbai.cli je void 1 --reason "duplicate"

# Parties (customers and vendors)
python -m qbai.cli parties add "Acme Corp"  --kind customer
python -m qbai.cli parties add "Office Depot" --kind vendor

# Customer invoice (A/R) — auto-posts JE on finalize
python -m qbai.cli invoice create --party "Acme Corp" --date 2026-05-01 --memo "Consulting"
python -m qbai.cli invoice add-line 1 --description "Consulting" --qty 20 --unit-price 150 --account 4100
python -m qbai.cli invoice finalize 1
python -m qbai.cli invoice show 1

# Vendor bill (A/P) — auto-posts JE on finalize
python -m qbai.cli bill create --party "Office Depot" --number "OD-9912"
python -m qbai.cli bill add-line 1 --description "Paper" --qty 2 --unit-price 25 --account 6200
python -m qbai.cli bill finalize 1

# Payments
python -m qbai.cli payment receive --party "Acme Corp" --amount 2000 --invoice 1:2000
python -m qbai.cli payment send    --party "Office Depot" --amount 50  --bill 1:50

# Standalone expense (paid directly, no bill)
python -m qbai.cli expense add --account 6300 --amount 38.50 --vendor "Some Restaurant" --memo "Client lunch"

# Reports
python -m qbai.cli trial-balance
```

## Document ingestion (the AI piece)

Drop a receipt, bill, or customer invoice as a PDF, CSV, or plain-text file.
Claude reads it, proposes an accounting entry, and the entry sits in a
`proposed` (for JEs) or `draft` (for bills/invoices) state until you approve it.

```bash
# One-shot: store the file, extract fields, propose an entry
python -m qbai.cli doc ingest /path/to/receipt.pdf --extract --propose

# Or step by step
python -m qbai.cli doc ingest /path/to/bill.pdf
python -m qbai.cli doc extract 1                 # run Claude extraction
python -m qbai.cli doc propose 1                 # stage proposed entry
python -m qbai.cli doc show 1                    # inspect extracted fields

# Review the queue
python -m qbai.cli doc list
python -m qbai.cli propose list

# Approve a proposed JE (becomes posted)
python -m qbai.cli propose approve 1
# Or reject (deletes the proposed JE)
python -m qbai.cli propose reject 1

# For proposed bills/invoices, review and finalize instead:
python -m qbai.cli bill show 1
python -m qbai.cli bill finalize 1 --tax 2.96 --tax-account 6999
```

### Extraction modes

- **Claude** (default): uses the Anthropic API. Needs `ANTHROPIC_API_KEY` and
  the `anthropic` SDK installed. Override the model with
  `QBAI_CLAUDE_MODEL=claude-sonnet-5-5`.
- **Heuristic** (`--heuristic` flag): no network; uses regex-based extraction.
  Deterministic; good for tests and demos, and the automatic fallback when
  Claude isn't configured.

PDF support needs `pypdf` (`pip install pypdf`). Images (PNG / JPG) are stored
but local OCR isn't included — Claude Vision support for images is a later
slice.

The DB lives at `data/qbai.db` by default; pass `--db /some/other.db`
to override.

## Run the tests

```bash
python -m unittest discover tests -v
```

## Design notes

- **Money is integer cents everywhere** that touches the ledger. Floats
  do not balance to the penny over long ledgers, so they live only in
  display formatting.
- **Every journal entry is balanced at post time** (sum of debits == sum
  of credits), inside a single SQLite transaction.
- **AI-generated entries are staged as `proposed`** and excluded from the
  trial balance until a human approves them — this is the seam where the
  agent will plug in.
- **Voids are reversing entries, not deletes.** The original entry stays
  in the ledger marked `voided` with a pointer to the reversal — that's
  what an accountant expects to see during verification.
- **Every state-changing action writes to `audit_log`** with actor, action,
  entity, and timestamp.
