"""The review console, including the claim its whole design rests on:
approve is the only route that reaches the executor.

These run against a temporary database so they never touch the real one.
"""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

from src.ticket_agent import agent
from src.ticket_agent.demo import OfflineClient
from src.ticket_agent.models import ProposalStatus, TicketStatus
from src.ticket_agent.seed import generate, ingest


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A fresh app bound to a throwaway database."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from src.ticket_agent.web import app as app_module

    importlib.reload(app_module)
    ingest(generate(42), app_module.store)
    yield app_module, TestClient(app_module.app)

    # Stop the worker and wait for it before closing the connection. The
    # worker shares this store across threads, and closing it mid-query takes
    # the interpreter down rather than raising.
    app_module._stop_worker()
    thread = app_module.worker.thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    app_module.store.close()


def _queue_one(app_module, *, gated: bool):
    """Queue a proposal, either clean or gate-held."""
    store = app_module.store
    client = OfflineClient(store)
    for ticket in store.list_tickets(status="open", limit=500):
        proposal = agent.triage_ticket(ticket.ticket_id, store, client, model="demo")
        if bool(proposal.gate_conditions) == gated:
            return proposal
    pytest.skip(f"no {'held' if gated else 'clean'} proposal available")


def test_health_and_pages_render(web):
    _, client = web
    assert client.get("/healthz").json()["status"] == "ok"
    for url in ("/", "/queue", "/tickets", "/sops", "/partials/status"):
        assert client.get(url).status_code == 200, url


def test_demo_mode_is_declared_on_the_page(web):
    """Running without a key must be visible, not silent."""
    _, client = web
    assert "without a model key" in client.get("/").text


def test_approving_a_clean_proposal_posts_the_reply(web):
    app_module, client = web
    proposal = _queue_one(app_module, gated=False)
    store = app_module.store
    before = len(store.get_ticket(proposal.ticket_id).comments)

    r = client.post(
        f"/queue/{proposal.proposal_id}/decide",
        data={"action": "approve", "draft": proposal.payload["draft_response"]},
        follow_redirects=False,
    )
    assert r.status_code == 303

    after = store.get_ticket(proposal.ticket_id)
    assert len(after.comments) == before + 1
    assert after.triaged is True
    assert store.get_proposal(proposal.proposal_id).status is ProposalStatus.APPROVED


def test_rejecting_writes_nothing(web):
    app_module, client = web
    proposal = _queue_one(app_module, gated=False)
    store = app_module.store
    before = store.get_ticket(proposal.ticket_id).model_dump()

    client.post(
        f"/queue/{proposal.proposal_id}/decide",
        data={"action": "reject", "reason": "wrong_sop", "note": "cites the wrong procedure"},
        follow_redirects=False,
    )

    assert store.get_ticket(proposal.ticket_id).model_dump() == before
    resolved = store.get_proposal(proposal.proposal_id)
    assert resolved.status is ProposalStatus.REJECTED
    assert resolved.reject_reason.value == "wrong_sop"


def test_a_held_proposal_is_not_sent_without_a_recorded_reason(web):
    """The HOLD is real: clicking approve on a held item does nothing until
    the reviewer records why they are overriding it."""
    app_module, client = web
    proposal = _queue_one(app_module, gated=True)
    store = app_module.store
    before = len(store.get_ticket(proposal.ticket_id).comments)

    r = client.post(
        f"/queue/{proposal.proposal_id}/decide",
        data={"action": "approve", "draft": "send it anyway", "note": ""},
        follow_redirects=False,
    )

    assert "blocked=1" in r.headers["location"]
    assert len(store.get_ticket(proposal.ticket_id).comments) == before, (
        "a held proposal was sent without a reviewer taking responsibility"
    )


def test_a_held_proposal_sends_once_the_reviewer_takes_it_on(web):
    app_module, client = web
    proposal = _queue_one(app_module, gated=True)
    store = app_module.store

    client.post(
        f"/queue/{proposal.proposal_id}/decide",
        data={
            "action": "approve",
            "draft": "Approved by the account owner for this unit.",
            "note": "Checked with the named owner for Finance.",
        },
        follow_redirects=False,
    )
    resolved = store.get_proposal(proposal.proposal_id)
    assert resolved.status in (ProposalStatus.APPROVED, ProposalStatus.EDITED)


