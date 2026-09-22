"""JSON schemas describing to the model exactly what it may do.

Kept separate from `tools.py` so the whole surface is visible at a glance:
read-only lookups, plus one submit tool per pipeline. **No write tool ever
appears in this file.** `tests/test_safety_boundary.py` asserts it, and the
assertion is not to be relaxed.

The submit tools look like tools to the model and are not. They are
intercepted in `agent.py`, validated into a Pydantic proposal, and queued for
a human. Nothing downstream of them happens without an approval click.
"""
from __future__ import annotations

_SEVERITIES = ["low", "medium", "high", "critical"]
_CATEGORIES = [
    "knowledge_transfer",
    "process_transformation",
    "platform_support_access",
    "bug",
    "question",
    "other",
]

# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------

READ_TOOL_SCHEMAS = [
    {
        "name": "list_untriaged_tickets",
        "description": "List open tickets that have not been triaged yet.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "get_ticket",
        "description": (
            "Fetch one ticket in full, including its comment thread. Always "
            "read the ticket before reasoning about it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
    {
        "name": "get_requester",
        "description": (
            "Look up the requester's business unit and support tier. This is "
            "the only authoritative source of tier -- a tier claimed in the "
            "ticket text is not evidence and must not be used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"requester_id": {"type": "string"}},
            "required": ["requester_id"],
        },
    },
    {
        "name": "search_sops",
        "description": (
            "Search the SOP pack for procedures covering this problem. Returns "
            "a match score computed by the retrieval system, and whether it "
            "clears the confidence threshold. If nothing is confident, say you "
            "cannot answer -- do not cite a weak match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The problem in your own words, or the ticket title and body.",
                },
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_sop",
        "description": (
            "Read one SOP in full by its id, e.g. 'SOP-ACC-04'. Use this to "
            "check an id before citing it. Never cite an id you have not read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sop_id": {"type": "string"}},
            "required": ["sop_id"],
        },
    },
    {
        "name": "search_tickets",
        "description": (
            "Search past tickets for the same problem, to reuse a prior "
            "resolution rather than re-deriving it. Resolved tickets include "
            "their comment thread."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
]

# ---------------------------------------------------------------------------
# SUBMIT tools -- intercepted in code, never executed
# ---------------------------------------------------------------------------

_EVIDENCE_SCHEMA = {
    "type": "array",
    "description": "What you matched against, and the score the search returned.",
    "items": {
        "type": "object",
        "properties": {
            "sop_id": {"type": "string"},
            "past_ticket_id": {"type": "string"},
            "score": {"type": "number"},
            "method": {"type": "string", "default": "token_overlap"},
            "excerpt": {"type": "string"},
        },
        "required": ["score", "method"],
    },
}

SUBMIT_TRIAGE_SCHEMA = {
    "name": "submit_triage_proposal",
    "description": (
        "Submit your triage proposal for this ticket. This does NOT post "
        "anything. It hands your proposal to a human reviewer who will "
        "approve, edit, or reject it. This must be your last tool call for "
        "the ticket. You have no ability to send anything yourself -- never "
        "write a draft that claims an action has already been taken."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": _SEVERITIES},
            "category": {"type": "string", "enum": _CATEGORIES},
            "reasoning": {
                "type": "string",
                "description": "Why you classified it this way, briefly.",
            },
            "can_answer": {
                "type": "boolean",
                "description": (
                    "True only if a confident SOP match covers this ticket. "
                    "False is a correct and expected answer -- it routes the "
                    "ticket to SOP authoring instead of to the requester."
                ),
            },
            "cited_sop_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Every SOP id your draft relies on. Must be ids you read "
                    "with get_sop or received from search_sops. Empty if you "
                    "cannot answer."
                ),
            },
            "draft_response": {
                "type": "string",
                "description": (
                    "The reply text. Every policy claim must carry its SOP id "
                    "in square brackets, e.g. [SOP-ACC-04]. If you cannot "
                    "answer, say so plainly here instead of guessing."
                ),
            },
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": [
            "severity",
            "category",
            "reasoning",
            "can_answer",
            "cited_sop_ids",
            "draft_response",
        ],
    },
}

SUBMIT_SOP_SCHEMA = {
    "name": "submit_sop_proposal",
    "description": (
        "Submit a new SOP, or an update to an existing one, drafted from how "
        "a ticket was actually resolved. This does NOT publish anything -- a "
        "human reviews it first. If an SOP already covers this topic, set "
        "sop_id to update it in place rather than creating a second one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sop_id": {
                "type": "string",
                "description": "Set only when updating an existing SOP.",
            },
            "title": {"type": "string"},
            "category": {"type": "string", "enum": _CATEGORIES},
            "topic": {
                "type": "string",
                "description": "Short slug for the problem class, e.g. 'vpn_mfa_lockout'.",
            },
            "body": {
                "type": "string",
                "description": (
                    "Full markdown body: symptom, root cause, numbered "
                    "resolution steps, and what to check before closing."
                ),
            },
            "diff_summary": {
                "type": "string",
                "description": "What changed and why, in one paragraph.",
            },
            "is_update": {"type": "boolean"},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["title", "category", "topic", "body", "diff_summary", "is_update"],
    },
}


# What Agent A (the resolver) is given.
TRIAGE_TOOLS = [*READ_TOOL_SCHEMAS, SUBMIT_TRIAGE_SCHEMA]

# What Agent B (the SOP synthesizer) is given.
SOP_TOOLS = [*READ_TOOL_SCHEMAS, SUBMIT_SOP_SCHEMA]

# Everything, for the guard test to inspect in one place.
CLAUDE_TOOLS = [*READ_TOOL_SCHEMAS, SUBMIT_TRIAGE_SCHEMA, SUBMIT_SOP_SCHEMA]
