# [SOP-PLT-07] Severe slowdown following a patch cycle

**Category:** bug
**Applies to:** laptop slow

## Symptom

Since the update pushed last week my laptop takes about ten minutes to become usable after login. Disk sits at 100 percent.

## Root cause

Post-update search re-index saturating disk on machines with small SSDs.

## Resolution steps

1. Confirmed the update build number and the machine's disk model.
2. Identified the search indexer rebuilding its catalogue after the update.
3. Paused indexing, then rebuilt the catalogue out of hours.
4. Confirmed normal boot-to-usable time with the user the following morning.

## Before you close

- Confirm the outcome with the requester, in their words, not yours.
- If the requester's tier is Gold, hand the close to the named owner for that
  business unit rather than closing directly.
