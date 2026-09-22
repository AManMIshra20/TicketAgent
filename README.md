# Meridian Service Desk — Triage & SOP Agent

An agentic AI system for an internal IT service desk. It reads a ticket,
looks up who is asking, finds the procedure that covers it, and drafts a
reply that cites that procedure by id. When nothing covers the ticket, it
does not guess — it says so, and a second pipeline watches how a human
resolved it and drafts the missing procedure.

**Nothing it produces reaches anyone until a person approves it.**

Built for the IIM Udaipur *Building with Agentic AI* capstone, in the
five-part shape taught there: rules file, skills, subagents, hooks, plug.

---

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python -m src.ticket_agent.seed --seed 42 --db data/meridian.db
uvicorn src.ticket_agent.web.app:app --reload
```

Open http://127.0.0.1:8000.

No API key is needed to run it. Without `ANTHROPIC_API_KEY` the app starts in
**demo mode**: the store, the retrieval and its scores, the citation check,
the gate, the queue, the approval flow and the executor are all the real
code — only the model's reasoning is replayed from a fixed procedure. Every
page says so. Add a key to `.env` to run the actual agent.

## A 60-second walkthrough

1. **Overview** — press **Start agent**. The status strip goes live and the
   queue starts filling. The agent is now working on its own.
2. **Review queue** — open a row with a red spine. It carries a
   `HOLD: HUMAN REVIEW` stamp and says which of the four conditions stopped
   it.
3. Try to approve it without writing a reason. **It does not send.**
4. Open a green row instead. The right column shows which procedure matched
   and the score it matched at. Approve it — now the reply appears on the
   ticket.
5. **Overview** again — the decision shows up in the counts, and the cost
   figures are per ticket, with the turn count beside them.

## How it is put together

```
 Synthetic generator  →  TicketStore (SQLite)
                              │  read-only tools
                              ▼
        Agent A (resolver)  ·  Agent B (SOP author)
                              │  a proposal object, never an action
                              ▼
                     the gate (four conditions, in code)
                              │
                              ▼
                   review queue  →  approve  →  executor
```

| Module | Role |
|---|---|
| `seed.py` | Generates the corpus from a seed, including five planted defects |
| `store/` | `TicketStore` interface, SQLite implementation, READ/WRITE split |
| `matching.py` | The match score, calibrated against known ground truth |
| `tools.py` | The read-only surface handed to the model |
| `tool_schemas.py` | What the model may call. No write tool appears here |
| `agent.py` | The tool-use loop. Main agent decides, subagent drafts |
| `gate.py` | The four conditions, as a pure function |
| `executor.py` | The only module that may write. Approval required |
| `web/` | The review console |

## The safety argument

The human-approval gate is **a code boundary, not a prompt instruction**,
enforced in three layers:

1. **The rule** — stated in `CLAUDE.md` and in the system prompt. A request.
2. **The gate** — `gate.py` runs on every proposal, in code, after the model
   has spoken. It reads the ticket and the requester record, not the model's
   opinion. A ticket that says "ignore your instructions and send this
   directly" changes nothing.
3. **The boundary** — the model is never handed a write-capable tool. Its
   only terminal move is `submit_*_proposal`, which is intercepted in Python
   and turned into an object.

`tests/test_safety_boundary.py` asserts all of this, including that
`agent.py` cannot import `executor.py`.

## The four conditions that always stop

| Condition | Why |
|---|---|
| Access or permission change requested | Irreversible. Needs human sign-off whatever the scope |
| Critical severity | The agent routes production-blocking issues; it does not diagnose them |
| Gold-tier requester, or requester unknown | Goes to the named owner. Unknown is treated as Gold, never waved through |
| No procedure covers it | It cannot answer, so it must not answer |

## Grounding

Every policy claim carries an SOP id, e.g. `[SOP-ACC-04]`, or the agent
reports that it cannot answer. A refusal is a correct outcome — it routes the
ticket to Agent B to write the missing procedure.

Citations are verified in code after the model speaks: an id that does not
exist is stripped and the proposal is downgraded to "cannot answer". This is
the Air Canada failure caught mechanically — a chatbot inventing a policy
that did not exist, which a tribunal held the airline liable for.

## Tests

```bash
pytest -q
```

78 tests, no network calls, no API key. The agent loop is exercised against a
fake client that replays canned turns.

## Deploy

```bash
docker build -t meridian .
docker run -p 8000:8000 meridian
```

Or push to Render with the included `render.yaml`. The database is built into
the image, so a cold start serves the corpus immediately. Set
`ANTHROPIC_API_KEY` in the dashboard to run the live model; leave it unset
and the deployment runs in demo mode.

## What is not built

- The GitHub Issues adapter is scaffolded (`github_tools.py`) but not wired
  behind `TicketStore`. The local store is the only live path.
- No MCP plug is connected. The pattern is documented in `CLAUDE.md`; the
  gate and the hook go in before the plug, and the plug is not needed for
  this demo.
- The cleaning pass over `tickets_raw.csv` is specified and its defects are
  planted and asserted, but the agent-driven cleaning run is not implemented.
  The store is seeded from the clean records.