def test_editing_the_draft_is_preserved_as_a_diff(web):
    app_module, client = web
    proposal = _queue_one(app_module, gated=False)
    store = app_module.store
    original = proposal.payload["draft_response"]

    client.post(
        f"/queue/{proposal.proposal_id}/decide",
        data={"action": "approve", "draft": "Reworded by the reviewer."},
        follow_redirects=False,
    )

    resolved = store.get_proposal(proposal.proposal_id)
    assert resolved.status is ProposalStatus.EDITED
    assert resolved.payload["draft_response"] == "Reworded by the reviewer."
    assert resolved.original_payload["draft_response"] == original

    # The edited text is what actually got posted, not the agent's version.
    posted = store.get_ticket(proposal.ticket_id).comments[-1].body
    assert posted == "Reworded by the reviewer."


def test_the_worker_only_enqueues_and_never_decides(web):
    """The 'Start agent' button must not be able to send anything."""
    app_module, client = web
    store = app_module.store

    client.post("/run/triage", data={"limit": "5"}, follow_redirects=False)

    proposals = store.list_proposals(limit=500)
    assert proposals, "triage run produced nothing"
    assert all(
        p.status in (ProposalStatus.PENDING, ProposalStatus.HELD) for p in proposals
    ), "the agent decided a proposal on its own"

    # And nothing was posted to any ticket.
    for ticket in store.list_tickets(status="open", limit=500):
        agent_comments = [
            c for c in ticket.comments if "Agent" in c.author
        ]
        assert not agent_comments, f"{ticket.ticket_id} was replied to without approval"


def test_run_sop_queues_held_sop_proposals(web):
    app_module, client = web
    store = app_module.store
    before = len(store.list_sops())

    client.post("/run/sop", data={"limit": "2"}, follow_redirects=False)

    sop_proposals = [p for p in store.list_proposals(limit=500) if p.kind == "sop"]
    assert sop_proposals, "no SOP proposals queued"
    assert all(p.status is ProposalStatus.HELD for p in sop_proposals)
    assert len(store.list_sops()) == before, "an SOP was published without approval"


def test_systems_board_shows_jira_confluence_and_the_agent(web):
    app_module, client = web
    page = client.get("/").text
    assert "Jira" in page
    assert "Confluence" in page
    assert "Triage agent" in page
    assert "Activate" in page


def test_stand_ins_are_not_described_as_connected(web):
    """Calling a local store 'Connected' would misdescribe the system to
    whoever is reading the dashboard."""
    app_module, _ = web
    systems = app_module._systems()
    by_key = {s["key"]: s for s in systems}

    assert by_key["jira"]["state"] == "stand-in"
    assert by_key["confluence"]["state"] == "stand-in"
    assert "SQLite" in by_key["jira"]["backing"]


def test_jira_reports_live_when_a_real_repo_is_configured(web, monkeypatch):
    app_module, _ = web
    monkeypatch.setenv("GITHUB_REPO", "someone/their-desk")
    jira = next(s for s in app_module._systems() if s["key"] == "jira")
    assert jira["state"] == "live"
    assert "someone/their-desk" in jira["backing"]


def test_the_agent_card_reports_its_real_state(web):
    app_module, client = web
    assert "Inactive" in client.get("/").text

    client.post("/run/start", follow_redirects=False)
    page = client.get("/").text
    assert "Active" in page and "Deactivate" in page

    client.post("/run/stop", follow_redirects=False)
    assert "Activate" in client.get("/").text


def test_start_and_stop_the_worker(web):
    app_module, client = web
    client.post("/run/start", follow_redirects=False)
    assert app_module.worker.running is True
    client.post("/run/stop", follow_redirects=False)
    assert app_module.worker.running is False


def test_queue_filters_do_not_error(web):
    app_module, client = web
    _queue_one(app_module, gated=True)
    for show in ("open", "held", "pending", "rejected", "all"):
        assert client.get(f"/queue?show={show}").status_code == 200


def test_unknown_proposal_redirects_rather_than_500s(web):
    _, client = web
    r = client.get("/queue/NOPE", follow_redirects=False)
    assert r.status_code == 303
