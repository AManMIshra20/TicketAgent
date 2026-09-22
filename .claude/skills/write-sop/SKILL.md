---
name: write-sop
description: Turn a resolved Meridian ticket into a standard operating procedure, or update the SOP that already covers the topic. Use when a ticket was solved but no procedure covered it, when asked to document how something was fixed, or to write or update an SOP.
---

# Write the procedure

When a ticket has no matching SOP, the agent does not try to solve it. It
watches how the human solved it, then writes the procedure so the next person
does not start from nothing.

## Run it

```
/write-sop MERT-1039
/write-sop next 3
```

`python -m src.ticket_agent.main sop --limit 3` runs the real pipeline.

## What to do

1. **Read the ticket and its full comment thread.** The resolution lives in
   the thread: what was diagnosed, what was tried, what finally worked. The
   `resolution_notes` field is the summary, not the method.

2. **Search the SOP pack for this topic first.** If a procedure already
   exists, read it and propose an **update** to it. Do not create a second
   document.

3. **Look at the other tickets on the same problem.** A procedure written
   from one case covers one case. Search for the variants so the steps hold
   for the next one too.

4. **Draft the SOP** in the pack's house format:

   ```markdown
   # [SOP-XXX-nn] Title

   **Category:** one of the Category values
   **Applies to:** the problem in a few words

   ## Symptom
   ## Root cause
   ## Resolution steps
   ## Before you close
   ```

   Write the steps so someone who has never seen this problem can follow
   them. "Clear the cache" is not a step; "clear the cached device
   certificate from the VPN client profile" is.

5. **Submit the proposal.** Publishing is always a human decision — every
   proposal from this pipeline is held, with no fast path.

## The rules this must follow

- **One SOP per topic.** Before drafting new, search for existing. A pack
  that grows a document per ticket is worse than no pack, because the agent
  then cites whichever copy retrieval happened to surface.
- **Do not allocate the id yourself.** `executor.py` assigns it on approval.
  An id you choose may already exist or may encode a category you guessed.
- **If the thread does not say what was done, do not invent a procedure.**
  Say what is missing in `diff_summary` and propose only what the thread
  supports. A thin SOP that is true beats a complete one that is imagined.
  This is the failure that made Air Canada liable for its chatbot: a
  confident, well-formed policy that did not exist.

## Done when

- The procedure traces to what the thread actually records.
- An existing SOP on the topic was updated rather than duplicated.
- `diff_summary` says what changed and why.
- Nothing has been published.
