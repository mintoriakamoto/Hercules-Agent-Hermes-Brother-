"""Durable Hebbian associations + spreading-activation recall.

The co-activation feature learns which facts prove useful together. Those
associations are now persisted as durable edges (``fact_associations``) so the
learned cluster survives the session, and a new ``spread`` recall action
surfaces the cluster by association strength — "fire together" completing the
"wire together".

Store-level tests cover the edge table directly; provider-level tests cover the
end-to-end learn-then-recall loop through ``fact_feedback`` and ``spread``.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore


# ---------------------------------------------------------------------------
# Store-level: fact_associations
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(tmp_path / "assoc.db")
    try:
        yield s
    finally:
        s.close()


def test_reinforce_creates_and_accumulates_edge(store):
    a = store.add_fact("Fact A about the caching layer")
    b = store.add_fact("Fact B about the caching layer")
    s1 = store.reinforce_association(a, b, delta=0.15)
    assert s1 == pytest.approx(0.15)
    s2 = store.reinforce_association(a, b, delta=0.15)
    assert s2 == pytest.approx(0.30)


def test_edge_is_undirected_and_canonical(store):
    a = store.add_fact("Alpha fact")
    b = store.add_fact("Beta fact")
    # Reinforce in the reverse order — must land on the same edge.
    store.reinforce_association(b, a, delta=0.2)
    store.reinforce_association(a, b, delta=0.2)
    from_a = store.get_associations(a)
    from_b = store.get_associations(b)
    assert [f["fact_id"] for f in from_a] == [b]
    assert [f["fact_id"] for f in from_b] == [a]
    assert from_a[0]["strength"] == pytest.approx(0.4)


def test_strength_is_capped_at_one(store):
    a = store.add_fact("Capped fact one")
    b = store.add_fact("Capped fact two")
    for _ in range(20):
        store.reinforce_association(a, b, delta=0.2)
    assert store.get_associations(a)[0]["strength"] == pytest.approx(1.0)


def test_self_edge_is_ignored(store):
    a = store.add_fact("Lonely fact")
    assert store.reinforce_association(a, a, delta=0.5) == 0.0
    assert store.get_associations(a) == []


def test_edge_to_missing_fact_is_ignored(store):
    a = store.add_fact("Real fact")
    assert store.reinforce_association(a, 99999, delta=0.5) == 0.0
    assert store.get_associations(a) == []


def test_get_associations_orders_by_strength_and_honors_min(store):
    a = store.add_fact("Hub fact")
    b = store.add_fact("Weakly linked fact")
    c = store.add_fact("Strongly linked fact")
    store.reinforce_association(a, b, delta=0.1)
    store.reinforce_association(a, c, delta=0.5)
    ranked = store.get_associations(a)
    assert [f["fact_id"] for f in ranked] == [c, b]
    filtered = store.get_associations(a, min_strength=0.3)
    assert [f["fact_id"] for f in filtered] == [c]


def test_removing_a_fact_prunes_its_edges(store):
    a = store.add_fact("Kept fact")
    b = store.add_fact("Doomed fact")
    store.reinforce_association(a, b, delta=0.4)
    assert store.get_associations(a)
    store.remove_fact(b)
    assert store.get_associations(a) == []


def test_superseded_facts_are_not_spread(store):
    a = store.add_fact("Current fact")
    b = store.add_fact("Old fact")
    store.reinforce_association(a, b, delta=0.4)
    new_id = store.add_fact("Replacement fact")
    store.supersede_fact(b, new_id)
    # Edge row still exists, but a superseded partner must not be surfaced.
    assert store.get_associations(a) == []


# ---------------------------------------------------------------------------
# Provider-level: learn via feedback, recall via spread
# ---------------------------------------------------------------------------

@pytest.fixture()
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "spread.db")})
    p.initialize("session-spread")
    try:
        yield p
    finally:
        p.shutdown()


def _add(provider, content, **kw):
    return int(json.loads(provider._handle_fact_store({"action": "add", "content": content, **kw}))["fact_id"])


def _search(provider, query, **kw):
    return json.loads(provider._handle_fact_store({"action": "search", "query": query, **kw}))


def _spread(provider, **kw):
    return json.loads(provider._handle_fact_store({"action": "spread", **kw}))


def _feedback(provider, fact_id, action="helpful"):
    return json.loads(provider._handle_fact_feedback({"action": action, "fact_id": fact_id}))


def test_helpful_feedback_lays_down_durable_edges(provider):
    a = _add(provider, "The staging cluster mirrors production topology exactly")
    b = _add(provider, "Production topology spans three availability zones")

    res = _search(provider, "production topology cluster")
    assert {a, b} <= {r["fact_id"] for r in res["results"]}
    _feedback(provider, a, "helpful")

    # The association survives in the store, independent of the session buffer.
    assoc = provider._store.get_associations(a)
    assert b in [f["fact_id"] for f in assoc]
    assert assoc[0]["strength"] > 0.0


def test_spread_by_fact_id_returns_the_learned_cluster(provider):
    a = _add(provider, "Widget service owns the billing reconciliation job")
    b = _add(provider, "Billing reconciliation job runs nightly at 0200 UTC")
    _search(provider, "billing reconciliation job widget")
    _feedback(provider, a, "helpful")

    out = _spread(provider, fact_id=a)
    assert out["seed"] == a
    assert b in [f["fact_id"] for f in out["facts"]]


def test_spread_by_query_resolves_seed_then_spreads(provider):
    a = _add(provider, "Zephyr module handles the websocket fan-out")
    b = _add(provider, "Websocket fan-out is rate-limited per tenant")
    _search(provider, "websocket fan-out zephyr")
    _feedback(provider, a, "helpful")

    out = _spread(provider, query="zephyr websocket")
    assert out["seed"] in (a, b)
    assert out["count"] >= 1


def test_spread_with_no_learned_edges_is_empty_not_error(provider):
    a = _add(provider, "An isolated fact with no associations yet")
    out = _spread(provider, fact_id=a)
    assert out["seed"] == a
    assert out["facts"] == []
    assert out["count"] == 0
    assert out["recall_confidence"] == 0.0


def test_spread_requires_a_seed(provider):
    out = _spread(provider)
    assert "error" in out or out.get("isError") or "requires" in json.dumps(out)


def test_spread_unresolvable_query_returns_empty(provider):
    out = _spread(provider, query="nonexistent-term-qqzzxx-9182")
    assert out["facts"] == []
    assert out["count"] == 0
