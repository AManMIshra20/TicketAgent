"""Shared fixtures.

No test in this suite makes a network call or reads a real API key. The
store is an in-memory SQLite database -- the real query code, not a
hand-written stub, so a bug in the SQL surfaces here instead of in the app.
"""
from __future__ import annotations

import pytest

from src.ticket_agent.seed import Corpus, generate, ingest
from src.ticket_agent.store import SQLiteStore, in_memory_store


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    """The seed-42 corpus. Session-scoped: generation is deterministic."""
    return generate(42)


@pytest.fixture
def store(corpus: Corpus) -> SQLiteStore:
    db = in_memory_store()
    ingest(corpus, db)
    yield db
    db.close()
