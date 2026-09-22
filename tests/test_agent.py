"""The agent loop, exercised end to end with no network.

The loop is the riskiest code here: it is where model output becomes a thing
the system acts on. These tests pin the behaviour that matters -- it cannot
execute, it cannot cite what does not exist, the subagent sees only what it
is given, and the gate overrules the model.
"""
from __future__ import annotations

import pytest

from src.ticket_agent import agent, executor
from src.ticket_agent.models import (
    Category,
    GateCondition,
    ProposalStatus,
    Severity,
    TicketStatus,
    Tier,
)
from tests.fake_llm import FakeLLM, text, tool_call

MODEL = "claude-sonnet-5"


def _submit(**kw):
    payload = dict(
        severity="medium",
        category="platform_support_access",
        reasoning="Matches the VPN MFA procedure.",
        can_answer=True,
        cited_sop_ids=["SOP-ACC-01"],
        draft_response="Per [SOP-ACC-01], clear the cached device certificate.",
    )
    payload.update(kw)
    return tool_call("submit_triage_proposal", **payload)


def _bronze_open_ticket(store, corpus):
    """An open ticket whose requester is Bronze -- so tier alone won't gate it."""
    for ticket in corpus.tickets:
        if ticket.status is not TicketStatus.OPEN or not ticket.requester_id:
            continue
        requester = store.get_requester(ticket.requester_id)
        if (
            requester
            and requester.tier is Tier.BRONZE
            and ticket.severity is not Severity.CRITICAL
            and "access" not in f"{ticket.title} {ticket.body}".lower()
        ):
            return ticket
    pytest.skip("corpus has no ungated open ticket")


def test_loop_returns_a_queued_proposal_and_writes_nothing_to_the_ticket(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    before = store.get_ticket(ticket.ticket_id).model_dump()

    llm = FakeLLM([_submit(), text("drafted reply", stop_reason="end_turn")])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )

    assert proposal.kind == "triage"
    assert proposal.status in (ProposalStatus.PENDING, ProposalStatus.HELD)
    assert store.get_ticket(ticket.ticket_id).model_dump() == before, (
        "running the agent changed the ticket -- it must only queue a proposal"
    )


