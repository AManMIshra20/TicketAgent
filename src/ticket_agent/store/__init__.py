"""Storage layer.

`TicketStore` is the interface; `SQLiteStore` is the only implementation and
the default. A GitHub Issues adapter can sit behind the same interface later
without the agent noticing -- see `github_tools.py`, which predates this
layer and still talks to GitHub directly.

Tests use `in_memory_store()` rather than a separate fake: an in-memory
SQLite database exercises the real query code, so a bug in the SQL is caught
by the tests instead of hiding behind a hand-written stub.
"""
from __future__ import annotations

from .base import TicketStore
from .sqlite_store import SQLiteStore

__all__ = ["TicketStore", "SQLiteStore", "in_memory_store", "get_store"]


def in_memory_store() -> SQLiteStore:
    """A throwaway store that never touches disk. For tests and demo mode."""
    return SQLiteStore(":memory:")


def get_store(db_path: str | None = None) -> SQLiteStore:
    """The application's store, from config unless overridden."""
    if db_path is None:
        from ..config import get_settings

        db_path = get_settings().db_path
    return SQLiteStore(db_path)
