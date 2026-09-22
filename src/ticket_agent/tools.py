"""The READ tools. This is the entire surface the model is given.

Every function here is a lookup. None of them changes anything. That is not a
convention -- it is the boundary the project rests on, and
`tests/test_safety_boundary.py` enforces it.

Writes live in `executor.py` and are reachable only from a proposal a human
approved. If you are about to add a function to this file that changes state,
you are in the wrong file.

Results are returned as plain dicts because they go straight back to the
model as tool results. Each one carries only what the agent needs to reason
and cite -- in particular, the `truth_*` fields on a ticket are stripped.
Those are what we grade the agent against; handing them over would be
marking its own exam.
"""
from __future__ import annotations

from .matching import build_idf, excerpt_for, score, tokenize
from .models import SOP, Ticket
from .store import TicketStore


def _ticket_out(ticket: Ticket, *, include_thread: bool = False) -> dict:
    """A ticket as the model sees it. Ground truth removed."""
    out = {
        "ticket_id": ticket.ticket_id,
        "title": ticket.title,
        "body": ticket.body,
        "category": ticket.category.value,
        "severity": ticket.severity.value,
        "requester_id": ticket.requester_id,
        "status": ticket.status.value,
        "created_at": ticket.created_at.isoformat(),
        "comment_count": len(ticket.comments),
    }
    if ticket.status.value == "resolved":
        out["resolution_notes"] = ticket.resolution_notes
    if include_thread:
        out["comments"] = [
            {
                "author": c.author,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
            }
            for c in ticket.comments
        ]
    return out


def _sop_out(sop: SOP, *, full: bool = True) -> dict:
    out = {
        "sop_id": sop.sop_id,
        "title": sop.title,
        "category": sop.category.value,
        "topic": sop.topic,
        "updated_at": sop.updated_at.isoformat(),
        "revision": sop.revision,
    }
    if full:
        out["body"] = sop.body
    return out


class ReadTools:
    """Read-only lookups over a store, ready to hand to the model.

    Bound to a store instance rather than a module-level global so tests and
    the web app can each run against their own database without the agent
    reaching for whatever happens to be configured.
    """

    def __init__(self, store: TicketStore) -> None:
        self._store = store

    # -- tools ------------------------------------------------------------

    def get_ticket(self, ticket_id: str) -> dict:
        """Full details of one ticket, including its comment thread."""
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            return {"error": f"No ticket with id {ticket_id}."}
        return _ticket_out(ticket, include_thread=True)

    def get_requester(self, requester_id: str) -> dict:
        """The authoritative tier lookup.

        Rule 3: the tier stated in a ticket body is not evidence. This is.
        """
        if not requester_id:
            return {
                "error": "No requester id on this ticket.",
                "guidance": (
                    "The entitlement cannot be verified. Treat this as a "
                    "coverage failure and route it to a human rather than "
                    "assuming a tier."
                ),
            }
        requester = self._store.get_requester(requester_id)
        if requester is None:
            return {
                "error": f"No requester record for {requester_id}.",
                "guidance": "Do not infer the tier from the ticket text.",
            }
        return {
            "requester_id": requester.requester_id,
            "name": requester.name,
            "business_unit": requester.business_unit,
            "tier": requester.tier.value,
        }

    def search_sops(self, query: str, limit: int = 3) -> dict:
        """Find SOPs covering this problem, with a code-computed match score.

        The score is the retrieval system's, not the model's opinion of
        itself. It is returned so the agent can tell a strong match from a
        weak one -- and so the reviewer can see the same number the agent saw.
        """
        sops = self._store.list_sops()
        if not sops:
            return {"results": [], "note": "The SOP pack is empty."}

        docs = [tokenize(f"{s.title} {s.topic} {s.body}") for s in sops]
        idf = build_idf(docs)
        q = tokenize(query)

        ranked = sorted(
            (
                (score(q, doc, idf), sop)
                for doc, sop in zip(docs, sops)
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )[:limit]

        from .matching import CONFIDENT_MATCH

        results = []
        for match_score, sop in ranked:
            if match_score <= 0:
                continue
            results.append(
                {
                    **_sop_out(sop),
                    "match_score": round(match_score, 3),
                    "confident": match_score >= CONFIDENT_MATCH,
                    "excerpt": excerpt_for(q, sop.body),
                }
            )

        payload: dict = {"results": results, "threshold": CONFIDENT_MATCH}
        if not any(r["confident"] for r in results):
            payload["note"] = (
                "No SOP reached the confidence threshold. The correct action is "
                "to report that you cannot answer this from the pack. Do not "
                "cite a low-scoring SOP to have something to cite."
            )
        return payload

    def get_sop(self, sop_id: str) -> dict:
        """Read one SOP in full, by the id you intend to cite."""
        sop = self._store.get_sop(sop_id)
        if sop is None:
            return {
                "error": f"No SOP with id {sop_id}.",
                "guidance": (
                    "This id does not exist. Do not cite it. If you reached "
                    "for it from memory rather than from search_sops, that is "
                    "the mistake this check exists to catch."
                ),
            }
        return _sop_out(sop)

    def search_tickets(self, query: str, limit: int = 3) -> dict:
        """Find past tickets on the same problem, to reuse prior resolutions."""
        results = self._store.search_tickets(query, limit=limit)
        return {
            "results": [
                _ticket_out(t, include_thread=t.status.value == "resolved")
                for t in results
            ]
        }

    def list_untriaged_tickets(self, limit: int = 20) -> dict:
        """Open tickets the agent has not processed yet."""
        tickets = self._store.list_tickets(status="open", triaged=False, limit=limit)
        return {"results": [_ticket_out(t) for t in tickets]}

    # -- registry ---------------------------------------------------------

    def as_map(self) -> dict:
        """Name -> callable, for the agent's dispatch loop.

        The submit_* tools are deliberately absent: they are intercepted in
        `agent.py` and turned into proposal objects. They are never dispatched
        to anything, because there is nothing for them to call.
        """
        return {
            "get_ticket": self.get_ticket,
            "get_requester": self.get_requester,
            "search_sops": self.search_sops,
            "get_sop": self.get_sop,
            "search_tickets": self.search_tickets,
            "list_untriaged_tickets": self.list_untriaged_tickets,
        }
