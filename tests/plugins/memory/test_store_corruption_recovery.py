"""Corruption resilience for the holographic memory store.

memory_store.db is a rebuildable cache, not a source of record — so a corrupt
file is quarantined (backed up) and rebuilt empty rather than crashing the
memory subsystem on every session. Recovery covers the broad "this isn't a
database" class, but never transient errors like "database is locked".
"""
from __future__ import annotations

import sqlite3

import pytest

from plugins.memory.holographic.store import MemoryStore, _is_corrupt_db_error


def test_is_corrupt_db_error_matches_corruption_only():
    assert _is_corrupt_db_error(sqlite3.DatabaseError("file is not a database"))
    assert _is_corrupt_db_error(sqlite3.DatabaseError("database disk image is malformed"))
    assert _is_corrupt_db_error(sqlite3.DatabaseError("malformed database schema"))
    # Transient/operational errors are NOT corruption — must not trigger rebuild.
    assert not _is_corrupt_db_error(sqlite3.OperationalError("database is locked"))
    assert not _is_corrupt_db_error(sqlite3.OperationalError("disk I/O error"))
    assert not _is_corrupt_db_error(ValueError("not even a sqlite error"))


def test_corrupt_db_is_quarantined_and_rebuilt(tmp_path):
    db = tmp_path / "m.db"
    # Garbage bytes → SQLite reports "file is not a database" on first read.
    db.write_bytes(b"NOT-A-SQLITE-DATABASE " * 200)

    store = MemoryStore(db)
    try:
        # The rebuilt store is fully functional.
        fid = store.add_fact("a fact stored after corruption recovery")
        assert isinstance(fid, int)
        rows = store.list_facts(limit=5)
        assert any(r["fact_id"] == fid for r in rows)

        # The corrupt bytes were preserved for forensics, not silently dropped.
        backups = list(tmp_path.glob("m.db.malformed-backup-*"))
        assert backups, "expected a quarantine backup of the corrupt file"

        # The shared registry was rebound to the fresh connection.
        assert store._entry["conn"] is store._conn
    finally:
        store.close()


def test_healthy_db_is_not_quarantined(tmp_path):
    db = tmp_path / "h.db"
    store = MemoryStore(db)
    try:
        store.add_fact("healthy fact")
        # No corruption → no backup file should ever be produced.
        assert list(tmp_path.glob("h.db.malformed-backup-*")) == []
    finally:
        store.close()
