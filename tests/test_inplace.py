"""Where an in-place item lives (`kraft.inplace`)."""

from __future__ import annotations

import asyncio
import os
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


@pytest.mark.parametrize(
    "hook", [None, "echo hook broke >&2; exit 1"], ids=["plain", "failing-hook"]
)
async def test_take_switches_with_plain_checkout_and_keeps_edits(
    item_on, database, run_dirs, repo, hook
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    if hook is not None:
        # git has switched by the time post-checkout runs, and exits with its status.
        write(repo, ".git/hooks/post-checkout", f"#!/bin/sh\n{hook}\n").chmod(0o755)
    edited = (repo / "calc.py").read_text() + "# the person's edit\n"
    write(repo, "calc.py", edited)

    await inplace.take(database, run_dirs, it.row(), attachments=[])

    assert _head(repo) == f"refs/heads/{branch}"
    assert (repo / "calc.py").read_text() == edited
    assert git(repo, "status", "--porcelain") == "M calc.py"
    [switched] = [e["payload"] for e in it.events("checkout_switched")]
    assert (switched["from"], switched["to"]) == ("main", branch)
    assert ("hook broke" in switched.get("output", "")) == (hook is not None)


def _ignored_on_main(repo, branch, name):
    """`name` ignored on `main` but tracked on `branch`, the person's copy on disk."""
    write(repo, ".gitignore", f"{name}\n")
    commit_all(repo, "ignore it")
    git(repo, "checkout", "-q", branch)
    write(repo, name, "the item's version\n")
    git(repo, "add", "-f", name)
    git(repo, "commit", "-qm", "track it")
    git(repo, "checkout", "-q", "main")


def _item_writes(repo, branch, name):
    _on_branch(repo, branch, name, "the item's version\n")


def _ignored_where_the_branch_has_a_directory(repo, branch, name):
    """`name` an ignored file on `main`; `branch` tracks `name/l.txt`."""
    write(repo, ".gitignore", f"{name}\n")
    commit_all(repo, "ignore it")
    git(repo, "checkout", "-q", branch)
    write(repo, f"{name}/l.txt", "the item's version\n")
    git(repo, "add", "-f", f"{name}/l.txt")
    git(repo, "commit", "-qm", "track it")
    git(repo, "checkout", "-q", "main")


def _ignored_in_another_case(repo, branch, name):
    """`name` ignored on `main`; `branch` tracks it as `name.title()`, the same
    file on a case-insensitive filesystem."""
    if not (repo / "CALC.PY").exists():
        pytest.skip("the filesystem is case-sensitive")
    git(repo, "config", "core.ignorecase", "true")
    write(repo, ".gitignore", f"{name}\n")
    commit_all(repo, "ignore it")
    git(repo, "checkout", "-q", branch)
    write(repo, name.title(), "the item's version\n")
    git(repo, "add", "-f", name.title())
    git(repo, "commit", "-qm", "track it")
    (repo / name.title()).unlink()
    git(repo, "checkout", "-q", "main")


def _checked_out_elsewhere(repo, branch, name):
    git(repo, "worktree", "add", "-q", str(repo.parent / "other"), branch)


@pytest.mark.parametrize(
    ("name", "setup"),
    [
        ("calc.py", _item_writes),
        ("notes.txt", _item_writes),
        ("gen.txt", _ignored_on_main),
        ("logs", _ignored_where_the_branch_has_a_directory),
        ("gen.txt", _ignored_in_another_case),
        ("calc.py", _checked_out_elsewhere),
    ],
    ids=[
        "tracked",
        "untracked",
        "ignored",
        "ignored-file-where-a-directory-goes",
        "ignored-in-another-case",
        "checked-out-elsewhere",
    ],
)
async def test_take_refuses_a_switch_that_would_overwrite_and_touches_nothing(
    request, item_on, database, run_dirs, repo, name, setup
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    setup(repo, branch, name)
    write(repo, name, "the person's version\n")

    with pytest.raises(inplace.CheckoutRefused) as refused:
        await inplace.take(database, run_dirs, it.row(), attachments=[])

    if request.node.callspec.id == "checked-out-elsewhere":
        assert refused.value.paths == []
        assert "would overwrite" not in str(refused.value)
        assert "already used by worktree" in str(refused.value), "git's own words are lost"
    else:
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


@pytest.mark.parametrize("unsettled", ["unmerged-paths", "mid-rebase", "unreadable-index"])
async def test_take_stops_on_an_unsettled_checkout(
    item_on, database, run_dirs, repo, monkeypatch, unsettled
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    if unsettled == "mid-rebase":
        (repo / ".git" / "rebase-merge").mkdir()
    elif unsettled == "unreadable-index":
        # Every read of the index fails: no check may read that as "nothing there".
        monkeypatch.setenv("GIT_INDEX_FILE", str(write(repo.parent, "junk-index", "junk")))
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
    if unsettled == "unreadable-index":
        assert "could not read" in str(refused.value)
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
    "already",
    [None, "# Spec\n", "# Someone else's\n", "no identity"],
    ids=["absent", "same", "different", "no-identity"],
)
async def test_the_first_take_commits_the_attachment_by_path(
    item_on, database, run_dirs, repo, tmp_path, monkeypatch, already
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    stored = write(tmp_path / "filed", "spec.md", "# Spec\n")
    attachment = {"kind": "spec", "path": "docs/spec.md", "source": str(stored)}
    if already == "no identity":
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        git(repo, "config", "--unset", "user.email")
    elif already is not None:
        write(repo, "docs/spec.md", already)
    write(repo, "calc.py", "# the person's edit\n")

    if already in ("# Someone else's\n", "no identity"):
        with pytest.raises(inplace.CheckoutRefused) as refused:
            await inplace.take(database, run_dirs, it.row(), attachments=[attachment])
        if already == "no identity":
            assert "user.email" in str(refused.value)
            assert not (repo / "docs/spec.md").exists()
        else:
            assert refused.value.paths == ["docs/spec.md"]
            assert (repo / "docs/spec.md").read_text() == already
        assert git(repo, "rev-parse", branch) == it.row()["base_ref"]
        assert [e["payload"] for e in it.events("checkout_switched")] == [
            {"from": "main", "to": branch}
        ], "the checkout moved before the refusal, and nothing says so"
        return
    await inplace.take(database, run_dirs, it.row(), attachments=[attachment])
    await inplace.take(database, run_dirs, it.row(), attachments=[attachment])

    assert git(repo, "show", f"{branch}:docs/spec.md") == "# Spec"
    assert git(repo, "rev-list", "--count", f"{it.row()['base_ref']}..{branch}") == "1"
    assert git(repo, "status", "--porcelain") == "M calc.py", "only the attachment was committed"


async def test_a_glob_like_path_on_the_branch_leaves_the_persons_file_alone(
    item_on, database, run_dirs, repo
):
    """`f[1].txt` read as a pathspec glob matches `f1.txt`."""
    write(repo, "f1.txt", "on main\n")
    commit_all(repo, "f1")
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    _on_branch(repo, branch, "f[1].txt", "the item's\n")
    write(repo, "f1.txt", "the person's edit\n")

    await inplace.take(database, run_dirs, it.row(), attachments=[])

    assert (repo / "f1.txt").read_bytes() == b"the person's edit\n"


async def test_a_cancel_mid_switch_waits_for_git_and_records_the_switch(
    item_on, database, run_dirs, repo
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    write(repo, ".git/hooks/post-checkout", "#!/bin/sh\nsleep 1\n").chmod(0o755)
    taking = asyncio.ensure_future(inplace.take(database, run_dirs, it.row(), attachments=[]))
    await asyncio.sleep(0.3)
    taking.cancel()
    await asyncio.sleep(0.1)
    taking.cancel()  # a second cancel loses nothing either

    with pytest.raises(asyncio.CancelledError):
        await taking

    assert _head(repo) == f"refs/heads/{branch}"
    assert [e["payload"]["to"] for e in it.events("checkout_switched")] == [branch]
