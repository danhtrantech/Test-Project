---
name: prep-taxes
description: Walk through a US federal tax-document checklist — enumerate the W-2s, 1099s, 1098s, 1095s, and receipts the user needs, search their connected email for what has already arrived, mark each item received/pending/missing, and produce concrete next steps for the gaps. Use this whenever the user is gathering, chasing, or taking stock of tax paperwork, including casual phrasings like "what am I still missing for taxes", "did my 1099 ever show up", "help me get my tax stuff together for my CPA", "time to do taxes", or when they mention W-2s, 1099s, tax forms, or a tax checklist. Also use it for follow-up runs as documents trickle in. This is document logistics, not tax advice, and it never asks for SSNs or tax IDs.
---

# Prep Taxes

Filing is blocked by paperwork far more often than by arithmetic. This skill closes that gap: it turns "I should do my taxes" into a concrete inventory of what has arrived, what is late, and who to chase.

The user is not asking how much they owe. They are asking what is still missing. Stay on that job.

## Establish the tax year first

Everything downstream depends on it, and getting it wrong silently produces a useless checklist built from the wrong year's email.

Infer a default from today's date — most of the year that means the most recently completed calendar year — and state your assumption rather than asking cold: "Working on tax year 2025 — say the word if you mean a different year." Only stop and ask if the context genuinely points two ways, such as someone mentioning an extension or amending an old return.

## Build the checklist

Start from the standard set below, then adapt. A W-2 employee with a mortgage and a freelance side business needs a different list than a retiree, and a checklist padded with irrelevant rows makes the real gaps harder to see.

| Document | Typically from | Usually due |
|---|---|---|
| W-2 | Each employer | Jan 31 |
| 1099-NEC / 1099-MISC | Freelance and contract clients | Jan 31 |
| 1099-INT / 1099-DIV | Banks, brokerages | Jan 31 |
| 1099-B | Brokerages (sales, cost basis) | Mid-February |
| 1099-K | Payment platforms | Jan 31 |
| 1099-R | Retirement distributions | Jan 31 |
| 1098 | Mortgage lender | Jan 31 |
| 1098-E | Student loan servicer | Jan 31 |
| 1098-T | Colleges | Jan 31 |
| 1095-A / B / C | Marketplace, insurer, or employer | Jan 31 – Mar |
| K-1 | Partnerships, S-corps, trusts | Often March or later |
| Charitable donation receipts | Each charity | Varies |
| Property tax records | County or lender escrow statement | Varies |
| Childcare expenses | Provider, with their tax ID | Varies |

Ask what applies to their situation rather than guessing from silence — one question about employers, side income, property, students, and dependents usually resolves the whole shape of the list. `references/documents.md` has the per-form detail: who issues it, what triggers needing it, and where to find it online when it never arrives.

## Search email for what has landed

This is where the skill earns its keep. Most of these documents arrive as "your tax document is ready" notifications rather than attachments, so they are sitting in the inbox unread and unrecognized.

Use whatever email tool is connected. Search per document type rather than firing one broad "tax" query — a single query buries real notices under receipts and promotions. `references/documents.md` lists search terms per form. Scope every search to the relevant window: issuers send in January and February for the prior tax year, so search from roughly December through April of the filing year.

Read the results for what they actually say. "Your 1099-INT is available" means received. "We'll notify you when your documents are ready" means pending, not received. Note the issuer name and date so the user can find the message again.

If no email tool is connected, say so plainly and run the checklist from what the user tells you. The skill still works; it just gets less done for them.

## Sort into three buckets

Every line lands in exactly one:

- **Received** — you found it, or the user confirms they have it.
- **Pending** — expected, not yet past its due date. Include the date it should arrive.
- **Missing** — past due, or expected and unaccounted for. These are the ones that need action.

Do not report a document as received on the strength of a "coming soon" email. A false positive here means the user discovers the gap at filing time, which is exactly the failure this skill exists to prevent.

## Turn every gap into an action

A gap with no next step just becomes anxiety. Each missing or overdue item gets a specific, doable action naming who to contact and how: log into the lender's portal, email the client who paid you, ask HR for the 1095-C, call the brokerage. `references/documents.md` covers where each form is usually retrievable online, which is often faster than waiting for a reissue.

## Output format

```
Tax Prep Checklist — 2025

Received
  W-2 — Acme Corp (email, Jan 22)
  1099-INT — Chase Bank (email, Jan 18)
  Charitable receipts — 3 found, $340 total

Pending
  1099-NEC — Baxter Design, due Jan 31
  1098 — Summit Mortgage, available in portal per Jan 12 notice

Missing
  1095-C — no notice found, past due

Next steps
  1. Download the 1098 from the Summit Mortgage portal
  2. Email Baxter Design if the 1099-NEC hasn't arrived by Feb 5
  3. Ask HR for the 1095-C
```

Keep it scannable. The buckets and the numbered next steps are the point; prose commentary between them dilutes it.

## Running it again

Documents arrive over weeks, so this is a repeat visit, not a one-shot. Write the checklist to a file — `tax-prep-<year>.md` in the working directory unless the user names somewhere else — so a later run starts from what was already resolved instead of re-interrogating the user. On a repeat run, read that file first, re-search only the still-open items, and lead with what changed since last time.

## Boundaries

**No sensitive identifiers.** Never ask for, echo, or write down Social Security numbers, ITINs, EINs, or full account numbers. Tracking whether a document arrived never requires them, and a checklist file that contains them becomes a liability sitting in a repo. If the user volunteers one, don't write it to the file. Childcare providers' tax IDs are a common near-miss: note that the user needs to collect it, don't record the number.

**Not tax advice.** Deductibility, filing status, and whether something is even reportable are questions for a tax professional. Say so when they come up — briefly, once, without a disclaimer on every line — and get back to the paperwork.

**US federal by default.** State returns usually need the same source documents plus state-specific ones, and other countries work differently. If the user is filing elsewhere, say the checklist is US-shaped and ask what their jurisdiction requires rather than mapping it yourself.
