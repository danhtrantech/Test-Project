# Used Car Buying Checklist — MASTER (Danh, 2025 Toyota Prius)

**This is the living checklist.** Read it and update it every run. Don't recreate it
from memory, and don't let a dealer conversation happen that isn't logged here.

Started 2026-07-30 · Skill: `used-car-buying` v0.1 (Ahmed)

> **Setup note:** the skill package ships `references/checklist.md` as a pointer to
> `C:\_Working-Claude-Code\Used-Car-2026\050-Documents\Used-Car-Buying-Checklist-MASTER.md`
> — the original author's own machine. That path does not exist here. This file
> replaces it as the working copy for Danh's search.

---

## 0. Standing rules (apply to EVERY dealer contact, no exceptions)

- **Never give a phone number.** Not on a lead form, not in an email signature. If a
  form won't submit without one, try leaving it blank first (many "required" markers
  aren't enforced). If it truly blocks, skip that dealer — don't fill it in.
- **Never disclose the pre-approval amount.** Danh is pre-approved through his bank/CU.
  Dealers get told "financing is handled" and nothing else. If they ask what he's
  approved for, the answer is "that's not relevant to the out-the-door price."
- **Negotiate out-the-door only.** Never discuss monthly payment. Never discuss
  down payment. If a salesperson answers a price question with a payment, repeat the
  question.
- **Decline in-person visits and test drives** until the full OTD breakdown is in
  writing, by email.
- **Everything in writing.** A number spoken on a phone call does not exist.
- **Check the FULL inbox every pass, not just unread** — plus spam. Dealer CRM blasts
  land in spam constantly, and an email opened on a phone reads as "read" without
  anyone having actually reviewed it.
- **Rideshare disclosure:** the skill's default rule is never to mention rideshare/
  Uber/Lyft use to a dealer or lender (many will refuse financing or repossess).
  ⚠️ **Open — Danh hasn't said whether this applies to him.** If it doesn't, this
  rule is harmless to keep. If it does, it is critical. Confirm on next pass.

---

## 1. Standing facts

