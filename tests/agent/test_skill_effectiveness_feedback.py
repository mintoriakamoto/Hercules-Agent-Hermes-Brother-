"""Tests for the skill effectiveness feedback loop.

Covers the quality signal the curator weighs alongside recency:
  * skill_usage.bump_feedback / net_effectiveness telemetry,
  * curator archive-grace protection for proven-helpful skills,
  * the skill_manage(action="feedback") tool surface.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _write_skill(skills_dir: Path, name: str) -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated HERCULES_HOME + freshly reloaded usage/curator/tool modules."""
    home = tmp_path / ".hercules"
    (home / "skills").mkdir(parents=True)
    monkeypatch.setenv("HERCULES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    import tools.skill_usage as skill_usage
    import agent.curator as curator
    import tools.skill_manager_tool as skill_manager_tool

    importlib.reload(skill_usage)
    importlib.reload(curator)
    importlib.reload(skill_manager_tool)
    return home, skill_usage, curator, skill_manager_tool


# ---------------------------------------------------------------------------
# Telemetry: bump_feedback + net_effectiveness
# ---------------------------------------------------------------------------

def test_empty_record_carries_effectiveness_fields(env):
    _home, skill_usage, _curator, _tool = env
    rec = skill_usage._empty_record()
    assert rec["helpful_count"] == 0
    assert rec["unhelpful_count"] == 0
    assert rec["last_feedback_at"] is None


def test_bump_feedback_increments_and_stamps(env):
    _home, skill_usage, _curator, _tool = env
    skill_usage.bump_feedback("alpha", helpful=True)
    skill_usage.bump_feedback("alpha", helpful=True)
    skill_usage.bump_feedback("alpha", helpful=False)

    rec = skill_usage.get_record("alpha")
    assert rec["helpful_count"] == 2
    assert rec["unhelpful_count"] == 1
    assert rec["last_feedback_at"] is not None
    assert skill_usage.net_effectiveness(rec) == 1


def test_net_effectiveness_is_defensive(env):
    _home, skill_usage, _curator, _tool = env
    assert skill_usage.net_effectiveness({}) == 0
    assert skill_usage.net_effectiveness({"helpful_count": "x", "unhelpful_count": None}) == 0
    assert skill_usage.net_effectiveness({"helpful_count": 5}) == 5


def test_feedback_is_not_counted_as_recency_activity(env):
    """A rating is a judgement about a past use, not a fresh use — it must not
    reset the inactivity clock, or a no-op skill could shield itself by being
    rated unhelpful."""
    _home, skill_usage, _curator, _tool = env
    skill_usage.bump_feedback("alpha", helpful=True)
    rec = skill_usage.get_record("alpha")
    # activity_count only spans use/view/patch — feedback is excluded.
    assert skill_usage.activity_count(rec) == 0
    assert skill_usage.latest_activity_at(rec) is None


# ---------------------------------------------------------------------------
# Curator: proven-helpful skills resist idle-archival
# ---------------------------------------------------------------------------

def test_proven_helpful_skill_resists_idle_archival(env, monkeypatch):
    home, skill_usage, curator, _tool = env
    skills_dir = home / "skills"
    _write_skill(skills_dir, "proven")
    _write_skill(skills_dir, "neutral")

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    old = (now - timedelta(days=200)).isoformat()
    # Both idle 200 days (well past a 90-day archive window), both actually used.
    skill_usage.save_usage({
        "proven": {
            "created_by": "agent",
            "created_at": old,
            "last_used_at": old,
            "use_count": 5,
            "helpful_count": 3,   # net +3 → past the keep threshold (2)
            "unhelpful_count": 0,
            "state": "active",
        },
        "neutral": {
            "created_by": "agent",
            "created_at": old,
            "last_used_at": old,
            "use_count": 5,
            "state": "active",
        },
    })
    monkeypatch.setattr(curator, "get_stale_after_days", lambda: 30)
    monkeypatch.setattr(curator, "get_archive_after_days", lambda: 90)

    counts = curator.apply_automatic_transitions(now=now)

    # Proven skill: 200d idle < 3×90d grace → not archived, only demoted to stale.
    assert skill_usage.get_record("proven")["state"] == "stale"
    # Neutral skill: 200d idle ≥ 90d → archived.
    assert skill_usage.get_record("neutral")["state"] == "archived"
    assert counts["archived"] == 1
    assert counts["marked_stale"] == 1


def test_proven_helpful_skill_still_archives_past_extended_window(env, monkeypatch):
    """Protection is bounded, not permanent — beyond 3× the window it archives."""
    home, skill_usage, curator, _tool = env
    skills_dir = home / "skills"
    _write_skill(skills_dir, "proven-but-ancient")

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    very_old = (now - timedelta(days=400)).isoformat()  # > 3×90 = 270
    skill_usage.save_usage({
        "proven-but-ancient": {
            "created_by": "agent",
            "created_at": very_old,
            "last_used_at": very_old,
            "use_count": 5,
            "helpful_count": 4,
            "unhelpful_count": 0,
            "state": "active",
        },
    })
    monkeypatch.setattr(curator, "get_stale_after_days", lambda: 30)
    monkeypatch.setattr(curator, "get_archive_after_days", lambda: 90)

    curator.apply_automatic_transitions(now=now)
    assert skill_usage.get_record("proven-but-ancient")["state"] == "archived"


def test_single_helpful_rating_does_not_shield(env, monkeypatch):
    """One stray helpful rating (net +1, below the +2 threshold) must not defer
    archival — a track record should, a fluke shouldn't."""
    home, skill_usage, curator, _tool = env
    skills_dir = home / "skills"
    _write_skill(skills_dir, "barely-rated")

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    old = (now - timedelta(days=200)).isoformat()
    skill_usage.save_usage({
        "barely-rated": {
            "created_by": "agent",
            "created_at": old,
            "last_used_at": old,
            "use_count": 5,
            "helpful_count": 1,
            "unhelpful_count": 0,
            "state": "active",
        },
    })
    monkeypatch.setattr(curator, "get_stale_after_days", lambda: 30)
    monkeypatch.setattr(curator, "get_archive_after_days", lambda: 90)

    curator.apply_automatic_transitions(now=now)
    assert skill_usage.get_record("barely-rated")["state"] == "archived"


# ---------------------------------------------------------------------------
# Curator: proven-harmful skills are archived sooner (symmetric to the grace)
# ---------------------------------------------------------------------------

def test_proven_harmful_skill_archives_sooner_than_neutral(env, monkeypatch):
    """A net-harmful skill (misled more than it helped) leaves context on a
    shortened window; a neutral skill at the same idle does not yet."""
    home, skill_usage, curator, _tool = env
    skills_dir = home / "skills"
    _write_skill(skills_dir, "harmful")
    _write_skill(skills_dir, "neutral")

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    # Idle 50 days: past the penalized 45d window (90//2) but shy of 90d.
    idle = (now - timedelta(days=50)).isoformat()
    skill_usage.save_usage({
        "harmful": {
            "created_by": "agent",
            "created_at": idle,
            "last_used_at": idle,
            "use_count": 5,
            "helpful_count": 1,
            "unhelpful_count": 4,   # net −3 → past the prune threshold (−2)
            "state": "active",
        },
        "neutral": {
            "created_by": "agent",
            "created_at": idle,
            "last_used_at": idle,
            "use_count": 5,
            "state": "active",
        },
    })
    monkeypatch.setattr(curator, "get_stale_after_days", lambda: 30)
    monkeypatch.setattr(curator, "get_archive_after_days", lambda: 90)

    counts = curator.apply_automatic_transitions(now=now)

    # Harmful: 50d ≥ 45d penalized window → archived.
    assert skill_usage.get_record("harmful")["state"] == "archived"
    # Neutral: 50d < 90d → only demoted to stale, still in context.
    assert skill_usage.get_record("neutral")["state"] == "stale"
    assert counts["archived"] == 1
    assert counts["marked_stale"] == 1


def test_harmful_acceleration_never_precedes_staleness(env, monkeypatch):
    """The penalized window is floored at stale_after_days: a harmful skill is
    never archived before it would even have been demoted to stale."""
    home, skill_usage, curator, _tool = env
    skills_dir = home / "skills"
    _write_skill(skills_dir, "harmful-young")

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    # Idle 27d. With stale=30, archive=50: raw penalty 50//2=25, but floored to
    # 30 — so 27d is inside the floor and must NOT archive (nor even go stale).
    idle = (now - timedelta(days=27)).isoformat()
    skill_usage.save_usage({
        "harmful-young": {
            "created_by": "agent",
            "created_at": idle,
            "last_used_at": idle,
            "use_count": 5,
            "helpful_count": 0,
            "unhelpful_count": 3,   # net −3
            "state": "active",
        },
    })
    monkeypatch.setattr(curator, "get_stale_after_days", lambda: 30)
    monkeypatch.setattr(curator, "get_archive_after_days", lambda: 50)

    curator.apply_automatic_transitions(now=now)
    # Not archived (floor protected it) and not stale (27 < 30).
    assert skill_usage.get_record("harmful-young")["state"] == "active"


def test_single_unhelpful_rating_does_not_accelerate(env, monkeypatch):
    """One stray downvote (net −1, above the −2 prune threshold) leaves the
    skill on the normal archive schedule — a track record prunes, a fluke won't."""
    home, skill_usage, curator, _tool = env
    skills_dir = home / "skills"
    _write_skill(skills_dir, "barely-downvoted")

    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    idle = (now - timedelta(days=50)).isoformat()
    skill_usage.save_usage({
        "barely-downvoted": {
            "created_by": "agent",
            "created_at": idle,
            "last_used_at": idle,
            "use_count": 5,
            "helpful_count": 0,
            "unhelpful_count": 1,   # net −1 → not past the −2 threshold
            "state": "active",
        },
    })
    monkeypatch.setattr(curator, "get_stale_after_days", lambda: 30)
    monkeypatch.setattr(curator, "get_archive_after_days", lambda: 90)

    curator.apply_automatic_transitions(now=now)
    # 50d < 90d normal window → not archived, only stale.
    assert skill_usage.get_record("barely-downvoted")["state"] == "stale"


# ---------------------------------------------------------------------------
# Tool surface: skill_manage(action="feedback")
# ---------------------------------------------------------------------------

def test_skill_manage_feedback_records_signal(env):
    home, skill_usage, _curator, tool = env
    _write_skill(home / "skills", "deploy-recipe")

    out = json.loads(tool.skill_manage(action="feedback", name="deploy-recipe", helpful=True))
    assert out["success"] is True
    assert out["feedback"] == "helpful"

    rec = skill_usage.get_record("deploy-recipe")
    assert rec["helpful_count"] == 1
    assert rec["unhelpful_count"] == 0

    tool.skill_manage(action="feedback", name="deploy-recipe", helpful=False)
    assert skill_usage.get_record("deploy-recipe")["unhelpful_count"] == 1


def test_skill_manage_feedback_requires_helpful_bool(env):
    home, _skill_usage, _curator, tool = env
    _write_skill(home / "skills", "deploy-recipe")

    out = json.loads(tool.skill_manage(action="feedback", name="deploy-recipe"))
    assert out["success"] is False
    assert "helpful" in out["error"].lower()


def test_skill_manage_feedback_unknown_skill_errors(env):
    _home, _skill_usage, _curator, tool = env
    out = json.loads(tool.skill_manage(action="feedback", name="does-not-exist", helpful=True))
    assert out["success"] is False
    assert "not found" in out["error"].lower()
