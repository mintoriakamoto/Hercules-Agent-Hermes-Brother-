"""Hebbian co-activation reinforcement in holographic memory.

Facts retrieved together in one recall are "co-activated". When the agent then
rates one of them helpful, the *association* is what paid off — so the facts
recalled alongside it get a small, sub-helpful trust nudge. Over sessions,
clusters of mutually-useful facts self-strengthen.

These tests drive the provider end-to-end (add → search → feedback) against a
real on-disk SQLite store, asserting on the observable trust deltas rather than
internal bookkeeping.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")  # store/retrieval import numpy indirectly

from plugins.memory.holographic import (
    _CO_ACTIVATION_DELTA,
    _CO_ACTIVATION_MAX_FANOUT,
    HolographicMemoryProvider,
)


@pytest.fixture()
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "coact.db")})
    p.initialize("session-coact")
    try:
        yield p
    finally:
        p.shutdown()


def _add(provider, content, **kw):
    out = json.loads(provider._handle_fact_store({"action": "add", "content": content, **kw}))
    return int(out["fact_id"])


def _trust(provider, fact_id):
    row = provider._store._conn.execute(
        "SELECT trust_score FROM facts WHERE fact_id = ?", (fact_id,)
    ).fetchone()
    return row["trust_score"]


def _search(provider, query, **kw):
    return json.loads(provider._handle_fact_store({"action": "search", "query": query, **kw}))


def _feedback(provider, fact_id, action="helpful"):
    return json.loads(provider._handle_fact_feedback({"action": action, "fact_id": fact_id}))


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_helpful_feedback_reinforces_co_recalled_facts(provider):
    """A helpful rating nudges the trust of facts recalled alongside it."""
    a = _add(provider, "The deployment pipeline runs on Kubernetes clusters")
    b = _add(provider, "Kubernetes clusters are monitored with Prometheus dashboards")

    res = _search(provider, "kubernetes clusters deployment")
    recalled = {r["fact_id"] for r in res["results"]}
    assert {a, b} <= recalled, "both facts should be co-recalled for the test to be meaningful"

    before_b = _trust(provider, b)
    result = _feedback(provider, a, "helpful")

    # The rated fact went up by the direct helpful delta; the co-activated
    # partner went up by the smaller co-activation delta.
    assert b in result["co_activated"]
    assert result["co_activation_delta"] == _CO_ACTIVATION_DELTA
    assert _trust(provider, b) == pytest.approx(before_b + _CO_ACTIVATION_DELTA)


def test_co_activation_delta_is_smaller_than_direct_helpful(provider):
    """Co-activation must be a *sub-helpful* nudge, never as strong as a rating."""
    a = _add(provider, "Alpha project uses the Postgres database backend")
    b = _add(provider, "Postgres database backend is tuned for write-heavy loads")

    _search(provider, "postgres database backend")
    before_a, before_b = _trust(provider, a), _trust(provider, b)
    _feedback(provider, a, "helpful")

    direct_gain = _trust(provider, a) - before_a
    coact_gain = _trust(provider, b) - before_b
    assert coact_gain > 0
    assert coact_gain < direct_gain


def test_unhelpful_feedback_does_not_touch_co_recalled_facts(provider):
    """A penalty is never propagated to co-activated facts."""
    a = _add(provider, "Bravo service exposes a GraphQL gateway endpoint")
    b = _add(provider, "GraphQL gateway endpoint sits behind the load balancer")

    _search(provider, "graphql gateway endpoint")
    before_b = _trust(provider, b)
    result = _feedback(provider, a, "unhelpful")

    assert "co_activated" not in result
    assert _trust(provider, b) == pytest.approx(before_b)


def test_feedback_without_prior_recall_is_a_plain_rating(provider):
    """No recall episode → feedback response is unchanged from the base store."""
    a = _add(provider, "Charlie module handles the retry backoff logic")
    result = _feedback(provider, a, "helpful")
    assert "co_activated" not in result
    assert set(result) >= {"fact_id", "old_trust", "new_trust", "helpful_count"}


def test_singleton_recall_creates_no_association(provider):
    """A recall returning a single fact is not an association — nothing to boost."""
    a = _add(provider, "Delta uniquely-worded singleton fact zzqqxx")
    res = _search(provider, "zzqqxx")
    assert len(res["results"]) == 1
    result = _feedback(provider, a, "helpful")
    assert "co_activated" not in result


def test_rated_fact_is_not_in_its_own_co_activation_set(provider):
    a = _add(provider, "Echo config stores secrets in the vault backend")
    b = _add(provider, "Vault backend rotates secrets on a fixed schedule")
    _search(provider, "vault backend secrets")
    result = _feedback(provider, a, "helpful")
    assert a not in result.get("co_activated", [])


def test_reinforcement_is_bounded_by_max_fanout(provider):
    """Even a wide recall boosts at most _CO_ACTIVATION_MAX_FANOUT partners."""
    ids = [
        _add(provider, f"Foxtrot shared-topic widget fact number {i} about caching layers")
        for i in range(_CO_ACTIVATION_MAX_FANOUT + 5)
    ]
    res = _search(provider, "foxtrot shared-topic widget caching layers", limit=50)
    recalled = {r["fact_id"] for r in res["results"]}
    # Need a broad co-activation for the cap to bite.
    assert len(recalled) > _CO_ACTIVATION_MAX_FANOUT + 1

    result = _feedback(provider, ids[0], "helpful")
    assert len(result.get("co_activated", [])) <= _CO_ACTIVATION_MAX_FANOUT


def test_repeated_helpful_feedback_accumulates_association(provider):
    """Reinforcing the same cluster twice compounds the co-activation trust."""
    a = _add(provider, "Golf release train ships every second Thursday")
    b = _add(provider, "Second Thursday release train freezes the main branch")

    _search(provider, "release train thursday")
    _feedback(provider, a, "helpful")
    after_one = _trust(provider, b)

    _search(provider, "release train thursday")
    _feedback(provider, a, "helpful")
    after_two = _trust(provider, b)

    assert after_two == pytest.approx(after_one + _CO_ACTIVATION_DELTA)


def test_co_activation_capture_never_raises_on_malformed_results(provider):
    """The capture helper is best-effort and tolerates junk without raising."""
    provider._note_co_activation([{"no_fact_id": 1}, "not-a-dict", None, {"fact_id": "x"}])
    # No episode should have been recorded from unusable rows.
    assert len(provider._last_recall) == 0
