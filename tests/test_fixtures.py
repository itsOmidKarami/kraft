from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"


def _run(cwd, env_extra):
    env = {**os.environ, "KRAFT_RESULT_PATH": str(cwd / "result.json"), **env_extra}
    return subprocess.run(
        [str(_SCRIPT), "-p", "fix it", "--append-system-prompt", "ctx", "--output-format", "json"],
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