def test_agent_uses_read_tools_before_submitting(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM(
        [
            tool_call("get_ticket", ticket_id=ticket.ticket_id),
            tool_call("search_sops", query="vpn mfa"),
            _submit(),
        ]
    )
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.turns == 3
    assert llm.exhausted


def test_invented_sop_citation_is_stripped(store, corpus):
    """The Air Canada failure, caught in code: an id that does not exist is
    removed rather than passed to a reviewer as if it were real."""
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([_submit(cited_sop_ids=["SOP-ACC-99"])])

    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    triage = proposal.triage()

    assert "SOP-ACC-99" not in triage.cited_sop_ids
    assert triage.can_answer is False
    assert "do not exist" in triage.reasoning
    assert GateCondition.NO_SOP_COVERAGE in proposal.gate_conditions


def test_a_real_citation_survives(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([_submit(cited_sop_ids=["SOP-ACC-01"])])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.triage().cited_sop_ids == ["SOP-ACC-01"]


def test_subagent_receives_only_ticket_tier_and_clause(store, corpus):
    """The lane separation, asserted on what actually crossed the boundary."""
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([_submit(), text("Per [SOP-ACC-01], please retry.", stop_reason="end_turn")])

    agent.triage_ticket(ticket.ticket_id, store, llm, model=MODEL, use_subagent=True)

    drafter_call = llm.calls[-1]
    assert drafter_call["tools"] is None, "the subagent was given tools"
    assert len(drafter_call["messages"]) == 1, "the subagent was given a transcript"

    content = drafter_call["messages"][0]["content"]
    assert ticket.title in content
    assert "REQUESTER TIER:" in content
    assert "SOP CLAUSE [SOP-ACC-01]" in content

    # It must not have been handed the rest of the corpus.
    other_titles = [
        t.title for t in corpus.tickets if t.ticket_id != ticket.ticket_id
    ]
    leaked = [t for t in other_titles if t and t in content and t != ticket.title]
    assert not leaked, f"subagent context leaked other tickets: {leaked[:3]}"


def test_subagent_draft_replaces_the_main_agent_draft(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM(
        [_submit(), text("Subagent wording, per [SOP-ACC-01].", stop_reason="end_turn")]
    )
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=True
    )
    assert proposal.triage().draft_response == "Subagent wording, per [SOP-ACC-01]."


def test_no_subagent_call_when_the_agent_cannot_answer(store, corpus):
    """Nothing to draft from means nothing to draft. The refusal stands."""
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([_submit(can_answer=False, cited_sop_ids=[], draft_response="Cannot answer.")])

    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=True
    )
    assert llm.exhausted, "a subagent was called with no clause to draft from"
    assert proposal.triage().draft_response == "Cannot answer."


def test_gate_overrules_the_model(store, corpus):
    """The model says it can answer; the ticket is an access request. The
    gate does not care what the model concluded."""
    ticket = next(
        t
        for t in corpus.tickets
        if t.status is TicketStatus.OPEN and "access" in f"{t.title} {t.body}".lower()
    )
    llm = FakeLLM([_submit(cited_sop_ids=["SOP-ACC-04"])])

    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.status is ProposalStatus.HELD
    assert GateCondition.ACCESS_GRANT in proposal.gate_conditions
    assert proposal.triage().actions == [], "a held proposal was given executable actions"


def test_held_proposal_cannot_be_executed(store, corpus):
    ticket = next(
        t
        for t in corpus.tickets
        if t.status is TicketStatus.OPEN and "access" in f"{t.title} {t.body}".lower()
    )
    llm = FakeLLM([_submit(cited_sop_ids=["SOP-ACC-04"])])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )

    # Even if something approves it, the executor refuses on the gate.
    approved = store.resolve_proposal(
        proposal.proposal_id, status=ProposalStatus.APPROVED
    )
    with pytest.raises(executor.NotApproved):
        executor.execute(approved, store)


