from pathlib import Path

import pytest

from kraft.paths import RunDirs, default_harnesses_dir


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


def test_rundirs_ensure_makes_the_run_dir_private_and_tightens_an_old_one(tmp_path):
    """Kraft-9efnk.18: run/ holds whole agent sessions."""
    base = tmp_path / "run"
    base.mkdir(mode=0o755)
    base.chmod(0o755)
    RunDirs(base).ensure()
    assert base.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "present, want",
    [
        ([], "config/harnesses"),
        (["templates/harnesses"], "templates/harnesses"),
        (["templates/harnesses", "config/harnesses"], "config/harnesses"),
    ],
    ids=["neither", "only-the-1x-one", "both"],
)
def test_the_harness_overlay_is_read_where_1x_kept_it_until_config_has_one(
    tmp_path, monkeypatch, present, want
):
    """1.x kept the overlay in `$KRAFT_HOME/templates/harnesses` whatever
    `KRAFT_TEMPLATES_DIR` named, in a `templates/` the 2.0 rename never
    adopts when the config lived elsewhere."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    for d in present:
        (tmp_path / d).mkdir(parents=True)
    assert default_harnesses_dir() == tmp_path / want
