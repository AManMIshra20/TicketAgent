"""Terminal review queue.

The same decision the web console offers, for when a browser is not to hand.
It calls `executor.execute` on approval and nowhere else -- the boundary is
identical, because it is the same boundary.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from . import executor, gate
from .models import ProposalStatus, RejectReason
from .store import TicketStore

console = Console()


def review_queue(store: TicketStore) -> None:
    queue = [
        p
        for p in store.list_proposals(limit=500)
        if p.status in (ProposalStatus.PENDING, ProposalStatus.HELD)
    ]
    if not queue:
        console.print("Nothing waiting for review.")
        return

    console.print(f"[bold]{len(queue)} proposal(s) waiting.[/bold]\n")
    for proposal in queue:
        _review_one(proposal, store)


def _review_one(proposal, store: TicketStore) -> None:
    ticket = store.get_ticket(proposal.ticket_id)
    payload = proposal.payload

    console.rule(f"{proposal.ticket_id} — {ticket.title if ticket else ''}")

    if proposal.gate_conditions:
        console.print(f"[bold red]{gate.stamp(proposal.gate_conditions)}[/bold red]")
        for reason in gate.explain(proposal.gate_conditions):
            console.print(f"  [red]•[/red] {reason}")
        console.print()

    if proposal.kind == "sop":
        body = payload.get("body", "")
        console.print(Panel(body[:1500], title="Proposed procedure"))
        console.print(f"[dim]{payload.get('diff_summary', '')}[/dim]")
    else:
        cited = ", ".join(payload.get("cited_sop_ids", [])) or "nothing"
        console.print(Panel(payload.get("draft_response", ""), title=f"Draft — cites {cited}"))
        console.print(f"[dim]{payload.get('reasoning', '')}[/dim]")

    for evidence in payload.get("evidence", []):
        console.print(
            f"[dim]matched {evidence.get('sop_id', '?')} at "
            f"{evidence.get('score', 0):.2f}[/dim]"
        )

    choice = Prompt.ask(
        "\n[bold]Decide[/bold]",
        choices=["a", "r", "s"],
        default="s",
        show_choices=False,
    )

    if choice == "s":
        console.print("[dim]Skipped.[/dim]\n")
        return

    if choice == "r":
        reason = Prompt.ask(
            "Reason",
            choices=[r.value for r in RejectReason],
            default=RejectReason.OTHER.value,
        )
        note = Prompt.ask("Note", default="")
        store.resolve_proposal(
            proposal.proposal_id,
            status=ProposalStatus.REJECTED,
            reviewer_note=note,
            reject_reason=RejectReason(reason),
        )
        console.print("[yellow]Rejected. Nothing was sent.[/yellow]\n")
        return

    note = ""
    if proposal.gate_conditions:
        console.print(
            "[red]This one is held. Approving means overriding a deliberate "
            "stop — say why, and it is recorded with your decision.[/red]"
        )
        note = Prompt.ask("Why are you taking this on")
        if not note.strip():
            console.print("[yellow]No reason given. Not sent.[/yellow]\n")
            return

    resolved = store.resolve_proposal(
        proposal.proposal_id, status=ProposalStatus.APPROVED, reviewer_note=note
    )
    if resolved.gate_conditions and note.strip():
        resolved.gate_conditions = []

    try:
        for line in executor.execute(resolved, store):
            console.print(f"[green]✓[/green] {line}")
    except executor.NotApproved as exc:
        console.print(f"[red]Refused: {exc}[/red]")
    console.print()


# Kept so `main.py` and older notes keep working.
def review_and_maybe_execute(proposal, store: TicketStore) -> None:
    _review_one(proposal, store)
