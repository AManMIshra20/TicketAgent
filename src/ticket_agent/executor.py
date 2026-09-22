"""Executes an APPROVED proposal.

This is the only module permitted to call a store write method. It is never
called by `agent.py` -- only by the review path, and only after a human has
approved the specific proposal being passed in.

Every function here re-checks the approval rather than trusting its caller.
Defence in depth: a bug in the web layer that routed a held proposal here
should fail loudly, not post to a requester.
"""
from __future__ import annotations

from datetime import datetime

from .models import (
    Proposal,
    ProposalStatus,
    ProposedAction,
    SOP,
    SOPProposal,
    TriageProposal,
)
from .store import TicketStore

AGENT_SIGNATURE = "Meridian Service Desk Agent (reviewed and approved by a human)"


class NotApproved(RuntimeError):
    """Refused: this proposal has not been approved by a human."""


def _require_approved(proposal: Proposal) -> None:
    if proposal.status not in (ProposalStatus.APPROVED, ProposalStatus.EDITED):
        raise NotApproved(
            f"{proposal.proposal_id} has status {proposal.status.value}. "
            "Only APPROVED or EDITED proposals may be executed."
        )
    if proposal.gate_conditions:
        raise NotApproved(
            f"{proposal.proposal_id} trips the human-review gate "
            f"({', '.join(c.value for c in proposal.gate_conditions)}) and cannot "
            "be executed. Clear the condition or handle it manually."
        )


def execute(proposal: Proposal, store: TicketStore) -> list[str]:
    """Run an approved proposal. Returns a log of what actually happened."""
    _require_approved(proposal)
    if proposal.kind == "triage":
        return _execute_triage(proposal, store)
    if proposal.kind == "sop":
        return _execute_sop(proposal, store)
    raise ValueError(f"Unknown proposal kind: {proposal.kind}")


def _execute_triage(proposal: Proposal, store: TicketStore) -> list[str]:
    triage = TriageProposal.model_validate(proposal.payload)
    log: list[str] = []

    for action in triage.actions:
        log.append(_run_action(triage.ticket_id, action, store))

    store.mark_triaged(triage.ticket_id)
    log.append(f"Marked {triage.ticket_id} as triaged.")
    return log


def _run_action(ticket_id: str, action: ProposedAction, store: TicketStore) -> str:
    if action.kind == "comment":
        body = (action.comment_body or "").strip()
        if not body:
            return f"Skipped empty comment on {ticket_id}."
        store.add_comment(ticket_id, AGENT_SIGNATURE, body)
        return f"Posted reply to {ticket_id} ({len(body)} chars)."

    if action.kind == "add_labels":
        # Labels are a GitHub-adapter concept; the SQLite store has no
        # equivalent, so record it rather than pretending it happened.
        return f"No-op on this store: add_labels {action.labels} on {ticket_id}."

    if action.kind == "assign":
        return f"No-op on this store: assign {ticket_id} to {action.assignee}."

    if action.kind == "publish_sop":
        return f"publish_sop must be carried by an SOP proposal, not a triage one."

    raise ValueError(f"Unknown action kind: {action.kind}")


def _execute_sop(proposal: Proposal, store: TicketStore) -> list[str]:
    sop_proposal = SOPProposal.model_validate(proposal.payload)
    now = datetime.now()

    if sop_proposal.sop_id:
        existing = store.get_sop(sop_proposal.sop_id)
        if existing is None:
            raise ValueError(
                f"{sop_proposal.sop_id} does not exist; refusing to create it "
                "under an id the agent chose."
            )
        store.upsert_sop(
            SOP(
                sop_id=existing.sop_id,
                title=sop_proposal.title or existing.title,
                category=sop_proposal.category,
                topic=existing.topic,
                body=sop_proposal.body,
                updated_at=now,
                revision=existing.revision + 1,
            )
        )
        return [
            f"Updated {existing.sop_id} to revision {existing.revision + 1} "
            f"from {sop_proposal.source_ticket_id}."
        ]

    sop_id = _next_sop_id(store, sop_proposal.category.value)
    store.upsert_sop(
        SOP(
            sop_id=sop_id,
            title=sop_proposal.title,
            category=sop_proposal.category,
            topic=sop_proposal.topic,
            body=sop_proposal.body,
            updated_at=now,
            revision=1,
        )
    )
    return [f"Published new SOP {sop_id} from {sop_proposal.source_ticket_id}."]


_PREFIXES = {
    "platform_support_access": "ACC",
    "knowledge_transfer": "KT",
    "process_transformation": "PRC",
    "bug": "PLT",
    "question": "GEN",
    "other": "GEN",
}


def _next_sop_id(store: TicketStore, category: str) -> str:
    """Allocate the next id in this category.

    Ids are allocated here, not by the model. An id the model invents is an id
    that may already exist, or that encodes a category it guessed at.
    """
    prefix = _PREFIXES.get(category, "GEN")
    existing = [s.sop_id for s in store.list_sops() if s.sop_id.startswith(f"SOP-{prefix}-")]
    numbers = []
    for sop_id in existing:
        tail = sop_id.rsplit("-", 1)[-1]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"SOP-{prefix}-{(max(numbers) + 1) if numbers else 1:02d}"
