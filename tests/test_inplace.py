"""Where an in-place item lives (`kraft.inplace`)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
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
    [None, "# Spec\n", "# Someone else's\n", "no identity", "glob-like"],
    ids=["absent", "same", "different", "no-identity", "glob-like-name"],
)
async def test_the_first_take_commits_the_attachment_by_path(
    item_on, database, run_dirs, repo, tmp_path, monkeypatch, already
):
    # `docs/spec[1].md` read as a pathspec glob would commit the person's `docs/spec1.md`.
    name = "docs/spec[1].md" if already == "glob-like" else "docs/spec.md"
    if already == "glob-like":
        write(repo, "docs/spec1.md", "on main\n")
        commit_all(repo, "spec1")
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    stored = write(tmp_path / "filed", "spec.md", "# Spec\n")
    attachment = {"kind": "spec", "path": name, "source": str(stored)}
    if already == "no identity":
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        git(repo, "config", "--unset", "user.email")
    elif already == "glob-like":
        write(repo, "docs/spec1.md", "the person's edit\n")
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

    assert git(repo, "--literal-pathspecs", "show", f"{branch}:{name}") == "# Spec"
    assert git(repo, "rev-list", "--count", f"{it.row()['base_ref']}..{branch}") == "1"
    assert git(repo, "status", "--porcelain", "--untracked-files=no").splitlines() == [
        "M calc.py",
        *([" M docs/spec1.md"] if already == "glob-like" else []),
    ], "only the attachment was committed"


async def test_the_take_runs_its_git_writes_in_their_own_session(
    item_on, database, run_dirs, repo, tmp_path
):
    """A terminal's Ctrl-C or a service stop's group kill must not cut the
    switch or the attachment's commit off part way through the checkout."""
    it, _ = await _prepared(item_on, database, run_dirs, repo)
    for hook in ("post-checkout", "post-commit"):
        sid = f"{sys.executable} -c 'import os; print(os.getsid(0))' > {tmp_path / hook}"
        write(repo, f".git/hooks/{hook}", f"#!/bin/sh\n{sid}\n").chmod(0o755)
    stored = write(tmp_path / "filed", "spec.md", "# Spec\n")
    attachment = {"kind": "spec", "path": "docs/spec.md", "source": str(stored)}

    await inplace.take(database, run_dirs, it.row(), attachments=[attachment])

    ours = str(os.getsid(0))
    for hook in ("post-checkout", "post-commit"):
        assert (tmp_path / hook).read_text().strip() != ours, f"{hook} ran in Kraft's session"


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


# -- before and after each agent task ------------------------------------------


