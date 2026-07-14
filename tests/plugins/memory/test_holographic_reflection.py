"""Tests for the reflection engine: importance scoring/weighting, insight
synthesis, evidence-linked profile promotion (provenance), and the LLM
reflect/score capabilities.
"""

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[3]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from plugins.memory.holographic.store import MemoryStore  # noqa: E402
from plugins.memory.holographic.retrieval import FactRetriever  # noqa: E402
from plugins.memory.holographic.llm import MemoryLLM  # noqa: E402
from plugins.memory.holographic import HolographicMemoryProvider  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "mem.db")


# ---------------------------------------------------------------------------
# Importance: storage + retrieval weighting
# ---------------------------------------------------------------------------

def test_importance_stored_and_clamped(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        fid = store.add_fact("a fact", importance=99)  # clamps to 10
        row = store._conn.execute(
            "SELECT importance FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        assert row["importance"] == 10
        assert store.set_importance(fid, 3) is True
        row = store._conn.execute(
            "SELECT importance FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        assert row["importance"] == 3
    finally:
        store.close()


def test_importance_weights_retrieval(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        # Equally-relevant facts for the query "coffee"; importance breaks the tie.
        store.add_fact("coffee is good", importance=2)
        store.add_fact("coffee is great", importance=10)
        retriever = FactRetriever(store=store)
        results = retriever.search("coffee", min_trust=0.0)
        assert results
        assert results[0]["content"] == "coffee is great"  # higher importance wins
    finally:
        store.close()


def test_default_importance_is_neutral(db_path):
    """Default importance (5) must not perturb ordering vs the pre-importance behavior."""
    store = MemoryStore(db_path=db_path)
    try:
        store.add_fact("alpha topic one")
        store.add_fact("alpha topic two")
        r = FactRetriever(store=store).search("alpha", min_trust=0.0)
        # Both default importance → factor 1.0 → they simply both return.
        assert {f["content"] for f in r} == {"alpha topic one", "alpha topic two"}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Reflection primitives
# ---------------------------------------------------------------------------

def test_unreflected_selection_and_mark(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        a = store.add_fact("obs one", fact_type="episodic")
        store.add_fact("obs two", fact_type="episodic")
        store.add_fact("a profile fact", fact_type="profile")  # excluded (not episodic)
        unreflected = store.select_unreflected_facts()
        ids = {f["fact_id"] for f in unreflected}
        assert a in ids
        assert len(unreflected) == 2  # only episodic
        store.mark_reflected([f["fact_id"] for f in unreflected])
        assert store.select_unreflected_facts() == []
    finally:
        store.close()


def test_derived_fact_provenance(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        s1 = store.add_fact("user shipped to fly on monday", fact_type="episodic")
        s2 = store.add_fact("user shipped to fly on friday", fact_type="episodic")
        insight_id = store.add_derived_fact(
            "user regularly deploys to Fly.io",
            category="identity",
            source_ids=[s1, s2],
            importance=9,
        )
        # It's a durable profile fact...
        profile = [p["content"] for p in store.list_profile_facts()]
        assert "user regularly deploys to Fly.io" in profile
        # ...with evidence links back to the observations.
        sources = store.get_fact_sources(insight_id)
        assert {s["fact_id"] for s in sources} == {s1, s2}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# MemoryLLM reflect / score / extract importance
# ---------------------------------------------------------------------------

def test_llm_reflect_parses_insights():
    def chat(system, user):
        return (
            '[{"content": "user is ops-focused", "category": "identity", '
            '"source_ids": [1, 2], "importance": 9}]'
        )

    out = MemoryLLM(chat_fn=chat).reflect([{"fact_id": 1, "content": "x"}])
    assert out and out[0]["content"] == "user is ops-focused"
    assert out[0]["source_ids"] == [1, 2]
    assert out[0]["importance"] == 9


def test_llm_reflect_empty_input():
    assert MemoryLLM(chat_fn=lambda s, u: "[]").reflect([]) == []


def test_llm_score_importance():
    assert MemoryLLM(chat_fn=lambda s, u: "The score is 8.").score_importance("x") == 8
    assert MemoryLLM(chat_fn=lambda s, u: "99").score_importance("x") == 10  # clamp


def test_llm_extract_includes_importance():
    def chat(system, user):
        return '[{"content": "f", "category": "fact", "fact_type": "episodic", "importance": 7}]'

    facts = MemoryLLM(chat_fn=chat).extract_facts("stuff")
    assert facts[0]["importance"] == 7


# ---------------------------------------------------------------------------
# End-to-end provider reflection
# ---------------------------------------------------------------------------

def test_provider_reflection_promotes_linked_insight(tmp_path):
    provider = HolographicMemoryProvider(
        config={"db_path": str(tmp_path / "m.db"), "auto_extract": False, "auto_reflect": False}
    )
    provider.initialize(session_id="s", hercules_home=str(tmp_path), platform="cli")
    try:
        store = provider._store
        id1 = store.add_fact("user deployed to Fly.io again", category="project", fact_type="episodic")
        id2 = store.add_fact("user chose Fly over Docker", category="project", fact_type="episodic")
        store.add_fact("user asked about Fly scaling", category="project", fact_type="episodic")

        def chat(system, user):
            return (
                '[{"content": "user standardizes ops on Fly.io", "category": "identity", '
                f'"source_ids": [{id1}, {id2}], "importance": 9}}]'
            )

        provider._llm = MemoryLLM(chat_fn=chat)
        summary = provider.reflect(min_facts=1)
        assert summary["insights"] == 1
        assert summary["sources_considered"] == 3

        # The insight became a durable profile fact with provenance.
        profile = store.list_profile_facts()
        insight = [p for p in profile if "standardizes ops" in p["content"]]
        assert insight, "reflection should promote an insight to profile memory"
        sources = store.get_fact_sources(insight[0]["fact_id"])
        assert {s["fact_id"] for s in sources} == {id1, id2}

        # Sources were marked reflected → a second pass finds no new experience.
        assert provider.reflect(min_facts=1)["insights"] == 0
    finally:
        provider.shutdown()
