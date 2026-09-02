from pathlib import Path

from kraft.paths import RunDirs


def test_rundirs_derives_paths():
    rd = RunDirs(Path("/run/kraft"))
    assert rd.db == Path("/run/kraft/orchestrator.db")
    assert rd.logs == Path("/run/kraft/logs")
    assert rd.results == Path("/run/kraft/results")
    assert rd.worktrees == Path("/run/kraft/worktrees")


def test_rundirs_ensure_creates_dirs(tmp_path):
    rd = RunDirs(tmp_path / "run").ensure()
    assert rd.logs.is_dir()
    assert rd.results.is_dir()
    assert rd.worktrees.is_dir()
    rd.ensure()  # idempotent, must not raise
