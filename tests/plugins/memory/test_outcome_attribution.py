"""Automatic outcome attribution — self-driving learning from session outcomes.

At session end the provider infers a coarse outcome from the user's own words
and nudges the trust of the facts the session recalled: a session that ended
well quietly reinforces the memory it leaned on, a corrected one discounts it.
No manual fact_feedback required. Conservative: trust-only (never helpful_count),
small inferred deltas, capped fan-out, and a no-op on ambiguous sessions.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")

from plugins.memory.holographic import (
    _AUTO_ATTRIBUTION_MAX_FACTS,
    _AUTO_ATTRIBUTION_NEGATIVE_DELTA,
    _AUTO_ATTRIBUTION_POSITIVE_DELTA,
    HolographicMemoryProvider,
)

_classify = HolographicMemoryProvider._classify_session_outcome


# --- the outcome classifier ------------------------------------------------

def test_classifier_detects_positive():
    assert _classify([{"role": "user", "content": "Thanks, that's exactly right!"}]) == "positive"
    assert _classify([{"role": "user", "content": "perfect, that worked"}]) == "positive"


def test_classifier_detects_negative():
    assert _classify([{"role": "user", "content": "no, that's wrong"}]) == "negative"
    assert _classify([{"role": "user", "content": "that didn't work at all"}]) == "negative"


def test_classifier_negative_wins_across_recent_turns():
    msgs = [
        {"role": "user", "content": "that's wrong"},
        {"role": "assistant", "content": "let me fix it"},
        {"role": "user", "content": "ok"},  # bland final turn
    ]
    assert _classify(msgs) == "negative"


def test_classifier_ambiguous_is_none():
    assert _classify([{"role": "user", "content": "what's the weather"}]) is None
    assert _classify([]) is None
    assert _classify([{"role": "assistant", "content": "thanks!"}]) is None  # not a user turn


# --- end-to-end attribution ------------------------------------------------

@pytest.fixture()
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "attr.db")})
    p.initialize("session-attr")
    try:
        yield p
    finally:
        p.shutdown()


def _search(provider, query, **kw):
    return json.loads(provider._handle_fact_store({"action": "search", "query": query, **kw}))


def _trust(provider, fid):
    return provider._store._conn.execute(
        "SELECT trust_score FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["trust_score"]


def test_positive_outcome_reinforces_recalled_facts(provider):
    fid = provider._store.add_fact("The deploy target is the eu-west region")
    res = _search(provider, "deploy target region")
    assert fid in {r["fact_id"] for r in res["results"]}
    before = _trust(provider, fid)
    helpful_before = provider._store._conn.execute(
        "SELECT helpful_count FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["helpful_count"]

    provider.on_session_end([{"role": "user", "content": "thanks, that's exactly right"}])

    assert _trust(provider, fid) == pytest.approx(before + _AUTO_ATTRIBUTION_POSITIVE_DELTA)
    # Trust-only: inferred signal must NOT inflate the confirmed-helpful count.
    helpful_after = provider._store._conn.execute(
        "SELECT helpful_count FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["helpful_count"]
    assert helpful_after == helpful_before


def test_negative_outcome_discounts_recalled_facts(provider):
    fid = provider._store.add_fact("The API rate limit is 100 requests per minute")
    _search(provider, "api rate limit")
    before = _trust(provider, fid)

    provider.on_session_end([{"role": "user", "content": "no, that's wrong"}])

    assert _trust(provider, fid) == pytest.approx(before + _AUTO_ATTRIBUTION_NEGATIVE_DELTA)


def test_ambiguous_outcome_changes_nothing(provider):
    fid = provider._store.add_fact("The staging URL uses a self-signed cert")
    _search(provider, "staging url cert")
    before = _trust(provider, fid)

    provider.on_session_end([{"role": "user", "content": "ok, moving on"}])

    assert _trust(provider, fid) == pytest.approx(before)


def test_no_recall_means_no_attribution(provider):
    fid = provider._store.add_fact("A fact that is never recalled this session")
    before = _trust(provider, fid)
    # No search happened → nothing to attribute, even on a clear positive.
    provider.on_session_end([{"role": "user", "content": "perfect, thanks!"}])
    assert _trust(provider, fid) == pytest.approx(before)


def test_attribution_is_capped(provider):
    ids = [provider._store.add_fact(f"Capped-topic fact number {i} about widgets")
           for i in range(_AUTO_ATTRIBUTION_MAX_FACTS + 5)]
    res = _search(provider, "capped-topic widgets fact", limit=50)
    assert len({r["fact_id"] for r in res["results"]}) > _AUTO_ATTRIBUTION_MAX_FACTS

    befores = {fid: _trust(provider, fid) for fid in ids}
    provider.on_session_end([{"role": "user", "content": "that's exactly right, thanks"}])

    changed = [fid for fid in ids if _trust(provider, fid) != pytest.approx(befores[fid])]
    assert len(changed) <= _AUTO_ATTRIBUTION_MAX_FACTS


def test_recall_history_does_not_leak_across_sessions(provider):
    """Session rotation delivers on_session_end (not a fresh initialize), so the
    recalled set must be cleared at session end — otherwise session 1's facts
    get re-credited when session 2 ends."""
    fid1 = provider._store.add_fact("Session-one fact about the load balancer")
    _search(provider, "session-one load balancer")
    # Session 1 ends positive → fid1 credited once.
    provider.on_session_end([{"role": "user", "content": "perfect, that's exactly right"}])
    after_s1 = _trust(provider, fid1)

    # Session 2 (same provider instance — no re-initialize, as on /new) recalls a
    # DIFFERENT fact and ends positive. fid1 was NOT recalled in session 2, so
    # its trust must be untouched by session 2's outcome.
    fid2 = provider._store.add_fact("Session-two fact about the message queue")
    _search(provider, "session-two message queue")
    provider.on_session_end([{"role": "user", "content": "thanks, that worked"}])

    assert _trust(provider, fid1) == pytest.approx(after_s1)          # not re-credited
    assert _trust(provider, fid2) == pytest.approx(0.5 + _AUTO_ATTRIBUTION_POSITIVE_DELTA)


def test_disabled_via_config(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "off.db"),
                                          "auto_attribution": False})
    p.initialize("session-off")
    try:
        fid = p._store.add_fact("A fact under disabled attribution")
        _search(p, "disabled attribution fact")
        before = p._store._conn.execute(
            "SELECT trust_score FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["trust_score"]
        p.on_session_end([{"role": "user", "content": "perfect, exactly right!"}])
        after = p._store._conn.execute(
            "SELECT trust_score FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["trust_score"]
        assert after == pytest.approx(before)
    finally:
        p.shutdown()
