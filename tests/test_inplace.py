"""Where an in-place item lives (`kraft.inplace`)."""

from __future__ import annotations

from pathlib import Path

from support.harness import v1_named_chain

from kraft import inplace
from kraft.templates.environment import WorkItemTarget


async def test_the_item_folder_is_the_repo_in_place_and_the_worktree_otherwise(
    item_on, run_dirs, repo
):
    chain = v1_named_chain(run_dirs.base / "t")
    target = WorkItemTarget.for_repository("sample", in_place=True)
    in_place = await item_on(chain, wid="here", target=target)
    worktree = await item_on(chain, wid="apart")

    assert inplace.item_folder(run_dirs, in_place.row()) == Path(repo)
    assert inplace.item_folder(run_dirs, worktree.row()) == run_dirs.worktrees / "apart"
    assert inplace.item_folder(run_dirs, {"id": "old", "repo": str(repo)}) == (
        run_dirs.worktrees / "old"
    ), "a row with no snapshot reads as a worktree item"
