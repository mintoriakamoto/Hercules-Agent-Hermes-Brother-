"""Tests for multi-hop entity-graph associative recall."""

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[3]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from plugins.memory.holographic.store import MemoryStore  # noqa: E402
from plugins.memory.holographic.retrieval import FactRetriever  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "mem.db")


def test_graph_recall_one_hop(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        # Entities are extracted from capitalized multi-word phrases.
        store.add_fact("Ada Lovelace works on Project Apollo")
        store.add_fact("Ada Lovelace prefers Rust Lang")
        results = store.graph_recall(["Ada Lovelace"], hops=1)
        contents = {r["content"] for r in results}
        assert "Ada Lovelace works on Project Apollo" in contents
        assert "Ada Lovelace prefers Rust Lang" in contents
        assert all(r["hop"] == 1 for r in results)
    finally:
        store.close()


def test_graph_recall_two_hop_association(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        # Hop 1: Ada -> "Project Apollo". Hop 2: Project Apollo -> Grace Hopper.
        store.add_fact("Ada Lovelace works on Project Apollo")
        store.add_fact("Grace Hopper also works on Project Apollo")
        store.add_fact("Grace Hopper wrote the Compiler Spec")

        one_hop = {r["content"] for r in store.graph_recall(["Ada Lovelace"], hops=1)}
        assert "Grace Hopper also works on Project Apollo" not in one_hop  # 2 hops away

        # Hop 2 reaches Grace Hopper's shared-project fact via Project Apollo,
        # but NOT her unrelated fact (that's 3 hops: Ada→Apollo→Grace→Spec).
        two_hop = {r["content"] for r in store.graph_recall(["Ada Lovelace"], hops=2)}
        assert "Grace Hopper also works on Project Apollo" in two_hop
        assert "Grace Hopper wrote the Compiler Spec" not in two_hop

        three_hop = {r["content"] for r in store.graph_recall(["Ada Lovelace"], hops=3)}
        assert "Grace Hopper wrote the Compiler Spec" in three_hop
    finally:
        store.close()


def test_graph_recall_excludes_superseded(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        old = store.add_fact("Ada Lovelace lives in London City")
        new = store.add_fact("Ada Lovelace lives in Paris France")
        store.supersede_fact(old, new)
        contents = {r["content"] for r in store.graph_recall(["Ada Lovelace"], hops=1)}
        assert "Ada Lovelace lives in London City" not in contents
        assert "Ada Lovelace lives in Paris France" in contents
    finally:
        store.close()


def test_graph_recall_unknown_entity_empty(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        store.add_fact("Ada Lovelace works on Project Apollo")
        assert store.graph_recall(["Nonexistent Person"], hops=2) == []
    finally:
        store.close()


def test_retriever_graph_search_extracts_entities(db_path):
    store = MemoryStore(db_path=db_path)
    try:
        store.add_fact("Ada Lovelace works on Project Apollo")
        retriever = FactRetriever(store=store)
        # A natural-language seed; the entity "Ada Lovelace" is extracted from it.
        results = retriever.graph_search("what about Ada Lovelace", hops=1)
        assert any("Project Apollo" in r["content"] for r in results)
    finally:
        store.close()
