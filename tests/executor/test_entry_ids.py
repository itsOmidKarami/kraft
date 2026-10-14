"""A new work item's id: 8 characters no item, leftover folder or branch
already has (spec "Item files and IDs", part 3)."""

from __future__ import annotations

import pytest
from support.harness import git
from support.schema import insert_item

from executor.test_entry import _intake  # `tests/` is on sys.path (CLAUDE.md)
from kraft import ids, item_paths

#: The first candidate every row below draws, taken one way per row.
_TAKEN = "aaaaaaaa"


async def test_intake_gives_a_new_item_an_8_character_id(database, run_dirs, tmp_path, repo):
    wid = await _intake(database, run_dirs, tmp_path, repo=str(repo))

    assert len(wid) == ids.WORK_ITEM_LENGTH == 8
    assert set(wid) <= set(ids.ALPHABET)


async def _take(how, database, run_dirs, repo):
    """Make `_TAKEN` taken the way `how` names."""
    if how == "equal-id":
        await database.write(lambda c: insert_item(c, wid=_TAKEN))
    elif how == "longer-id-it-starts":
        await database.write(lambda c: insert_item(c, wid=_TAKEN + "0" * 24))
    elif how.endswith("-folder"):
        folder = {
            "item-folder": item_paths.item_dir(run_dirs, _TAKEN),
            "worktree-folder": run_dirs.worktrees / _TAKEN,
            "attachments-folder": run_dirs.attachments / _TAKEN,
            "sandbox-home-folder": run_dirs.base / "sandbox-home" / _TAKEN,
        }[how]
        folder.mkdir(parents=True)
    else:
        ref = {
            "plain-branch": f"refs/heads/kraft/{_TAKEN}",
            "titled-branch": f"refs/heads/kraft/some-title-{_TAKEN}",
            "remote-branch": f"refs/remotes/origin/kraft/{_TAKEN}",
            "titled-remote-branch": f"refs/remotes/origin/kraft/some-title-{_TAKEN}",
        }[how]
        git(repo, "update-ref", ref, "HEAD")


@pytest.mark.parametrize(
    "how",
    [
        "equal-id",
        "longer-id-it-starts",
        "item-folder",
        "worktree-folder",
        "attachments-folder",
        "sandbox-home-folder",
        "plain-branch",
        "titled-branch",
        "remote-branch",
        "titled-remote-branch",
    ],
)
async def test_intake_draws_again_when_the_first_id_is_taken(
    monkeypatch, database, run_dirs, tmp_path, repo, how
):
    """The first candidate is `aaaaaaaa`, taken one way per row; the second
    is `bbbbbbbb`, which nothing holds."""
    await _take(how, database, run_dirs, repo)
    draws = iter("a" * 8 + "b" * 8)
    monkeypatch.setattr(ids.secrets, "choice", lambda _alphabet: next(draws))

    assert await _intake(database, run_dirs, tmp_path, repo=str(repo)) == "bbbbbbbb"


@pytest.mark.parametrize(
    "ref",
    ["refs/heads/kraft/xaaaaaaaa", "refs/heads/feature/aaaaaaaa", "refs/tags/kraft/aaaaaaaa"],
    ids=["longer-name", "outside-kraft", "a-tag"],
)
async def test_intake_keeps_an_id_no_kraft_branch_ends_with(
    monkeypatch, database, run_dirs, tmp_path, repo, ref
):
    """Only a whole `kraft/` branch name, or its `-<id>` end, takes an id."""
    git(repo, "update-ref", ref, "HEAD")
    monkeypatch.setattr(ids.secrets, "choice", lambda _alphabet: "a")

    assert await _intake(database, run_dirs, tmp_path, repo=str(repo)) == _TAKEN
