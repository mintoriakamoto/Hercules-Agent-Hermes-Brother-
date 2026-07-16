"""Metacognition: calibrated recall confidence.

Every recalled fact is tagged with a 0-1 `confidence` (blending learned trust,
corroboration, and recency) plus a `confidence_label`, and each recall reports
an overall `recall_confidence` — so the agent can hedge a weak memory instead of
asserting it as fact.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("numpy")

from plugins.memory.holographic import (
    _confidence_label,
    _recall_confidence,
    HolographicMemoryProvider,
)


# --- the confidence function ----------------------------------------------

def test_confidence_rewards_trust_corroboration_recency():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    strong = {"trust_score": 0.95, "helpful_count": 5, "updated_at": now}
    weak = {"trust_score": 0.2, "helpful_count": 0, "updated_at": now}
    assert _recall_confidence(strong) > _recall_confidence(weak)
    assert _recall_confidence(strong) >= 0.66   # high
    assert _recall_confidence(weak) < 0.5


def test_confidence_decays_with_age():
    fresh = {"trust_score": 0.6, "helpful_count": 1,
             "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    old = {"trust_score": 0.6, "helpful_count": 1,
           "updated_at": (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")}
    assert _recall_confidence(old) < _recall_confidence(fresh)


def test_confidence_is_bounded_and_defensive():
    assert 0.0 <= _recall_confidence({}) <= 1.0
    # Malformed fields must not raise and stay in range.
    assert 0.0 <= _recall_confidence({"trust_score": "x", "helpful_count": None}) <= 1.0
    assert 0.0 <= _recall_confidence({"trust_score": 5.0, "helpful_count": -3}) <= 1.0


def test_confidence_labels():
    assert _confidence_label(0.9) == "high"
    assert _confidence_label(0.5) == "medium"
    assert _confidence_label(0.1) == "low"


# --- end-to-end through the provider --------------------------------------

@pytest.fixture()
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "conf.db")})
    p.initialize("session-confidence")
    try:
        yield p
    finally:
        p.shutdown()


def _search(provider, query, **kw):
    return json.loads(provider._handle_fact_store({"action": "search", "query": query, **kw}))


def test_search_results_carry_confidence_and_overall(provider):
    fid = provider._store.add_fact("The build server lives in the west datacenter")
    # Train it up: repeated helpful feedback raises trust + corroboration.
    for _ in range(4):
        provider._store.record_feedback(fid, helpful=True)

    res = _search(provider, "build server west datacenter")
    assert res["results"], "fact should be recalled"
    top = res["results"][0]
    assert 0.0 <= top["confidence"] <= 1.0
    assert top["confidence_label"] in {"high", "medium", "low"}
    assert res["recall_confidence"] == pytest.approx(
        max(r["confidence"] for r in res["results"])
    )
    # A well-trained, fresh fact should read as high confidence.
    assert top["confidence_label"] == "high"


def test_untrained_fact_reads_lower_confidence(provider):
    provider._store.add_fact("A freshly noted, never-rated detail about caching")
    res = _search(provider, "freshly noted caching detail")
    assert res["results"]
    # Default trust 0.5, no corroboration → not "high".
    assert res["results"][0]["confidence_label"] in {"medium", "low"}


def test_empty_recall_has_zero_confidence(provider):
    res = _search(provider, "nothing-matches-zzqqxx-9182")
    assert res["results"] == []
    assert res["recall_confidence"] == 0.0
