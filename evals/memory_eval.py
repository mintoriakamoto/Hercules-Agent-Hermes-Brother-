"""Memory learning-system scorecard.

Scores the holographic memory's learning behaviours end-to-end against scenarios
with known-correct answers, deterministically and without a live LLM:

  * recall_hit_rate        — does semantic/keyword recall surface the right fact?
  * trust_learning         — does a helpfully-rated fact outrank an unrated rival?
  * confidence_calibration — is a proven fact "high" confidence and a fresh one not?
  * association_recall     — does a reinforced Hebbian edge surface via `spread`?
  * outcome_attribution    — does a positive session end lift the recalled facts?

Each metric is in [0, 1]; the aggregate is their mean. Run as a script to print
a scorecard (stamped with the git SHA); the test suite asserts per-metric floors
so a regression in any learning subsystem fails CI.
"""
from __future__ import annotations

import statistics
import tempfile
from pathlib import Path
from typing import Callable, Dict


def _provider(base_dir: Path, name: str):
    from plugins.memory.holographic import HolographicMemoryProvider

    p = HolographicMemoryProvider(config={"db_path": str(base_dir / f"{name}.db")})
    p.initialize(f"eval-{name}")
    return p


def _search(provider, query, **kw):
    import json

    return json.loads(
        provider._handle_fact_store({"action": "search", "query": query, **kw})
    )


def _top_ids(provider, query, k=3):
    return [r["fact_id"] for r in _search(provider, query, limit=k)["results"]]


# ---------------------------------------------------------------------------
# Individual metrics
# ---------------------------------------------------------------------------

def _score_recall(base_dir: Path) -> float:
    """Fraction of queries whose intended fact lands in the top 3."""
    p = _provider(base_dir, "recall")
    try:
        facts = {
            "db": "The production database runs PostgreSQL 16 with streaming replication",
            "fe": "The frontend is built with React and TypeScript and Vite",
            "deploy": "Deployments roll out to the staging environment before production",
            "ci": "The continuous integration pipeline runs on GitHub Actions runners",
            "cache": "User sessions and hot lookups are cached in Redis",
        }
        ids = {k: p._store.add_fact(v) for k, v in facts.items()}
        probes = [
            ("which database do we run in production", ids["db"]),
            ("what framework is the frontend built with", ids["fe"]),
            ("where do deployments roll out first", ids["deploy"]),
            ("what runs our continuous integration pipeline", ids["ci"]),
            ("where are user sessions cached", ids["cache"]),
        ]
        hits = sum(1 for q, want in probes if want in _top_ids(p, q, k=3))
        return hits / len(probes)
    finally:
        p.shutdown()


def _score_trust_learning(base_dir: Path) -> float:
    """A helpfully-rated fact should outrank an unrated same-topic rival."""
    p = _provider(base_dir, "trust")
    try:
        good = p._store.add_fact("The API gateway rate limit is 100 requests per minute")
        rival = p._store.add_fact("The API gateway rate limit is still under review")
        for _ in range(3):
            p._store.record_feedback(good, helpful=True)
        ranked = _top_ids(p, "api gateway rate limit", k=2)
        return 1.0 if ranked and ranked[0] == good else 0.0
    finally:
        p.shutdown()


def _score_confidence_calibration(base_dir: Path) -> float:
    """A proven fact reads high confidence; a fresh unrated one reads lower."""
    p = _provider(base_dir, "confidence")
    try:
        proven = p._store.add_fact("The billing service reconciles invoices nightly at 0200 UTC")
        for _ in range(4):
            p._store.record_feedback(proven, helpful=True)
        fresh = p._store.add_fact("A freshly noted, never-rated detail about webhooks")

        proven_conf = _search(p, "billing service reconciles invoices nightly")["results"]
        fresh_conf = _search(p, "freshly noted webhooks detail")["results"]
        if not proven_conf or not fresh_conf:
            return 0.0
        pc = proven_conf[0].get("confidence", 0.0)
        fc = fresh_conf[0].get("confidence", 0.0)
        score = 0.0
        if proven_conf[0].get("confidence_label") == "high":
            score += 0.5
        if pc - fc >= 0.2:  # clear separation between proven and fresh
            score += 0.5
        return score
    finally:
        p.shutdown()


