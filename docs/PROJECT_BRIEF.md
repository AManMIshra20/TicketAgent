# Ticket Triage & SOP Agent — Project Brief (handoff to VS Code / Claude Code)

This file is the single source of truth for picking this project back up in
a different tool. It captures every decision made so far, what's already
built, and what's next. Read this before doing anything else in the new
workspace.

## 1. What we're building (current, v2 scope)

> An agent that checks each new ticket against past fixes and SOP docs,
> drafts (or applies) the fix when there's a match, and otherwise just
> watches how you solve it so it can write the SOP itself — nothing posts
> or publishes without your OK.

This grew out of Aman's TCS work: Jira ticket triage, Confluence lookups,
ServiceNow tracking, 5-6 tickets/sprint, KT documentation via Copilot. The
agent automates the mechanical parts of that loop (read ticket, check
history, draft response/doc) and keeps every judgment call and every
publish/post action gated behind explicit human approval.

## 2. Decisions locked in

| Decision | Choice | Why |
|---|---|---|
| LLM | Claude via Anthropic API | Nvidia Nemotron Ultra (253B) needs multi-GPU hardware not available locally; Claude gives strong tool-use reasoning with no infra to manage |
| Ticket platform (Jira stand-in) | GitHub Issues | Free, real API/webhooks, issue comments map naturally to ticket updates |
| Docs platform (Confluence stand-in) | `docs/` folder in the same repo (or GitHub Wiki) | Same repo, no extra service, easy to diff/version |
| Language | Python | Matches Aman's existing Python/pandas/scikit-learn background |
| Core safety pattern | Human-approval gate is a **code boundary**, not a prompt instruction | LLM has no write-capable tool at all; it can only emit a structured proposal object. Immune to prompt injection talking the model out of asking permission |

## 3. Architecture (unchanged core pattern)

```
GitHub Issue (new/updated)
        |
        v
  [Context Tools]  -- read issue, search past issues/SOP docs
        |
        v
  [Claude: classify + draft]  -- returns structured JSON proposal only
        |
        v
  [Human Review CLI]  -- approve / edit / reject   <-- Aman, every time
        |  (only on approve)
        v
  [Executor]  -- posts comment / applies label / assigns / publishes SOP
```

The LLM's tools are split hard into **READ tools** (given to the model) and
**WRITE tools** (never given to the model — only called by `executor.py`,
only after human approval). The model's only possible terminal action is
calling `submit_triage_proposal`, which is intercepted in code and turned
into a `TriageProposal` object, never executed directly.

## 4. What's already built (v1 — done, untested against a live repo)

Located in this same folder tree (`ticket-agent/`). All files syntax-checked
clean with `python -m py_compile`, not yet run against a real GitHub
repo/API keys.

| File | Role | Status |
|---|---|---|
| `docs/ARCHITECTURE.md` | Full v1 design rationale | Done |
| `requirements.txt` | anthropic, PyGithub, pydantic, python-dotenv, rich | Done |
| `.env.example` | Template for `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`, `AGENT_MODEL` | Done |
| `src/ticket_agent/config.py` | Loads/validates env vars | Done |
| `src/ticket_agent/models.py` | `TriageProposal`, `ProposedAction`, `Severity`, `Category` (Pydantic) | Done |
| `src/ticket_agent/github_tools.py` | READ tools (`get_open_issues_without_label`, `get_issue`, `search_similar_issues`) vs WRITE tools (`post_comment`, `add_labels`, `assign_issue`) | Done — WRITE tools not yet exposed to LLM by design |
| `src/ticket_agent/tool_schemas.py` | Claude tool-use JSON schemas — deliberately excludes any write tool | Done |
| `src/ticket_agent/agent.py` | Claude tool-use loop; terminates by intercepting `submit_triage_proposal` | Done |
| `src/ticket_agent/executor.py` | Only file allowed to call GitHub write functions | Done |
| `src/ticket_agent/review_cli.py` | Human approval gate (Rich-based terminal UI: approve/edit/reject) | Done |
| `src/ticket_agent/main.py` | Wires it together: find untriaged issues -> agent -> review | Done |
| `README.md` | Setup instructions, file map | Done |
| `tests/` | Empty — no tests yet | **Not started** |

