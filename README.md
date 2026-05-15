# qbai

A standalone bookkeeping engine with a Claude-powered AI agent on top.

Goal: an AI agent that does the kind of work a QuickBooks user would do —
ingest receipts/invoices/bank statements, keep the books in proper
double-entry form, and produce the reports an accountant would expect for
verification. **Not** an exact clone of QuickBooks Advanced — that's a
multi-year proprietary platform. This is a focused, open implementation of
the core accounting workflow.

## Current status: foundation slice

This first slice contains the load-bearing core that everything else will
sit on top of.

| Module | Status |
| --- | --- |
| SQLite schema (accounts, journal entries, journal lines, audit log) | done |
| Standard chart of accounts | done |
| Double-entry ledger with `debits == credits` enforcement | done |
| `proposed` → `approved` workflow (for AI-generated entries) | done |
| Voiding via reversing entries | done |
| Trial balance report | done |
| Audit log | done |
| Money as integer cents (no float drift) | done |
| Customers / vendors | next slice |
| A/R invoices + payments | next slice |
| A/P bills + payments | next slice |
| Document ingestion + Claude extraction | next slice |
| Profit & Loss, Balance Sheet, General Ledger | next slice |
| Conversational agent (Claude tool-use loop) | next slice |
| Payroll / inventory | later slices |

## Try it

```bash
# Initialize DB and seed the chart of accounts
python -m qbai.cli init

# List accounts
python -m qbai.cli accounts list
python -m qbai.cli accounts list --type expense

# Post a manual journal entry (cash sale of $250)
python -m qbai.cli je post \
  --date 2026-05-15 \
  --memo "Cash sale of widgets" \
  --line "1010:debit:250.00:Deposit" \
  --line "4000:credit:250.00:Widget sale"

# Inspect entries
python -m qbai.cli je list
python -m qbai.cli je show 1

# Trial balance
python -m qbai.cli trial-balance

# Void an entry (creates a reversing entry; original is marked 'voided')
python -m qbai.cli je void 1 --reason "duplicate"
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
