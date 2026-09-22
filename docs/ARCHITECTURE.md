# Ticket Triage Agent — Architecture

## Why this maps to your TCS work

At TCS, your loop for each of the 5–6 tickets/sprint was: a ticket lands in
Jira → you read it, figure out severity/category, check Confluence/prior
tickets for context → you draft a response or fix → you (or a reviewer)
approve it → you post the update and move the ticket forward. That loop is
almost entirely mechanical *except* the judgment calls (is this actually
critical? does this response commit us to something risky?) and the final
"is this good enough to send" check. Those two things are exactly what we
keep human-in-the-loop for. Everything else — reading the ticket, pulling
context, drafting the first pass — is what the agent takes over.

## The five-stage pipeline

1. **Trigger** — something happens that should start agent work.
   For v1: a new GitHub Issue is opened (polling, later a webhook).

2. **Context gathering (tools)** — the agent calls read-only tools to
   understand the ticket: issue body, labels, similar past issues, relevant
   wiki/doc pages. This mirrors you cross-referencing Jira + Confluence.

3. **Reasoning (the LLM)** — Claude classifies the ticket (severity, type,
   which "competency area" it falls under — your resume literally has these:
   Knowledge Transfer, Financial Process, Platform Support/Access) and
   drafts a proposed first response and a proposed action (comment, label,
   assign, escalate).

4. **Human approval gate — the non-negotiable checkpoint** — the agent
   *never* posts, labels, or closes anything on its own. It produces a
   proposal object (classification + draft response + proposed action) and
   stops. You see it in a CLI review screen, and you approve / edit / reject.
   This is the "final approval from human whenever required" requirement —
   architecturally enforced, not just a prompt instruction, so it can't be
   prompt-injected away by a malicious issue body.

5. **Action execution** — only after explicit approval does a separate,
   narrowly-scoped function actually call the GitHub API to post the
   comment/label/etc. The LLM never has direct write access; it only ever
   returns a structured proposal that a human-gated executor consumes.

```
GitHub Issue (new/updated)
        │
        ▼
  [Context Tools]  ── read issue, search past issues, fetch wiki page
        │
        ▼
  [Claude: classify + draft]  ── returns structured JSON proposal
        │
        ▼
  [Human Review CLI]  ── approve / edit / reject   <── YOU, every time
        │  (only on approve)
        ▼
  [Executor]  ── posts comment / applies label / assigns
```

## Why the human gate is a code boundary, not a prompt

A system prompt saying "always ask before posting" is a suggestion an LLM
can be talked out of by cleverly worded ticket content (prompt injection —
a real risk once the agent reads untrusted external text). So instead:

- The LLM's tools are split into **read tools** (always available to the
  agent) and **write tools** (not exposed to the LLM at all — only to the
  executor code path that runs after your approval).
- The agent's only possible output for an action is a typed proposal
  (Pydantic model), never a live API call.

This is the same pattern real agentic systems use (e.g. "plan mode",
staged/dry-run before apply) — worth understanding deeply since it's the
core safety pattern for any agent with side effects.

## Tech stack (matches your decisions)

- **LLM**: Claude (Anthropic API) via the `anthropic` Python SDK, using
  native tool use for the read-tools and structured output for the proposal.
- **Platform stand-in for Jira/Confluence**: a GitHub repo — Issues =
  tickets, a `docs/` folder or GitHub Wiki = Confluence, labels = your
  ticket categories/severity.
- **Language**: Python (matches your existing Python competency from TCS/
  DataCamp cert).
- **State**: a local SQLite file tracking which issues have been seen/
  proposed/approved, so we don't re-process the same ticket.

## v1 scope (what we're building first)

Ticket triage + first-response draft only:
1. Poll a GitHub repo for new issues.
2. For each new issue: classify (severity + category) and draft a reply.
3. Show you the proposal in a terminal review screen.
4. On approval, post the comment and apply the label(s) to the real issue.

Deliberately out of scope for v1 (future stages, once v1 works):
- Auto-triage escalation routing, SLA timers, auto-close, webhook-based
  real-time trigger (vs. polling), a proper web review UI instead of CLI,
  KT-doc generation stage, ops-dashboard rollup stage.