| Field | Value |
|---|---|
| Buyer | Danh (danhakadanh@gmail.com) |
| Search area | Garden Grove, CA 92840 — 50-mile radius |
| Target vehicle | Used 2025 Toyota Prius, any trim, best price |
| Financing | Pre-approved, bank/credit union — **not disclosed to dealers** |
| Franchised-dealer-only? | Not required (pre-approval isn't dealer-tied) — independents are in play |
| Trade-in | None stated |
| Selling a car privately in parallel? | None stated |
| Personal negotiating lever | ⚠️ **None identified yet** — see §5 |
| Sales tax (registration address) | 8.75% (Garden Grove) |

---

## 2. Price discipline (full detail in `2025-Prius-Price-Benchmarks.md`)

| Trim (FWD) | Good price | Walk away above |
|---|---|---|
| LE | ≤ $24,000 | $26,500 |
| XLE | ≤ $27,000 | $29,500 |
| Nightshade | ≤ $28,000 | $30,000 |
| Limited | ≤ $30,000 | $31,500 |

- OTD ≈ sale price × 1.0875 + $585
- CA doc fee is capped at **$85** — anything higher is illegal, and calling it out
  early resets the whole negotiation in your favor
- **Hard ceiling:** if a used 2025 LE can't be had under ~$26,000, buy the new 2026
  LE instead. The OTD gap stops being worth a year of used warranty.

---

## 3. Phase status

- [x] **Phase 1 — Shopping (pricing benchmark)** — done 2026-07-30. KBB/Edmunds values,
      OC market averages, trim MSRPs, OTD math, new-vs-used crossover all established.
- [ ] **Phase 1b — Live listing pull** — ⛔ **BLOCKED.** This session's egress policy
      returns 403 at the gateway for every listing host (cars.com, cargurus.com,
      kbb.com, truecar.com, edmunds.com, and dealer sites incl. toyotaofhb.com).
      Not a site-side block and not something to route around. Needs either a session
      with those hosts allowed, or Danh pulls the listings himself using §4.
- [ ] **Phase 2 — Initial dealer contact** — not started. Also needs Claude-in-Chrome
      on Danh's signed-in Gmail; the Gmail connector can draft but cannot send.
- [ ] **Phase 3 — Negotiating & closing** — not started.

---

## 4. Dealer target list (Orange County, franchised Toyota)

Verified as existing dealers in the search radius. **None contacted yet.** Inventory
not yet verified — that's Phase 1b.

| Dealer | City | Site | Contacted | Notes |
|---|---|---|---|---|
| Toyota of Huntington Beach | Huntington Beach | toyotaofhb.com | ☐ | Large TCUV inventory |
| Toyota of Anaheim | Anaheim | toyotaofanaheim.com | ☐ | Closest to Garden Grove |
| Toyota of Orange | Orange | toyotaoforange.com | ☐ | Wilson Automotive group |
| Tustin Toyota | Tustin | tustintoyota.com | ☐ | |
| AutoNation Toyota Irvine | Irvine | autonationtoyotairvine.com | ☐ | CPO-heavy |

Worth adding once reachable: Cabe Toyota (Long Beach), Norwalk Toyota, Longo Toyota
(El Monte — largest Toyota dealer in the US, worth the drive), Puente Hills Toyota.
Also check Carvana/CarMax — no-haggle, but they set a real price floor to negotiate
franchised dealers against.

**When pulling listings, verify each one against the dealer's own inventory search
by VIN.** Aggregator snippets go stale; a car that "exists" on CarGurus may have sold
two weeks ago.

---

## 5. Negotiating levers

- **New-2026-LE comparison.** The strongest lever available. A used 2025 asking $27k+
  is competing with a new car ~$4k away, and the salesperson knows it.
- **The $85 doc fee cap.** Naming it early signals you know CA law.
- **Trade-in: none.** ⚠️ Per the skill, a buyer with no trade-in should bring a
  substitute lever — something that costs the dealer little but is worth real money
  to the buyer. The author's own example was a trailer hitch swap. **Danh needs to
  pick his own:** all-weather floor mats, roof rack, first two services, ceramic
  tint (worth ~$400 in Southern California sun and near-free for a dealer with an
  in-house installer), extra key fob. Ask on next pass.
- **Multi-dealer parallel bidding.** Contact all five at once, tell each that others
  are quoting, and let the low OTD number surface. The author ran nine at once.
- **End of month / end of quarter.** Today is July 30 — **the last two days of the
  month are live right now.** If Phase 2 can start immediately, it starts at the
  single best moment of the quarter.

---

## 6. Kill criteria

- Walk if no written OTD breakdown within **48 hours** of an inquiry.
- Walk if a dealer insists on a phone call before sending numbers.
- Walk on any doc fee over $85, or any add-on the dealer refuses to remove.
- Walk if a pre-purchase inspection at Danh's own mechanic is refused (see §7).
- Walk on any LE over $26,500 / XLE over $29,500 — buy new instead.
- **Hard date:** set one before Phase 2 opens, so this can't drift.

---

## 7. Pre-purchase inspection (Phase 3 — read before signing anything)

- Independent PPI at Danh's own mechanic, **before signing**, not after.
- Coordinate a loaner / drop-off / mobile-PPI if the dealer resists letting the car
  leave the lot.
- A post-delivery "return window" is a much weaker substitute. **California's Car
  Buyer's Bill of Rights only gives a *paid, optional* 2-day/250-mile contract
  cancellation option on used cars — it is not a free right to return, and most
  buyers wrongly assume it is.** Don't accept it in place of a real inspection.
- Demand the Carfax **and** the reconditioning repair order. The recon RO shows what
  the dealer actually fixed; the Carfax only shows what got reported.
- On a one-year-old Prius, verify the remaining factory warranty transfers and check
  whether the hybrid battery warranty (10yr/150k on 2025) is intact.

---

## 8. Open questions

1. Does the rideshare non-disclosure rule apply to Danh? (§0)
2. What's his personal negotiating lever, replacing the author's trailer hitch? (§5)
3. Mileage ceiling — is a 25k-mile 2025 acceptable at a discount, or does he want
   under 15k?
4. Color/trim dealbreakers, or is this purely a price hunt?
5. Hard deadline — when does he need the car by? Sets the kill date in §6.
6. Can a future session get the listing hosts unblocked, or does he pull listings
   himself?

---

## 9. Dealer log

*No dealers contacted. First entry goes here.*

| Date | Dealer | Channel | What happened | OTD quoted | Next step |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

---

## 10. Ready-to-send inquiry email

Compliant with §0. Send from Danh's own Gmail, one per dealer, no phone number
anywhere including the signature.

> **Subject:** 2025 Prius — out-the-door price request
>
> Hello,
>
> I'm looking for a used 2025 Toyota Prius and I'd like to know what you currently
> have in stock. I'm ready to buy this week.
>
> Could you send me, by email:
>
> 1. The stock number, VIN, trim, mileage and color of each 2025 Prius you have
> 2. The **complete out-the-door price** for each — vehicle price, doc fee, tax and
>    DMV fees itemized separately. Not the advertised or "special" price.
> 3. The Carfax and the reconditioning repair order
>
> Financing is already handled on my end, so I'm only comparing out-the-door numbers.
> I'm in contact with several dealers in the area and will move on the best written
> offer.
>
> Please reply by email — I'm not available for calls.
>
> Thanks,
> Danh

**Do not send this from this session.** Sending requires Claude-in-Chrome driving
Danh's own signed-in browser; the Gmail connector drafts but has no send tool. And
this environment can't reach the dealer sites at all (§3).
