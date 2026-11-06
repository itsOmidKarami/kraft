"""The fake agents' markers (`fixtures/fake-claude.sh`, `tests/support/fake_agent.py`):
`KRAFT_SUBAGENTS`' two sub-agents in the shapes claude 2.1.316 writes, so the
log's agent labels and filter have something to show in a dev instance and the
e2e mocks; and no marker or field read from a skill's method."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from support import fake_agent

from kraft import skill

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"
FAKE_AGENT = Path(__file__).resolve().parent / "support" / "fake_agent.py"


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


def _method(text: str) -> str:
    # Framed as Kraft frames it, so renaming either delimiter fails these rows.
    return skill.HEADING + text + skill.UNAVAILABLE


@pytest.mark.parametrize(
    ("shell", "prompt", "context", "exit_code"),
    [
        ("sh", "t", "Node: n" + _method("Mention KRAFT_FAIL here."), 0),
        ("bash", "t", "Node: n" + _method("Mention KRAFT_FAIL here."), 0),
        # channel: prompt -- the method is in the instruction itself.
        ("sh", "t" + _method("KRAFT_FAIL") + "\nand after it", "Node: n", 0),
        ("sh", "t" + _method("a") + _method("KRAFT_FAIL"), "Node: n", 0),
        ("sh", "t" + _method("a") + " KRAFT_FAIL" + _method("b"), "Node: n", 3),
        ("sh", "t KRAFT_FAIL", "Node: n" + _method("nothing"), 3),
    ],
    ids=[
        "in-the-context-under-dash",
        "in-the-context-under-bash",
        "in-the-instruction",
        "in-the-second-of-two-methods",
        "between-two-methods",
        "in-the-title",
    ],
)
def test_a_marker_in_a_skills_method_steers_nothing(tmp_path, shell, prompt, context, exit_code):
    """A skill's method is the same text for every work item: a marker in it
    would steer every test that walks a chain, so the fake reads none there."""
    done = subprocess.run(
        [shell, str(FAKE), "-p", prompt, "--append-system-prompt", context],
        cwd=tmp_path,
        env={**os.environ, "KRAFT_RESULT_PATH": str(tmp_path / "result.json")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == exit_code, done.stderr


@pytest.mark.parametrize("fake", ["sh", "python"])
@pytest.mark.parametrize(
    ("context", "written"),
    [
        (_method("Write your plan to {doc}, creating that directory."), False),
        ("Write your plan to {doc}, creating that directory." + _method("x"), True),
    ],
    ids=["named-only-in-a-method", "named-by-the-contract"],
)
def test_a_fake_takes_no_field_from_a_method(tmp_path, fake, context, written):
    doc = tmp_path / "docs" / "plan.md"
    command = ["sh", str(FAKE)] if fake == "sh" else [sys.executable, str(FAKE_AGENT)]
    done = subprocess.run(
        # A work item, which fake-claude.sh needs before it writes a document.
        [*command, "--append-system-prompt", "Work item: w\n" + context.format(doc=doc), "-p", "t"],
        cwd=tmp_path,
        env={
            **os.environ,
            "KRAFT_FAKE_AGENT": "noop",
            "KRAFT_RESULT_PATH": str(tmp_path / "r.json"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert doc.exists() == written


@pytest.mark.parametrize(
    "argv",
    [
        ["fake", "-c", "developer_instructions=Node: n" + _method("Hook point: h"), "-p", "t"],
        ["fake", "-p", "Node: n" + _method("Hook point: h") + "\n\nt"],
        ["fake", "--append-system-prompt", "Node: n" + _method("Hook point: h"), "-p", "t"],
    ],
    ids=["codex-developer-instructions", "the-prompt-channel", "claude-system-prompt"],
)
def test_the_python_fake_reads_no_field_from_a_method_on_any_channel(argv):
    assert fake_agent._ctx_fields(argv) == {"Node": "n"}


@pytest.mark.parametrize(
    ("instruction", "target"),
    [("t\nreplayed onto abc123\n", "abc123"), ("t" + _method("replayed onto abc123"), None)],
    ids=["in-the-instruction", "only-in-a-method"],
)
def test_the_rebase_target_is_never_read_from_a_method(instruction, target):
    assert fake_agent._rebase_target(["fake", "-p", instruction]) == target
