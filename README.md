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
| Document ingestion + Claude extraction | next slice |
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
