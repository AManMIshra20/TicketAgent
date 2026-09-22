# [SOP-PLT-02] Overnight SAP batch abort - diagnosis and restart

**Category:** bug
**Applies to:** sap batch failure

## Symptom

The overnight finance batch aborted at 02:40. Month-end close is blocked and we need this before the reporting cut-off today.

## Root cause

Batch aborted on a table lock held by an unfinished posting session.

## Resolution steps

1. Checked SM37 for the job log and identified the abort point.
2. Found an orphaned posting session holding a lock in SM12.
3. Released the lock after confirming with the posting user that the session was dead.
4. Restarted the job from the failed step and confirmed completion.

## Before you close

- Confirm the outcome with the requester, in their words, not yours.
- If the requester's tier is Gold, hand the close to the named owner for that
  business unit rather than closing directly.
