"""Typed data structures.

The Proposal models are the hard boundary between "the LLM decided" and "an
action actually happened" -- the agent can only ever produce a Proposal,
never call a write tool directly. Everything the reviewer sees, and
everything the executor acts on, passes through these types.

Nothing in this module may import the Anthropic SDK or the store. It is the
vocabulary both sides share, and it stays pure so tests can build any object
here without a database or an API key.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Category(str, Enum):
    KNOWLEDGE_TRANSFER = "knowledge_transfer"
    PROCESS_TRANSFORMATION = "process_transformation"
    PLATFORM_SUPPORT_ACCESS = "platform_support_access"
    BUG = "bug"
    QUESTION = "question"
    OTHER = "other"


class Tier(str, Enum):
    """Support tier of the requester's business unit.

    Gold is the gate condition -- see CLAUDE.md section 5. The tier is
    authoritative only from requesters.csv; a tier stated in the ticket body
    is not trusted.
    """

    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"


class TicketStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"          # approved, but the reviewer changed the body
    REJECTED = "rejected"
    HELD = "held"              # a gate condition fired; cannot be executed


class GateCondition(str, Enum):
    """The four conditions that always route to a human.

    Adapted from the workshop's refund / critical / Enterprise / missing-info
    set to this project's domain. The shape is preserved: one irreversible
    action, one severity, one high-value party, one "I do not know".
    """

    ACCESS_GRANT = "access_grant"
    CRITICAL_SEVERITY = "critical_severity"
    GOLD_TIER = "gold_tier"
    NO_SOP_COVERAGE = "no_sop_coverage"


class RejectReason(str, Enum):
    """Why a reviewer rejected a proposal.

    Drawn from the failure modes in CLAUDE.md 12a so the reject count is a
    diagnosis rather than a number.
    """

    FABRICATED = "fabricated"              # cited something that does not exist
    WRONG_SOP = "wrong_sop"                # cited a real SOP, wrong one
    MISSED_GATE = "missed_gate"            # should have routed to a human
    STALE_SOP = "stale_sop"                # SOP itself is out of date
    BAD_DATA = "bad_data"                  # acted on a dirty record
    TONE = "tone"                          # correct but unsendable
    OTHER = "other"


# ---------------------------------------------------------------------------
# Domain records
# ---------------------------------------------------------------------------

class Requester(BaseModel):
    """A person who files tickets. The authoritative source of tier."""

    requester_id: str
    name: str
    business_unit: str
    tier: Tier


class Comment(BaseModel):
    author: str
    body: str
    created_at: datetime


class Ticket(BaseModel):
    """One ticket, as stored.

    Records are loaded from the raw feed *as they are* -- including the
    planted defects. Cleaning is an agent task that reports what it changed,
    not a silent preprocessing step, so a ticket in the store may legitimately
    have a missing requester or a blank body.
    """

    ticket_id: str
    title: str
    body: str = ""
    category: Category = Category.OTHER
    severity: Severity = Severity.MEDIUM
    requester_id: str | None = None
    status: TicketStatus = TicketStatus.OPEN
    created_at: datetime
    resolved_at: datetime | None = None
    comments: list[Comment] = Field(default_factory=list)
    resolution_notes: str = ""

    # Bookkeeping, not ground truth the agent may read.
    triaged: bool = False
    cleaned: bool = False
    data_issues: list[str] = Field(default_factory=list)

    # Ground truth for evaluation only. Never exposed through a read tool --
    # it is what we grade the agent against, not what we tell it.
    truth_topic: str | None = None
    truth_sop_id: str | None = None


class SOP(BaseModel):
    """A standard operating procedure. The unit the agent cites."""

    sop_id: str
    title: str
    category: Category
    topic: str
    body: str
    updated_at: datetime
    revision: int = 1


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class MatchEvidence(BaseModel):
    """Why the agent believes this ticket matches this SOP or past ticket.

    A score the *code* computed, not one the model reported about itself. The
    UI renders this beside every proposal so a reviewer can judge the match
    rather than take the model's word for it.
    """

    sop_id: str | None = None
    past_ticket_id: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    method: str = Field(description="How the score was computed, e.g. 'token_overlap'.")
    excerpt: str = Field(default="", description="The matched passage, for the reviewer.")


# ---------------------------------------------------------------------------
# Proposals -- the only thing the agent can produce
# ---------------------------------------------------------------------------

class ProposedAction(BaseModel):
    """One concrete, reviewable action the executor could take on approval."""

    kind: Literal["comment", "add_labels", "assign", "publish_sop"]
    detail: str = Field(
        description="Human-readable description of exactly what this action will do."
    )
    comment_body: str | None = None
    labels: list[str] | None = None
    assignee: str | None = None
    sop_path: str | None = None
    sop_body: str | None = None


class TriageProposal(BaseModel):
    """What Agent A produces for a single ticket. Never auto-executed."""

    ticket_id: str
    severity: Severity
    category: Category
    reasoning: str = Field(description="Why the agent classified it this way.")
    draft_response: str = Field(description="Draft first-response text.")
    cited_sop_ids: list[str] = Field(default_factory=list)
    can_answer: bool = Field(
        default=True,
        description="False when no SOP covers this. A false here is a correct "
        "outcome, not a failure -- it hands the ticket to Agent B.",
    )
    evidence: list[MatchEvidence] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)


class SOPProposal(BaseModel):
    """What Agent B produces: a new SOP, or an update to an existing one.

    `sop_id` being set means update-in-place. Rule 7 in CLAUDE.md: one SOP per
    topic. Creating a second SOP for a topic that already has one is a defect.
    """

    source_ticket_id: str
    sop_id: str | None = None
    title: str
    category: Category
    topic: str
    body: str = Field(description="Full markdown body of the new or updated SOP.")
    diff_summary: str = Field(description="What changed and why, in one paragraph.")
    is_update: bool = False
    evidence: list[MatchEvidence] = Field(default_factory=list)


class Proposal(BaseModel):
    """Queue envelope around a TriageProposal or SOPProposal.

    The reviewer acts on this. `original_payload` is kept so an
    approve-with-edits yields a diff -- per CLAUDE.md section 11, that diff is
    the most valuable signal the system produces and must not be discarded.
    """

    proposal_id: str
    kind: Literal["triage", "sop"]
    ticket_id: str
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime
    payload: dict
    original_payload: dict | None = None
    gate_conditions: list[GateCondition] = Field(default_factory=list)
    reviewed_at: datetime | None = None
    reviewer_note: str = ""
    reject_reason: RejectReason | None = None

    # Measurement -- logged per ticket, not per call. A tool-use turn resends
    # the whole transcript, so turn count is part of the cost story.
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    latency_ms: int = 0

    @property
    def is_held(self) -> bool:
        return bool(self.gate_conditions)

    def triage(self) -> TriageProposal:
        return TriageProposal.model_validate(self.payload)

    def sop(self) -> SOPProposal:
        return SOPProposal.model_validate(self.payload)
