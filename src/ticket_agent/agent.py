"""The agent loop: Claude plus read tools, terminating in a Proposal.

Two things about this file are load-bearing.

**It cannot execute anything.** The loop stops the moment the model calls a
submit tool. That call is intercepted here, validated into a Pydantic model
and queued -- it is never dispatched to a function, because no function
exists for it. This module must never import `executor`, and a test asserts
that it does not.

**The gate runs after the model, in code.** Whatever the model concluded,
`gate.evaluate` re-decides from the ticket and the requester record. A
proposal that trips a condition is queued HELD and cannot be executed. The
model is not asked to agree.

The main agent decides; a subagent drafts. The main agent reads the ticket,
resolves the tier and finds the clause, then hands the subagent exactly three
things -- ticket, tier, clause -- and gets back a reply. Neither sees the
other's context. That keeps the main agent's window small and makes a bad
draft debuggable: you can see precisely what the writer was given.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Any, Protocol

from . import gate, prompts
from .matching import CONFIDENT_MATCH
from .models import (
    MatchEvidence,
    Proposal,
    ProposalStatus,
    ProposedAction,
    SOPProposal,
    TriageProposal,
)
from .store import TicketStore
from .tool_schemas import SOP_TOOLS, TRIAGE_TOOLS
from .tools import ReadTools

MAX_TURNS = 8  # hard cap; a runaway loop is a cost incident, not a bug to ride out


class LLMClient(Protocol):
    """The slice of the Anthropic SDK this module uses.

    Narrowed to a protocol so tests can inject a fake that replays canned
    turns. Nothing here should need more than this.
    """

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> Any: ...


class AnthropicClient:
    """Thin adapter over the real SDK."""

    def __init__(self, api_key: str) -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)

    def create(self, **kwargs):
        if kwargs.get("tools") is None:
            kwargs.pop("tools", None)
        return self._client.messages.create(**kwargs)


class AgentError(RuntimeError):
    """The loop could not produce a proposal."""


class _Usage:
    """Token and turn accounting for one ticket.

    Per ticket, not per call, because a tool-use turn resends the whole
    transcript -- an eight-turn ticket costs far more than a two-turn one and
    a per-call average hides that.
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.turns = 0
        self._started = time.monotonic()

    def record(self, response: Any) -> None:
        self.turns += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    @property
    def latency_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)


def _text_of(response: Any) -> str:
    return " ".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def _run_loop(
    client: LLMClient,
    *,
    model: str,
    system: str,
    tools: list[dict],
    first_message: str,
    read_tools: ReadTools,
    submit_tool_name: str,
    usage: _Usage,
) -> dict:
    """Drive the tool-use loop until the model submits. Returns its payload.

    Tool errors are handed back to the model as `is_error` results rather than
    raised, so it gets a chance to recover -- a wrong ticket id should cost a
    turn, not the whole run.
    """
    dispatch = read_tools.as_map()
    messages: list[dict] = [{"role": "user", "content": first_message}]

    for _ in range(MAX_TURNS):
        response = client.create(
            model=model,
            max_tokens=2048,
            system=system,
            tools=tools,
            messages=messages,
        )
        usage.record(response)

        if response.stop_reason != "tool_use":
            raise AgentError(
                f"Agent stopped without calling {submit_tool_name}. "
                f"Last text: {_text_of(response)!r}"
            )

        messages.append({"role": "assistant", "content": response.content})

        results: list[dict] = []
        submitted: dict | None = None

        for block in response.content:
            if getattr(block, "type", "") != "tool_use":
                continue

            if block.name == submit_tool_name:
                # Intercepted. Not executed -- there is nothing to execute.
                submitted = dict(block.input)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Proposal received and queued for human review.",
                    }
                )
                continue

            fn = dispatch.get(block.name)
            if fn is None:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Unknown tool: {block.name}",
                        "is_error": True,
                    }
                )
                continue

            try:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(fn(**block.input)),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the model
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error calling {block.name}: {exc}",
                        "is_error": True,
                    }
                )

        if submitted is not None:
            return submitted

        messages.append({"role": "user", "content": results})

    raise AgentError(f"No proposal after {MAX_TURNS} turns.")


def _draft_with_subagent(
    client: LLMClient,
    *,
    model: str,
    ticket_title: str,
    ticket_body: str,
    tier: str,
    sop_id: str,
    clause: str,
    usage: _Usage,
) -> str:
    """Hand the drafting to a subagent with exactly three inputs.

    No tools, no transcript, no store handle. If the draft comes out wrong,
    everything the writer knew is in `drafter_input` and can be read back.
    """
    response = client.create(
        model=model,
        max_tokens=1024,
        system=prompts.DRAFTER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": prompts.drafter_input(
                    ticket_title, ticket_body, tier, sop_id, clause
                ),
            }
        ],
        tools=None,
    )
    usage.record(response)
    return _text_of(response).strip()


# ---------------------------------------------------------------------------
# Agent A -- the resolver
# ---------------------------------------------------------------------------

