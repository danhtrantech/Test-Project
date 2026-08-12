# Tax Document Reference

Per-form detail for the prep-taxes checklist: who issues it, what makes someone need it, how to search email for it, and where to get it when it never shows up.

Deadlines are the usual IRS furnishing dates for recent years. They shift when the date lands on a weekend or holiday, and issuers can request extensions, so treat "past due" as a prompt to follow up rather than proof something went wrong. Confirm current-year dates at irs.gov if precision matters.

## Contents

- [Income](#income)
- [Interest, dividends, and investments](#interest-dividends-and-investments)
- [Retirement and health savings](#retirement-and-health-savings)
- [Deductions and credits](#deductions-and-credits)
- [Health insurance](#health-insurance)
- [Pass-through and small business](#pass-through-and-small-business)
- [Email search strategy](#email-search-strategy)

## Income

### W-2 — Wage and Tax Statement
**Needed by** anyone who was an employee at any point in the year, including jobs held for a few weeks.
**From** each employer separately. Job changes are the most common source of a missing W-2.
**Due** January 31.
**Search** `W-2`, `W2`, `tax statement`, plus the employer name and common payroll providers: ADP, Workday, Gusto, Paychex, Justworks, Rippling, TriNet.
**If missing** check the payroll provider's portal directly — most post W-2s there whether or not the notification email arrived. Former employees usually retain portal access. Failing that, contact the employer's HR or payroll. If the employer is unreachable or defunct, the IRS can be contacted after mid-February.

### 1099-NEC — Nonemployee Compensation
**Needed by** freelancers and contractors, for each client who paid $600 or more.
**From** each client.
**Due** January 31.
**Search** `1099`, `1099-NEC`, plus each client's name.
**If missing** email the client's accounts payable contact. Income is reportable whether or not the form arrives, so reconstruct the total from invoices and bank deposits — worth flagging to the user, since a missing 1099 is not a reason to omit the income.

### 1099-MISC — Miscellaneous Information
**Needed by** recipients of rent, royalties, prizes, or legal settlements.
**Due** January 31 to the recipient.
**Search** `1099-MISC`, `royalty statement`, `settlement`.

### 1099-K — Payment Card and Third-Party Network Transactions
**Needed by** anyone selling through payment platforms or marketplaces.
**From** PayPal, Stripe, Venmo (business), Etsy, eBay, Airbnb, Uber, DoorDash, and similar.
**Due** January 31.
**Search** the platform names above plus `1099-K`.
**Note** the reporting threshold has changed repeatedly and platform practice varies. A user may receive a 1099-K they did not expect, or owe tax on income with no 1099-K at all. Flag it and route threshold questions to a tax professional.

## Interest, dividends, and investments

### 1099-INT — Interest Income
**From** banks and credit unions, generally for $10 or more of interest.
**Due** January 31.
**Search** `1099-INT`, `interest income`, `tax documents are ready`, plus bank names.
**If missing** nearly always downloadable under the statements or tax documents section of online banking.

### 1099-DIV — Dividends and Distributions
**From** brokerages and funds.
**Due** January 31.
**Search** `1099-DIV`, `dividend`, plus brokerage names.

### 1099-B — Proceeds From Broker Transactions
**Needed by** anyone who sold stocks, funds, or crypto.
**Due** February 15, and corrected versions in March are common.
**Search** `1099-B`, `consolidated 1099`, `cost basis`.
**Note** brokerages usually issue a consolidated 1099 combining INT, DIV, and B. One document can satisfy three checklist rows — check the contents before marking the others missing. Corrected 1099s arriving after filing are a known headache; if the user files early, mention that a correction may follow.

### 1099-R — Distributions From Pensions and Retirement Plans
**Needed by** anyone who took a distribution or did a rollover. Rollovers generate a 1099-R even when nothing is taxable.
**Due** January 31.
**Search** `1099-R`, `distribution`, plus plan administrator names like Fidelity, Vanguard, Schwab, Empower.

## Retirement and health savings

### 5498 / 5498-SA — Contribution statements
**From** IRA and HSA custodians.
**Due** May 31, after the filing deadline, because contributions can be made up to the deadline.
**Note** informational. The user generally needs their own contribution records to file, not this form. Don't let a pending 5498 hold up a return.

### 1099-SA — HSA Distributions
**Needed by** anyone who spent from an HSA.
**Due** January 31.
**Search** `1099-SA`, `HSA`, plus the custodian name.

## Deductions and credits

### 1098 — Mortgage Interest Statement
**From** the mortgage servicer, which may have changed mid-year — that produces two 1098s.
**Due** January 31.
**Search** `1098`, `mortgage interest`, `year-end statement`, plus servicer names.
**If missing** available in the servicer's online portal. Also carries property tax paid from escrow, which can cover the property tax row.

### 1098-E — Student Loan Interest Statement
**From** the loan servicer, for $600 or more of interest.
**Due** January 31.
**Search** `1098-E`, `student loan interest`, plus servicer names like Nelnet, MOHELA, Aidvantage.

### 1098-T — Tuition Statement
**From** the college or university.
**Due** January 31.
**Search** `1098-T`, `tuition statement`, plus the school name.
**If missing** posted in the student account portal; students often have to consent to electronic delivery before it appears.

### Charitable donation receipts
**From** each charity. Donations of $250 or more need a written acknowledgment from the organization.
**Search** `donation receipt`, `thank you for your gift`, `your contribution`, `tax-deductible`, plus recurring charity names. Also check for `annual giving statement` — many charities send one summary covering the year, which is easier to work with than a dozen individual receipts.
**Note** total these up rather than just confirming they exist; the total is what the user needs.

### Property tax records
**From** the county assessor or treasurer, or the escrow section of the 1098.
**Search** `property tax`, `assessor`, `tax bill`.

### Childcare expenses
**Needed for** the child and dependent care credit.
**From** the provider, who must supply their name, address, and tax ID.
**Note** track that the user needs to request the provider's tax ID. Do not record the number itself.

## Health insurance

### 1095-A — Health Insurance Marketplace Statement
**Needed by** anyone with healthcare.gov or a state exchange plan. This one genuinely blocks filing — the return needs its figures to reconcile premium tax credits.
**From** the marketplace.
**Due** January 31.
**Search** `1095-A`, `marketplace`, `healthcare.gov`.
**If missing** downloadable from the marketplace account. Prioritize this over other gaps.

### 1095-B / 1095-C — Coverage statements
**From** insurers and larger employers.
**Due** typically early March.
**Search** `1095`, `health coverage`, plus insurer and employer names.
**Note** usually informational and not required to file. Note as pending without treating it as a blocker.

## Pass-through and small business

### Schedule K-1
**Needed by** partners in partnerships, S-corp shareholders, and trust or estate beneficiaries.
**Due** March 15 for calendar-year entities, and routinely later — extensions are common.
**Search** `K-1`, `Schedule K-1`, plus the entity name.
**Note** the most common reason a return waits. If the user is expecting a K-1, say early that it may push them toward an extension so it isn't a surprise in April.

### Business records
Not IRS forms, but needed alongside them: income summaries, expense records, mileage logs, home office square footage, asset purchases for depreciation, and prior-year depreciation schedules. Ask whether these exist rather than assuming; reconstructing a mileage log in April is miserable.

## Email search strategy

**Search per document type, not once for "tax."** A single broad query returns promotional mail and receipts, and the real notices get buried. Ten targeted searches beat one broad one.

**Search issuer names too.** Many notification emails never contain the form number — "Your 2025 tax documents are ready" from a brokerage is the whole message. Ask the user which banks, brokerages, employers, and servicers they use, then search those names directly.

**Scope the date range.** December through April of the filing year catches almost everything. Widen it for late K-1s and corrected 1099s.

**Useful generic phrases:** `tax documents are ready`, `your tax forms`, `available for download`, `year-end statement`, `annual statement`, `important tax information`, `tax document notification`.

**Read before classifying.** "Your tax documents will be available by January 31" is pending. "Your 1099-INT is now available" is received. The distinction is the whole value of the search — misreading it hands the user a checklist that says they have something they don't.
