"""Where an in-place item lives (`kraft.inplace`)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import IN_PLACE, commit_all, entry_of, git, v1_named_chain, write

from kraft import builtins, inplace, store


async def test_the_item_folder_is_the_repo_in_place_and_the_worktree_otherwise(
    item_on, run_dirs, repo
):
    chain = v1_named_chain(run_dirs.base / "t")
    in_place = await item_on(chain, wid="here", target=IN_PLACE)
    worktree = await item_on(chain, wid="apart")

    assert inplace.item_folder(run_dirs, in_place.row()) == Path(repo)
    assert inplace.item_folder(run_dirs, worktree.row()) == run_dirs.worktrees / "apart"
    assert inplace.item_folder(run_dirs, {"id": "old", "repo": str(repo)}) == (
        run_dirs.worktrees / "old"
    ), "a row with no snapshot reads as a worktree item"


async def _prepared(item_on, database, run_dirs, repo):
    """An in-place item whose branch ref `prepare` made, the person still on `main`."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=IN_PLACE)
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


@pytest.mark.parametrize(
    ("title", "walk", "refused"),
    [
        ("Other", False, "another item, Other, is using the checkout"),
        ("", False, "another item, other, is using the checkout"),
        (None, True, "no longer running"),
    ],
    ids=["another-item-holds-it", "an-untitled-item-holds-it", "the-walks-item-paused-meanwhile"],
)
async def test_take_looks_at_the_lease_again_before_it_switches(
    item_on, database, run_dirs, repo, title, walk, refused
):
    """A broken base's soft pause stops no walk mid-node: its next take must
    not switch a checkout another item has taken since."""
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    if title is not None:
        chain = v1_named_chain(run_dirs.base / "t")
        await item_on(chain, wid="other", target=IN_PLACE, title=title)
    await database.write(lambda c: store.pause_work_item(c, "w1", []))

    with pytest.raises(inplace.NotRunning if walk else inplace.CheckoutRefused, match=refused):
        await inplace.take(database, run_dirs, it.row(), attachments=[], walk=walk)

    assert _head(repo) == "refs/heads/main"
    assert it.events("checkout_switched") == []


