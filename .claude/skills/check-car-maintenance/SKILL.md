---
name: check-car-maintenance
description: Tracks car maintenance and renewal deadlines — oil changes, tire rotations, air filters, brake inspections, registration, state inspection, emissions, and insurance — from saved vehicle data, and reports what's due. Use this whenever the user asks about their car's upkeep, mentions an oil change or tire rotation, wonders whether registration or insurance is about to expire, gives you a new odometer reading, wants a monthly or periodic car check-in, or says something like "is my car due for anything" or "when's my next service" — even if they don't use the word "maintenance." Also use it when logging completed service so the next check is accurate.
---

# Check Car Maintenance

A check-in on everything the car needs: oil, tires, filters, brakes, registration,
inspection, insurance. The point is that nothing expires or gets neglected because
it quietly slipped past. Most months the answer is "you're fine" — say so quickly
and get out of the way.

This works as an on-demand check ("anything due on the car?") or as a recurring
monthly one. Same flow either way.

## 1. Load vehicle info

Read `~/.claude/data/car/vehicle.json`. If it's missing, also check
`~/.openclaw/data/car/vehicle.json` — this skill was ported from an OpenClaw
workflow, so returning users may have data there. Copy it to the Claude path if found.

If there's no saved data, set it up conversationally. Ask for what you need in a
couple of natural turns, not as a numbered form — this is a chat, and a wall of
form fields makes people bail. Reasonable defaults are fine when someone doesn't
know (e.g. 1,000 miles/month is a typical driver); say what you assumed.

What to collect:

| Field | Notes |
|---|---|
| year / make / model | Intervals differ by vehicle; this also lets you defer to the owner's manual |
| current mileage | The anchor for every mileage-based projection |
| average miles per month | Drives every "when will I hit X miles" estimate |
| last oil change | Date **and** mileage — both matter |
| last tire rotation | Date and mileage, if known |
| registration renewal | Month or exact date |
| state inspection due | If the state requires one |
| emissions test due | If applicable |
| insurance renewal | Date |

Save as `~/.claude/data/car/vehicle.json`:

```json
{
  "vehicle": { "year": 2022, "make": "Honda", "model": "Civic" },
  "mileage": { "current": 42300, "as_of": "2026-03-01", "miles_per_month": 950 },
  "service": {
    "oil_change":       { "last_date": "2025-10-14", "last_mileage": 40100, "interval_miles": 7500, "interval_months": 6 },
    "tire_rotation":    { "last_date": "2025-10-14", "last_mileage": 40100, "interval_miles": 7500 },
    "air_filter":       { "last_mileage": 30000, "interval_miles": 20000 },
    "brake_inspection": { "last_mileage": 25000, "interval_miles": 25000 }
  },
  "renewals": {
    "registration": "2026-04-30",
    "inspection":   "2026-07-15",
    "emissions":    null,
    "insurance":    "2026-08-15"
  },
  "history": [
    { "date": "2025-10-14", "mileage": 40100, "service": "oil change + tire rotation" }
  ]
}
```

Omit keys that don't apply rather than inventing values — a null insurance date is
honest, a guessed one is worse than nothing. Write the file back whenever mileage
or service data changes, and append to `history` when a service gets logged, so the
next run starts from real numbers instead of asking again.

## 2. Work out what's due

Estimate current mileage as `mileage.current + miles_per_month × months since as_of`,
then project forward to find when each service comes due. If the user just gave you a
fresh odometer reading, use it — and update `as_of`. A real reading also lets you
correct `miles_per_month` from actual elapsed miles, which makes every later
projection better.

Default intervals when the vehicle's own schedule is unknown:

- **Oil change** — 5,000–7,500 miles or 6 months, whichever comes first
- **Tire rotation** — 5,000–7,500 miles
- **Air filter** — 15,000–30,000 miles
- **Brake inspection** — every 25,000 miles

These are conservative middle-of-the-road figures. Modern synthetic-oil cars often
go longer and some older or hard-driven ones need shorter; when the user's manual
says otherwise, the manual wins and the stored `interval_*` values should be updated
to match.

Then bucket everything:

- **Due now** — past its interval, or a renewal inside 14 days
- **Coming up** — due within roughly 2,000 miles, or a renewal inside 60 days
- **Later** — everything else, mentioned only briefly so the year is visible

Group services that share a shop visit. Suggesting the air filter be done at the next
oil change saves a trip, and that kind of batching is most of the practical value here.

## 3. Present the report

Use this shape:

```
🚗 Car Maintenance — March 2026

VEHICLE: 2022 Honda Civic | ~42,300 miles

✅ UP TO DATE
   Oil change — done at 40,100 mi. Next due ~45,000 mi (May)
   Tire rotation — done at 40,100 mi. Next due ~45,000 mi

⚠️ COMING UP
   Registration — expires April 30. Renew this month.
   Air filter — due around 45,000 mi. Do it with next oil change.

📅 LATER THIS YEAR
   Insurance renewal — August 15
   Brake inspection — around 50,000 mi (September)
```

Drop any section that's empty — an "⚠️ COMING UP" header with nothing under it just
adds noise. When everything is genuinely fine, a few lines is the correct length.
Mark mileage-based projections as estimates (`~`, "around") since they rest on an
assumed driving rate.

## 4. Deliver

If a messaging or notification skill is available and the user has asked for these
to be sent somewhere, deliver it there; otherwise just reply in the conversation.
Flag anything due within 14 days as high priority, since those are the ones that
turn into a ticket or a lapsed policy.

To make this recurring, offer to set up a scheduled task — the original workflow
ran on the 1st of each month at 9am, which is a sensible default.

## Notes

- This is a reminder system, not a mechanic. Defer to the owner's manual for exact
  intervals, and to a shop for anything diagnostic.
- Don't estimate repair costs or recommend specific shops unless asked.
- Keep it brief. Most months everything is fine; padding the report to look
  thorough trains the user to skim past the one month it actually matters.
- If the user mentions getting something done ("got the oil changed Saturday"),
  log it — update the service entry, append to `history`, and confirm in a line.
  Silent state updates are unnerving; a one-line confirmation is enough.
