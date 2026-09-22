# [SOP-PLT-05] Stuck print queue - spooler recovery

**Category:** platform_support_access
**Applies to:** printer queue

## Symptom

Jobs sit in the queue on the floor 3 printer and never come out. Three of us are affected.

## Root cause

Print spooler service hung on the print server; queue cleared and restarted.

## Resolution steps

1. Confirmed the device was online and reachable from the print server.
2. Found the spooler service in a hung state on the server hosting that queue.
3. Cleared the stuck job from the spool directory.
4. Restarted the spooler and confirmed a test page from the affected floor.

## Before you close

- Confirm the outcome with the requester, in their words, not yours.
- If the requester's tier is Gold, hand the close to the named owner for that
  business unit rather than closing directly.
