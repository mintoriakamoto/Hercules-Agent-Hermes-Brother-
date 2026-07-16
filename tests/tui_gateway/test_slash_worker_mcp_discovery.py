"""Integration coverage for profile-local MCP discovery in slash workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import textwrap
import threading
import time

import pytest
import yaml

pytest.importorskip("mcp.server.fastmcp")


def test_profile_local_mcp_tool_is_visible_in_slash_worker(tmp_path):
    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    marker = "profile-local-61922"
    server = tmp_path / "fastmcp_probe.py"
    server.write_text(
        textwrap.dedent(
            f"""
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("profileprobe")

            @mcp.tool()
            def hercules_61922_profile_probe() -> str:
                return {marker!r}

            if __name__ == "__main__":
                mcp.run(transport="stdio")
            """
        ),
        encoding="utf-8",
    )
    (profile_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "profileprobe": {
                        "enabled": True,
                        "command": sys.executable,
                        "args": [str(server)],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_API_KEY") or key.endswith("_TOKEN"):
            env.pop(key)
    env["HERCULES_HOME"] = str(profile_home)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    env["HERCULES_SLASH_WATCHDOG_GRACE_S"] = "0"
    env["HERCULES_SLASH_WATCHDOG_POLL_S"] = "0.05"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "tui_gateway.slash_worker",
            "--session-key",
            "agent:main:tui:dm:mcp-profile-test",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    output: queue.Queue[str] = queue.Queue()
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        stdout = proc.stdout

        # Drain every stdout line for the lifetime of the test so each repeated
        # /tools poll below can collect its own response.
        def _pump() -> None:
            for line in stdout:
                output.put(line)

        threading.Thread(target=_pump, daemon=True).start()

        # Two independent slownesses stack under CI load: the worker boots the
        # whole app before answering /tools at all (handled by the generous
        # deadline), AND MCP discovery of the profile-local probe server can
        # finish slightly AFTER the worker starts answering — so a single poll
        # can race and see a tool list that doesn't include the probe yet. Poll
        # /tools until the probe tool appears or the deadline elapses: a slow
        # boot or slightly-late discovery isn't a failure; a genuinely missing
        # tool after the full window is.
        marker = "mcp__profileprobe__hercules_61922_profile_probe"
        deadline = time.monotonic() + 60.0
        req_id = 0
        last_output = ""
        found = False
        while time.monotonic() < deadline:
            req_id += 1
            proc.stdin.write(json.dumps({"id": req_id, "command": "/tools"}) + "\n")
            proc.stdin.flush()
            try:
                line = output.get(timeout=max(1.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            response = json.loads(line)
            assert response["ok"] is True
            last_output = response.get("output", "")
            if marker in last_output:
                found = True
                break
            time.sleep(0.5)  # let discovery settle, then re-poll
        assert found, (
            f"profile-local MCP tool {marker!r} not visible within 60s; "
            f"last /tools output: {last_output!r}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