@pytest.mark.parametrize("own", [True, False], ids=["commits-only-head-holds", "at-a-branch"])
async def test_take_never_leaves_detached_commits_to_the_reflog(
    item_on, database, run_dirs, repo, own
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    git(repo, "checkout", "-q", "--detach")
    if own:
        git(repo, "commit", "-q", "--allow-empty", "-m", "the person's, on no branch")
    detached = git(repo, "rev-parse", "HEAD")

    if own:
        with pytest.raises(inplace.CheckoutRefused, match=detached[:12]):
            await inplace.take(database, run_dirs, it.row(), attachments=[])
    else:
        await inplace.take(database, run_dirs, it.row(), attachments=[])

    assert git(repo, "rev-parse", "HEAD") == (detached if own else git(repo, "rev-parse", branch))


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


@pytest.mark.parametrize("on", ["main", "feature"], ids=["on-main", "on-another-branch"])
async def test_prepare_never_runs_setup_or_writes_git_config(item_on, database, run_dirs, repo, on):
    """The branch forks from the base, never from whatever the person has out."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=IN_PLACE)
    main = git(repo, "rev-parse", "main")
    if on != "main":
        git(repo, "checkout", "-q", "-b", on)
        write(repo, "theirs.txt", "the person's own commit\n")
        commit_all(repo, "theirs")
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
    assert it.row()["base_ref"] == main
    assert git(repo, "rev-parse", store.branch_for(it.row())) == main
    assert (git(repo, "rev-parse", "HEAD") == main) is (on == "main")
    assert _head(repo) == f"refs/heads/{on}"


def _spec(tmp_path, name="docs/spec.md") -> dict:
    stored = write(tmp_path / "filed", "spec.md", "# Spec\n")
    return {"kind": "spec", "path": name, "source": str(stored)}


def _theirs_there(repo, tmp_path, monkeypatch) -> str:
    write(repo, "docs/spec.md", "# Someone else's\n")
    return "docs/spec.md"


def _no_identity(repo, tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git(repo, "config", "--unset", "user.email")
    return "user.email"


def _symlink_at_dest(repo, tmp_path, monkeypatch) -> str:
    """To a file in the checkout that reads the same: only the link refuses it."""
    write(repo, "other.md", "# Spec\n")
    (repo / "docs").mkdir()
    (repo / "docs/spec.md").symlink_to("../other.md")
    return "docs/spec.md"


def _parent_dir_escapes(repo, tmp_path, monkeypatch) -> str:
    (repo / "docs").symlink_to((tmp_path / "outside").resolve(), target_is_directory=True)
    (tmp_path / "outside").mkdir()
    return "docs/spec.md"


def _a_hook_switches_back(repo, tmp_path, monkeypatch) -> str:
    hook = "[ -f .git/back ] || { touch .git/back; git checkout -q main; }"
    write(repo, ".git/hooks/post-checkout", f"#!/bin/sh\n{hook}\n").chmod(0o755)
    return "left"


@pytest.mark.parametrize(
    "arrange",
    [_theirs_there, _no_identity, _symlink_at_dest, _parent_dir_escapes, _a_hook_switches_back],
    ids=[
        "different",
        "no-identity",
        "symlink-at-dest",
        "symlink-parent-dir-escapes",
        "a-hook-switches-back",
    ],
)
async def test_the_first_take_refuses_an_attachment_it_would_put_over_anything(
    item_on, database, run_dirs, repo, tmp_path, monkeypatch, arrange
):
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    named = arrange(repo, tmp_path, monkeypatch)
    there, main = repo / "docs/spec.md", git(repo, "rev-parse", "main")
    before = there.read_bytes() if there.exists() else None

    with pytest.raises(inplace.CheckoutRefused, match=named):
        await inplace.take(database, run_dirs, it.row(), attachments=[_spec(tmp_path)])

    assert (there.read_bytes() if there.exists() else None) == before
    assert git(repo, "rev-parse", branch) == it.row()["base_ref"]
    assert git(repo, "rev-parse", "main") == main
    assert not (tmp_path / "outside" / "spec.md").exists()
    assert [e["payload"] for e in it.events("checkout_switched")] == [
        {"from": "main", "to": branch}
    ], "the checkout moved before the refusal, and nothing says so"


@pytest.mark.parametrize(
    "already", [None, "# Spec\n", "glob-like"], ids=["absent", "same", "glob-like-name"]
)
async def test_the_first_take_commits_the_attachment_by_path(
    item_on, database, run_dirs, repo, tmp_path, already
):
    # `docs/spec[1].md` read as a pathspec glob would commit the person's `docs/spec1.md`.
    name = "docs/spec[1].md" if already == "glob-like" else "docs/spec.md"
    if already == "glob-like":
        write(repo, "docs/spec1.md", "on main\n")
        commit_all(repo, "spec1")
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    if already == "glob-like":
        write(repo, "docs/spec1.md", "the person's edit\n")
    elif already is not None:
        write(repo, "docs/spec.md", already)
    write(repo, "calc.py", "# the person's edit\n")
    attachment = _spec(tmp_path, name)

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


@pytest.mark.parametrize(
    ("sibling", "live", "co_task"),
    [
        ("implementation.main.a", True, True),
        ("implementation.main.a", False, False),
        ("verify.main.a", True, False),
    ],
    ids=["sibling-running", "sibling-ended", "another-nodes-task"],
)
async def test_mark_takes_a_running_co_tasks_edits_for_its_own(
    item_on, run_dirs, repo, sibling, live, co_task
):
    """Co-tasks of one step share the checkout: a path dirtied after a
    sibling started is its work in flight, which it may commit, not the
    person's. The person's edit from before stays theirs."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation", target=IN_PLACE)
    git(repo, "checkout", "-q", "-b", store.branch_for(it.row()))
    write(repo, "calc.py", "# the person's edit\n")
    marking = it.database, run_dirs, it.row(), repo
    await inplace.mark_task(*marking, sibling.split(".")[0], sibling)
    await it.session("s1", sibling, None if live else "done")
    write(repo, "a.txt", "the sibling's work in flight\n")

    await inplace.mark_task(*marking, "implementation", "implementation.main.b")

    m = inplace.load_mark(run_dirs, it.id, "implementation.main.b")
    assert m.dirty == ({"calc.py"} if co_task else {"calc.py", "a.txt"})


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


