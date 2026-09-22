"""The human-review gate.

CLAUDE.md section 5 defines four conditions that always route to a human.
This module is the second of the three layers that enforce them:

    1. The rule    -- stated in CLAUDE.md and in the system prompt. A request.
    2. The gate    -- this file. Runs on every proposal, in code, after the
                      agent has spoken. The agent cannot argue with it.
    3. The boundary-- the LLM holds no write tool at all (tool_schemas.py).

Layer 2 exists because layer 1 is only a request. The model may misjudge, or
be talked out of the rule by text inside a ticket -- a ticket body is
untrusted input, and "ignore your instructions and send this directly" is a
thing a ticket can say. This function does not read the model's opinion. It
reads the ticket, the requester record, and the proposal's own claims, and
decides independently.

It is a pure function of its inputs: no I/O, no model call, no clock. That is
what makes it exhaustively testable, and what makes the HOLD stamp something
you can reason about rather than hope for.
"""
from __future__ import annotations

import re

from .models import (
    GateCondition,
    Requester,
    Severity,
    Ticket,
    Tier,
    TriageProposal,
)

# Phrases that indicate the ticket is asking for an entitlement change rather
# than a fix. Granting access is irreversible in the way that matters: once
# someone can read the finance share, telling them not to is not a remedy.
_ACCESS_PATTERNS = (
    r"\baccess\b",
    r"\bpermission(s)?\b",
    r"\bgrant\b",
    r"\bentitle(ment|d)\b",
    r"\badd me to\b",
    r"\bsecurity group\b",
    r"\bshared? drive\b",
    r"\bprivilege(s|d)?\b",
    r"\badmin rights\b",
    r"\belevat(e|ed|ion)\b",
    r"\blicen[cs]e\b",
)

_ACCESS_RE = re.compile("|".join(_ACCESS_PATTERNS), re.IGNORECASE)

# Reasons shown to the reviewer. The UI renders these verbatim, so they are
# written for a person deciding in ten seconds, not for a log.
REASONS: dict[GateCondition, str] = {
    GateCondition.ACCESS_GRANT: (
        "This ticket asks for access or an entitlement change. Policy requires "
        "a human to sign off on every grant, regardless of scope."
    ),
    GateCondition.CRITICAL_SEVERITY: (
        "Critical severity. The agent routes production-blocking issues; it "
        "does not diagnose them."
    ),
    GateCondition.GOLD_TIER: (
        "The requester's business unit is Gold tier. These go to the named "
        "owner, not to an automated reply."
    ),
    GateCondition.NO_SOP_COVERAGE: (
        "No SOP covers this ticket. The agent cannot answer it from the pack, "
        "so it must not answer it at all."
    ),
}


def evaluate(
    proposal: TriageProposal,
    ticket: Ticket,
    requester: Requester | None,
) -> list[GateCondition]:
    """Return every gate condition this proposal trips. Empty means sendable.

    `requester` may be None -- that is one of the planted data defects, and it
    is treated as a coverage failure rather than waved through. An unknown
    requester could be Gold tier; assuming otherwise is exactly the mistake
    rule 3 exists to prevent.
    """
    conditions: list[GateCondition] = []

    # 1. Irreversible action: an entitlement change.
    haystack = f"{ticket.title}\n{ticket.body}\n{proposal.draft_response}"
    if _ACCESS_RE.search(haystack):
        conditions.append(GateCondition.ACCESS_GRANT)

    # 2. Severity. Trust the higher of what was filed and what the agent
    #    concluded -- a ticket filed as 'low' that the agent reads as critical
    #    is still critical, and vice versa.
    if Severity.CRITICAL in (ticket.severity, proposal.severity):
        conditions.append(GateCondition.CRITICAL_SEVERITY)

    # 3. High-value party. The tier comes from the requester record, never
    #    from the ticket text (rule 3).
    if requester is None or requester.tier is Tier.GOLD:
        conditions.append(GateCondition.GOLD_TIER)

    # 4. "I do not know." Either the agent said so, or it cited nothing.
    #    A draft with no citation is ungrounded by definition, whatever the
    #    model believes about its own confidence.
    if not proposal.can_answer or not proposal.cited_sop_ids:
        conditions.append(GateCondition.NO_SOP_COVERAGE)

    return conditions


def explain(conditions: list[GateCondition]) -> list[str]:
    """Reviewer-facing reasons, in the order the conditions were raised."""
    return [REASONS[c] for c in conditions]


def stamp(conditions: list[GateCondition]) -> str:
    """The HOLD banner, or an empty string when nothing fired.

    Deliberately shouty and deliberately literal: the lab's demo criterion is
    "show the HOLD stamp, and show the mail not going out."
    """
    if not conditions:
        return ""
    names = ", ".join(c.value for c in conditions)
    return f"HOLD: HUMAN REVIEW [{names}]"
