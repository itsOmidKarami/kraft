"""Where an in-place item lives (`kraft.inplace`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.harness import commit_all, entry_of, git, v1_named_chain, write

from kraft import builtins, inplace, store
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


_IN_PLACE = WorkItemTarget.for_repository("sample", in_place=True)


async def _prepared(item_on, database, run_dirs, repo):
    """An in-place item whose branch ref `prepare` made, the person still on `main`."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=_IN_PLACE)
    await inplace.prepare(database, run_dirs, it.row(), base="main")
    return it, store.branch_for(it.row())


def _head(repo) -> str:
    return git(repo, "symbolic-ref", "HEAD")


def _on_branch(repo, branch, name, body) -> None:
    """One commit on `branch` writing `name`, the person back on `main` after."""
    git(repo, "checkout", "-q", branch)
    write(repo, name, body)
    commit_all(repo, f"item writes {name}")
    git(repo, "checkout", "-q", "main")


async def test_take_switches_with_plain_checkout_and_keeps_edits(item_on, database, run_dirs, repo):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    edited = (repo / "calc.py").read_text() + "# the person's edit\n"
    write(repo, "calc.py", edited)

    await inplace.take(database, run_dirs, it.row(), attachments=[])

    assert _head(repo) == f"refs/heads/{branch}"
    assert (repo / "calc.py").read_text() == edited
    assert git(repo, "status", "--porcelain") == "M calc.py"
    assert [e["payload"] for e in it.events("checkout_switched")] == [
        {"from": "main", "to": branch}
    ]


@pytest.mark.parametrize(
    ("name", "person_tracks_it"),
    [("calc.py", True), ("notes.txt", False)],
    ids=["tracked", "untracked"],
)
async def test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing(
    item_on, database, run_dirs, repo, name, person_tracks_it
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    _on_branch(repo, branch, name, "the item's version\n")
    write(repo, name, "the person's version\n")
    assert bool(git(repo, "ls-files", name)) == person_tracks_it

    with pytest.raises(inplace.CheckoutRefused) as refused:
        await inplace.take(database, run_dirs, it.row(), attachments=[])

    assert refused.value.paths == [name]
    assert name in str(refused.value)
    assert (repo / name).read_text() == "the person's version\n"
    assert _head(repo) == "refs/heads/main"
    assert it.events("checkout_switched") == []


async def test_take_stops_when_the_branch_is_gone(item_on, database, run_dirs, repo):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    git(repo, "branch", "-D", branch)

    with pytest.raises(inplace.CheckoutRefused, match=f"{branch} is gone"):
        await inplace.take(database, run_dirs, it.row(), attachments=[])

    assert _head(repo) == "refs/heads/main"
    assert git(repo, "branch", "--list", branch) == "", "Kraft made the branch again"


@pytest.mark.parametrize("unsettled", ["unmerged-paths", "mid-rebase"])
async def test_take_stops_on_an_unsettled_checkout(item_on, database, run_dirs, repo, unsettled):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    if unsettled == "mid-rebase":
        (repo / ".git" / "rebase-merge").mkdir()
    else:
        # What an autostash clash leaves: conflicted entries, no operation.
        git(repo, "checkout", "-q", "-b", "theirs")
        write(repo, "calc.py", "theirs\n")
        commit_all(repo, "theirs")
        git(repo, "checkout", "-q", "main")
        write(repo, "calc.py", "ours\n")
        commit_all(repo, "ours")
        subprocess.run(["git", "merge", "theirs"], cwd=repo, capture_output=True)
        for leftover in ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE"):
            (repo / ".git" / leftover).unlink(missing_ok=True)
        assert git(repo, "diff", "--name-only", "--diff-filter=U") == "calc.py"

    with pytest.raises(inplace.CheckoutRefused) as refused:
        await inplace.take(database, run_dirs, it.row(), attachments=[])

    if unsettled == "unmerged-paths":
        assert refused.value.paths == ["calc.py"]
    assert _head(repo) == "refs/heads/main"


async def test_prepare_never_runs_setup_or_writes_git_config(item_on, database, run_dirs, repo):
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=_IN_PLACE)
    config = (repo / ".git" / "config").read_bytes()

    folder = await builtins.ensure_worktree(
        database,
        run_dirs,
        repo=str(repo),
        work_item_id=it.id,
        repo_entry=entry_of({"setup_command": "touch SETUP_RAN"}),
    )

    assert folder == repo
    assert not (repo / "SETUP_RAN").exists()
    assert (repo / ".git" / "config").read_bytes() == config
    assert not (run_dirs.worktrees / it.id).exists()
    head = git(repo, "rev-parse", "HEAD")
    assert it.row()["base_ref"] == head
    assert git(repo, "rev-parse", store.branch_for(it.row())) == head
    assert _head(repo) == "refs/heads/main"


@pytest.mark.parametrize(
    "already", [None, "# Spec\n", "# Someone else's\n"], ids=["absent", "same", "different"]
)
async def test_the_first_take_commits_the_attachment_by_path(
    item_on, database, run_dirs, repo, tmp_path, already
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    stored = write(tmp_path / "filed", "spec.md", "# Spec\n")
    attachment = {"kind": "spec", "path": "docs/spec.md", "source": str(stored)}
    if already is not None:
        write(repo, "docs/spec.md", already)
    write(repo, "calc.py", "# the person's edit\n")

    if already == "# Someone else's\n":
        with pytest.raises(inplace.CheckoutRefused) as refused:
            await inplace.take(database, run_dirs, it.row(), attachments=[attachment])
        assert refused.value.paths == ["docs/spec.md"]
        assert (repo / "docs/spec.md").read_text() == already
        assert git(repo, "rev-parse", branch) == it.row()["base_ref"]
        return
    await inplace.take(database, run_dirs, it.row(), attachments=[attachment])
    await inplace.take(database, run_dirs, it.row(), attachments=[attachment])

    assert git(repo, "show", f"{branch}:docs/spec.md") == "# Spec"
    assert git(repo, "rev-list", "--count", f"{it.row()['base_ref']}..{branch}") == "1"
    assert git(repo, "status", "--porcelain") == "M calc.py", "only the attachment was committed"
