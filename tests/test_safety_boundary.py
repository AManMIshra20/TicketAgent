"""The safety invariant, as an executable assertion.

CLAUDE.md section 5 claims the human-review gate is a code boundary rather
than a prompt instruction. That claim is only worth something if it is
checked. This file is the check.

**Do not weaken these tests to make something pass.** If a test here fails,
the boundary has been broken and the project's central argument with it. Fix
the code, or change the design deliberately and update CLAUDE.md in the same
commit.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.ticket_agent import agent, tool_schemas
from src.ticket_agent.models import ProposalStatus

SRC = Path(__file__).resolve().parents[1] / "src" / "ticket_agent"

# Every tool the LLM is permitted to see. Read-only lookups, plus the submit
# tools that are intercepted in code and turned into proposals.
#
# Adding a name here is a deliberate act. Before you do, ask whether the tool
# can change anything a human has not approved. If it can, it does not belong
# in this list -- it belongs in executor.py.
ALLOWED_TOOL_NAMES = {
    # read -- lookups with no side effects
    "list_untriaged_tickets",
    "get_ticket",
    "search_tickets",
    "get_requester",
    "get_sop",
    "search_sops",
    # submit -- intercepted in agent.py, never dispatched to anything
    "submit_triage_proposal",
    "submit_sop_proposal",
}

# Substrings that betray a mutating tool. A tool whose name contains one of
# these has no business being handed to the model.
WRITE_VERBS = (
    "post", "create", "update", "delete", "add_", "assign", "publish",
    "send", "close", "merge", "write", "set_", "remove", "execute", "apply",
)


def test_no_tool_exposed_to_the_model_is_outside_the_allowlist():
    names = {t["name"] for t in tool_schemas.CLAUDE_TOOLS}
    unexpected = names - ALLOWED_TOOL_NAMES
    assert not unexpected, (
        f"tool(s) exposed to the LLM that are not on the allowlist: {unexpected}. "
        "If one of these writes anything, the safety boundary is broken."
    )


def test_no_tool_name_looks_like_a_write():
    offenders = {
        t["name"]
        for t in tool_schemas.CLAUDE_TOOLS
        if any(verb in t["name"].lower() for verb in WRITE_VERBS)
        and not t["name"].startswith("submit_")
    }
    assert not offenders, f"write-shaped tool names exposed to the LLM: {offenders}"


def test_submit_tools_are_the_only_terminal_action():
    """The model's only way to finish is to hand over a proposal."""
    names = {t["name"] for t in tool_schemas.CLAUDE_TOOLS}
    assert any(n.startswith("submit_") for n in names), (
        "no submit tool -- the agent has no way to produce a reviewable proposal"
    )


def test_agent_module_does_not_import_the_executor():
    """agent.py must not be able to execute anything, even by accident."""
    tree = ast.parse((SRC / "agent.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    forbidden = {name for name in imported if "executor" in name}
    assert not forbidden, (
        f"agent.py imports {forbidden}. The agent must not be able to reach "
        "the executor -- that is the whole boundary."
    )


def test_agent_source_never_calls_a_store_write_method():
    """A read tool that quietly mutates would defeat the split."""
    source = inspect.getsource(agent)
    write_methods = (
        "upsert_ticket", "upsert_sop", "upsert_requester", "add_comment",
        "mark_triaged", "resolve_proposal",
    )
    called = [m for m in write_methods if m in source]
    assert not called, f"agent.py references store write method(s): {called}"


@pytest.mark.parametrize("module_name", ["agent", "tool_schemas", "matching", "models"])
def test_read_side_modules_do_not_import_the_executor(module_name):
    tree = ast.parse((SRC / f"{module_name}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "executor" in node.module:
            pytest.fail(f"{module_name}.py imports the executor")


def test_a_rejected_proposal_performs_no_writes(store, corpus):
    """Rejection must be inert. Nothing about the ticket may change."""
    from datetime import datetime

    from src.ticket_agent.models import Proposal, RejectReason

    ticket = next(t for t in corpus.tickets if not t.triaged)
    before = store.get_ticket(ticket.ticket_id)
    assert before is not None
    comments_before = len(before.comments)

    proposal = Proposal(
        proposal_id="P-TEST-1",
        kind="triage",
        ticket_id=ticket.ticket_id,
        created_at=datetime.now(),
        payload={"ticket_id": ticket.ticket_id, "draft_response": "should never be posted"},
    )
    store.enqueue_proposal(proposal)
    store.resolve_proposal(
        "P-TEST-1",
        status=ProposalStatus.REJECTED,
        reject_reason=RejectReason.FABRICATED,
        reviewer_note="cited an SOP that does not exist",
    )

    after = store.get_ticket(ticket.ticket_id)
    assert after is not None
    assert len(after.comments) == comments_before, "a rejected proposal wrote a comment"
    assert after.triaged == before.triaged, "a rejected proposal marked the ticket triaged"
    assert after.model_dump() == before.model_dump(), "a rejected proposal mutated the ticket"


def test_an_edited_approval_preserves_what_the_agent_originally_said(store, corpus):
    """The edit diff is the only ground truth this system generates about its
    own mistakes. Losing it on approve-with-edits would throw that away."""
    from datetime import datetime

    from src.ticket_agent.models import Proposal

    ticket = corpus.tickets[0]
    agent_draft = {"ticket_id": ticket.ticket_id, "draft_response": "agent wording"}
    store.enqueue_proposal(
        Proposal(
            proposal_id="P-TEST-2",
            kind="triage",
            ticket_id=ticket.ticket_id,
            created_at=datetime.now(),
            payload=agent_draft,
        )
    )

    resolved = store.resolve_proposal(
        "P-TEST-2",
        status=ProposalStatus.EDITED,
        payload={"ticket_id": ticket.ticket_id, "draft_response": "human wording"},
        reviewer_note="tone",
    )

    assert resolved.payload["draft_response"] == "human wording"
    assert resolved.original_payload == agent_draft, (
        "the agent's original draft was not preserved -- the edit signal is lost"
    )
