"""Tests for the advanced curation layer: typed memory (profile/episodic),
self-maintaining consolidation (semantic dedup + supersede), and the pluggable
memory LLM (salience extraction, reconciliation, HyDE query expansion).
"""

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[3]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from plugins.memory.holographic.embeddings import Embedder  # noqa: E402
from plugins.memory.holographic.store import MemoryStore  # noqa: E402
from plugins.memory.holographic.retrieval import FactRetriever  # noqa: E402
from plugins.memory.holographic.llm import MemoryLLM, _extract_json  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "mem.db")


def _embedder(vec_map):
    """Embedder whose vectors we control exactly, for precise similarity tests."""
    def fn(texts):
        return [list(vec_map.get(t, [0.01, 0.01, 0.01, 0.01])) for t in texts]

    return Embedder(embed_fn=fn)


class _FakeReconciler:
    """Stand-in for MemoryLLM.reconcile with a canned decision."""

    enabled = True

    def __init__(self, action, target_first=False):
        self._action = action
        self._target_first = target_first

    def reconcile(self, new_content, candidates):
        target = candidates[0]["fact_id"] if (self._target_first and candidates) else None
        return {"action": self._action, "target_fact_id": target}


# ---------------------------------------------------------------------------
# Typed memory
# ---------------------------------------------------------------------------

def test_profile_facts_listed_separately(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        store.add_fact("user prefers dark mode", category="user_pref", fact_type="profile")
        store.add_fact("we fixed the login bug today", category="project", fact_type="episodic")
        profile = store.list_profile_facts()
        contents = [p["content"] for p in profile]
        assert "user prefers dark mode" in contents
        assert "we fixed the login bug today" not in contents
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Supersede
# ---------------------------------------------------------------------------

def test_superseded_fact_excluded_from_retrieval(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        old_id = store.add_fact("user lives in New York")
        new_id = store.add_fact("user lives in San Francisco")
        assert store.supersede_fact(old_id, new_id) is True

        retriever = FactRetriever(store=store)
        results = retriever.search("where does the user live", min_trust=0.0)
        contents = [r["content"] for r in results]
        assert "user lives in New York" not in contents  # retired
        # list_facts also hides it
        listed = [f["content"] for f in store.list_facts(min_trust=0.0)]
        assert "user lives in New York" not in listed
        assert "user lives in San Francisco" in listed
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Curated add: dedup / update / new
# ---------------------------------------------------------------------------

def test_curated_add_detects_duplicate(db_path):
    # Two contents map to the same vector → cosine 1.0 → near-dup short-circuit.
    emb = _embedder({
        "user likes tea": [1.0, 0.0, 0.0, 0.0],
        "the user likes tea": [1.0, 0.0, 0.0, 0.0],
    })
    store = MemoryStore(db_path=db_path, embedder=emb)
    try:
        first = store.add_fact_curated("user likes tea")
        assert first["action"] == "new"
        dup = store.add_fact_curated("the user likes tea")
        assert dup["action"] == "duplicate"
        assert dup["fact_id"] == first["fact_id"]
    finally:
        store.close()


def test_curated_add_supersedes_on_update(db_path):
    # Similar-but-not-identical vectors → a neighbor is found, reconciler says
    # "update" → the old fact is superseded.
    emb = _embedder({
        "user lives in New York": [1.0, 0.0, 0.0, 0.0],
        "user moved to San Francisco": [0.85, 0.5, 0.0, 0.0],  # cosine ~0.86
    })
    store = MemoryStore(db_path=db_path, embedder=emb)
    try:
        old = store.add_fact_curated("user lives in New York")
        res = store.add_fact_curated(
            "user moved to San Francisco",
            reconciler=_FakeReconciler("update", target_first=True),
        )
        assert res["action"] == "update"
        assert old["fact_id"] in res["superseded"]
        # The retired fact is gone from retrieval; the new one remains.
        listed = [f["content"] for f in store.list_facts(min_trust=0.0)]
        assert "user lives in New York" not in listed
        assert "user moved to San Francisco" in listed
    finally:
        store.close()


def test_curated_add_independent_is_new(db_path):
    emb = _embedder({
        "user lives in New York": [1.0, 0.0, 0.0, 0.0],
        "the project uses Postgres": [0.0, 0.0, 1.0, 0.0],  # orthogonal
    })
    store = MemoryStore(db_path=db_path, embedder=emb)
    try:
        store.add_fact_curated("user lives in New York")
        res = store.add_fact_curated(
            "the project uses Postgres",
            reconciler=_FakeReconciler("update", target_first=True),
        )
        # No semantic neighbor above threshold → reconciler never consulted →
        # plain new insert (nothing superseded).
        assert res["action"] == "new"
        assert res["superseded"] == []
    finally:
        store.close()


def test_curated_add_without_embedder_is_plain_insert(db_path):
    store = MemoryStore(db_path=db_path)  # no embedder
    try:
        res = store.add_fact_curated("a plain fact")
        assert res["action"] == "new"
        assert res["fact_id"] > 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# MemoryLLM
# ---------------------------------------------------------------------------

def test_extract_json_handles_fences_and_prose():
    assert _extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert _extract_json('Sure! Here: {"x": 2} done') == {"x": 2}
    assert _extract_json("not json at all") is None


def test_llm_disabled_without_creds(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm = MemoryLLM.from_config({})
    assert llm.enabled is False
    assert llm.extract_facts("hi") is None


def test_llm_extract_facts_parses_typed_facts():
    def chat(system, user):
        return (
            '[{"content": "user prefers vim", "category": "user_pref", "fact_type": "profile"},'
            ' {"content": "we chose Postgres", "category": "project", "fact_type": "episodic"}]'
        )

    llm = MemoryLLM(chat_fn=chat)
    facts = llm.extract_facts("user: I always use vim\nassistant: noted")
    assert facts is not None
    assert facts[0]["content"] == "user prefers vim"
    assert facts[0]["fact_type"] == "profile"
    assert facts[1]["fact_type"] == "episodic"


def test_llm_reconcile_parses_decision():
    def chat(system, user):
        return '{"action": "update", "target_fact_id": 7}'

    llm = MemoryLLM(chat_fn=chat)
    decision = llm.reconcile("new", [{"fact_id": 7, "content": "old"}])
    assert decision == {"action": "update", "target_fact_id": 7}


def test_llm_reconcile_empty_candidates_is_new():
    llm = MemoryLLM(chat_fn=lambda s, u: "")
    assert llm.reconcile("x", []) == {"action": "new", "target_fact_id": None}


def test_llm_expand_query_returns_single_line():
    llm = MemoryLLM(chat_fn=lambda s, u: "The user's production deploys to Fly.io.\nextra")
    assert llm.expand_query("where do we deploy") == "The user's production deploys to Fly.io."
