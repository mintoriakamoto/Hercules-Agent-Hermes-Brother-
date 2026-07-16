"""Effectiveness annotation in skills_list.

The skill listing surfaces the same outcome telemetry the curator uses — so
the agent can preferentially reach for skills that have actually worked. Fields
are conditional (only skills with a real signal are annotated); a net-positive
track record earns a `proven` flag and a top-level pointer. Ordering is never
changed.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import tools.skills_tool as skills_tool_module
from tools.skills_tool import skills_list


def _make_skill(skills_dir, name, category=None):
    skill_dir = (skills_dir / category / name) if category else (skills_dir / name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Description for {name}.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _skill_by_name(result, name):
    return next(s for s in result["skills"] if s["name"] == name)


def _list_with_usage(tmp_path, monkeypatch, usage):
    monkeypatch.setattr("tools.skill_usage.load_usage", lambda: usage)
    with patch.object(skills_tool_module, "SKILLS_DIR", tmp_path):
        for name in usage:
            _make_skill(tmp_path, name)
        raw = skills_list()
    return json.loads(raw)


def test_proven_skill_is_flagged(tmp_path, monkeypatch):
    result = _list_with_usage(tmp_path, monkeypatch, {
        "winner": {"use_count": 6, "helpful_count": 4, "unhelpful_count": 1},  # net +3
    })
    winner = _skill_by_name(result, "winner")
    assert winner["proven"] is True
    assert winner["net_effectiveness"] == 3
    assert winner["use_count"] == 6
    assert result["proven"] == ["winner"]
    assert "proven" in result["hint"]


def test_below_threshold_is_annotated_but_not_proven(tmp_path, monkeypatch):
    result = _list_with_usage(tmp_path, monkeypatch, {
        "okay": {"use_count": 3, "helpful_count": 1, "unhelpful_count": 0},  # net +1
    })
    okay = _skill_by_name(result, "okay")
    assert okay["net_effectiveness"] == 1
    assert "proven" not in okay
    assert "proven" not in result  # no top-level pointer when nothing qualifies


def test_used_but_unrated_skill_gets_use_count_only(tmp_path, monkeypatch):
    result = _list_with_usage(tmp_path, monkeypatch, {
        "busy": {"use_count": 9, "helpful_count": 0, "unhelpful_count": 0},
    })
    busy = _skill_by_name(result, "busy")
    assert busy["use_count"] == 9
    assert "net_effectiveness" not in busy  # no feedback → no effectiveness field
    assert "proven" not in busy


def test_net_negative_skill_is_annotated_not_proven(tmp_path, monkeypatch):
    result = _list_with_usage(tmp_path, monkeypatch, {
        "misleader": {"use_count": 4, "helpful_count": 0, "unhelpful_count": 3},
    })
    bad = _skill_by_name(result, "misleader")
    assert bad["net_effectiveness"] == -3
    assert "proven" not in bad
    assert "proven" not in result


def test_unused_skill_stays_minimal(tmp_path, monkeypatch):
    # A skill on disk with NO usage record must keep the minimal shape.
    monkeypatch.setattr("tools.skill_usage.load_usage", lambda: {})
    with patch.object(skills_tool_module, "SKILLS_DIR", tmp_path):
        _make_skill(tmp_path, "fresh")
        result = json.loads(skills_list())
    fresh = _skill_by_name(result, "fresh")
    assert set(fresh.keys()) == {"name", "description", "category"}
    assert "proven" not in result


def test_record_without_signal_stays_minimal(tmp_path, monkeypatch):
    # A record exists but has zero use and zero feedback — no annotation.
    result = _list_with_usage(tmp_path, monkeypatch, {
        "ghost": {"use_count": 0, "helpful_count": 0, "unhelpful_count": 0},
    })
    ghost = _skill_by_name(result, "ghost")
    assert set(ghost.keys()) == {"name", "description", "category"}


def test_annotation_is_defensive(tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("usage backend down")

    monkeypatch.setattr("tools.skill_usage.load_usage", _boom)
    with patch.object(skills_tool_module, "SKILLS_DIR", tmp_path):
        _make_skill(tmp_path, "resilient")
        result = json.loads(skills_list())
    assert result["success"] is True
    assert _skill_by_name(result, "resilient")["name"] == "resilient"
    assert "proven" not in result
