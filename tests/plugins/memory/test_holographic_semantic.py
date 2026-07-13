"""Tests for the semantic-embedding upgrade to the holographic memory store.

Covers the embeddings module utilities, semantic *recall* (surfacing a fact
whose meaning matches with zero keyword overlap), semantic *rerank*, graceful
lexical fallback when embeddings are off, and the entity-resolution fix.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[3]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from plugins.memory.holographic.embeddings import (  # noqa: E402
    Embedder,
    bytes_to_vec,
    cosine,
    vec_to_bytes,
)
from plugins.memory.holographic.store import MemoryStore  # noqa: E402
from plugins.memory.holographic.retrieval import FactRetriever  # noqa: E402


# A tiny deterministic "semantic space" for tests: each concept is a basis
# direction; a text embeds to the sum of the concept directions it mentions.
# This lets us assert meaning-based matching without a network/API key.
_CONCEPTS = {
    # deployment / hosting
    "production": (1, 0, 0, 0), "fly": (1, 0, 0, 0), "deploy": (1, 0, 0, 0),
    "ship": (1, 0, 0, 0), "hosting": (1, 0, 0, 0), "server": (1, 0, 0, 0),
    # pets / animals
    "cat": (0, 1, 0, 0), "tuna": (0, 1, 0, 0), "pet": (0, 1, 0, 0), "kitten": (0, 1, 0, 0),
    # programming languages
    "python": (0, 0, 1, 0), "code": (0, 0, 1, 0), "programming": (0, 0, 1, 0),
    # food / cooking
    "recipe": (0, 0, 0, 1), "bake": (0, 0, 0, 1), "oven": (0, 0, 0, 1),
}


def _fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.0, 0.0]
        tl = t.lower()
        for concept, vec in _CONCEPTS.items():
            if concept in tl:
                v = [a + b for a, b in zip(v, vec)]
        if v == [0.0, 0.0, 0.0, 0.0]:
            v = [0.01, 0.01, 0.01, 0.01]  # avoid degenerate all-zero vector
        out.append(v)
    return out


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "mem.db")


# ---------------------------------------------------------------------------
# embeddings module utilities
# ---------------------------------------------------------------------------

def test_vec_bytes_roundtrip():
    vec = [0.5, -1.25, 3.0, 0.0]
    back = bytes_to_vec(vec_to_bytes(vec))
    assert back == pytest.approx(vec)


def test_cosine_values():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([], [1]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0  # degenerate → 0, never NaN


def test_embedder_disabled_without_backend(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    emb = Embedder.from_config({})
    assert emb.enabled is False
    assert emb.embed(["hello"]) is None


def test_embedder_enabled_with_embed_fn():
    emb = Embedder(embed_fn=_fake_embed)
    assert emb.enabled is True
    assert emb.embed_one("python code") == [0.0, 0.0, 2.0, 0.0]


def test_embedder_explicit_disable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    emb = Embedder.from_config({"embedding_enabled": False})
    assert emb.enabled is False


def test_embed_fn_exception_returns_none():
    def boom(_texts):
        raise RuntimeError("nope")

    emb = Embedder(embed_fn=boom)
    assert emb.embed(["x"]) is None  # swallowed, never crashes a write


# ---------------------------------------------------------------------------
# store: embeddings are computed + stored
# ---------------------------------------------------------------------------

def test_store_computes_embedding_when_enabled(db_path):
    store = MemoryStore(db_path=db_path, embedder=Embedder(embed_fn=_fake_embed))
    try:
        fid = store.add_fact("production runs on fly")
        row = store._conn.execute(
            "SELECT embedding FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        assert row["embedding"] is not None
        assert bytes_to_vec(row["embedding"]) == [2.0, 0.0, 0.0, 0.0]
    finally:
        store.close()


def test_store_no_embedding_without_embedder(db_path):
    store = MemoryStore(db_path=db_path)  # no embedder
    try:
        fid = store.add_fact("production runs on fly")
        row = store._conn.execute(
            "SELECT embedding FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        assert row["embedding"] is None  # lexical-only, unchanged behavior
    finally:
        store.close()


# ---------------------------------------------------------------------------
# retrieval: semantic recall + rerank
# ---------------------------------------------------------------------------

def _seed(store):
    store.add_fact("production runs on fly")            # deploy concept
    store.add_fact("the cat enjoys tuna")               # pet concept
    store.add_fact("i write python code every day")     # programming concept


def test_semantic_recall_with_zero_keyword_overlap(db_path):
    """A query sharing NO tokens with the target still recalls it by meaning."""
    embedder = Embedder(embed_fn=_fake_embed)
    store = MemoryStore(db_path=db_path, embedder=embedder)
    try:
        _seed(store)
        retriever = FactRetriever(store=store, embedder=embedder)

        # "where do we deploy the app" shares no content words with
        # "production runs on fly", so pure FTS5/lexical recall returns nothing.
        results = retriever.search("where do we deploy the app", min_trust=0.0)
        assert results, "semantic recall should surface the deploy fact"
        assert results[0]["content"] == "production runs on fly"
    finally:
        store.close()


def test_lexical_only_misses_semantic_match(db_path):
    """Control: with embeddings OFF, the same keyword-free query recalls nothing."""
    store = MemoryStore(db_path=db_path)  # no embedder
    try:
        _seed(store)
        retriever = FactRetriever(store=store)  # lexical + HRR only
        results = retriever.search("where do we deploy the app", min_trust=0.0)
        assert results == []  # no keyword overlap → nothing, as before
    finally:
        store.close()


def test_semantic_rerank_prefers_closer_meaning(db_path):
    """Among candidates, the semantically-closest fact ranks first."""
    embedder = Embedder(embed_fn=_fake_embed)
    store = MemoryStore(db_path=db_path, embedder=embedder)
    try:
        _seed(store)
        retriever = FactRetriever(store=store, embedder=embedder)
        # A hosting/server query should rank the deploy fact above the others.
        results = retriever.search("hosting and server setup", min_trust=0.0)
        assert results
        assert results[0]["content"] == "production runs on fly"
    finally:
        store.close()


def test_retrieval_count_tracked_on_hybrid_path(db_path):
    embedder = Embedder(embed_fn=_fake_embed)
    store = MemoryStore(db_path=db_path, embedder=embedder)
    try:
        fid = store.add_fact("production runs on fly")
        retriever = FactRetriever(store=store, embedder=embedder)
        retriever.search("deploy the app", min_trust=0.0)
        row = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()
        assert row["retrieval_count"] >= 1
    finally:
        store.close()


# ---------------------------------------------------------------------------
# entity resolution fix (LIKE wildcard injection)
# ---------------------------------------------------------------------------

def test_entity_resolution_no_wildcard_merge(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        # A quoted entity containing '%' must not LIKE-wildcard-match another
        # entity that merely shares its prefix.
        id_kg = store._resolve_entity("100kg")
        id_pct = store._resolve_entity("100%")
        assert id_kg != id_pct, "distinct entities must not merge via LIKE wildcard"
        # And re-resolving each returns its own stable id.
        assert store._resolve_entity("100%") == id_pct
        assert store._resolve_entity("100KG") == id_kg  # case-insensitive exact
    finally:
        store.close()