## 5. What's NOT built yet (v2 scope — the actual next work)

The conversation moved past plain triage to a richer design: split into two
coupled pipelines, both reusing the same human-gate pattern.

### Agent A — Known-issue resolver (extends existing triage flow)
Ticket arrives -> search past issues **and** SOP docs -> if similarity is
above a confidence threshold, draft the *actual proposed fix* (not just a
severity/category label) -> ask for confirmation -> post it.

**Needed:**
- A real similarity/confidence score from the search step (not just the
  model's self-reported confidence — needs an actual scoring mechanism,
  e.g. embedding similarity or a graded rubric in the prompt).
- A branch in `agent.py`: solvable-from-KB vs novel.
- Extend `search_similar_issues` (or add a new tool) to also search the
  `docs/` SOP folder, not just past issues.

### Agent B — SOP synthesizer (net-new pipeline)
When a ticket has no good match, the agent doesn't try to solve it. It
watches the ticket's comment thread as the human resolves it, then drafts
or updates an SOP document — itself gated by approval before publishing.

**Needed (open design questions, in order of how much they block starting):**
1. **Trigger**: no live "watch comments" stream — needs a poll for
   recently-*closed* issues since the last run.
2. **Extraction quality is bounded by comment detail.** Only works if the
   resolution comments actually narrate what was done. Worth deciding: do
   we rely on real ticket discipline, or add a lightweight "how did you fix
   this" prompt step to the workflow itself?
3. **Merge, don't duplicate**: before drafting a new SOP, search
   `docs/` for an existing one on this category and update it rather than
   create a new file per ticket.
4. **New write action**: `publish_sop(path, content)` — added to
   `github_tools.py`'s WRITE section only, never exposed to the LLM
   directly, called only from `review_cli.py` after approval, same as
   every other write action.
5. **New Pydantic model**: something like `SOPProposal` (category, target
   doc path, new/updated markdown body, diff summary) parallel to
   `TriageProposal`.

## 6. Setup steps (do these first in the new workspace)

1. Open this folder (`ticket-agent/`) in VS Code, with the Claude Code
   extension/CLI installed and signed in.
2. Create (or reuse) a GitHub repo to act as the test "Jira" — private is
   fine. Open 2-3 test issues in it, and close one or two with descriptive
   resolution comments (needed later for Agent B testing).
3. Generate a GitHub fine-grained personal access token with
   `Issues: read and write` scope on that repo.
4. Get an Anthropic API key from the Claude Console.
5. `cp .env.example .env` and fill in `GITHUB_TOKEN`, `GITHUB_REPO`
   (`username/repo`), `ANTHROPIC_API_KEY`.
6. `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
7. Smoke-test v1 as-is: `python -m src.ticket_agent.main` against the test
   repo's open issues. This has not been run live yet — first real run,
   debug whatever breaks.

## 7. Suggested order of work in the new session

1. Run the v1 smoke test (step 7 above) and fix anything that breaks on
   first contact with the real GitHub/Anthropic APIs.
2. Add `tests/` with a mocked GitHub client so the loop can be tested
   without hitting real APIs.
3. Add the confidence-score branch to `agent.py` (Agent A).
4. Extend search to cover `docs/` SOP files, not just past issues.
5. Build the closed-issue poller + comment-extraction step for Agent B.
6. Add `SOPProposal` model, `publish_sop` write tool, and wire it through
   `review_cli.py`.
7. Only after both pipelines work individually: decide whether they share
   one `main.py` entry point or run as two separate scheduled jobs.

## 8. Things explicitly agreed NOT to build yet

- Auto-triage escalation/SLA timers, auto-close.
- Real-time webhook trigger (polling is fine for v1/v2).
- A web review UI (CLI is fine for now).
- Self-hosting Nemotron or any local model — revisit only after the Claude
  version's quality is a known baseline, if a cost/latency comparison
  becomes worth doing.

## 9. Reference material already produced this session

- Full v1 code + `docs/ARCHITECTURE.md` — in this same folder tree.
- This brief supersedes the informal one-liners exchanged in chat; treat
  section 1 above as the current canonical description of the project.
