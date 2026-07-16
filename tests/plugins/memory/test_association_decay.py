"""Time-decay + hygiene pruning for durable Hebbian associations.

An edge that stops being reinforced fades on the same exponential curve as fact
recency, so spreading activation reflects what is *currently* useful. Edges
whose effective strength drops below the floor are pruned. Elapsed time is
simulated by backdating an edge's ``updated_at`` directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from plugins.memory.holographic.store import (
    _ASSOCIATION_HALF_LIFE_DAYS,
    _ASSOCIATION_PRUNE_FLOOR,
    _decay_factor,
    MemoryStore,
)


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(tmp_path / "decay.db")
    try:
        yield s
    finally:
        s.close()


def _backdate_edge(store, a, b, days):
    lo, hi = sorted((a, b))
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    store._conn.execute(
        "UPDATE fact_associations SET updated_at = ? WHERE fact_a = ? AND fact_b = ?",
        (ts, lo, hi),
    )
    store._conn.commit()


# --- the decay curve -------------------------------------------------------

def test_decay_factor_curve():
    now = datetime.now(timezone.utc)
    assert _decay_factor(now.strftime("%Y-%m-%d %H:%M:%S")) == pytest.approx(1.0, abs=1e-3)
    half = (now - timedelta(days=_ASSOCIATION_HALF_LIFE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    assert _decay_factor(half) == pytest.approx(0.5, abs=5e-3)
    two = (now - timedelta(days=2 * _ASSOCIATION_HALF_LIFE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    assert _decay_factor(two) == pytest.approx(0.25, abs=5e-3)


def test_decay_factor_defensive():
    assert _decay_factor(None) == 1.0
    assert _decay_factor("not-a-timestamp") == 1.0
    # A future timestamp never boosts an edge above its stored value.
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    assert _decay_factor(future) == 1.0


# --- decay in retrieval + reinforcement ------------------------------------

def test_get_associations_reports_decayed_strength(store):
    a = store.add_fact("Fact A that pairs with B")
    b = store.add_fact("Fact B that pairs with A")
    store.reinforce_association(a, b, delta=0.8)
    _backdate_edge(store, a, b, _ASSOCIATION_HALF_LIFE_DAYS)  # one half-life idle

    assoc = store.get_associations(a)
    assert len(assoc) == 1
    assert assoc[0]["strength"] == pytest.approx(0.4, abs=5e-3)  # 0.8 × 0.5


def test_reinforce_decays_standing_strength_before_adding(store):
    a = store.add_fact("Alpha pairs with Beta")
    b = store.add_fact("Beta pairs with Alpha")
    store.reinforce_association(a, b, delta=0.8)
    _backdate_edge(store, a, b, _ASSOCIATION_HALF_LIFE_DAYS)

    # 0.8 decays to ~0.4, then +0.1 → ~0.5 (not 0.9 — the idle time was charged).
    new = store.reinforce_association(a, b, delta=0.1)
    assert new == pytest.approx(0.5, abs=5e-3)


def test_decayed_edge_below_min_strength_is_filtered(store):
    a = store.add_fact("Gamma pairs weakly with Delta")
    b = store.add_fact("Delta pairs weakly with Gamma")
    store.reinforce_association(a, b, delta=0.5)
    _backdate_edge(store, a, b, 3 * _ASSOCIATION_HALF_LIFE_DAYS)  # 0.5 × 0.125 = 0.0625

    assert store.get_associations(a, min_strength=0.2) == []
    # Still visible with no floor, at its decayed value.
    weak = store.get_associations(a, min_strength=0.0)
    assert weak and weak[0]["strength"] == pytest.approx(0.0625, abs=5e-3)


# --- hygiene pruning -------------------------------------------------------

def test_prune_removes_edges_decayed_below_floor(store):
    a = store.add_fact("Stale hub fact")
    stale = store.add_fact("Stale partner fact")
    fresh = store.add_fact("Fresh partner fact")
    store.reinforce_association(a, stale, delta=0.8)
    _backdate_edge(store, a, stale, 5 * _ASSOCIATION_HALF_LIFE_DAYS)  # 0.8/32 = 0.025 < floor
    store.reinforce_association(a, fresh, delta=0.8)  # fresh, well above floor

    assert _ASSOCIATION_PRUNE_FLOOR == 0.05
    pruned = store.prune_associations()
    assert pruned == 1

    remaining = {f["fact_id"] for f in store.get_associations(a)}
    assert remaining == {fresh}


def test_prune_is_noop_when_all_edges_healthy(store):
    a = store.add_fact("Healthy hub")
    b = store.add_fact("Healthy partner")
    store.reinforce_association(a, b, delta=0.6)
    assert store.prune_associations() == 0