def _score_association_recall(base_dir: Path) -> float:
    """A reinforced Hebbian edge should surface the partner via `spread`."""
    import json

    p = _provider(base_dir, "assoc")
    try:
        a = p._store.add_fact("The deploy runbook lives in the ops wiki")
        b = p._store.add_fact("The on-call rotation is published in PagerDuty")
        p._store.reinforce_association(a, b, delta=0.6)
        out = json.loads(p._handle_fact_store({"action": "spread", "fact_id": a}))
        return 1.0 if b in {f["fact_id"] for f in out["facts"]} else 0.0
    finally:
        p.shutdown()


def _score_outcome_attribution(base_dir: Path) -> float:
    """A positive session end should lift the trust of the facts it recalled."""
    p = _provider(base_dir, "attr")
    try:
        fid = p._store.add_fact("The load balancer terminates TLS at the edge")
        before = p._store._conn.execute(
            "SELECT trust_score FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["trust_score"]
        _search(p, "load balancer tls edge")  # recall → tracked for attribution
        p.on_session_end([{"role": "user", "content": "thanks, that's exactly right"}])
        after = p._store._conn.execute(
            "SELECT trust_score FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["trust_score"]
        return 1.0 if after > before else 0.0
    finally:
        p.shutdown()


_METRICS: Dict[str, Callable[[Path], float]] = {
    "recall_hit_rate": _score_recall,
    "trust_learning": _score_trust_learning,
    "confidence_calibration": _score_confidence_calibration,
    "association_recall": _score_association_recall,
    "outcome_attribution": _score_outcome_attribution,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_memory_eval(base_dir: "str | Path | None" = None) -> dict:
    """Run every metric and return {"metrics": {...}, "aggregate": float}.

    Each metric runs against its own isolated store under *base_dir* (a fresh
    temp dir when omitted). Metrics are independent — a failure in one is scored
    0.0 rather than aborting the run — so the scorecard is always complete.
    """
    owns_dir = base_dir is None
    base = Path(base_dir or tempfile.mkdtemp(prefix="hercules-mem-eval-"))
    metrics: Dict[str, float] = {}
    try:
        for name, fn in _METRICS.items():
            try:
                metrics[name] = round(float(fn(base)), 4)
            except Exception as exc:  # a broken metric scores 0, never aborts
                metrics[name] = 0.0
                metrics[f"{name}__error"] = str(exc)  # type: ignore[assignment]
    finally:
        if owns_dir:
            import shutil

            shutil.rmtree(base, ignore_errors=True)
    numeric = [v for k, v in metrics.items() if not k.endswith("__error")]
    aggregate = round(statistics.mean(numeric), 4) if numeric else 0.0
    return {"metrics": metrics, "aggregate": aggregate}


def format_scorecard(scorecard: dict, version: str = "") -> str:
    lines = ["Hercules memory learning scorecard"]
    if version:
        lines.append(f"  version: {version}")
    for name, value in scorecard.get("metrics", {}).items():
        if name.endswith("__error"):
            lines.append(f"    ! {name}: {value}")
            continue
        bar = "█" * int(round(float(value) * 20))
        lines.append(f"  {name:24s} {float(value):.3f}  {bar}")
    lines.append(f"  {'AGGREGATE':24s} {scorecard.get('aggregate', 0.0):.3f}")
    return "\n".join(lines)


def _git_sha() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def main() -> None:
    scorecard = run_memory_eval()
    print(format_scorecard(scorecard, version=_git_sha()))


if __name__ == "__main__":
    main()
