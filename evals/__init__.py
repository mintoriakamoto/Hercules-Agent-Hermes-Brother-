"""Offline evaluation harness for Hercules' learning subsystems.

Turns "the agent improves itself" from a story into a measured, version-tracked
number. Each eval scores a deterministic subsystem (no live LLM required, so it
runs in CI) on synthetic-but-realistic scenarios with known-correct outcomes,
and emits a scorecard. Tracked across versions, the aggregate is both a
progress curve and a regression net.
"""