def _git_now(repo, *args: str) -> str:
    """`git` at the real time: the reflog `check` reads is timed, and
    `support.harness.git` commits at a fixed date in the past."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _on_item_branch(repo) -> str:
    git(repo, "checkout", "-q", "-b", "item")
    return "item"


def test_mark_counts_the_persons_edits_and_not_the_items_own_leftovers(repo):
    branch = _on_item_branch(repo)
    write(repo, "kept.txt", "the item left this\n")
    write(repo, "edited.txt", "the item left this\n")
    left = inplace._hashes(repo, ["kept.txt", "edited.txt"])
    write(repo, "edited.txt", "then the person changed it\n")
    write(repo, "calc.py", "# the person's edit\n")

    m = inplace.mark(repo, branch, left)

    assert m.dirty == {"edited.txt", "calc.py"}
    assert m.tip == git(repo, "rev-parse", branch)


def test_check_names_a_moved_checkout_and_the_commits_made_off_the_branch(repo):
    branch = _on_item_branch(repo)
    m = inplace.mark(repo, branch, {})
    _git_now(repo, "commit", "-q", "--allow-empty", "-m", "on the item's branch: not stray")
    _git_now(repo, "checkout", "-q", "main")
    write(repo, "calc.py", "# onto the person's branch\n")
    _git_now(repo, "commit", "-q", "-am", "stray")
    stray = git(repo, "rev-parse", "HEAD")

    found = inplace.check(repo, branch, m)

    assert found.moved_to == "main"
    assert found.stray_commits == [stray]
    _git_now(repo, "checkout", "-q", "--detach")
    assert inplace.check(repo, branch, m).moved_to == f"detached at {stray}"


_REWRITES = {
    "amend": [
        ("commit", "--allow-empty", "-m", "a"),
        ("commit", "--amend", "--allow-empty", "-m", "b"),
    ],
    "reset": [("commit", "--allow-empty", "-m", "a"), ("reset", "-q", "--hard", "HEAD~1")],
    "rebase": [
        ("commit", "--allow-empty", "-m", "on the branch"),
        ("checkout", "-q", "main"),
        ("commit", "--allow-empty", "-m", "main moved"),
        ("checkout", "-q", "item"),
        ("rebase", "-q", "main"),
    ],
    "merged-back": [
        ("checkout", "-q", "main"),
        ("commit", "--allow-empty", "-m", "made off the branch"),
        ("checkout", "-q", "item"),
        ("merge", "-q", "--no-edit", "main"),
    ],
}


@pytest.mark.parametrize("how", list(_REWRITES))
def test_check_names_no_stray_commit_for_history_the_branch_holds_or_rewrote(repo, how):
    """Only what HEAD reached while off the item's branch, and that the branch
    still lacks, is stray: an amend's or a reset's old commit is neither."""
    branch = _on_item_branch(repo)
    m = inplace.mark(repo, branch, {})
    for args in _REWRITES[how]:
        _git_now(repo, *args)

    found = inplace.check(repo, branch, m)

    assert (found.moved_to, found.stray_commits) == (None, [])


@pytest.mark.parametrize("how", ["merge", "commit"])
def test_check_overlap_is_the_tasks_own_commits_not_a_merged_main(repo, how):
    branch = _on_item_branch(repo)
    git(repo, "checkout", "-q", "main")
    write(repo, "calc.py", "# main moved on\n")
    commit_all(repo, "main")
    git(repo, "checkout", "-q", branch)
    write(repo, "calc.py", "# the person's edit\n")
    m = inplace.mark(repo, branch, {})
    git(repo, "checkout", "-q", "--", "calc.py")  # for git to allow the task's work below

    if how == "merge":
        git(repo, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    else:
        write(repo, "calc.py", "# the agent's\n")
        commit_all(repo, "agent")

    assert m.dirty == {"calc.py"}
    assert inplace.check(repo, branch, m).overlap == ([] if how == "merge" else ["calc.py"])


@pytest.mark.parametrize("unreadable", [False, True], ids=["plain", "one-unreadable"])
def test_check_leftovers_are_only_what_the_task_left(repo, unreadable):
    branch = _on_item_branch(repo)
    write(repo, "calc.py", "# the person's edit\n")
    m = inplace.mark(repo, branch, {})
    write(repo, "calc.py", "# the person's edit, and the agent's\n")
    write(repo, "new.txt", "the agent forgot this\n")
    if unreadable:  # fails `hash-object` for the whole call it is in
        write(repo, "locked.txt", "x\n").chmod(0)

    found = inplace.check(repo, branch, m)

    expected = {"new.txt": git(repo, "hash-object", "new.txt")}
    assert found.leftovers == expected | ({"locked.txt": ""} if unreadable else {})
    assert (found.moved_to, found.stray_commits, found.overlap) == (None, [], [])


def test_check_refuses_on_a_git_without_dense_combined_merges(repo, monkeypatch):
    branch = _on_item_branch(repo)
    m = inplace.mark(repo, branch, {})
    monkeypatch.setattr(inplace, "_DIFF_MERGES", "--diff-merges=what-older-git-lacks")

    with pytest.raises(inplace.CheckoutRefused, match="needs git 2.31 or newer"):
        inplace.check(repo, branch, m)


@pytest.mark.parametrize("name", ["with space.txt", "ünïcode.txt", "-leading.txt"])
def test_mark_and_check_read_odd_paths_exactly(repo, name):
    branch = _on_item_branch(repo)
    write(repo, name, "on the branch\n")
    commit_all(repo, "odd")
    write(repo, name, "the person's edit\n")
    m = inplace.mark(repo, branch, {})
    git(repo, "checkout", "-q", "--", name)
    write(repo, name, "the agent's\n")
    git(repo, "commit", "-q", "-m", "agent", "--", name)
    left = name.replace(".txt", ".left")
    write(repo, left, "left\n")

    found = inplace.check(repo, branch, m)

    assert m.dirty == {name}
    assert found.overlap == [name]
    assert found.leftovers == {left: git(repo, "hash-object", "--", left)}


async def test_drop_branch_reports_git_it_could_not_run(
    item_on, database, run_dirs, repo, monkeypatch
):
    """Archive has already flipped the row: a git that cannot start must not
    turn its answer into a 500."""
    it, branch = await _prepared(item_on, database, run_dirs, repo)

    def no_git(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(inplace.subprocess, "run", no_git)

    assert inplace.drop_branch(Path(repo), branch, "w1", keep=False) == {
        "branch_kept": "git failed"
    }
