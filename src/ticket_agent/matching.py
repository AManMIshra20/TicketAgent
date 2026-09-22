"""Similarity scoring.

CLAUDE.md requires a real match score, not the model's self-reported
confidence. This is that score: computed in code, deterministic, and shown to
the reviewer as evidence beside every proposal.

It is deliberately simple -- weighted token overlap with inverse document
frequency, no embeddings, no vector store. That is a considered choice for
this corpus, not a shortcut:

  * The SOP pack is tens of documents, not tens of thousands. Retrieval
    quality is not the bottleneck; the gate and the citation rule are.
  * It runs with no API call, so the deployed demo works without a key and
    the tests run offline.
  * A reviewer can see *why* something matched. An embedding distance is not
    explainable to the person approving the draft.

If the corpus grows past a few hundred documents this should be replaced with
embeddings plus a reranking pass -- similarity is not relevance -- but that
is a different project with different costs (retrieval infra is its own
budget line).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

# Words that carry no signal in a service-desk corpus. Every ticket says
# "issue" and "please"; letting them match inflates every score equally.
_STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those is are was were be
    been being am do does did doing have has had having i me my we our you
    your he she it they them their as at by for from in into of on to with
    without about over under again further once here there when where why how
    all any both each few more most other some such no nor not only own same
    so too very can will just should now please thanks thank hi hello dear
    regards issue issues problem problems help need needs able unable get got
    getting able team ticket raised raising kindly asap urgent
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords and single characters removed."""
    if not text:
        return []
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    ]


def build_idf(documents: Sequence[Iterable[str]]) -> dict[str, float]:
    """Inverse document frequency over a corpus of tokenized documents.

    A term appearing in every SOP tells you nothing about which SOP to pick.
    Smoothed so a term in every document scores near zero rather than
    exactly zero, which keeps a match from collapsing to 0.0 on a corpus
    where the vocabulary happens to be uniform.
    """
    n = len(documents)
    if n == 0:
        return {}
    seen: Counter[str] = Counter()
    for doc in documents:
        seen.update(set(doc))
    return {term: math.log(1.0 + n / (1 + count)) for term, count in seen.items()}


def score(
    query_tokens: Sequence[str],
    doc_tokens: Sequence[str],
    idf: dict[str, float] | None = None,
) -> float:
    """Similarity of a query to one document, in [0, 1].

    Weighted Jaccard: the IDF mass the two share, over the IDF mass of the
    query. Asymmetric on purpose -- a long SOP that fully covers a short
    ticket should score high, and dividing by the union would punish it for
    length.
    """
    q = set(query_tokens)
    d = set(doc_tokens)
    if not q or not d:
        return 0.0

    weights = idf or {}

    def mass(terms: set[str]) -> float:
        return sum(weights.get(t, 1.0) for t in terms)

    shared = mass(q & d)
    total = mass(q)
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, shared / total))


def excerpt_for(query_tokens: Sequence[str], text: str, *, width: int = 240) -> str:
    """The passage of `text` that best explains the match.

    Picks the line with the most query terms in it, so the reviewer sees the
    sentence the score came from rather than the document's first paragraph.
    """
    q = set(query_tokens)
    if not text.strip():
        return ""

    best_line, best_hits = "", -1
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        hits = len(q & set(tokenize(stripped)))
        if hits > best_hits:
            best_line, best_hits = stripped, hits

    if best_hits <= 0:
        best_line = next(
            (ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")),
            "",
        )

    return best_line[:width] + ("…" if len(best_line) > width else "")


# Above this, Agent A may draft a fix from the matched SOP. Below it, the
# ticket is treated as uncovered and handed to Agent B -- which fires the
# NO_SOP_COVERAGE gate condition.
#
# Calibrated against the seed-42 corpus, where ground truth is known:
#
#   correct matches    n=38  min 0.191  median 0.535  max 1.000
#   uncovered tickets  n=15  max 0.337
#
# The distributions overlap, so no threshold is free. 0.37 sits in the gap
# between the uncovered maximum (0.337) and the next correct score (0.399).
#
# The choice is deliberately asymmetric. At this value no uncovered ticket is
# ever confidently matched, at the cost of ~13% of covered tickets being
# routed to a human as "no SOP covers this". That is the safe direction: a
# false refusal costs a reviewer thirty seconds, while a false confident
# citation is the Air Canada failure -- an agent stating a policy that does
# not apply, in writing, to a requester who will act on it.
#
# Re-derive this with tests/test_matching.py if the corpus or the SOP format
# changes. A threshold carried over from a different corpus is not calibrated.
CONFIDENT_MATCH = 0.37
