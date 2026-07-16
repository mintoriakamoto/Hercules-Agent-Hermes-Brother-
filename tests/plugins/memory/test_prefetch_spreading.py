"""Automatic spreading activation on prefetch.

The every-turn memory injection (``prefetch``) now blends in facts durably
associated with the top hit — recall of the whole proven cluster, not just the
keyword match — bounded and gated on a meaningful learned edge strength. The
spread-in facts are marked with a ``↳`` so they're distinguishable from direct
hits.

Associations are created directly via ``store.reinforce_association`` here so
the tests target the prefetch logic in isolation (the learn-via-feedback path
is covered in test_associative_recall.py).
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")

from plugins.memory.holographic import (
    _PREFETCH_SPREAD_MAX,
    _PREFETCH_SPREAD_MIN_STRENGTH,
    HolographicMemoryProvider,
)


@pytest.fixture()
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "pf.db")})
    p.initialize("session-prefetch")
    try:
        yield p
    finally:
        p.shutdown()


def _add(provider, content, **kw):
    return provider._store.add_fact(content, **kw)


def _spread_lines(output):
    return [ln for ln in output.splitlines() if "↳" in ln]


def test_strong_association_is_spread_into_prefetch(provider):
    # A matches the query; B is associated with A but shares no query keywords.
    a = _add(provider, "The Titan service uses a blue-green deployment rollout")
    b = _add(provider, "Escalation contact for incidents is the on-call pager")
    provider._store.reinforce_association(a, b, delta=0.6)

    out = provider.prefetch("titan blue-green deployment rollout")
    assert "blue-green deployment" in out          # direct hit present
    spread = _spread_lines(out)
    assert len(spread) == 1
    assert "on-call pager" in spread[0]             # surfaced purely by association


def test_weak_association_below_threshold_is_not_spread(provider):
    a = _add(provider, "The Atlas service caches results in Redis")
    b = _add(provider, "Quarterly planning happens in the second week")
    # Strength below _PREFETCH_SPREAD_MIN_STRENGTH must not surface.
    provider._store.reinforce_association(a, b, delta=_PREFETCH_SPREAD_MIN_STRENGTH - 0.1)

    out = provider.prefetch("atlas redis cache")
    assert _spread_lines(out) == []


def test_spread_is_capped(provider):
    a = _add(provider, "The Orion pipeline compiles the nightly build")
    partners = [
        _add(provider, f"Unrelated durable note number {i} about finance reviews")
        for i in range(_PREFETCH_SPREAD_MAX + 3)
    ]
    for p in partners:
        provider._store.reinforce_association(a, p, delta=0.8)

    out = provider.prefetch("orion pipeline nightly build")
    assert len(_spread_lines(out)) == _PREFETCH_SPREAD_MAX


def test_spread_does_not_duplicate_a_direct_hit(provider):
    # Both facts match the query AND are associated — the partner is already a
    # direct hit, so it must not also appear as a spread line.
    a = _add(provider, "The Nova database runs Postgres 16 in production")
    b = _add(provider, "The Nova database backups run every six hours")
    provider._store.reinforce_association(a, b, delta=0.9)

    out = provider.prefetch("nova database")
    assert _spread_lines(out) == []
    # Both still present as direct hits.
    assert "Postgres 16" in out
    assert "every six hours" in out


def test_no_associations_leaves_prefetch_unchanged(provider):
    _add(provider, "The Vega worker drains the queue on shutdown")
    out = provider.prefetch("vega worker drain queue")
    assert "Vega worker" in out
    assert _spread_lines(out) == []


def test_prefetch_spread_helper_is_defensive(provider):
    # Malformed results must never raise.
    assert provider._prefetch_spread([]) == []
    assert provider._prefetch_spread([{"no_fact_id": 1}]) == []


def test_empty_store_prefetch_still_empty(provider):
    assert provider.prefetch("anything at all") == ""
