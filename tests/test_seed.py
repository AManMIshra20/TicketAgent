"""The corpus must be reproducible, and its defects must be real.

If a seed stops producing the same corpus, every evidence run recorded in
docs/EVIDENCE.md becomes unverifiable -- so determinism is tested first.
"""
from __future__ import annotations

from src.ticket_agent.models import Category, TicketStatus
from src.ticket_agent.seed import PLANTED_DEFECTS, TOPICS, generate


def test_same_seed_produces_identical_corpus():
    a, b = generate(42), generate(42)
    assert [t.model_dump() for t in a.tickets] == [t.model_dump() for t in b.tickets]
    assert [s.model_dump() for s in a.sops] == [s.model_dump() for s in b.sops]
    assert a.raw_rows == b.raw_rows


def test_different_seed_produces_different_corpus():
    a, b = generate(42), generate(7)
    assert [t.model_dump() for t in a.tickets] != [t.model_dump() for t in b.tickets]


def test_every_defect_is_planted_at_the_stated_count(corpus):
    truth = corpus.truth
    assert len(truth["missing_requester"]) == PLANTED_DEFECTS["missing_requester"]
    assert len(truth["duplicate_ticket"]) == PLANTED_DEFECTS["duplicate_ticket"]
    assert len(truth["wrong_category"]) == PLANTED_DEFECTS["wrong_category"]
    assert len(truth["blank_description"]) == PLANTED_DEFECTS["blank_description"]
    assert len(truth["date_formats"]) == PLANTED_DEFECTS["date_formats"]


def test_defects_are_actually_present_in_the_raw_feed(corpus):
    """The truth file is not enough -- the rows themselves must be broken."""
    rows = corpus.raw_rows

    blank = [r for r in rows if not r["requester_id"]]
    assert len(blank) >= PLANTED_DEFECTS["missing_requester"]

    empty_body = [r for r in rows if not r["description"]]
    assert len(empty_body) >= PLANTED_DEFECTS["blank_description"]

    ids = [r["ticket_id"] for r in rows]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert len(dupes) == PLANTED_DEFECTS["duplicate_ticket"]

    # A duplicate must differ in description, or it is not the defect we mean.
    for dupe_id in dupes:
        versions = [r["description"] for r in rows if r["ticket_id"] == dupe_id]
        assert len(set(versions)) > 1, f"{dupe_id} duplicated with identical text"


def test_three_date_formats_are_mixed(corpus):
    """A time filter written against one format silently drops the others."""
    created = [r["created_at"] for r in corpus.raw_rows]
    iso = [c for c in created if c[4:5] == "-"]
    dmy = [c for c in created if "/" in c]
    longform = [c for c in created if "," in c]
    assert iso and dmy and longform, "expected all three formats present"


def test_wrong_category_rows_are_mislabelled_as_question(corpus):
    by_id = {r["ticket_id"]: r for r in corpus.raw_rows}
    for ticket_id in corpus.truth["wrong_category"]:
        assert by_id[ticket_id]["category"] == Category.QUESTION.value
        # They describe something urgent, which is what makes the label wrong.
        assert by_id[ticket_id]["severity"] in ("high", "critical")


def test_corpus_has_both_covered_and_uncovered_topics(corpus):
    """Agent A needs matches to find; Agent B needs gaps to fill."""
    sop_topics = {s.topic for s in corpus.sops}
    ticket_topics = {t.truth_topic for t in corpus.tickets}
    assert sop_topics, "no SOPs -- Agent A has nothing to cite"
    assert ticket_topics - sop_topics, "no gaps -- Agent B has nothing to write"


def test_uncovered_topics_have_resolved_tickets_that_narrate_the_fix(corpus):
    """Agent B can only work if the resolution thread says what was done."""
    sop_topics = {s.topic for s in corpus.sops}
    uncovered = [
        t
        for t in corpus.tickets
        if t.truth_topic not in sop_topics and t.status is TicketStatus.RESOLVED
    ]
    assert uncovered, "no resolved tickets on uncovered topics"
    for ticket in uncovered:
        assert ticket.resolution_notes, f"{ticket.ticket_id} has no resolution"
        closing = ticket.comments[-1].body
        assert "Root cause" in closing and len(closing) > 120, (
            f"{ticket.ticket_id} resolution comment is too thin for Agent B"
        )


def test_every_sop_carries_its_id_in_the_body(corpus):
    """The agent cites the id. A document that does not state its own id
    cannot be cited correctly."""
    for sop in corpus.sops:
        assert f"[{sop.sop_id}]" in sop.body


def test_access_grant_topic_exists(corpus):
    """The gate needs an irreversible-action condition to fire on."""
    assert any(t.is_access_grant for t in TOPICS)
