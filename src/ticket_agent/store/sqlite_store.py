"""SQLite implementation of TicketStore.

The default store. Chosen so the deployed demo needs no external service, no
API token and no rate limit -- a submission link that breaks because a
third-party credential expired is a failed submission.

Rows are stored close to their Pydantic shape: scalar columns for anything
queried or filtered, JSON for the nested parts (comments, payloads,
evidence). That keeps the schema readable without an ORM.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..matching import build_idf, score, tokenize
from ..models import (
    Comment,
    GateCondition,
    Proposal,
    ProposalStatus,
    RejectReason,
    Requester,
    SOP,
    Ticket,
)
from .base import TicketStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS requesters (
    requester_id  TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    business_unit TEXT NOT NULL,
    tier          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id        TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    body             TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL,
    severity         TEXT NOT NULL,
    requester_id     TEXT,
    status           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    resolved_at      TEXT,
    comments_json    TEXT NOT NULL DEFAULT '[]',
    resolution_notes TEXT NOT NULL DEFAULT '',
    triaged          INTEGER NOT NULL DEFAULT 0,
    cleaned          INTEGER NOT NULL DEFAULT 0,
    data_issues_json TEXT NOT NULL DEFAULT '[]',
    truth_topic      TEXT,
    truth_sop_id     TEXT
);

CREATE TABLE IF NOT EXISTS sops (
    sop_id     TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    category   TEXT NOT NULL,
    topic      TEXT NOT NULL,
    body       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id       TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    ticket_id         TEXT NOT NULL,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    original_json     TEXT,
    gate_json         TEXT NOT NULL DEFAULT '[]',
    reviewed_at       TEXT,
    reviewer_note     TEXT NOT NULL DEFAULT '',
    reject_reason     TEXT,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    turns             INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tickets_status  ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_triaged ON tickets(triaged);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteStore(TicketStore):
    def __init__(self, db_path: str | Path = "ticket_agent.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -----------------------------------------------------------------------
    # Row <-> model
    # -----------------------------------------------------------------------

    @staticmethod
    def _to_ticket(row: sqlite3.Row) -> Ticket:
        return Ticket(
            ticket_id=row["ticket_id"],
            title=row["title"],
            body=row["body"],
            category=row["category"],
            severity=row["severity"],
            requester_id=row["requester_id"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=_dt(row["resolved_at"]),
            comments=[Comment.model_validate(c) for c in json.loads(row["comments_json"])],
            resolution_notes=row["resolution_notes"],
            triaged=bool(row["triaged"]),
            cleaned=bool(row["cleaned"]),
            data_issues=json.loads(row["data_issues_json"]),
            truth_topic=row["truth_topic"],
            truth_sop_id=row["truth_sop_id"],
        )

    @staticmethod
    def _to_sop(row: sqlite3.Row) -> SOP:
        return SOP(
            sop_id=row["sop_id"],
            title=row["title"],
            category=row["category"],
            topic=row["topic"],
            body=row["body"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            revision=row["revision"],
        )

    @staticmethod
    def _to_proposal(row: sqlite3.Row) -> Proposal:
        return Proposal(
            proposal_id=row["proposal_id"],
            kind=row["kind"],
            ticket_id=row["ticket_id"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            payload=json.loads(row["payload_json"]),
            original_payload=json.loads(row["original_json"]) if row["original_json"] else None,
            gate_conditions=[GateCondition(g) for g in json.loads(row["gate_json"])],
            reviewed_at=_dt(row["reviewed_at"]),
            reviewer_note=row["reviewer_note"],
            reject_reason=RejectReason(row["reject_reason"]) if row["reject_reason"] else None,
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            turns=row["turns"],
            latency_ms=row["latency_ms"],
        )

    # -----------------------------------------------------------------------
    # READ -- safe to expose to the LLM directly (no side effects).
    # -----------------------------------------------------------------------

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return self._to_ticket(row) if row else None

    def list_tickets(
        self,
        *,
        status: str | None = None,
        triaged: bool | None = None,
        limit: int = 100,
    ) -> list[Ticket]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if triaged is not None:
            clauses.append("triaged = ?")
            params.append(int(triaged))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM tickets {where} ORDER BY created_at DESC LIMIT ?", params
        ).fetchall()
        return [self._to_ticket(r) for r in rows]

    def search_tickets(self, query: str, *, limit: int = 5) -> list[Ticket]:
        tickets = [
            self._to_ticket(r)
            for r in self._conn.execute("SELECT * FROM tickets").fetchall()
        ]
        return self._rank(
            query,
            tickets,
            lambda t: f"{t.title} {t.body} {t.resolution_notes}",
            limit,
        )

    def get_requester(self, requester_id: str) -> Requester | None:
        row = self._conn.execute(
            "SELECT * FROM requesters WHERE requester_id = ?", (requester_id,)
        ).fetchone()
        return Requester.model_validate(dict(row)) if row else None

    def get_sop(self, sop_id: str) -> SOP | None:
        row = self._conn.execute("SELECT * FROM sops WHERE sop_id = ?", (sop_id,)).fetchone()
        return self._to_sop(row) if row else None

    def list_sops(self) -> list[SOP]:
        rows = self._conn.execute("SELECT * FROM sops ORDER BY sop_id").fetchall()
        return [self._to_sop(r) for r in rows]

    def search_sops(self, query: str, *, limit: int = 5) -> list[SOP]:
        return self._rank(
            query, self.list_sops(), lambda s: f"{s.title} {s.topic} {s.body}", limit
        )

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return self._to_proposal(row) if row else None

    def list_proposals(
        self, *, status: ProposalStatus | None = None, limit: int = 100
    ) -> list[Proposal]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_proposal(r) for r in rows]

    @staticmethod
    def _rank(query, items, text_of, limit):
        """Score `items` against `query` and return the best `limit` of them."""
        if not items:
            return []
        docs = [tokenize(text_of(i)) for i in items]
        idf = build_idf(docs)
        q = tokenize(query)
        scored = [(score(q, d, idf), i) for d, i in zip(docs, items)]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for s, item in scored[:limit] if s > 0]

    # -----------------------------------------------------------------------
    # WRITE -- NOT exposed to the LLM. executor.py only, post-approval only.
    # -----------------------------------------------------------------------

    def upsert_ticket(self, ticket: Ticket) -> None:
        self._conn.execute(
            """
            INSERT INTO tickets (
                ticket_id, title, body, category, severity, requester_id, status,
                created_at, resolved_at, comments_json, resolution_notes,
                triaged, cleaned, data_issues_json, truth_topic, truth_sop_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticket_id) DO UPDATE SET
                title=excluded.title, body=excluded.body, category=excluded.category,
                severity=excluded.severity, requester_id=excluded.requester_id,
                status=excluded.status, created_at=excluded.created_at,
                resolved_at=excluded.resolved_at, comments_json=excluded.comments_json,
                resolution_notes=excluded.resolution_notes, triaged=excluded.triaged,
                cleaned=excluded.cleaned, data_issues_json=excluded.data_issues_json,
                truth_topic=excluded.truth_topic, truth_sop_id=excluded.truth_sop_id
            """,
            (
                ticket.ticket_id,
                ticket.title,
                ticket.body,
                ticket.category.value,
                ticket.severity.value,
                ticket.requester_id,
                ticket.status.value,
                ticket.created_at.isoformat(),
                ticket.resolved_at.isoformat() if ticket.resolved_at else None,
                json.dumps([json.loads(c.model_dump_json()) for c in ticket.comments]),
                ticket.resolution_notes,
                int(ticket.triaged),
                int(ticket.cleaned),
                json.dumps(ticket.data_issues),
                ticket.truth_topic,
                ticket.truth_sop_id,
            ),
        )
        self._conn.commit()

    def upsert_requester(self, requester: Requester) -> None:
        self._conn.execute(
            """
            INSERT INTO requesters (requester_id, name, business_unit, tier)
            VALUES (?,?,?,?)
            ON CONFLICT(requester_id) DO UPDATE SET
                name=excluded.name, business_unit=excluded.business_unit,
                tier=excluded.tier
            """,
            (
                requester.requester_id,
                requester.name,
                requester.business_unit,
                requester.tier.value,
            ),
        )
        self._conn.commit()

    def upsert_sop(self, sop: SOP) -> None:
        self._conn.execute(
            """
            INSERT INTO sops (sop_id, title, category, topic, body, updated_at, revision)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(sop_id) DO UPDATE SET
                title=excluded.title, category=excluded.category, topic=excluded.topic,
                body=excluded.body, updated_at=excluded.updated_at,
                revision=excluded.revision
            """,
            (
                sop.sop_id,
                sop.title,
                sop.category.value,
                sop.topic,
                sop.body,
                sop.updated_at.isoformat(),
                sop.revision,
            ),
        )
        self._conn.commit()

    def add_comment(self, ticket_id: str, author: str, body: str) -> None:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(f"No such ticket: {ticket_id}")
        ticket.comments.append(
            Comment(author=author, body=body, created_at=datetime.now())
        )
        self.upsert_ticket(ticket)

    def mark_triaged(self, ticket_id: str) -> None:
        self._conn.execute(
            "UPDATE tickets SET triaged = 1 WHERE ticket_id = ?", (ticket_id,)
        )
        self._conn.commit()

    def enqueue_proposal(self, proposal: Proposal) -> None:
        self._conn.execute(
            """
            INSERT INTO proposals (
                proposal_id, kind, ticket_id, status, created_at, payload_json,
                original_json, gate_json, reviewed_at, reviewer_note, reject_reason,
                input_tokens, output_tokens, turns, latency_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                status=excluded.status, payload_json=excluded.payload_json,
                gate_json=excluded.gate_json
            """,
            (
                proposal.proposal_id,
                proposal.kind,
                proposal.ticket_id,
                proposal.status.value,
                proposal.created_at.isoformat(),
                json.dumps(proposal.payload),
                json.dumps(proposal.original_payload) if proposal.original_payload else None,
                json.dumps([g.value for g in proposal.gate_conditions]),
                proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
                proposal.reviewer_note,
                proposal.reject_reason.value if proposal.reject_reason else None,
                proposal.input_tokens,
                proposal.output_tokens,
                proposal.turns,
                proposal.latency_ms,
            ),
        )
        self._conn.commit()

    def resolve_proposal(
        self,
        proposal_id: str,
        *,
        status: ProposalStatus,
        payload: dict | None = None,
        reviewer_note: str = "",
        reject_reason: RejectReason | None = None,
    ) -> Proposal:
        existing = self.get_proposal(proposal_id)
        if existing is None:
            raise KeyError(f"No such proposal: {proposal_id}")

        new_payload = existing.payload
        original = existing.original_payload
        if payload is not None and payload != existing.payload:
            # The reviewer changed something. Keep what the agent said, so the
            # edit can be diffed -- this is the signal we are here to collect.
            original = existing.original_payload or existing.payload
            new_payload = payload

        self._conn.execute(
            """
            UPDATE proposals SET status=?, payload_json=?, original_json=?,
                reviewed_at=?, reviewer_note=?, reject_reason=?
            WHERE proposal_id=?
            """,
            (
                status.value,
                json.dumps(new_payload),
                json.dumps(original) if original else None,
                datetime.now().isoformat(),
                reviewer_note,
                reject_reason.value if reject_reason else None,
                proposal_id,
            ),
        )
        self._conn.commit()
        resolved = self.get_proposal(proposal_id)
        assert resolved is not None
        return resolved
