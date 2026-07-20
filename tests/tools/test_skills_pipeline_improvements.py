"""Tests for the skills-pipeline improvements: near-duplicate creation guard,
skills_list relevance ranking, and the bounded skills-index injection."""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from tools.skill_manager_tool import _create_skill, _find_similar_skills


@contextmanager
def _skill_dir(tmp_path):
    with patch("tools.skill_manager_tool.SKILLS_DIR", tmp_path), \
         patch("agent.skill_utils.get_all_skills_dirs", return_value=[tmp_path]):
        yield


def _content(name, description):
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nStep 1.\n"


class TestNearDuplicateGuard:
    def test_similar_description_blocked(self, tmp_path):
        with _skill_dir(tmp_path):
            first = _create_skill(
                "deploy-docker", _content("deploy-docker", "Deploy services with docker compose on a remote host")
            )
            assert first["success"] is True
            second = _create_skill(
                "docker-deployment", _content("docker-deployment", "Deploy services with docker compose to remote hosts")
            )
        assert second["success"] is False
        assert "near-duplicate" in second["error"]
        assert second["similar_skills"][0]["name"] == "deploy-docker"

    def test_similar_name_blocked(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("fix-ci-pipeline", _content("fix-ci-pipeline", "Repair continuous integration failures"))
            second = _create_skill(
                "fix-ci-pipelines", _content("fix-ci-pipelines", "Something about totally unrelated gardening topics entirely")
            )
        assert second["success"] is False
        assert "near-duplicate" in second["error"]

    def test_force_overrides_guard(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("deploy-docker", _content("deploy-docker", "Deploy services with docker compose"))
            second = _create_skill(
                "docker-deployment",
                _content("docker-deployment", "Deploy services with docker compose"),
                force=True,
            )
        assert second["success"] is True

    def test_distinct_skill_passes(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("deploy-docker", _content("deploy-docker", "Deploy services with docker compose"))
            second = _create_skill(
                "write-blog-post", _content("write-blog-post", "Draft and publish articles for the personal blog")
            )
        assert second["success"] is True

    def test_find_similar_empty_when_no_skills(self, tmp_path):
        with _skill_dir(tmp_path):
            assert _find_similar_skills("anything", _content("anything", "whatever text here")) == []


class TestSkillsListQuery:
    def _seed(self, tmp_path):
        for name, desc in [
            ("deploy-docker", "Deploy services with docker compose on remote hosts"),
            ("write-blog-post", "Draft and publish blog articles"),
            ("fix-ci", "Repair continuous integration failures in github actions"),
        ]:
            d = tmp_path / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(_content(name, desc), encoding="utf-8")

    def test_query_ranks_relevant_first_and_filters(self, tmp_path, monkeypatch):
        from tools import skills_tool

        self._seed(tmp_path)
        monkeypatch.setattr(skills_tool, "SKILLS_DIR", tmp_path, raising=False)
        with patch("agent.skill_utils.get_all_skills_dirs", return_value=[tmp_path]):
            payload = json.loads(skills_tool.skills_list(query="deploy docker compose"))
        names = [s["name"] for s in payload["skills"]]
        assert names[0] == "deploy-docker"
        assert "write-blog-post" not in names

    def test_no_query_keeps_full_listing(self, tmp_path, monkeypatch):
        from tools import skills_tool

        self._seed(tmp_path)
        monkeypatch.setattr(skills_tool, "SKILLS_DIR", tmp_path, raising=False)
        with patch("agent.skill_utils.get_all_skills_dirs", return_value=[tmp_path]):
            payload = json.loads(skills_tool.skills_list())
        assert payload["count"] == 3

    def test_rank_helper_scores_name_over_description(self):
        from tools.skills_tool import _rank_skills_by_query

        skills = [
            {"name": "other", "description": "mentions docker once", "category": ""},
            {"name": "docker-deploy", "description": "ship it", "category": ""},
        ]
        ranked = _rank_skills_by_query(skills, "docker")
        assert ranked[0]["name"] == "docker-deploy"


class TestBoundedIndexInjection:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        from agent.prompt_builder import clear_skills_system_prompt_cache

        clear_skills_system_prompt_cache(clear_snapshot=True)
        yield
        clear_skills_system_prompt_cache(clear_snapshot=True)

    def _seed_many(self, tmp_path, n):
        for i in range(n):
            d = tmp_path / "skills" / f"skill-{i:03d}"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                _content(f"skill-{i:03d}", f"Long description for skill number {i} " + "words " * 30),
                encoding="utf-8",
            )

    def test_index_capped_with_names_only_overflow(self, tmp_path, monkeypatch):
        from agent.prompt_builder import build_skills_system_prompt

        monkeypatch.setenv("HERCULES_HOME", str(tmp_path))
        self._seed_many(tmp_path, 30)
        import hercules_cli.config as cfgmod

        monkeypatch.setattr(
            cfgmod, "load_config_readonly",
            lambda: {"skills": {"index_max_chars": 800}},
        )
        result = build_skills_system_prompt()
        assert "more skills, descriptions" in result
        assert "skills_list(query=" in result
        # every skill remains discoverable by name even past the cap
        assert "skill-029" in result

    def test_no_cap_when_disabled(self, tmp_path, monkeypatch):
        from agent.prompt_builder import build_skills_system_prompt

        monkeypatch.setenv("HERCULES_HOME", str(tmp_path))
        self._seed_many(tmp_path, 30)
        import hercules_cli.config as cfgmod

        monkeypatch.setattr(
            cfgmod, "load_config_readonly",
            lambda: {"skills": {"index_max_chars": 0}},
        )
        result = build_skills_system_prompt()
        assert "more skills, descriptions" not in result
        assert "skill-029" in result
