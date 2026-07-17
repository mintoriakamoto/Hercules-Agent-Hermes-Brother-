"""The memory scorecard doubles as a CI regression net for the learning systems.

If any learning subsystem regresses (recall stops surfacing the right fact,
trusted facts stop outranking rivals, confidence stops separating proven from
fresh, associations stop spreading, or a good session stops reinforcing), the
corresponding metric drops below its floor and this fails — turning the whole
self-improvement stack into something measured, not merely asserted per-unit.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")  # the retriever's HRR path needs numpy

from evals.memory_eval import format_scorecard, run_memory_eval


def test_memory_eval_meets_quality_floor(tmp_path):
    sc = run_memory_eval(tmp_path)
    m = sc["metrics"]

    # No metric crashed (a crash records a "<name>__error" key and scores 0).
    errors = {k: v for k, v in m.items() if k.endswith("__error")}
    assert not errors, f"eval metrics errored: {errors}"

    # Per-metric floors — the regression net for each learning subsystem.
    assert m["recall_hit_rate"] >= 0.8            # recall surfaces the right fact
    assert m["trust_learning"] == 1.0             # rated facts outrank rivals
    assert m["confidence_calibration"] == 1.0     # proven reads high, fresh doesn't
    assert m["association_recall"] == 1.0         # reinforced edges spread
    assert m["outcome_attribution"] == 1.0        # good sessions reinforce recall
    assert sc["aggregate"] >= 0.9


def test_scorecard_shape_and_formatting(tmp_path):
    sc = run_memory_eval(tmp_path)
    assert set(sc) == {"metrics", "aggregate"}
    assert 0.0 <= sc["aggregate"] <= 1.0
    for name in ("recall_hit_rate", "trust_learning", "confidence_calibration",
                 "association_recall", "outcome_attribution"):
        assert 0.0 <= sc["metrics"][name] <= 1.0
    rendered = format_scorecard(sc, version="testsha")
    assert "AGGREGATE" in rendered
    assert "testsha" in rendered


def test_broken_metric_scores_zero_not_crash(monkeypatch, tmp_path):
    """A metric that raises must be scored 0.0 and recorded, never abort the run."""
    import evals.memory_eval as ev

    def _boom(_base):
        raise RuntimeError("simulated subsystem failure")

    monkeypatch.setitem(ev._METRICS, "recall_hit_rate", _boom)
    sc = run_memory_eval(tmp_path)
    assert sc["metrics"]["recall_hit_rate"] == 0.0
    assert "recall_hit_rate__error" in sc["metrics"]
    # The other metrics still ran and the aggregate still computed.
    assert sc["metrics"]["association_recall"] == 1.0
    assert 0.0 <= sc["aggregate"] <= 1.0
