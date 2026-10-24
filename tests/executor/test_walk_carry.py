"""`carry` reaches `ensure_worktree` through `run_once`, as `local_files` does."""

import pytest
from support.harness import commit_all, entry_of

from .test_walk import _exec, _sub, _walk


@pytest.mark.parametrize(
    ("ignored", "expected_in_report"),
    [
        (True, "carried from the checkout: .testmondata"),
        (False, "carry entries NOT carried"),
    ],
    ids=["ignored", "not-ignored"],
)
async def test_run_once_threads_carry_from_the_launch_context(
    item_on, repo, ignored, expected_in_report
):
    if ignored:
        (repo / ".gitignore").write_text(".testmondata\n")
        commit_all(repo, "ignore the state")
    (repo / ".testmondata").write_text("db\n")
    it = await item_on([_exec("work", _sub("noop", ["true"]))])

    entry = entry_of({"carry": [".testmondata"], "setup_command": ""})
    assert await _walk(it, repo_entry=entry) == "completed"

    assert (it.worktree / ".testmondata").exists() is ignored
    assert expected_in_report in it.events("worktree_prepared")[0]["payload"]["report"]
