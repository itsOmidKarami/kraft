"""Which work item a session is in — the lookup every client function defaults to."""

from __future__ import annotations

import pytest

from kraft import client


@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    base = tmp_path / "run"
    (base / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(base))
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    return base


def test_env_var_wins_and_marks_the_caller_a_worker(run_dir, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "abc123")
    assert client.resolve_context(cwd=run_dir) == ("abc123", "worker")


def test_worktree_root_resolves_to_its_work_item(run_dir):
    wt = run_dir / "worktrees" / "deadbeef"
    wt.mkdir()
    assert client.resolve_context(cwd=wt) == ("deadbeef", "user")


def test_a_subdirectory_of_the_worktree_resolves_too(run_dir):
    deep = run_dir / "worktrees" / "deadbeef" / "src" / "kraft"
    deep.mkdir(parents=True)
    assert client.resolve_context(cwd=deep) == ("deadbeef", "user")


def test_outside_any_worktree_resolves_to_nothing(run_dir, tmp_path):
    elsewhere = tmp_path / "some" / "other" / "repo"
    elsewhere.mkdir(parents=True)
    assert client.resolve_context(cwd=elsewhere) == (None, "user")


def test_the_worktrees_directory_itself_is_not_a_work_item(run_dir):
    assert client.resolve_context(cwd=run_dir / "worktrees") == (None, "user")


def test_a_missing_cwd_does_not_raise(run_dir):
    assert client.resolve_context(cwd=run_dir / "gone") == (None, "user")


def test_resolve_work_item_returns_the_standing_item(run_dir, monkeypatch):
    """`cli.py` reads this to filter the instance-wide event bus, so it is public
    surface, not a private helper (Kraft-t5s9). An explicit id wins; with none,
    the session's own item answers."""
    import asyncio

    assert asyncio.run(client.resolve_work_item("explicit-id")) == "explicit-id"
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "abc123")
    assert asyncio.run(client.resolve_work_item(None)) == "abc123"
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID")
    with pytest.raises(ValueError, match="no work item"):
        asyncio.run(client.resolve_work_item(None))
