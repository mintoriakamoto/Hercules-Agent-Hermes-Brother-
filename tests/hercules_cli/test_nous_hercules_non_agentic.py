"""Tests for the Nous-Hercules-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"hercules"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``hercules-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "hercules" tag namespace.

``is_nous_hercules_non_agentic`` should only match the actual Nous Research
Hercules-3 / Hercules-4 chat family.
"""

from __future__ import annotations

import pytest

from hercules_cli.model_switch import (
    _HERCULES_MODEL_WARNING,
    _check_hercules_model_warning,
    is_nous_hercules_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/Hercules-3-Llama-3.1-70B",
        "NousResearch/Hercules-3-Llama-3.1-405B",
        "hercules-3",
        "Hercules-3",
        "hercules-4",
        "hercules-4-405b",
        "hercules_4_70b",
        "openrouter/hercules3:70b",
        "openrouter/nousresearch/hercules-4-405b",
        "NousResearch/Hercules3",
        "hercules-3.1",
    ],
)
def test_matches_real_nous_hercules_chat_models(model_name: str) -> None:
    assert is_nous_hercules_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Hercules 3/4"
    )
    assert _check_hercules_model_warning(model_name) == _HERCULES_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "hercules-brain:qwen3-14b-ctx16k",
        "hercules-brain:qwen3-14b-ctx32k",
        "hercules-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat Hercules models we don't warn about
        "hercules-llm-2",
        "hercules2-pro",
        "nous-hercules-2-mistral",
        # Edge cases
        "",
        "hercules",  # bare "hercules" isn't the 3/4 family
        "hercules-brain",
        "brain-hercules-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_hercules_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous Hercules 3/4"
    )
    assert _check_hercules_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_hercules_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_hercules_model_warning("") == ""
