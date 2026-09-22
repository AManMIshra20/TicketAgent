"""Demo mode: the pipeline without the model.

A submission link that returns 500 because an API key expired is a failed
submission. So the app boots and works with no `ANTHROPIC_API_KEY`, using
this client in place of Claude.

**What is real in demo mode and what is not.** Everything except the
reasoning: the store, the retrieval and its scores, the citation check, the
gate, the queue, the approval flow, the executor and the metrics are the same
code that runs in live mode. What this class replaces is the model's
judgement -- it follows a fixed decision procedure over the retrieval results
rather than reading the ticket.

That makes demo mode an honest demonstration of the *safety architecture* and
a dishonest demonstration of the *agent's intelligence*. The UI says so on
every page, and nothing here should ever be presented as model output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .matching import CONFIDENT_MATCH, build_idf, excerpt_for, score, tokenize
from .models import Severity
from .store import TicketStore


@dataclass
class _Block:
    type: str


@dataclass
class _TextBlock(_Block):
    text: str


@dataclass
class _ToolUseBlock(_Block):
    name: str
    input: dict
    id: str = "toolu_demo"


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Response:
    content: list[Any]
    stop_reason: str
    usage: _Usage = field(default_factory=lambda: _Usage(0, 0))


class OfflineClient:
    """Stands in for the Anthropic client when no key is configured.

    Token counts are reported as zero rather than invented. A fabricated cost
    figure in the metrics dashboard would be worse than a missing one.
    """

    is_demo = True

    def __init__(self, store: TicketStore) -> None:
        self._store = store

    # -- the LLMClient protocol -------------------------------------------

    def create(self, **kwargs) -> _Response:
        messages = kwargs.get("messages", [])
        tools = kwargs.get("tools")

        # No tools means this is the drafting subagent.
        if tools is None:
            return self._draft(messages)

        turn = sum(1 for m in messages if m["role"] == "assistant")
        ticket_id = self._ticket_id_from(messages)

        # Which pipeline this is, decided from the tool list rather than by
        # sniffing the prompt text. The prompts are prose and change; a
        # substring match on them collides (the triage prompt mentions "SOP
        # authoring", which contains "SOP author"). The tool list is
        # structural and cannot drift.
        tool_names = {t["name"] for t in tools}
        if "submit_sop_proposal" in tool_names:
            return self._sop_turn(turn, ticket_id)
        return self._triage_turn(turn, ticket_id)

    # -- triage ------------------------------------------------------------

    def _triage_turn(self, turn: int, ticket_id: str) -> _Response:
        if turn == 0:
            return self._tool("get_ticket", ticket_id=ticket_id)

        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            return self._tool(
                "submit_triage_proposal",
                severity="medium",
                category="other",
                reasoning="Ticket could not be read.",
                can_answer=False,
                cited_sop_ids=[],
                draft_response="This ticket could not be retrieved.",
            )

        if turn == 1:
            return self._tool("search_sops", query=f"{ticket.title} {ticket.body}")

        best, best_score = self._best_sop(ticket)
        confident = best is not None and best_score >= CONFIDENT_MATCH

        if confident:
            reasoning = (
                f"Retrieval matched [{best.sop_id}] at {best_score:.2f}, above the "
                f"{CONFIDENT_MATCH} threshold. Classified from the ticket content."
            )
            draft = (
                f"Thanks for raising this. This matches a known procedure: "
                f"{best.title} [{best.sop_id}].\n\n"
                f"Recommended next step, per [{best.sop_id}]: "
                f"{excerpt_for(tokenize(ticket.title + ' ' + ticket.body), best.body)}\n\n"
                "Please confirm whether that resolves it."
            )
        else:
            top = f"{best_score:.2f}" if best is not None else "0.00"
            reasoning = (
                f"No SOP cleared the {CONFIDENT_MATCH} threshold (best {top}). "
                "Reporting that this cannot be answered from the pack."
            )
            draft = (
                "I cannot answer this from the current SOP pack. No existing "
                "procedure covers it, so it needs a human and should become a "
                "new SOP once resolved."
            )

        return self._tool(
            "submit_triage_proposal",
            severity=self._severity(ticket).value,
            category=ticket.category.value,
            reasoning=reasoning,
            can_answer=confident,
            cited_sop_ids=[best.sop_id] if confident else [],
            draft_response=draft,
            evidence=(
                [
                    {
                        "sop_id": best.sop_id,
                        "score": round(best_score, 3),
                        "method": "token_overlap",
                        "excerpt": excerpt_for(
                            tokenize(f"{ticket.title} {ticket.body}"), best.body
                        ),
                    }
                ]
                if best is not None
                else []
            ),
        )

    # -- SOP authoring ------------------------------------------------------

    def _sop_turn(self, turn: int, ticket_id: str) -> _Response:
        if turn == 0:
            return self._tool("get_ticket", ticket_id=ticket_id)

        ticket = self._store.get_ticket(ticket_id)
        if ticket is None or not ticket.resolution_notes:
            return self._tool(
                "submit_sop_proposal",
                title="Insufficient resolution detail",
                category="other",
                topic="unknown",
                body="# Not enough information\n\nThe resolution thread does not "
                "describe what was done.",
                diff_summary="No procedure drafted: the thread does not say what was done.",
                is_update=False,
            )

        steps = [
            c.body for c in ticket.comments if c.author not in {"", ticket.requester_id}
        ]
        body = (
            f"# {ticket.title}\n\n"
            f"**Category:** {ticket.category.value}\n\n"
            "## Symptom\n\n"
            f"{ticket.body}\n\n"
            "## Root cause\n\n"
            f"{ticket.resolution_notes}\n\n"
            "## Resolution steps\n\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
            + "\n\n## Before you close\n\n"
            "- Confirm the outcome with the requester.\n"
            "- If the requester is Gold tier, hand the close to the named owner.\n"
        )

        return self._tool(
            "submit_sop_proposal",
            title=ticket.title,
            category=ticket.category.value,
            topic=ticket.truth_topic or ticket.ticket_id.lower(),
            body=body,
            diff_summary=(
                f"Drafted from the resolution thread of {ticket.ticket_id}, which "
                f"records {len(steps)} action(s) taken."
            ),
            is_update=False,
        )

    # -- helpers -----------------------------------------------------------

    def _draft(self, messages: list[dict]) -> _Response:
        """The subagent's reply. Still only sees what it was handed."""
        content = messages[0]["content"] if messages else ""
        sop_id = ""
        for line in content.splitlines():
            if line.startswith("SOP CLAUSE ["):
                sop_id = line.split("[", 1)[1].split("]", 1)[0]
                break
        text = (
            "Thanks for raising this.\n\n"
            f"This is covered by our standard procedure [{sop_id}]. "
            "A service desk engineer will apply the documented steps and "
            "confirm with you once it is done.\n\n"
            "If anything above does not match what you are seeing, reply here "
            "and we will take another look."
        )
        return _Response(
            content=[_TextBlock(type="text", text=text)], stop_reason="end_turn"
        )

    def _best_sop(self, ticket):
        sops = self._store.list_sops()
        if not sops:
            return None, 0.0
        docs = [tokenize(f"{s.title} {s.topic} {s.body}") for s in sops]
        idf = build_idf(docs)
        q = tokenize(f"{ticket.title} {ticket.body}")
        ranked = sorted(
            ((score(q, d, idf), s) for d, s in zip(docs, sops)),
            key=lambda p: p[0],
            reverse=True,
        )
        best_score, best = ranked[0]
        return best, best_score

    @staticmethod
    def _severity(ticket) -> Severity:
        """Re-read severity from content, so the planted mislabels are caught."""
        text = f"{ticket.title} {ticket.body}".lower()
        if any(w in text for w in ("production", "month end", "blocked", "line has stopped")):
            return Severity.CRITICAL
        return ticket.severity

    @staticmethod
    def _ticket_id_from(messages: list[dict]) -> str:
        first = messages[0]["content"] if messages else ""
        if isinstance(first, str):
            for token in first.replace(".", " ").split():
                if token.startswith("MERT-"):
                    return token
        return ""

    @staticmethod
    def _tool(name: str, **kwargs) -> _Response:
        return _Response(
            content=[_ToolUseBlock(type="tool_use", name=name, input=kwargs)],
            stop_reason="tool_use",
        )
