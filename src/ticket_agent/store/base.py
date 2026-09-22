"""The store interface, split hard into READ and WRITE.

This split is the same one `github_tools.py` makes, lifted to an interface so
the agent can run against SQLite locally or a real ticket platform without
either side knowing. It is the load-bearing boundary of the project:

    READ methods  -- safe to expose to the LLM as tools. No side effects.
    WRITE methods -- never exposed to the LLM. Called only by executor.py,
                     and only from a proposal a human approved.

If you add a method here, decide which half it belongs to before you write
the body, and put it under the right banner. The banners are not decoration;
`tests/test_no_write_tools.py` reads them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import (
    Proposal,
    ProposalStatus,
    RejectReason,
    Requester,
    SOP,
    Ticket,
)


class TicketStore(ABC):
    """Everything the agent and the app need from persistent storage."""

    # -----------------------------------------------------------------------
    # READ -- safe to expose to the LLM directly (no side effects).
    # -----------------------------------------------------------------------

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> Ticket | None:
        """Fetch one ticket by id, or None if it does not exist."""

    @abstractmethod
    def list_tickets(
        self,
        *,
        status: str | None = None,
        triaged: bool | None = None,
        limit: int = 100,
    ) -> list[Ticket]:
        """List tickets, newest first, optionally filtered."""

    @abstractmethod
    def search_tickets(self, query: str, *, limit: int = 5) -> list[Ticket]:
        """Find past tickets similar to the query text."""

    @abstractmethod
    def get_requester(self, requester_id: str) -> Requester | None:
        """The authoritative tier lookup. Rule 3: do not trust the ticket."""

    @abstractmethod
    def get_sop(self, sop_id: str) -> SOP | None:
        """Fetch one SOP by its cited id, e.g. 'SOP-ACC-04'."""

    @abstractmethod
    def list_sops(self) -> list[SOP]:
        """Every SOP in the pack."""

    @abstractmethod
    def search_sops(self, query: str, *, limit: int = 5) -> list[SOP]:
        """Find SOPs relevant to the query text."""

    @abstractmethod
    def get_proposal(self, proposal_id: str) -> Proposal | None:
        """Fetch one queued proposal."""

    @abstractmethod
    def list_proposals(
        self,
        *,
        status: ProposalStatus | None = None,
        limit: int = 100,
    ) -> list[Proposal]:
        """The review queue, newest first."""

    # -----------------------------------------------------------------------
    # WRITE -- NOT exposed to the LLM. Only executor.py may call these, and
    # only for an action a human approved.
    # -----------------------------------------------------------------------

    @abstractmethod
    def upsert_ticket(self, ticket: Ticket) -> None:
        """Insert or replace a ticket. Used by ingest and by the cleaning pass."""

    @abstractmethod
    def upsert_requester(self, requester: Requester) -> None:
        """Insert or replace a requester record."""

    @abstractmethod
    def upsert_sop(self, sop: SOP) -> None:
        """Publish or update an SOP. Rule 7: update in place, do not duplicate."""

    @abstractmethod
    def add_comment(self, ticket_id: str, author: str, body: str) -> None:
        """Append a comment to a ticket -- the equivalent of replying."""

    @abstractmethod
    def mark_triaged(self, ticket_id: str) -> None:
        """Flag a ticket as processed so it is not picked up again."""

    # Proposal queue writes. Enqueueing is not an action on the world -- the
    # background worker may do it. Resolving a proposal is a human decision
    # and must carry who decided and why.

    @abstractmethod
    def enqueue_proposal(self, proposal: Proposal) -> None:
        """Add a proposal to the review queue. Never executes anything."""

    @abstractmethod
    def resolve_proposal(
        self,
        proposal_id: str,
        *,
        status: ProposalStatus,
        payload: dict | None = None,
        reviewer_note: str = "",
        reject_reason: RejectReason | None = None,
    ) -> Proposal:
        """Record a reviewer's decision.

        When `payload` differs from what the agent proposed, the original is
        preserved on the record so the edit can be diffed later. That diff is
        the only ground truth this system generates about its own mistakes.
        """
