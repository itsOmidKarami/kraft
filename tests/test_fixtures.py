from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"


def _run(cwd, env_extra, ctx="ctx"):
    env = {**os.environ, "KRAFT_RESULT_PATH": str(cwd / "result.json"), **env_extra}
    return subprocess.run(
        [
            str(_SCRIPT),
            "-p",
            "fix it",
            "--append-system-prompt",
            ctx,
            "--output-format",
            "stream-json",
            "--verbose",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def test_fix_mode_patches_calc_and_writes_result(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "fix"})
    assert proc.returncode == 0
    assert "a + b" in (tmp_path / "calc.py").read_text()
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}
    assert json.loads(proc.stdout.strip().splitlines()[-1])["is_error"] is False


def test_noop_mode_leaves_calc_but_writes_result(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "noop"})
    assert proc.returncode == 0
    assert "a - b" in (tmp_path / "calc.py").read_text()
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}
    assert json.loads(proc.stdout.strip().splitlines()[-1])["is_error"] is False


def test_slow_mode_delays(tmp_path):
    import time

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    start = time.monotonic()
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "1"})
    assert proc.returncode == 0
    assert time.monotonic() - start >= 1.0
    assert "a + b" in (tmp_path / "calc.py").read_text()
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}
    assert json.loads(proc.stdout.strip().splitlines()[-1])["is_error"] is False


def test_status_defaults_to_done(tmp_path):
    """No knob set: every existing caller must see today's behaviour unchanged."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "fix"})
    assert proc.returncode == 0
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


def test_status_knob_reports_done_with_concerns_and_the_concerns_text(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(
        tmp_path,
        {
            "KRAFT_FAKE_CLAUDE": "noop",
            "KRAFT_FAKE_CLAUDE_STATUS": "done_with_concerns",
            "KRAFT_FAKE_CLAUDE_CONCERNS": "tests were flaky",
        },
    )
    assert proc.returncode == 0
    assert json.loads((tmp_path / "result.json").read_text()) == {
        "status": "done_with_concerns",
        "concerns": "tests were flaky",
    }


def test_status_knob_reports_needs_context_and_the_question_text(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(
        tmp_path,
        {
            "KRAFT_FAKE_CLAUDE": "noop",
            "KRAFT_FAKE_CLAUDE_STATUS": "needs_context",
            "KRAFT_FAKE_CLAUDE_QUESTION": "which repo?",
        },
    )
    assert proc.returncode == 0
    assert json.loads((tmp_path / "result.json").read_text()) == {
        "status": "needs_context",
        "question": "which repo?",
    }


def test_fix_mode_writes_session_summary_from_injected_context(tmp_path):
    """fake-claude obeys the 04 §6 summary instructions the agent adapter injects."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    ctx = (
        "Work item: w1\nNode: implementation\n"
        "Hook point: on.implementation.start\nWorker session: s9\n"
    )
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "fix"}, ctx=ctx)
    assert proc.returncode == 0
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["session_summary_ref"] == ".engineering/sessions/s9.md"
    summary = (tmp_path / ".engineering" / "sessions" / "s9.md").read_text()
    assert "work_item_ids: [w1]" in summary
    assert "worker_session_id: s9" in summary


def test_a_needs_context_report_writes_no_artifact(tmp_path):
    """A real worker that stops to ask a question has written nothing, and
    `agent._resolve_status` holds only a *claim* of success to the artifact.
    A fake that writes the document whatever it reports masks exactly the
    regressions that guard exists to catch -- the needs_context downgrade bug in
    MR !58 passed every needs_context test for this reason.

    The session summary is still written: a stopped worker is told to write one,
    and `run_agent_task` reads it back the same way.
    """
    ctx = (
        "Work item: w1\nNode: spec\nHook point: spec.main.author\nWorker session: s9\n"
        "Write your spec to .engineering/specs/w1.md, relative to the repo root.\n"
    )
    proc = _run(
        tmp_path,
        {
            "KRAFT_FAKE_CLAUDE": "noop",
            "KRAFT_FAKE_CLAUDE_STATUS": "needs_context",
            "KRAFT_FAKE_CLAUDE_QUESTION": "which repo?",
        },
        ctx=ctx,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / ".engineering" / "specs" / "w1.md").exists()
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "needs_context"
    assert (tmp_path / ".engineering" / "sessions" / "s9.md").is_file()


def test_the_skip_knob_suppresses_the_artifact_on_a_done_report(tmp_path):
    """The positive control and the knob in one test: `done` writes the plan,
    and `KRAFT_FAKE_CLAUDE_SKIP_ARTIFACT=1` suppresses it for a test that wants
    the empty-gate failure on purpose (the same knob `fake_agent.py` has)."""
    ctx = (
        "Work item: w2\nNode: plan\nHook point: plan.main.author\nWorker session: s7\n"
        "Write your plan to .engineering/plans/w2.md, relative to the repo root.\n"
    )
    artifact = tmp_path / ".engineering" / "plans" / "w2.md"

    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "noop"}, ctx=ctx)
    assert proc.returncode == 0, proc.stderr
    assert artifact.is_file()

    artifact.unlink()
    proc = _run(
        tmp_path,
        {"KRAFT_FAKE_CLAUDE": "noop", "KRAFT_FAKE_CLAUDE_SKIP_ARTIFACT": "1"},
        ctx=ctx,
    )
    assert proc.returncode == 0, proc.stderr
    assert not artifact.exists()
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "done"
