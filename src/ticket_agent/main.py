"""Command line entry point.

The web console is the primary interface; this is for setup, for running a
pipeline without a browser, and for the evidence runs that go in
docs/EVIDENCE.md.

    python -m src.ticket_agent.main seed
    python -m src.ticket_agent.main triage --limit 5
    python -m src.ticket_agent.main sop --limit 3
    python -m src.ticket_agent.main review
    python -m src.ticket_agent.main status
"""
from __future__ import annotations

import argparse
import os
import sys

from rich.console import Console
from rich.table import Table

from .agent import AgentError, synthesize_sop, triage_ticket, uncovered_resolved_tickets
from .demo import OfflineClient
from .models import ProposalStatus
from .seed import generate, ingest
from .store import SQLiteStore

console = Console()
DB_PATH = os.getenv("DB_PATH", "data/meridian.db")


def _client(store: SQLiteStore):
    """The live client if a key is set, the offline one otherwise."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        console.print(
            "[yellow]No ANTHROPIC_API_KEY set — running in demo mode. "
            "Retrieval, the gate and the queue are real; the reasoning is not.[/yellow]"
        )
        return OfflineClient(store)
    from .agent import AnthropicClient

    return AnthropicClient(key)


def cmd_seed(args, store: SQLiteStore) -> None:
    corpus = generate(args.seed)
    ingest(corpus, store)
    console.print(
        f"[green]Seeded[/green] {len(corpus.tickets)} tickets and "
        f"{len(corpus.sops)} SOPs from seed {args.seed}."
    )


def cmd_triage(args, store: SQLiteStore) -> None:
    client = _client(store)
    tickets = store.list_tickets(status="open", triaged=False, limit=args.limit)
    if not tickets:
        console.print("No untriaged tickets.")
        return

    for ticket in tickets:
        try:
            proposal = triage_ticket(ticket.ticket_id, store, client, model=args.model)
        except AgentError as exc:
            console.print(f"[red]{ticket.ticket_id}: {exc}[/red]")
            continue
        store.mark_triaged(ticket.ticket_id)

        if proposal.gate_conditions:
            conditions = ", ".join(c.value for c in proposal.gate_conditions)
            console.print(f"[red]HELD[/red]  {ticket.ticket_id}  {conditions}")
        else:
            cited = ", ".join(proposal.payload.get("cited_sop_ids", [])) or "nothing"
            console.print(f"[green]READY[/green] {ticket.ticket_id}  cites {cited}")


def cmd_sop(args, store: SQLiteStore) -> None:
    client = _client(store)
    ids = uncovered_resolved_tickets(store, limit=args.limit)
    if not ids:
        console.print("Every resolved ticket is already covered by an SOP.")
        return

    for ticket_id in ids:
        try:
            proposal = synthesize_sop(ticket_id, store, client, model=args.model)
        except AgentError as exc:
            console.print(f"[red]{ticket_id}: {exc}[/red]")
            continue
        sop = proposal.sop()
        verb = "update" if sop.is_update else "new"
        console.print(f"[yellow]DRAFTED[/yellow] {ticket_id}  {verb}: {sop.title}")


def cmd_review(args, store: SQLiteStore) -> None:
    from .review_cli import review_queue

    review_queue(store)


def cmd_status(args, store: SQLiteStore) -> None:
    proposals = store.list_proposals(limit=1000)
    tickets = store.list_tickets(limit=1000)

    table = Table(show_header=False, box=None)
    table.add_row("Tickets", str(len(tickets)))
    table.add_row("  untriaged", str(sum(1 for t in tickets if not t.triaged and t.status.value == "open")))
    table.add_row("SOPs", str(len(store.list_sops())))
    table.add_row("Proposals", str(len(proposals)))
    for status in ProposalStatus:
        count = sum(1 for p in proposals if p.status is status)
        if count:
            table.add_row(f"  {status.value}", str(count))

    tokens = sum(p.input_tokens + p.output_tokens for p in proposals)
    table.add_row("Tokens used", f"{tokens:,}")
    if proposals:
        table.add_row("Tokens per ticket", f"{tokens // len(proposals):,}")
    console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(prog="ticket-agent", description=__doc__)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", "claude-sonnet-5"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="Generate and load the synthetic corpus.")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("triage", help="Run Agent A over untriaged tickets.")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("sop", help="Run Agent B over uncovered resolved tickets.")
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=cmd_sop)

    p = sub.add_parser("review", help="Work the approval queue in the terminal.")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("status", help="Counts and token usage.")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    store = SQLiteStore(args.db)
    try:
        args.func(args, store)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
