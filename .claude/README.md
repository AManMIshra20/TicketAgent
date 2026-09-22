# The five parts, as files

The workshop's model is that an agent is made of five things, used in this
order: **CLAUDE.md → skills → subagents → hooks → MCP.** This directory holds
four of them. The fifth, MCP, is deliberately absent — see below.

| Part | Where it is | What it does here |
|---|---|---|
| **CLAUDE.md** | `../CLAUDE.md` | The rules. What is always true, read at the start of every session |
| **Skills** | `skills/triage`, `skills/write-sop`, `skills/clean-tickets` | Prompts that worked, saved under names, so the job runs the same way every time |
| **Subagents** | `agents/reply-drafter.md` | The specialist writer. Gets ticket + tier + clause, nothing else |
| **Hooks** | `hooks/check_tool_boundary.py`, wired in `settings.json` | Refuses any edit that would hand the model a write tool |
| **MCP** | *not connected* | Nothing here sends outside the system. See below |

## Two implementations of the same thing

Each part exists twice, and that is not duplication by accident.

The files here are for **an agent working on this repository** — Claude Code,
reading `CLAUDE.md`, invoking `/triage`, blocked by the hook when it tries to
widen the tool surface.

The Python is for **the deployed application** — the agent that actually
triages Meridian tickets for a user in a browser:

| Part | As a file, for Claude Code | In Python, for the app |
|---|---|---|
| Rules | `../CLAUDE.md` | `src/ticket_agent/prompts.py` |
| Skill | `skills/triage/SKILL.md` | `prompts.TRIAGE_SYSTEM` |
| Skill | `skills/write-sop/SKILL.md` | `prompts.SOP_SYSTEM` |
| Subagent | `agents/reply-drafter.md` | `prompts.DRAFTER_SYSTEM` + `agent._draft_with_subagent` |
| Hook | `hooks/check_tool_boundary.py` | `src/ticket_agent/gate.py` |

Keep them in step. If a rule changes in one, change it in the other in the
same commit — a skill file that disagrees with the system prompt is worse
than not having it, because it will be believed.

## The hook

It runs on every Write and Edit and parses `tool_schemas.py` without
importing it. If a tool appears that is not on the allowlist, or whose name
looks like a write, or if `agent.py` has gained an import of `executor.py`,
it exits 2 and the edit is refused with an explanation.

Check it by hand:

```bash
python .claude/hooks/check_tool_boundary.py
```

It is the fourth layer of the same argument the rest of the project makes.
`CLAUDE.md` states the rule — that is a request. `tests/test_safety_boundary.py`
proves it holds — but only when someone runs it. The hook enforces it whether
or not anyone remembered to ask, which is the whole difference between a rule
and a control.

## Why there is no MCP plug

MCP does not make an agent smarter. It makes it **consequential**. Everything
built before the plug is reversible; the moment a real send is connected, it
is not.

This system has no reason to send outside itself: the reply goes on the
ticket in its own store, and the SOP is published to its own pack. Adding a
mail plug would add irreversibility and demonstrate nothing the approval
queue does not already demonstrate.

If one is ever added, the order in `CLAUDE.md` section 8 holds — gate and
hook first, plug last — and the credential is scoped, revocable, stored in
config and never in a prompt. A test send to your own address before any
ticket is touched.
