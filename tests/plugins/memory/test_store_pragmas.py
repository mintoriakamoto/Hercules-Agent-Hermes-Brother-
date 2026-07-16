"""Performance-pragma tuning for the holographic memory store.

The store is on the hot path (a search every prefetch, writes on every fact
op), so the shared connection is tuned once at open: NORMAL sync under WAL,
in-memory temp store, a larger page cache, and memory-mapped reads. Every
pragma is best-effort — a build or filesystem that rejects one must not stop
the store from opening.
"""
from __future__ import annotations

import sqlite3

import pytest

from plugins.memory.holographic.store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(tmp_path / "prag.db")
    try:
        yield s
    finally:
        s.close()


def _pragma(store, name):
    return store._conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_wal_store_applies_performance_pragmas(store):
    # tmp_path is a normal local filesystem → WAL succeeds → NORMAL sync.
    assert str(_pragma(store, "journal_mode")).lower() == "wal"
    assert _pragma(store, "synchronous") == 1        # NORMAL
    assert _pragma(store, "temp_store") == 2         # MEMORY
    assert _pragma(store, "cache_size") == -8000     # ~8 MiB
    assert _pragma(store, "mmap_size") == 134217728  # 128 MiB


def test_store_is_functional_after_tuning(store):
    fid = store.add_fact("a fact stored under the tuned connection")
    rows = store.list_facts(limit=5)
    assert any(r["fact_id"] == fid for r in rows)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Records executed SQL; reports a chosen journal_mode; can fail-hard."""

    def __init__(self, mode="delete", fail=False):
        self.mode = mode
        self.fail = fail
        self.calls: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.calls.append(sql)
        if self.fail:
            raise sqlite3.OperationalError("pragma unsupported")
        if sql == "PRAGMA journal_mode":
            return _FakeCursor((self.mode,))
        return _FakeCursor(None)


def test_non_wal_store_does_not_force_normal_synchronous(store, monkeypatch):
    # DELETE fallback (fragile filesystem): NORMAL must NOT be issued so
    # durability stays at the FULL default, while the journal-mode-independent
    # pragmas still take effect.
    fake = _FakeConn(mode="delete")
    monkeypatch.setattr(store, "_conn", fake)
    store._apply_performance_pragmas()
    assert "PRAGMA synchronous=NORMAL" not in fake.calls
    assert "PRAGMA temp_store=MEMORY" in fake.calls
    assert "PRAGMA cache_size=-8000" in fake.calls
    assert any(c.startswith("PRAGMA mmap_size=") for c in fake.calls)


def test_wal_branch_issues_normal_synchronous(store, monkeypatch):
    fake = _FakeConn(mode="wal")
    monkeypatch.setattr(store, "_conn", fake)
    store._apply_performance_pragmas()
    assert "PRAGMA synchronous=NORMAL" in fake.calls


def test_pragmas_are_best_effort(store, monkeypatch):
    fake = _FakeConn(fail=True)
    monkeypatch.setattr(store, "_conn", fake)
    # Every statement raises — the method must swallow and return cleanly.
    store._apply_performance_pragmas()
