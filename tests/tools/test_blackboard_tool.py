"""Tests for the shared blackboard — the inter-agent communication channel."""

import json
import threading

import pytest

from tools import blackboard_tool as bb


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the store at a per-test database and a clean board env."""
    monkeypatch.setenv("HERCULES_BLACKBOARD_DB", str(tmp_path / "blackboard.db"))
    monkeypatch.delenv("HERCULES_BLACKBOARD_BOARD", raising=False)


def _read_entries(**kwargs):
    return bb.read(**kwargs)["entries"]


class TestMergeSemantics:
    def test_post_then_read_roundtrip(self):
        bb.post("status", "researching", author="worker:a")
        result = bb.read()
        assert result["entries"] == {"status": "researching"}
        assert result["_authors"] == {"status": "worker:a"}

    def test_last_writer_wins_per_key(self):
        bb.post("status", "started", author="worker:a")
        bb.post("status", "done", author="worker:b")
        bb.post("other", "untouched", author="worker:a")
        result = bb.read()
        assert result["entries"]["status"] == "done"
        assert result["_authors"]["status"] == "worker:b"
        assert result["entries"]["other"] == "untouched"

    def test_json_values_stored_structurally(self):
        bb.post("findings", json.dumps({"files": ["a.py", "b.py"], "count": 2}))
        entries = _read_entries()
        assert entries["findings"] == {"files": ["a.py", "b.py"], "count": 2}

    def test_plain_text_value_survives(self):
        bb.post("note", "not json: {oops")
        assert _read_entries()["note"] == "not json: {oops"

    def test_single_key_read(self):
        bb.post("a", "1")
        bb.post("b", "2")
        result = bb.read(key="a")
        assert result["entries"] == {"a": 1}
        assert "b" not in result["entries"]

    def test_missing_key_read_is_empty(self):
        bb.post("a", "1")
        assert bb.read(key="nope")["entries"] == {}


class TestBoards:
    def test_board_isolation(self):
        bb.post("k", "board-one", board="one")
        bb.post("k", "board-two", board="two")
        assert _read_entries(board="one") == {"k": "board-one"}
        assert _read_entries(board="two") == {"k": "board-two"}

    def test_env_board_shared_by_default(self, monkeypatch):
        """Parent sets the env once; in-process subagents inherit the board."""
        monkeypatch.setenv("HERCULES_BLACKBOARD_BOARD", "session-42")
        bb.post("handoff", "from parent")  # no explicit board
        assert _read_entries(board="session-42") == {"handoff": "from parent"}
        assert _read_entries() == {"handoff": "from parent"}

    def test_boards_listing(self):
        bb.post("x", "1", board="alpha")
        bb.post("y", "2", board="beta")
        names = {row["board"] for row in bb.boards()}
        assert {"alpha", "beta"} <= names

    def test_clear_key_and_board(self):
        bb.post("keep", "1", board="c")
        bb.post("drop", "2", board="c")
        assert bb.clear(board="c", key="drop")["deleted"] == 1
        assert _read_entries(board="c") == {"keep": 1}
        bb.clear(board="c")
        assert _read_entries(board="c") == {}


class TestConcurrency:
    def test_parallel_writers_all_land(self):
        """Parent + N subagent threads writing concurrently (the real topology)."""
        errors = []

        def worker(i):
            try:
                for j in range(10):
                    bb.post(f"worker-{i}-item-{j}", str(j), author=f"worker:{i}")
            except Exception as exc:  # pragma: no cover - failure detail
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(_read_entries()) == 80


class TestWait:
    def test_wait_returns_immediately_when_present(self):
        bb.post("ready", "yes", author="producer")
        result = bb.wait("ready", timeout_seconds=5)
        assert result["entries"] == {"ready": "yes"}
        assert result["waited_seconds"] < 1.0

    def test_wait_picks_up_concurrent_post(self):
        def producer():
            import time as _t

            _t.sleep(0.4)
            bb.post("handoff", '{"rows": 3}', author="worker:a")

        t = threading.Thread(target=producer)
        t.start()
        result = bb.wait("handoff", timeout_seconds=10, poll_interval=0.05)
        t.join()
        assert result["entries"] == {"handoff": {"rows": 3}}
        assert result["_authors"] == {"handoff": "worker:a"}
        assert result["waited_seconds"] >= 0.3

    def test_wait_times_out(self):
        with pytest.raises(TimeoutError):
            bb.wait("never-posted", timeout_seconds=0.3, poll_interval=0.05)

    def test_wait_requires_key(self):
        with pytest.raises(ValueError):
            bb.wait("")

    def test_wait_timeout_capped(self):
        import time as _t

        bb.post("x", "1")
        start = _t.monotonic()
        result = bb.wait("x", timeout_seconds=10_000)
        assert _t.monotonic() - start < 5

    def test_tool_dispatch_wait_timeout_is_tool_error(self):
        out = bb.blackboard_tool("wait", key="ghost", timeout_seconds=0.2)
        assert "did not appear" in out
        assert "producer" in out


class TestToolEntryPoint:
    def test_post_requires_key(self):
        out = bb.blackboard_tool("post", key="", value="v")
        assert "key is required" in out

    def test_unknown_action(self):
        assert "unknown action" in bb.blackboard_tool("bogus")

    def test_value_size_cap(self):
        out = bb.blackboard_tool("post", key="big", value="x" * (bb._MAX_VALUE_CHARS + 1))
        assert "too large" in out

    def test_dispatch_roundtrip(self):
        bb.blackboard_tool("post", key="k", value="v", author="parent")
        payload = json.loads(bb.blackboard_tool("read"))
        assert payload["entries"] == {"k": "v"}
        assert payload["_authors"] == {"k": "parent"}

    def test_oversized_board_read_truncates_to_keys(self, monkeypatch):
        monkeypatch.setattr(bb, "_MAX_READ_CHARS", 200)
        for i in range(20):
            bb.post(f"key-{i:02d}", "some value that adds up")
        payload = json.loads(bb.blackboard_tool("read"))
        assert payload["truncated"] is True
        assert payload["keys"] == sorted(f"key-{i:02d}" for i in range(20))


class TestIntegration:
    def test_registered_in_registry_under_blackboard_toolset(self):
        from tools.registry import registry

        assert "blackboard" in registry.get_tool_names_for_toolset("blackboard")

    def test_toolset_declared_statically(self):
        import toolsets

        ts = toolsets.get_toolset("blackboard", include_registry=False)
        assert ts is not None
        assert "blackboard" in ts["tools"]

    def test_not_blocked_for_subagents(self):
        from tools.delegate_tool import DELEGATE_BLOCKED_TOOLS

        assert "blackboard" not in DELEGATE_BLOCKED_TOOLS
        # The isolation rationale for these still holds — guard against
        # accidental unblocking while we're here.
        assert "memory" in DELEGATE_BLOCKED_TOOLS

    def test_child_prompt_mentions_blackboard(self):
        from tools.delegate_tool import _build_child_system_prompt

        prompt = _build_child_system_prompt("do the thing")
        assert "blackboard" in prompt