def test_pending_proposal_cannot_be_executed_without_approval(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([_submit()])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    with pytest.raises(executor.NotApproved):
        executor.execute(proposal, store)


def test_approved_proposal_posts_exactly_one_comment(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    before = len(store.get_ticket(ticket.ticket_id).comments)

    llm = FakeLLM([_submit()])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.status is ProposalStatus.PENDING, proposal.gate_conditions

    approved = store.resolve_proposal(proposal.proposal_id, status=ProposalStatus.APPROVED)
    log = executor.execute(approved, store)

    after = store.get_ticket(ticket.ticket_id)
    assert len(after.comments) == before + 1
    assert after.triaged is True
    assert any("Posted reply" in line for line in log)


def test_model_that_never_submits_raises(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([text("I think I will just chat instead.", stop_reason="end_turn")])
    with pytest.raises(agent.AgentError, match="without calling"):
        agent.triage_ticket(ticket.ticket_id, store, llm, model=MODEL, use_subagent=False)


def test_turn_cap_is_enforced(store, corpus):
    """A runaway loop is a cost incident. It must stop."""
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([tool_call("search_sops", query="anything") for _ in range(agent.MAX_TURNS + 2)])
    with pytest.raises(agent.AgentError, match="No proposal after"):
        agent.triage_ticket(ticket.ticket_id, store, llm, model=MODEL, use_subagent=False)


def test_tool_error_is_returned_to_the_model_not_raised(store, corpus):
    """A bad ticket id should cost a turn, not the run."""
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([tool_call("get_ticket", ticket_id="NOPE-1"), _submit()])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.turns == 2


def test_unknown_tool_is_reported_not_dispatched(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([tool_call("delete_everything", target="prod"), _submit()])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.turns == 2


def test_tokens_are_accumulated_per_ticket_not_per_call(store, corpus):
    ticket = _bronze_open_ticket(store, corpus)
    llm = FakeLLM([tool_call("search_sops", query="x"), tool_call("search_sops", query="y"), _submit()])
    proposal = agent.triage_ticket(
        ticket.ticket_id, store, llm, model=MODEL, use_subagent=False
    )
    assert proposal.turns == 3
    assert proposal.input_tokens == 3 * 800
    assert proposal.output_tokens == 3 * 400


# ---------------------------------------------------------------------------
# Agent B
# ---------------------------------------------------------------------------

def _submit_sop(**kw):
    payload = dict(
        title="Teams recording not in the channel",
        category="platform_support_access",
        topic="teams_recording_missing",
        body="# Symptom\n...\n# Resolution steps\n1. Check where the meeting was created.",
        diff_summary="New procedure drafted from the resolved thread.",
        is_update=False,
    )
    payload.update(kw)
    return tool_call("submit_sop_proposal", **payload)


def test_sop_proposals_are_always_held(store, corpus):
    ticket = next(t for t in corpus.tickets if t.status is TicketStatus.RESOLVED)
    llm = FakeLLM([_submit_sop()])
    proposal = agent.synthesize_sop(ticket.ticket_id, store, llm, model=MODEL)
    assert proposal.status is ProposalStatus.HELD


def test_a_new_sop_for_an_existing_topic_is_converted_to_an_update(store, corpus):
    """Rule 7: one SOP per topic. The pack must not grow a duplicate."""
    ticket = next(t for t in corpus.tickets if t.status is TicketStatus.RESOLVED)
    existing = store.get_sop("SOP-ACC-01")
    assert existing is not None

    llm = FakeLLM([_submit_sop(topic=existing.topic, is_update=False)])
    proposal = agent.synthesize_sop(ticket.ticket_id, store, llm, model=MODEL)
    sop = proposal.sop()

    assert sop.is_update is True
    assert sop.sop_id == existing.sop_id
    assert "Converted to an update" in sop.diff_summary


def test_approved_sop_update_bumps_the_revision_in_place(store, corpus):
    ticket = next(t for t in corpus.tickets if t.status is TicketStatus.RESOLVED)
    existing = store.get_sop("SOP-ACC-01")
    before_count = len(store.list_sops())

    llm = FakeLLM([_submit_sop(topic=existing.topic, sop_id=existing.sop_id, is_update=True)])
    proposal = agent.synthesize_sop(ticket.ticket_id, store, llm, model=MODEL)

    # A held SOP proposal still needs the gate cleared to publish; clear it
    # the way the review path does for an explicit human decision.
    proposal.gate_conditions = []
    proposal.status = ProposalStatus.APPROVED
    log = executor.execute(proposal, store)

    updated = store.get_sop(existing.sop_id)
    assert updated.revision == existing.revision + 1
    assert len(store.list_sops()) == before_count, "the pack grew a duplicate"
    assert "Updated" in log[0]


def test_new_sop_gets_an_id_allocated_by_code_not_the_model(store, corpus):
    ticket = next(t for t in corpus.tickets if t.status is TicketStatus.RESOLVED)
    llm = FakeLLM([_submit_sop(topic="a_brand_new_topic")])
    proposal = agent.synthesize_sop(ticket.ticket_id, store, llm, model=MODEL)

    proposal.gate_conditions = []
    proposal.status = ProposalStatus.APPROVED
    log = executor.execute(proposal, store)

    created = [s for s in store.list_sops() if s.topic == "a_brand_new_topic"]
    assert len(created) == 1, "expected exactly one new SOP for the new topic"
    new_sop = created[0]

    # The id follows the category's prefix scheme and does not collide.
    assert new_sop.sop_id.startswith("SOP-ACC-")
    assert new_sop.sop_id in log[0]
    assert new_sop.revision == 1
    assert len({s.sop_id for s in store.list_sops()}) == len(store.list_sops())


def test_uncovered_resolved_tickets_finds_agent_b_work(store, corpus):
    ids = agent.uncovered_resolved_tickets(store, limit=20)
    assert ids, "no uncovered resolved tickets -- Agent B has nothing to do"
    sop_topics = {s.topic for s in corpus.sops}
    by_id = {t.ticket_id: t for t in corpus.tickets}
    for ticket_id in ids:
        assert by_id[ticket_id].truth_topic not in sop_topics, (
            f"{ticket_id} is covered by an SOP but was queued for authoring"
        )
