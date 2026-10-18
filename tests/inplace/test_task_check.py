"""Before and after an in-place agent task (`kraft.inplace.task_check`)."""

from __future__ import annotations

import subprocess

import pytest
from support.harness import IN_PLACE, commit_all, git, v1_named_chain, write

from kraft import inplace, store


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
    # Begun from the item's branch, but rebasing `feature`.
    "rebase-of-another-branch": [("rebase", "-x", "false", "item", "feature")],
}


@pytest.mark.parametrize("how", list(_THEIR_OPERATION))
def test_check_tells_the_persons_operation_on_the_branch_from_a_move(repo, how):
    """On the item's branch, an operation is the person's to finish. On
    another branch, it is a move, and their commit there is not the task's.
    Either way, its conflicted files are not the task's leftovers."""
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

    assert found.leftovers == {}
    if how == "merge-on-another-branch":
        theirs = git(repo, "rev-parse", "feature")
        assert (found.moved_to, found.under_way, found.stray_commits) == (
            "feature",
            False,
            [theirs],
        )
    elif how == "rebase-of-another-branch":
        assert (found.moved_to[:12], found.under_way) == ("detached at ", False)
    else:
        assert (found.moved_to, found.under_way) == (None, True)


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


_ITEMS_CALC = "# the item's, before the task\n"
_Q = "".join(f"{i}\n" for i in range(1, 8))
_COMMIT_CALC = ("commit", "-q", "-m", "agent", "--", "calc.py")
_MERGE_MAIN = ("merge", "-q", "--no-ff", "-m", "merge main", "main")
_REBASE_MAIN = ("rebase", "-q", "--autostash", "main")


def _main_has_the_items_calc(repo) -> None:
    """A hotfix main also took: its commit touches calc.py, the merge does not."""
    write(repo, "calc.py", _ITEMS_CALC)
    commit_all(repo, "the same fix on main")


def _main_drifts_the_items_context(repo) -> None:
    """Near the item's q.txt hunk: no conflict, but its replay is a new patch."""
    write(repo, "q.txt", _Q.replace("2\n", "2 main\n"))
    commit_all(repo, "main edits near the item's hunk")


def _main_clashes_with_the_items_hunk(repo) -> None:
    write(repo, "q.txt", _Q.replace("5\n", "5 main\n"))
    commit_all(repo, "main edits the item's line")


#: A step git stops part way, for the next steps to finish.
_STOPS = "stops"
_TAKE_THEIRS = ("checkout", "--theirs", "--", "q.txt")


#: What main does before the mark, what the task does after it, and whether
#: the task committed the person's calc.py.
_TASK_WORK = {
    "commit": (None, [_COMMIT_CALC], True),
    "merge-main": (None, [_MERGE_MAIN], False),
    "merge-main-unchanged": (_main_has_the_items_calc, [_MERGE_MAIN], False),
    "side-branch": (
        None,
        [
            ("checkout", "-q", "-b", "side"),
            ("commit", "-q", "-m", "side", "--", "calc.py"),
            ("checkout", "-q", "item"),
            ("merge", "-q", "--no-ff", "-m", "merge side", "side"),
        ],
        True,
    ),
    "rebase": (None, [_REBASE_MAIN], False),
    "rebase-autostash-replayed": (_main_drifts_the_items_context, [_REBASE_MAIN], False),
    "revert": (None, [_COMMIT_CALC, ("revert", "--no-edit", "HEAD")], True),
    "amend": (None, [("commit", "-q", "--amend", "--no-edit", "--", "calc.py")], True),
    # The amended commit touched calc.py before the task; the amend did not.
    "amend-the-message": (None, [("commit", "-q", "--amend", "-m", "reworded")], False),
    "commit-then-reset-hard": (None, [_COMMIT_CALC, ("reset", "-q", "--hard", "HEAD~1")], True),
    # The replay of the item's earlier commit, committed by hand: it carries
    # calc.py from before the mark, and the autostash puts the edit back.
    "rebase-conflict-committed": (
        _main_clashes_with_the_items_hunk,
        [
            (_STOPS, *_REBASE_MAIN),
            _TAKE_THEIRS,
            ("add", "q.txt"),
            ("commit", "-q", "-m", "an earlier task"),
            ("-c", "core.editor=true", "rebase", "--continue"),
        ],
        False,
    ),
    "squash-whole-branch": (
        None,
        [("reset", "--soft", "main~1"), ("commit", "-q", "-m", "all")],
        False,
    ),
    "cherry-pick-committed-then-reset-hard": (
        _main_clashes_with_the_items_hunk,
        [
            (_STOPS, "cherry-pick", "main"),
            _TAKE_THEIRS,
            ("commit", "-q", "-a", "-m", "picked"),
            ("reset", "-q", "--hard", "HEAD~1"),
        ],
        True,
    ),
}


@pytest.mark.parametrize("how", list(_TASK_WORK))
def test_check_overlap_is_every_path_the_branch_gained_whatever_its_shape(repo, how):
    """A merged side branch's files are the task's, and so is a commit a later
    one or a reset undid; main's commits, or the item's own a rebase replays,
    are not, even where they touch the person's file."""
    main_does, task_does, overlap = _TASK_WORK[how]
    write(repo, "q.txt", _Q)
    commit_all(repo, "q")
    branch = _on_item_branch(repo)
    write(repo, "calc.py", _ITEMS_CALC)
    write(repo, "q.txt", _Q.replace("5\n", "5 item\n"))
    commit_all(repo, "an earlier task")
    git(repo, "checkout", "-q", "main")
    write(repo, "main.txt", "main moved on\n")
    commit_all(repo, "main")
    if main_does:
        main_does(repo)
    git(repo, "checkout", "-q", branch)
    write(repo, "calc.py", "# the person's edit\n")
    m = inplace.mark(repo, branch, {})

    for args in task_does:  # at the real time: `check` reads the reflog
        if args[0] == _STOPS:
            with pytest.raises(subprocess.CalledProcessError):
                _git_now(repo, *args[1:])
        else:
            _git_now(repo, *args)

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
