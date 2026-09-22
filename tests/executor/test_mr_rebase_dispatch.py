"""`kind: builtin, ref: kraft.mr_rebase` -- `BuiltinAction`'s second member
(Kraft-3llig): which function `dispatch.dispatch_node` reaches, and with what;
and, dispatched for real, what it does on the `open_mr` re-entry path -- a
branch already pushed to origin, from a rejected review walking back through
`draft_merge_request` or a crashed `open` retried.

A sibling of test_dispatch.py, not a section inside it: that file is at its
allowlisted line-budget ceiling (dev/check_tests.py), which may shrink but
never grow.

`builtins.mr_rebase`'s own rebase/persist/report behaviour is
tests/test_builtins_rebase.py's. The default chain's own shape (rebase before
open, no restart on `merge_request_feedback`) is
tests/executor/test_default_chain.py's."""

import subprocess

import pytest
from support.harness import _git, entry_of

from kraft import builtins as kraft_builtins
from kraft import store
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))


async def _dispatch_one(it):
    node = it.chain.chain.nodes[0]
    return await dispatch.dispatch_node(
        it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo, launch=NO_SETUP
    )


def _rebase_chain():
    raw = {"id": "rebase", "kind": "builtin", "ref": "kraft.mr_rebase"}
    return [{"id": "draft_merge_request", "kind": "exec", "steps": [{"id": "s", "tasks": [raw]}]}]


async def test_a_pushed_branch_is_not_force_rewritten_on_re_entry(item_on, run_dirs, repo):
    """`refresh_worktree_base`'s pushed-branch guard, reached for real through
    this binding: `draft_merge_request` re-entered with its branch already on
    origin (a rejected review walked back, or a crashed `open` retried) must
    not force-rewrite history a reviewer may already be reading."""
    it = await item_on(_rebase_chain(), repo=repo)
    worktree = await kraft_builtins.ensure_worktree(
        it.database,
        run_dirs,
        repo=str(repo),
        work_item_id=it.id,
        repo_entry=entry_of({"setup_command": ""}),
    )
    branch = store.branch_for(it.row())
    origin = run_dirs.base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(worktree, "push", "-q", "-u", "origin", branch)
    before = git_read(worktree, "rev-parse", "HEAD")
    before_base_ref = it.row()["base_ref"]
    # Origin's main moves again after the push -- something to rebase onto,
    # if the guard did not stop it.
    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on")
    _git(repo, "push", "-q", "origin", "main")

    node = it.chain.chain.nodes[0]
    status = await dispatch.dispatch_node(
        it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), worktree, launch=NO_SETUP
    )

    assert status == "done"
    assert git_read(worktree, "rev-parse", "HEAD") == before
    assert it.row()["base_ref"] == before_base_ref


@pytest.mark.parametrize(
    ("declared", "bounce"),
    [(None, False), ({"restart_from": "draft_merge_request"}, True)],
    ids=["undeclared", "declared"],
)
async def test_a_builtin_mr_rebase_task_dispatches_to_the_rebase_builtin(
    item_on, monkeypatch, declared, bounce
):
    """`ref: kraft.mr_rebase` reaches `builtins.mr_rebase` -- not
    `_run_changed_test_scopes`, the only other `BuiltinAction` -- given the
    item's own repo/worktree/branch, and a bounce only when *this task's own
    node* declares `on_base_changed` (not whatever runs after it)."""
    calls = []

    async def fake_mr_rebase(db, run_dirs, **kw):
        calls.append(kw)
        return "done"

    monkeypatch.setattr(dispatch._builtins, "mr_rebase", fake_mr_rebase)
    raw = {"id": "rebase", "kind": "builtin", "ref": "kraft.mr_rebase"}
    node = {"id": "draft_merge_request", "kind": "exec", "steps": [{"id": "s", "tasks": [raw]}]}
    if declared:
        node["on_base_changed"] = declared
    it = await item_on([node])

    assert await _dispatch_one(it) == "done"
    [kw] = calls
    row = it.row()
    assert (kw["repo"], kw["worktree"], kw["branch"]) == (
        row["repo"],
        str(it.repo),
        store.branch_for(row),
    )
    assert kw["has_rebase_bounce"] is bounce
    # Kraft-3llig review fix 1: dropped entirely, a hanging rebase (a slow
    # hook, a smudge/LFS filter) would hold the worker slot forever -- must
    # reach builtins.mr_rebase even when unset (None, no cap resolved here).
    assert "time_cap" in kw