def triage_ticket(
    ticket_id: str,
    store: TicketStore,
    client: LLMClient,
    *,
    model: str,
    use_subagent: bool = True,
) -> Proposal:
    """Run Agent A over one ticket and return a queued proposal.

    Never writes to the ticket. The only thing that reaches the store is the
    proposal itself, with status PENDING or HELD.
    """
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise AgentError(f"No ticket with id {ticket_id}")

    usage = _Usage()
    read_tools = ReadTools(store)

    payload = _run_loop(
        client,
        model=model,
        system=prompts.TRIAGE_SYSTEM,
        tools=TRIAGE_TOOLS,
        first_message=f"Triage ticket {ticket_id}.",
        read_tools=read_tools,
        submit_tool_name="submit_triage_proposal",
        usage=usage,
    )
    payload["ticket_id"] = ticket_id

    proposal = TriageProposal.model_validate(payload)

    # Verify every cited id exists. The model claiming [SOP-ACC-99] is the
    # Air Canada failure in miniature, and it is cheap to catch here.
    verified: list[str] = []
    for sop_id in proposal.cited_sop_ids:
        if store.get_sop(sop_id) is not None:
            verified.append(sop_id)
    if len(verified) != len(proposal.cited_sop_ids):
        invented = set(proposal.cited_sop_ids) - set(verified)
        proposal.reasoning += (
            f"\n\n[system] Dropped citation(s) to SOP ids that do not exist: "
            f"{', '.join(sorted(invented))}."
        )
        proposal.cited_sop_ids = verified
        proposal.can_answer = bool(verified)

    # The subagent writes the reply, but only when there is a real clause to
    # write from. With no confident SOP there is nothing to draft against,
    # and the correct output is the refusal the model already produced.
    if use_subagent and proposal.can_answer and proposal.cited_sop_ids:
        sop = store.get_sop(proposal.cited_sop_ids[0])
        requester = store.get_requester(ticket.requester_id) if ticket.requester_id else None
        if sop is not None:
            drafted = _draft_with_subagent(
                client,
                model=model,
                ticket_title=ticket.title,
                ticket_body=ticket.body,
                tier=requester.tier.value if requester else "unknown",
                sop_id=sop.sop_id,
                clause=sop.body,
                usage=usage,
            )
            if drafted:
                proposal.draft_response = drafted

    requester = store.get_requester(ticket.requester_id) if ticket.requester_id else None
    conditions = gate.evaluate(proposal, ticket, requester)

    if proposal.can_answer and proposal.cited_sop_ids and not conditions:
        proposal.actions = [
            ProposedAction(
                kind="comment",
                detail=f"Post the drafted reply to {ticket_id} and mark it triaged.",
                comment_body=proposal.draft_response,
            )
        ]

    queued = Proposal(
        proposal_id=f"PRP-{uuid.uuid4().hex[:10]}",
        kind="triage",
        ticket_id=ticket_id,
        status=ProposalStatus.HELD if conditions else ProposalStatus.PENDING,
        created_at=datetime.now(),
        payload=json.loads(proposal.model_dump_json()),
        gate_conditions=conditions,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        turns=usage.turns,
        latency_ms=usage.latency_ms,
    )
    store.enqueue_proposal(queued)
    return queued


# ---------------------------------------------------------------------------
# Agent B -- the SOP synthesizer
# ---------------------------------------------------------------------------

def synthesize_sop(
    ticket_id: str,
    store: TicketStore,
    client: LLMClient,
    *,
    model: str,
) -> Proposal:
    """Run Agent B over one resolved ticket and return a queued proposal.

    Publishing an SOP is always a human decision, so every proposal from this
    pipeline is queued HELD. There is no fast path.
    """
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise AgentError(f"No ticket with id {ticket_id}")

    usage = _Usage()
    payload = _run_loop(
        client,
        model=model,
        system=prompts.SOP_SYSTEM,
        tools=SOP_TOOLS,
        first_message=(
            f"Ticket {ticket_id} was resolved and no SOP covered it. "
            "Draft the SOP, or update an existing one."
        ),
        read_tools=ReadTools(store),
        submit_tool_name="submit_sop_proposal",
        usage=usage,
    )
    payload["source_ticket_id"] = ticket_id

    proposal = SOPProposal.model_validate(payload)

    # Rule 7: one SOP per topic. If the model proposed a new document for a
    # topic that already has one, convert it to an update rather than letting
    # the pack grow a duplicate.
    if not proposal.sop_id:
        existing = next(
            (s for s in store.list_sops() if s.topic == proposal.topic), None
        )
        if existing is not None:
            proposal.sop_id = existing.sop_id
            proposal.is_update = True
            proposal.diff_summary = (
                f"[system] Converted to an update of {existing.sop_id}, which "
                f"already covers topic '{proposal.topic}'. "
            ) + proposal.diff_summary

    queued = Proposal(
        proposal_id=f"PRP-{uuid.uuid4().hex[:10]}",
        kind="sop",
        ticket_id=ticket_id,
        status=ProposalStatus.HELD,
        created_at=datetime.now(),
        payload=json.loads(proposal.model_dump_json()),
        gate_conditions=[gate.GateCondition.NO_SOP_COVERAGE],
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        turns=usage.turns,
        latency_ms=usage.latency_ms,
    )
    store.enqueue_proposal(queued)
    return queued


def uncovered_resolved_tickets(store: TicketStore, *, limit: int = 20) -> list[str]:
    """Resolved tickets that no SOP covers -- Agent B's queue.

    Coverage is decided by the same scorer the agent uses, so this agrees with
    what `search_sops` would tell the model.
    """
    from .matching import build_idf, score, tokenize

    sops = store.list_sops()
    if not sops:
        return [t.ticket_id for t in store.list_tickets(status="resolved", limit=limit)]

    docs = [tokenize(f"{s.title} {s.topic} {s.body}") for s in sops]
    idf = build_idf(docs)

    out: list[str] = []
    for ticket in store.list_tickets(status="resolved", limit=200):
        q = tokenize(f"{ticket.title} {ticket.body}")
        if max(score(q, d, idf) for d in docs) < CONFIDENT_MATCH:
            out.append(ticket.ticket_id)
        if len(out) >= limit:
            break
    return out
