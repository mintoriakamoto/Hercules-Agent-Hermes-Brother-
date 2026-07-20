"""Tests for delegate_task's adversarial verification wave (verify=True)."""

import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from tools.delegate_tool import delegate_task, _build_verifier_goal


def _make_mock_parent(depth=0):
    """Mock parent agent with the fields delegate_task expects."""
    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "***"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "anthropic/claude-sonnet-4"
    parent.platform = "cli"
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent._session_db = None
    parent._delegate_depth = depth
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent._interrupt_requested = False
    return parent


def _primary(idx=0, status="completed", summary="Wrote /tmp/out.txt with 3 rows"):
    return {
        "task_index": idx,
        "status": status,
        "summary": summary,
        "api_calls": 3,
        "duration_seconds": 5.0,
    }


def _verifier(idx=0, verdict="VERDICT: verified\nRead /tmp/out.txt: 3 rows present."):
    return {
        "task_index": idx,
        "status": "completed",
        "summary": verdict,
        "api_calls": 2,
        "duration_seconds": 2.0,
        "_child_cost_usd": 0.01,
    }


@patch("tools.delegate_tool._build_child_agent", return_value=MagicMock())
class TestVerificationWave(unittest.TestCase):
    @patch("tools.delegate_tool._run_single_child")
    def test_verify_off_by_default(self, mock_run, _mock_build):
        mock_run.return_value = _primary()
        parent = _make_mock_parent()
        result = json.loads(delegate_task(goal="do thing", parent_agent=parent))
        self.assertNotIn("verification", result["results"][0])
        self.assertEqual(mock_run.call_count, 1)

    @patch("tools.delegate_tool._run_single_child")
    def test_verified_task_gets_verdict(self, mock_run, _mock_build):
        mock_run.side_effect = [_primary(), _verifier()]
        parent = _make_mock_parent()
        result = json.loads(
            delegate_task(goal="do thing", verify=True, parent_agent=parent)
        )
        entry = result["results"][0]
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(entry["verification"]["verdict"], "VERDICT: verified")
        self.assertIn("[adversarial verification]", entry["summary"])
        self.assertIn("VERDICT: verified", entry["summary"])

    @patch("tools.delegate_tool._run_single_child")
    def test_refuted_verdict_surfaces(self, mock_run, _mock_build):
        mock_run.side_effect = [
            _primary(),
            _verifier(verdict="VERDICT: refuted — /tmp/out.txt does not exist"),
        ]
        parent = _make_mock_parent()
        result = json.loads(
            delegate_task(goal="do thing", verify=True, parent_agent=parent)
        )
        entry = result["results"][0]
        self.assertTrue(entry["verification"]["verdict"].startswith("VERDICT: refuted"))
        self.assertIn("does not exist", entry["summary"])

    @patch("tools.delegate_tool._run_single_child")
    def test_failed_task_not_verified(self, mock_run, _mock_build):
        mock_run.return_value = _primary(status="failed", summary=None)
        parent = _make_mock_parent()
        result = json.loads(
            delegate_task(goal="do thing", verify=True, parent_agent=parent)
        )
        self.assertEqual(mock_run.call_count, 1)
        self.assertNotIn("verification", result["results"][0])

    @patch("tools.delegate_tool._run_single_child")
    def test_verifier_crash_yields_unverifiable(self, mock_run, _mock_build):
        calls = {"n": 0}

        def side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _primary()
            raise RuntimeError("verifier exploded")

        mock_run.side_effect = side_effect
        parent = _make_mock_parent()
        result = json.loads(
            delegate_task(goal="do thing", verify=True, parent_agent=parent)
        )
        entry = result["results"][0]
        self.assertIn("unverifiable", entry["verification"]["verdict"])

    @patch("tools.delegate_tool._run_single_child")
    def test_batch_verifies_each_completed_task(self, mock_run, _mock_build):
        outcomes = {
            "primaries": [_primary(0), _primary(1, summary="Built the site")],
            "verifiers": [
                _verifier(0),
                _verifier(1, verdict="VERDICT: refuted — build dir is empty"),
            ],
        }
        lock = threading.Lock()

        def side_effect(task_index=0, **kwargs):
            with lock:
                if outcomes["primaries"]:
                    for i, p in enumerate(outcomes["primaries"]):
                        if p["task_index"] == task_index:
                            return outcomes["primaries"].pop(i)
                for i, v in enumerate(outcomes["verifiers"]):
                    if v["task_index"] == task_index:
                        return outcomes["verifiers"].pop(i)
            raise AssertionError(f"unexpected call for task {task_index}")

        mock_run.side_effect = side_effect
        parent = _make_mock_parent()
        result = json.loads(
            delegate_task(
                tasks=[{"goal": "task A"}, {"goal": "task B"}],
                verify=True,
                parent_agent=parent,
            )
        )
        self.assertEqual(mock_run.call_count, 4)
        verdicts = [e["verification"]["verdict"] for e in result["results"]]
        self.assertEqual(verdicts[0], "VERDICT: verified")
        self.assertTrue(verdicts[1].startswith("VERDICT: refuted"))

    @patch("tools.delegate_tool._run_single_child")
    def test_verifier_goal_embeds_task_and_claim(self, mock_run, mock_build):
        mock_run.side_effect = [_primary(summary="Fixed the bug in api.py"), _verifier()]
        parent = _make_mock_parent()
        delegate_task(goal="fix the bug", verify=True, parent_agent=parent)
        # Second _build_child_agent call is the verifier.
        verifier_goal = mock_build.call_args_list[1].kwargs["goal"]
        self.assertIn("adversarial verifier", verifier_goal)
        self.assertIn("fix the bug", verifier_goal)
        self.assertIn("Fixed the bug in api.py", verifier_goal)
        self.assertIn("VERDICT:", verifier_goal)


class TestVerifierGoal(unittest.TestCase):
    def test_prompt_structure(self):
        goal = _build_verifier_goal("build the docs", "Docs built at site/")
        self.assertIn("--- ORIGINAL TASK ---", goal)
        self.assertIn("build the docs", goal)
        self.assertIn("--- CLAIMED OUTCOME ---", goal)
        self.assertIn("Docs built at site/", goal)
        self.assertIn("REFUTE", goal)


if __name__ == "__main__":
    unittest.main()
