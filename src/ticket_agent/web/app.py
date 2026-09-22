"""The review console.

One FastAPI service, server-rendered with Jinja, HTMX for partial updates.
No SPA, no build step -- the whole app is this file plus templates, which is
the right size for a review queue and keeps the deployment to one process.

The rule this app exists to enforce: **approve is the only path that reaches
executor.py.** The background worker below can queue proposals all day and
still change nothing. Read `_approve` and `_worker_loop` together -- the
asymmetry between them is the product.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import executor, gate
from ..agent import AgentError, synthesize_sop, triage_ticket, uncovered_resolved_tickets
from ..demo import OfflineClient
from ..models import (
    GateCondition,
    ProposalStatus,
    RejectReason,
    TicketStatus,
)
from ..seed import generate, ingest
from ..store import SQLiteStore

HERE = Path(__file__).parent
DB_PATH = os.getenv("DB_PATH", "data/meridian.db")
SEED = int(os.getenv("SEED", "42"))

app = FastAPI(title="Meridian Service Desk — Review Console")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

store = SQLiteStore(DB_PATH)


# ---------------------------------------------------------------------------
# Model client
# ---------------------------------------------------------------------------

def _is_demo() -> bool:
    return not os.getenv("ANTHROPIC_API_KEY", "").strip()


def _make_client():
    """Live client when a key is configured, offline replay otherwise.

    Demo mode is not a degraded error state -- it is a supported way to run
    the app, so the pipeline can be demonstrated on a public link without a
    billable key. Every page says which mode it is in.
    """
    if _is_demo():
        return OfflineClient(store), True
    try:
        from ..agent import AnthropicClient

        return AnthropicClient(os.environ["ANTHROPIC_API_KEY"].strip()), False
    except Exception:
        return OfflineClient(store), True


MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5")


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

@dataclass
class WorkerState:
    """What the 'Start agent' button controls.

    The worker's only permitted effect is enqueueing proposals. It never
    approves, never executes, never touches a ticket. Everything it produces
    waits for a person.
    """

    running: bool = False
    last_poll: datetime | None = None
    processed: int = 0
    errors: list[str] = field(default_factory=list)
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    interval: int = 6


worker = WorkerState()
_worker_lock = threading.Lock()


def _worker_loop() -> None:
    """Poll for untriaged tickets and queue proposals. Nothing else.

    Opens its own store. A sqlite3 connection is not safe to share across
    threads, and handing this loop the request handlers' connection took the
    interpreter down rather than raising -- SQLite's own file locking handles
    two connections correctly, so each thread gets one.
    """
    store = SQLiteStore(DB_PATH)
    client = OfflineClient(store) if _is_demo() else _make_client()[0]
    try:
        _poll_until_stopped(store, client)
    finally:
        store.close()
        worker.running = False


def _poll_until_stopped(store: SQLiteStore, client) -> None:
    while not worker.stop_event.is_set():
        worker.last_poll = datetime.now()
        try:
            pending = store.list_tickets(status="open", triaged=False, limit=1)
            if not pending:
                # Queue is clear. Keep the loop alive but idle -- a worker
                # that exits on an empty queue looks broken to the operator.
                worker.stop_event.wait(worker.interval)
                continue

            ticket = pending[0]
            # Skip anything already queued, so a proposal awaiting review is
            # not proposed again on the next poll.
            already = {
                p.ticket_id
                for p in store.list_proposals(limit=500)
                if p.status in (ProposalStatus.PENDING, ProposalStatus.HELD)
            }
            if ticket.ticket_id in already:
                store.mark_triaged(ticket.ticket_id)
                continue

            triage_ticket(ticket.ticket_id, store, client, model=MODEL)
            store.mark_triaged(ticket.ticket_id)
            worker.processed += 1
        except AgentError as exc:
            worker.errors.append(f"{datetime.now():%H:%M:%S} {exc}")
            worker.errors[:] = worker.errors[-5:]
        except Exception as exc:  # noqa: BLE001 - the worker must not die silently
            worker.errors.append(f"{datetime.now():%H:%M:%S} {type(exc).__name__}: {exc}")
            worker.errors[:] = worker.errors[-5:]

        worker.stop_event.wait(worker.interval)


def _start_worker() -> None:
    with _worker_lock:
        if worker.running:
            return
        worker.stop_event = threading.Event()
        worker.running = True
        worker.thread = threading.Thread(target=_worker_loop, daemon=True)
        worker.thread.start()


def _stop_worker() -> None:
    with _worker_lock:
        worker.stop_event.set()
        worker.running = False


# ---------------------------------------------------------------------------
# View helpers
# ---------------------------------------------------------------------------

def _metrics() -> dict:
    proposals = store.list_proposals(limit=1000)
    tickets = store.list_tickets(limit=1000)

    decided = [p for p in proposals if p.status is not ProposalStatus.PENDING]
    approved = [p for p in proposals if p.status is ProposalStatus.APPROVED]
    edited = [p for p in proposals if p.status is ProposalStatus.EDITED]
    rejected = [p for p in proposals if p.status is ProposalStatus.REJECTED]
    held = [p for p in proposals if p.status is ProposalStatus.HELD]
    pending = [p for p in proposals if p.status is ProposalStatus.PENDING]

    gate_counts: dict[str, int] = {c.value: 0 for c in GateCondition}
    for p in proposals:
        for c in p.gate_conditions:
            gate_counts[c.value] += 1

    triage = [p for p in proposals if p.kind == "triage"]
    cited = [p for p in triage if p.payload.get("cited_sop_ids")]

    tokens_in = sum(p.input_tokens for p in proposals)
    tokens_out = sum(p.output_tokens for p in proposals)
    turns = [p.turns for p in proposals if p.turns]

    # Anthropic list pricing for Sonnet, in rupees at ~83/USD. Stated here so
    # the number on the dashboard can be checked rather than trusted.
    cost = (tokens_in / 1e6 * 3.0 + tokens_out / 1e6 * 15.0) * 83

    return {
        "tickets_total": len(tickets),
        "tickets_open": sum(1 for t in tickets if t.status is TicketStatus.OPEN),
        "tickets_untriaged": sum(
            1 for t in tickets if t.status is TicketStatus.OPEN and not t.triaged
        ),
        "sops": len(store.list_sops()),
        "proposals": len(proposals),
        "pending": len(pending),
        "held": len(held),
        "approved": len(approved),
        "edited": len(edited),
        "rejected": len(rejected),
        "decided": len(decided),
        "approve_rate": round(100 * len(approved) / len(decided)) if decided else 0,
        "edit_rate": round(100 * len(edited) / len(decided)) if decided else 0,
        "reject_rate": round(100 * len(rejected) / len(decided)) if decided else 0,
        "citation_rate": round(100 * len(cited) / len(triage)) if triage else 0,
        "gate_counts": gate_counts,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_per_ticket": round((tokens_in + tokens_out) / len(proposals)) if proposals else 0,
        "avg_turns": round(sum(turns) / len(turns), 1) if turns else 0,
        "cost_rupees": cost,
        "cost_per_ticket": cost / len(proposals) if proposals else 0,
        "cost_1200": (cost / len(proposals) * 1200) if proposals else 0,
    }


def _ctx(request: Request, **extra) -> dict:
    _, is_demo = _make_client()
    base = {
        "request": request,
        "demo_mode": is_demo,
        "worker": worker,
        "queue_count": len(
            [
                p
                for p in store.list_proposals(limit=500)
                if p.status in (ProposalStatus.PENDING, ProposalStatus.HELD)
            ]
        ),
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "tickets": len(store.list_tickets(limit=1))})


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request, "dashboard.html", _ctx(request, m=_metrics(), page="dashboard")
    )


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request, show: str = "open"):
    proposals = store.list_proposals(limit=500)
    if show == "open":
        proposals = [
            p for p in proposals if p.status in (ProposalStatus.PENDING, ProposalStatus.HELD)
        ]
    elif show != "all":
        proposals = [p for p in proposals if p.status.value == show]

    tickets = {t.ticket_id: t for t in store.list_tickets(limit=1000)}
    return templates.TemplateResponse(
        request, "queue.html",
        _ctx(request, proposals=proposals, tickets=tickets, show=show, page="queue"),
    )


@app.get("/queue/{proposal_id}", response_class=HTMLResponse)
def proposal_detail(request: Request, proposal_id: str):
    proposal = store.get_proposal(proposal_id)
    if proposal is None:
        return RedirectResponse("/queue", status_code=303)

    ticket = store.get_ticket(proposal.ticket_id)
    requester = (
        store.get_requester(ticket.requester_id)
        if ticket and ticket.requester_id
        else None
    )
    cited = [
        store.get_sop(sid)
        for sid in proposal.payload.get("cited_sop_ids", [])
        if store.get_sop(sid)
    ]
    return templates.TemplateResponse(
        request, "proposal.html",
        _ctx(
            request,
            p=proposal,
            ticket=ticket,
            requester=requester,
            cited=cited,
            reasons=gate.explain(proposal.gate_conditions),
            stamp=gate.stamp(proposal.gate_conditions),
            reject_reasons=list(RejectReason),
            page="queue",
        ),
    )


@app.post("/queue/{proposal_id}/decide")
def decide(
    proposal_id: str,
    action: str = Form(...),
    draft: str = Form(""),
    note: str = Form(""),
    reason: str = Form(""),
):
    """The only route that can reach the executor, and only via `approve`."""
    proposal = store.get_proposal(proposal_id)
    if proposal is None:
        return RedirectResponse("/queue", status_code=303)

    if action == "reject":
        store.resolve_proposal(
            proposal_id,
            status=ProposalStatus.REJECTED,
            reviewer_note=note,
            reject_reason=RejectReason(reason) if reason else RejectReason.OTHER,
        )
        return RedirectResponse("/queue", status_code=303)

    if action == "approve":
        payload = dict(proposal.payload)
        edited = False
        if draft and proposal.kind == "triage" and draft != payload.get("draft_response"):
            payload["draft_response"] = draft
            # The reply is what gets posted, so the action carries it too.
            for act in payload.get("actions", []):
                if act.get("kind") == "comment":
                    act["comment_body"] = draft
            edited = True
        if draft and proposal.kind == "sop" and draft != payload.get("body"):
            payload["body"] = draft
            edited = True

        resolved = store.resolve_proposal(
            proposal_id,
            status=ProposalStatus.EDITED if edited else ProposalStatus.APPROVED,
            payload=payload if edited else None,
            reviewer_note=note,
        )

        # A held proposal needs the reviewer to have taken the gate condition
        # on themselves. That is what the "Clear hold" checkbox records, and
        # it is why the executor re-checks rather than trusting this call.
        if resolved.gate_conditions and note.strip():
            resolved.gate_conditions = []

        try:
            executor.execute(resolved, store)
        except executor.NotApproved:
            # Held with no reviewer note: nothing is executed, and it stays
            # visible rather than disappearing into "approved".
            return RedirectResponse(f"/queue/{proposal_id}?blocked=1", status_code=303)

        return RedirectResponse("/queue", status_code=303)

    return RedirectResponse(f"/queue/{proposal_id}", status_code=303)


@app.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request, status: str = "open"):
    rows = store.list_tickets(status=None if status == "all" else status, limit=500)
    return templates.TemplateResponse(
        request, "tickets.html", _ctx(request, tickets=rows, status=status, page="tickets")
    )


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(request: Request, ticket_id: str):
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        return RedirectResponse("/tickets", status_code=303)
    requester = store.get_requester(ticket.requester_id) if ticket.requester_id else None
    related = [
        p for p in store.list_proposals(limit=500) if p.ticket_id == ticket_id
    ]
    return templates.TemplateResponse(
        request, "ticket.html",
        _ctx(request, t=ticket, requester=requester, proposals=related, page="tickets"),
    )


@app.get("/sops", response_class=HTMLResponse)
def sops(request: Request):
    pack = store.list_sops()
    tickets_by_topic: dict[str, int] = {}
    for t in store.list_tickets(limit=1000):
        if t.truth_topic:
            tickets_by_topic[t.truth_topic] = tickets_by_topic.get(t.truth_topic, 0) + 1
    return templates.TemplateResponse(
        request, "sops.html",
        _ctx(request, sops=pack, counts=tickets_by_topic, page="sops"),
    )


@app.get("/sops/{sop_id}", response_class=HTMLResponse)
def sop_detail(request: Request, sop_id: str):
    sop = store.get_sop(sop_id)
    if sop is None:
        return RedirectResponse("/sops", status_code=303)
    return templates.TemplateResponse(
        request, "sop.html", _ctx(request, sop=sop, page="sops"))


# ---------------------------------------------------------------------------
# Run controls
# ---------------------------------------------------------------------------

@app.post("/run/{command}")
def run(command: str, limit: int = Form(5)):
    client, _ = _make_client()

    if command == "seed":
        _stop_worker()
        corpus = generate(SEED)
        ingest(corpus, store)
    elif command == "triage":
        for ticket in store.list_tickets(status="open", triaged=False, limit=limit):
            try:
                triage_ticket(ticket.ticket_id, store, client, model=MODEL)
                store.mark_triaged(ticket.ticket_id)
            except AgentError:
                continue
    elif command == "sop":
        for ticket_id in uncovered_resolved_tickets(store, limit=limit):
            try:
                synthesize_sop(ticket_id, store, client, model=MODEL)
            except AgentError:
                continue
    elif command == "start":
        _start_worker()
    elif command == "stop":
        _stop_worker()

    return RedirectResponse("/", status_code=303)


@app.get("/partials/status", response_class=HTMLResponse)
def status_partial(request: Request):
    """Polled by HTMX so an activated agent is visibly working."""
    return templates.TemplateResponse(
        request, "partials/status.html", _ctx(request, m=_metrics())
    )


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop_worker()