#: The person's git, mid-task, each left stopped part way (`exec false`, a conflict).
_THEIR_OPERATION = {
    "rebase": [
        ("commit", "-q", "--allow-empty", "-m", "item"),
        ("rebase", "-x", "false", "HEAD~1"),
    ],
    "merge": [("merge", "feature")],
    "merge-on-another-branch": [
        ("checkout", "-q", "feature"),
        ("commit", "-q", "--allow-empty", "-m", "theirs"),
        ("merge", "item"),
    ],
}


@pytest.mark.parametrize("how", list(_THEIR_OPERATION))
def test_check_tells_the_persons_operation_on_the_branch_from_a_move(repo, how):
    """Begun on the item's branch, an operation is the person's to finish, and
    its conflicted files are not the task's leftovers. Begun on another
    branch, it is a move, and their commit there is not the task's."""
    branch = _on_item_branch(repo)
    write(repo, "f.txt", "the item's\n")
    commit_all(repo, "item")
    git(repo, "checkout", "-q", "-b", "feature", "main")
    write(repo, "f.txt", "feature's\n")
    commit_all(repo, "feature")
    git(repo, "checkout", "-q", branch)
    m = inplace.mark(repo, branch, {})
    *setup, last = _THEIR_OPERATION[how]
    for args in setup:
        _git_now(repo, *args)
    with pytest.raises(subprocess.CalledProcessError):  # stopped part way
        _git_now(repo, *last)

    found = inplace.check(repo, branch, m)

    if how == "merge-on-another-branch":
        theirs = git(repo, "rev-parse", "feature")
        assert (found.moved_to, found.under_way, found.stray_commits) == (
            "feature",
            False,
            [theirs],
        )
    else:
        assert (found.moved_to, found.under_way, found.leftovers) == (None, True, {})


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


_TASK_WORK = {
    "commit": [("commit", "-q", "-m", "agent", "--", "calc.py")],
    "merge-main": [("merge", "-q", "--no-ff", "-m", "merge main", "main")],
    "side-branch": [
        ("checkout", "-q", "-b", "side"),
        ("commit", "-q", "-m", "side", "--", "calc.py"),
        ("checkout", "-q", "item"),
        ("merge", "-q", "--no-ff", "-m", "merge side", "side"),
    ],
    "rebase": [("rebase", "-q", "--autostash", "main")],
    "revert": [("commit", "-q", "-m", "agent", "--", "calc.py"), ("revert", "--no-edit", "HEAD")],
    "amend": [("commit", "-q", "--amend", "--no-edit", "--", "calc.py")],
}


@pytest.mark.parametrize(
    ("how", "overlap"),
    [
        ("commit", True),
        ("merge-main", False),
        ("side-branch", True),
        ("rebase", False),
        ("revert", True),
        ("amend", True),
    ],
    ids=list(_TASK_WORK),
)
def test_check_overlap_is_every_path_the_branch_gained_whatever_its_shape(repo, how, overlap):
    """A merged side branch's files are the task's, and so is a commit a later
    one undid; main's, or the item's own commits a rebase replays, are not."""
    branch = _on_item_branch(repo)
    write(repo, "calc.py", "# the item's, before the task\n")
    commit_all(repo, "an earlier task")
    git(repo, "checkout", "-q", "main")
    write(repo, "main.txt", "main moved on\n")
    commit_all(repo, "main")
    git(repo, "checkout", "-q", branch)
    write(repo, "calc.py", "# the person's edit\n")
    m = inplace.mark(repo, branch, {})

    for args in _TASK_WORK[how]:
        git(repo, *args)

    assert m.dirty == {"calc.py"}
    assert inplace.check(repo, branch, m).overlap == (["calc.py"] if overlap else [])


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
