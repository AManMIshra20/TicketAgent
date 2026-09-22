---
name: clean-tickets
description: Clean the raw Meridian ticket feed and report what was wrong with it - missing requesters, mixed date formats, duplicate ids, mislabelled categories, empty bodies. Use when asked to clean, validate, or check the quality of tickets_raw.csv or the ticket data.
---

# Clean the ticket feed

The data you get is never the data you want. `data/seed/tickets_raw.csv`
carries five defects on purpose, because every real support queue carries
them.

Cleaning is an agent task that **reports what it changed** — not a silent
preprocessing step. A cleaner that quietly fixes things teaches you nothing
about your queue.

## Run it

```
/clean-tickets
```

## The five defects

Counts are for seed 42 and are asserted in `tests/test_seed.py`.

| Defect | Count | Why it matters |
|---|---|---|
| Missing requester | 14 | Entitlement cannot be checked, so the tier gate cannot be evaluated |
| Mixed date formats | 3 formats | ISO, `dd/mm/yyyy`, `Mon dd, yyyy`. Any time filter silently drops rows |
| Duplicate ticket id | 3 | Same id twice, different descriptions. Appended out of position, so comparing neighbouring rows misses them |
| Mislabelled category | 8 | Labelled `question`, describing an outage. Severity routing goes wrong |
| Blank description | 4 | Subject line, empty body. Nothing to match an SOP against |

## What to do

1. **Count before you change anything.** Report how many rows and how many
   of each defect you found. If your counts disagree with the table above,
   say so — either the data changed or your detection is wrong, and both are
   worth knowing.

2. **Normalise the dates** to ISO. Note how many rows were in each format.

3. **Resolve the duplicates.** Same id, different text: keep the fuller
   description and record that you merged them. Do not silently drop one.

4. **Re-derive the category from the body**, not the label. A ticket saying
   "production is down" is not a `question` whatever the field says.

5. **Flag, do not invent.** A missing requester stays missing — it must not
   be guessed, because the tier gate depends on it and an assumed tier is
   worse than a known gap. A blank body stays blank and is routed to a
   human.

6. **Report.** A short table: defect, found, fixed, left for a human.

## Done when

- Every one of the five defects has a count.
- Dates are in one format.
- Nothing was invented to fill a gap — gaps are reported as gaps.
