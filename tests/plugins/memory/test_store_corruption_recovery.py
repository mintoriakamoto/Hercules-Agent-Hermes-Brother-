"""Corruption resilience for the holographic memory store.

memory_store.db is a rebuildable cache, not a source of record — so a corrupt
file is quarantined (backed up) and rebuilt empty rather than crashing the
memory subsystem on every session. Recovery covers the broad "this isn't a
database" class, but never transient errors like "database is locked".
"""
from __future__ import annotations

import sqlite3
import threading

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


def test_sibling_opened_after_rebuild_uses_the_live_connection(tmp_path):
    db = tmp_path / "s.db"
    db.write_bytes(b"NOT-A-SQLITE-DATABASE " * 200)

    a = MemoryStore(db)  # triggers the rebuild, swapping the shared connection
    b = MemoryStore(db)  # same path → shared registry (now ready)
    try:
        # Both instances must reference the SAME live, rebuilt connection —
        # never the closed pre-rebuild handle.
        assert a._conn is b._conn is a._entry["conn"]
        fid = a.add_fact("written through A after rebuild")
        assert any(r["fact_id"] == fid for r in b.list_facts(limit=5))
    finally:
        a.close()
        b.close()


def test_concurrent_open_on_corrupt_db_never_yields_a_closed_connection(tmp_path):
    """Two instances constructed simultaneously on a corrupt file: the one that
    doesn't win the rebuild must still end up on the live connection, not the
    closed pre-rebuild handle it may have captured before the swap."""
    db = tmp_path / "c.db"
    db.write_bytes(b"NOT-A-SQLITE-DATABASE " * 200)

    start = threading.Barrier(2)
    stores: list = []
    errors: list = []

    def _open_and_write(tag):
        try:
            start.wait(timeout=5)
            s = MemoryStore(db)
            stores.append(s)
            s.add_fact(f"fact from {tag}")  # fails if _conn is the closed handle
        except Exception as exc:  # noqa: BLE001 - surface any thread failure
            errors.append(exc)

    threads = [threading.Thread(target=_open_and_write, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    try:
        assert not errors, f"a concurrent opener failed: {errors}"
        assert len(stores) == 2
        assert stores[0]._conn is stores[1]._conn  # one shared, live connection
    finally:
        for s in stores:
            s.close()
