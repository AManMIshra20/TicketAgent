"""The match score has to actually work.

This is the test that decides whether Agent A is possible at all. If the
scorer cannot find the right SOP for a ticket whose topic is covered, no
amount of prompting fixes it -- the agent would be citing whatever the search
happened to return.

The corpus gives us ground truth for free: every generated ticket records the
topic and SOP it really came from, so retrieval can be graded rather than
eyeballed.
"""
from __future__ import annotations

import pytest

from src.ticket_agent.matching import (
    CONFIDENT_MATCH,
    build_idf,
    excerpt_for,
    score,
    tokenize,
)
from src.ticket_agent.models import TicketStatus


def _query(ticket) -> str:
    return f"{ticket.title} {ticket.body}"


def test_tokenize_drops_stopwords_and_noise():
    tokens = tokenize("Please help! The VPN is not working, urgent issue.")
    assert "vpn" in tokens
    assert "working" in tokens
    for noise in ("please", "the", "is", "not", "urgent", "issue", "help"):
        assert noise not in tokens


def test_score_is_bounded_and_symmetric_on_identical_input():
    t = tokenize("vpn authentication failure after mfa re-enrolment")
    assert score(t, t) == pytest.approx(1.0)
    assert score(t, []) == 0.0
    assert score([], t) == 0.0


def test_unrelated_texts_score_low():
    a = tokenize("printer queue stuck on floor three spooler")
    b = tokenize("sap batch job aborted month end close")
    assert score(a, b) < 0.2


def test_top_sop_is_the_correct_one_for_covered_tickets(store, corpus):
    """Retrieval precision@1 on the tickets that have a right answer."""
    covered = [t for t in corpus.tickets if t.truth_sop_id]
    assert covered

    hits = 0
    misses = []
    for ticket in covered:
        results = store.search_sops(_query(ticket), limit=1)
        if results and results[0].sop_id == ticket.truth_sop_id:
            hits += 1
        else:
            got = results[0].sop_id if results else None
            misses.append((ticket.ticket_id, ticket.truth_sop_id, got))

    precision = hits / len(covered)
    assert precision >= 0.95, (
        f"precision@1 = {precision:.0%} over {len(covered)} tickets. Misses: {misses[:5]}"
    )


def test_covered_tickets_clear_the_confidence_threshold(store, corpus):
    """A correct match that scores below the threshold is still a failure --
    it would be routed to Agent B as 'no SOP covers this'."""
    covered = [t for t in corpus.tickets if t.truth_sop_id]
    sops = store.list_sops()
    docs = [tokenize(f"{s.title} {s.topic} {s.body}") for s in sops]
    idf = build_idf(docs)
    by_id = {s.sop_id: d for s, d in zip(sops, docs)}

    below = []
    for ticket in covered:
        s = score(tokenize(_query(ticket)), by_id[ticket.truth_sop_id], idf)
        if s < CONFIDENT_MATCH:
            below.append((ticket.ticket_id, round(s, 3)))

    # Not 100%, and deliberately so -- see the calibration note on
    # CONFIDENT_MATCH. Some tickets are genuinely too thin to match on, and
    # routing those to a human is the correct outcome, not a bug. What this
    # guards is regression: if recall drops much below the calibrated level,
    # the scorer or the corpus has changed underneath us.
    ratio = 1 - len(below) / len(covered)
    assert ratio >= 0.85, (
        f"recall at threshold fell to {ratio:.0%}. "
        f"{len(below)}/{len(covered)} correct matches scored under "
        f"CONFIDENT_MATCH={CONFIDENT_MATCH}. Lowest: {sorted(below, key=lambda x: x[1])[:5]}"
    )


def test_threshold_sits_in_the_gap_between_the_distributions(store, corpus):
    """The threshold is a calibrated number, not a guess. This asserts the
    calibration still holds: no uncovered ticket may reach it, and it must
    not be so high that most correct matches fall under it."""
    sops = store.list_sops()
    docs = [tokenize(f"{s.title} {s.topic} {s.body}") for s in sops]
    idf = build_idf(docs)
    by_id = {s.sop_id: d for s, d in zip(sops, docs)}
    sop_topics = {s.topic for s in corpus.sops}

    correct = [
        score(tokenize(_query(t)), by_id[t.truth_sop_id], idf)
        for t in corpus.tickets
        if t.truth_sop_id
    ]
    uncovered_top = [
        max(score(tokenize(_query(t)), d, idf) for d in docs)
        for t in corpus.tickets
        if t.truth_topic not in sop_topics
    ]

    assert max(uncovered_top) < CONFIDENT_MATCH, (
        f"an uncovered ticket scores {max(uncovered_top):.3f}, at or above the "
        f"threshold {CONFIDENT_MATCH} -- the gate would let a wrong citation through"
    )
    assert sorted(correct)[len(correct) // 2] > CONFIDENT_MATCH, (
        "median correct match is below the threshold -- the agent would refuse "
        "more often than it answers"
    )


def test_uncovered_tickets_do_not_reach_the_threshold(store, corpus):
    """The gate depends on this. If an uncovered ticket scores confidently
    against some unrelated SOP, the agent will cite it and be wrong -- which
    is precisely the Air Canada failure mode."""
    sop_topics = {s.topic for s in corpus.sops}
    uncovered = [
        t
        for t in corpus.tickets
        if t.truth_topic not in sop_topics and t.status is TicketStatus.OPEN
    ]
    assert uncovered, "corpus has no uncovered open tickets to check"

    false_confident = []
    for ticket in uncovered:
        results = store.search_sops(_query(ticket), limit=1)
        if not results:
            continue
        sops = store.list_sops()
        docs = [tokenize(f"{s.title} {s.topic} {s.body}") for s in sops]
        idf = build_idf(docs)
        top = max(score(tokenize(_query(ticket)), d, idf) for d in docs)
        if top >= CONFIDENT_MATCH:
            false_confident.append((ticket.ticket_id, round(top, 3)))

    assert not false_confident, (
        f"uncovered tickets scored confidently against an SOP: {false_confident}"
    )


def test_search_tickets_finds_the_origin_of_a_cluster(store, corpus):
    """Agent A reuses prior resolutions. That only works if a cluster member
    can find the resolved ticket it duplicates."""
    clustered = [
        t
        for t in corpus.tickets
        if t.status is TicketStatus.OPEN and t.truth_sop_id
    ]
    checked = 0
    for ticket in clustered[:12]:
        results = store.search_tickets(_query(ticket), limit=5)
        same_topic_resolved = [
            r
            for r in results
            if r.truth_topic == ticket.truth_topic
            and r.status is TicketStatus.RESOLVED
        ]
        assert same_topic_resolved, f"{ticket.ticket_id} found no prior resolution"
        checked += 1
    assert checked


def test_excerpt_points_at_the_matching_line():
    body = "# Title\n\nUnrelated preamble line.\nThe print spooler service hung on the server.\n"
    got = excerpt_for(tokenize("spooler hung server"), body)
    assert "spooler" in got.lower()
    assert not got.startswith("#")
