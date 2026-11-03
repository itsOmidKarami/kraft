"""The fake agent's `KRAFT_SUBAGENTS` marker (`fixtures/fake-claude.sh`): two
sub-agents in the shapes claude 2.1.316 writes, so the log's agent labels and
filter have something to show in a dev instance and the e2e mocks."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"


def _run(tmp_path: Path, title: str, **env: str) -> subprocess.Popen:
    # An empty cwd: the default `fix` mode only edits a `calc.py` when one exists.
    return subprocess.Popen(
        ["sh", str(FAKE), "-p", title],
        cwd=tmp_path,
        env={**os.environ, "KRAFT_RESULT_PATH": str(tmp_path / "result.json"), **env},
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def test_subagents_marker_prints_two_subagents_with_result_last(tmp_path):
    out = _run(tmp_path, "t KRAFT_SUBAGENTS").communicate(timeout=30)[0]
    lines = [json.loads(line) for line in out.splitlines()]

    started = {x["task_id"]: x for x in lines if x.get("subtype") == "task_started"}
    assert len(started) == 2
    assert {x["task_type"] for x in started.values()} == {"local_agent"}
    said = [x for x in lines if "agent_id" in x]
    assert said
    for line in said:
        assert line["parent_tool_use_id"] == started[line["agent_id"]]["tool_use_id"]
    ended = {x["task_id"]: x["status"] for x in lines if x.get("subtype") == "task_notification"}
    assert sorted(ended.values()) == ["completed", "failed"]
    assert set(ended) == set(started)
    assert lines[0]["subtype"] == "init"
    assert lines[-1]["type"] == "result"


def test_subagents_with_hang_prints_the_starts_before_it_sleeps(tmp_path):
    proc = _run(tmp_path, "t KRAFT_SUBAGENTS KRAFT_HANG", KRAFT_FAKE_CLAUDE_HANG="30")
    try:
        time.sleep(3)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
    out = proc.communicate(timeout=10)[0]
    subtypes = [json.loads(line).get("subtype") for line in out.splitlines()]
    assert subtypes == ["init", "task_started", "task_started"]
