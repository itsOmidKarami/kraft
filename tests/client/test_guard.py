"""A worker cannot act on the work item that is running it (design §6 rule 2)."""

from __future__ import annotations

import asyncio

import pytest

from kraft import client
from kraft.client import actions


@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    base = tmp_path / "run"
    (base / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(base))
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    return base


def test_a_worker_cannot_act_on_its_own_item(run_dir, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        client.context._forbid_self_action("mine")


def test_a_worker_acting_with_no_target_resolves_to_itself_and_is_refused(run_dir, monkeypatch):
    """The default target IS the caller's item, so a bare approve_gate() from a
    worker is exactly the self-approval the guard exists to stop."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        client.context._forbid_self_action(None)


def test_a_worker_may_act_on_a_different_item(run_dir, monkeypatch):
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    assert client.context._forbid_self_action("someone-elses") == "someone-elses"


def test_a_human_in_a_worktree_may_act_on_that_item(run_dir, monkeypatch):
    """A person standing in a worktree approving that item's gate is the whole
    point of the feature — origin is `user`, so the guard does not apply."""
    wt = run_dir / "worktrees" / "abc"
    wt.mkdir()
    monkeypatch.chdir(wt)
    assert client.context._forbid_self_action(None) == "abc"


def test_acting_with_no_target_and_no_context_says_so(run_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="no work item"):
        client.context._forbid_self_action(None)


def test_a_worker_cannot_set_its_own_agent_overrides(run_dir, monkeypatch):
    """Kraft-g1ebw: set_agent_overrides used to resolve via `resolve_work_item`,
    which skips the guard -- a worker could dial its own model/effort mid-run
    with no gate, unlike every other mutating verb here."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        asyncio.run(actions.set_agent_overrides(model="opus"))


def test_a_worker_cannot_set_its_own_node_overrides(run_dir, monkeypatch):
    """Kraft-g1ebw, same gap on the node-scoped override door."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        asyncio.run(actions.set_node_overrides("some_node", auto_escalate=True))


def test_a_worker_cannot_set_its_own_policy(run_dir, monkeypatch):
    """Kraft-j89jc: a worker raising its own caps or waits is the self-action
    Kraft-g1ebw closed on the two override doors beside this one."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(PermissionError, match="its own work item"):
        asyncio.run(actions.set_work_item_policy({"max_attempts": 9}))
