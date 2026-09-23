"""`kraft.builtins.mr_rebase` on a workspace item (Kraft-ei38e): each changed
member rebased onto its own origin before the draft opens, the root kept
clean and pointing at the rebased member, the item's `base_ref` left the
root's. A sibling of test_builtins_rebase.py, which covers a single
repository.

Git is real: a root with one real submodule (`workspace_item`), whose own
source repository stands in as the member's origin."""

import subprocess
from pathlib import Path

import pytest
from support.harness import _git
from support.workspace import workspace_item

from kraft import builtins as kraft_builtins
from kraft import caps, store
from kraft.config import git_read
from kraft.executor.context import BASE_MOVED


def _commit(cwd, name, text, message):
    (cwd / name).write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-qm", message)
    return git_read(cwd, "rev-parse", "HEAD")


async def _workspace(
    database, run_dirs, tmp_path, *, member_change="x = 1\n", root_commits_pointer=True
):
    """A workspace item whose member `repos/pkg` carries a commit on the item's
    branch (none when `member_change` is None), its pointer committed in the
    root the way the straggler sweep commits it (unless not
    `root_commits_pointer`). Returns `(row, root, member)`."""
    task = {"id": "t", "kind": "subprocess", "command": "true"}
    row, _, root = await workspace_item(database, run_dirs, tmp_path, [task])
    member = root / "repos" / "pkg"
    if member_change is not None:
        _commit(member, "calc.py", member_change, "member change")
    if member_change is not None and root_commits_pointer:
        _git(root, "add", "repos/pkg")
        _git(root, "commit", "-qm", "wip: uncommitted work from t")
    return row, root, member


def _move_member_origin(tmp_path, text="landed meanwhile\n", name="moved.txt"):
    """The member's own origin (its source repository) moves on."""
    return _commit(tmp_path / "pkg", name, text, "the member's main moves")


async def _mr_rebase(database, run_dirs, row, root, **kw):
    return await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id=row["id"],
        node_id="draft_merge_request",
        hook_point="draft_merge_request.rebase.rebase",
        round=0,
        repo=row["repo"],
        worktree=str(root),
        branch=store.branch_for(row),
        **kw,
    )


def _base_ref(database, row):
    return database.read(
        lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (row["id"],)).fetchone()
    )["base_ref"]


def _porcelain(cwd):
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize(("bounce", "expected"), [(False, "done"), (True, BASE_MOVED)])
async def test_a_changed_member_is_rebased_and_the_root_repointed_at_it(
    database, run_dirs, tmp_path, bounce, expected
):
    row, root, member = await _workspace(database, run_dirs, tmp_path)
    moved = _move_member_origin(tmp_path)
    base_ref = _base_ref(database, row)

    status = await _mr_rebase(database, run_dirs, row, root, has_rebase_bounce=bounce)

    assert status == expected
    assert git_read(member, "merge-base", "--is-ancestor", moved, "HEAD") == ""
    # The root stays publishable: `assert_clean` would refuse its draft over
    # a gitlink left at the member's pre-rebase head.
    assert _porcelain(root) == ""
    assert git_read(root, "rev-parse", "HEAD:repos/pkg") == git_read(member, "rev-parse", "HEAD")
    assert (
        git_read(root, "log", "-1", "--format=%s") == "chore: repoint members after pre-MR rebase"
    )
    # `base_ref` is the root's; the member's move is never written there.
    assert _base_ref(database, row) == base_ref


async def test_an_unchanged_member_is_not_touched(database, run_dirs, tmp_path):
    row, root, member = await _workspace(database, run_dirs, tmp_path, member_change=None)
    before = git_read(member, "rev-parse", "HEAD")
    _move_member_origin(tmp_path)

    status = await _mr_rebase(database, run_dirs, row, root, has_rebase_bounce=True)

    assert status == "done"
    assert git_read(member, "rev-parse", "HEAD") == before
    assert _porcelain(root) == ""


async def test_a_member_conflict_stops_the_item_naming_the_member(database, run_dirs, tmp_path):
    row, root, member = await _workspace(database, run_dirs, tmp_path, member_change="ours\n")
    before = git_read(member, "rev-parse", "HEAD")
    _move_member_origin(tmp_path, text="theirs\n", name="calc.py")
    base_ref = _base_ref(database, row)

    with pytest.raises(kraft_builtins.RebaseConflict, match=r"(?s)repos/pkg: .*CONFLICT"):
        await _mr_rebase(database, run_dirs, row, root)

    assert git_read(member, "rev-parse", "HEAD") == before
    assert _base_ref(database, row) == base_ref


async def test_the_root_commits_no_pointer_it_had_not_committed(database, run_dirs, tmp_path):
    """Only a gitlink the root had committed at the member's old head is moved
    for it: a pointer the root never took stays the root's own business."""
    row, root, _ = await _workspace(database, run_dirs, tmp_path, root_commits_pointer=False)
    before = git_read(root, "rev-parse", "HEAD")
    _move_member_origin(tmp_path)

    await _mr_rebase(database, run_dirs, row, root)

    assert git_read(root, "rev-parse", "HEAD") == before


async def test_a_later_members_conflict_keeps_the_earlier_members_repoint(
    database, run_dirs, tmp_path
):
    """Review fix: `repos/pkg` moves, then `repos/pkg2` conflicts. `pkg` has
    nothing left to rebase on the retry, so the raise must not cost it its
    repoint -- a root left at its old head fails `assert_clean` for good."""
    task = {"id": "t", "kind": "subprocess", "command": "true"}
    row, _, root = await workspace_item(database, run_dirs, tmp_path, [task], second=True)
    pkg, pkg2 = root / "repos" / "pkg", root / "repos" / "pkg2"
    _commit(pkg, "lib.py", "x = 1\n", "member change")
    _commit(pkg2, "calc.py", "ours\n", "member change")
    _git(root, "add", "repos/pkg", "repos/pkg2")
    _git(root, "commit", "-qm", "wip: uncommitted work from t")
    _move_member_origin(tmp_path)
    _commit(tmp_path / "pkg2", "calc.py", "theirs\n", "the member's main moves")

    with pytest.raises(kraft_builtins.RebaseConflict, match=r"repos/pkg2"):
        await _mr_rebase(database, run_dirs, row, root)

    assert _porcelain(root) == ""
    assert git_read(root, "rev-parse", "HEAD:repos/pkg") == git_read(pkg, "rev-parse", "HEAD")

    # A person resolves `pkg2` and commits its pointer; the retry finds the
    # root still clean.
    _git(pkg2, "rebase", "-q", "-X", "theirs", "origin/main")
    _git(root, "add", "repos/pkg2")
    _git(root, "commit", "-qm", "resolved pkg2")
    assert await _mr_rebase(database, run_dirs, row, root) == "done"
    assert _porcelain(root) == ""


async def test_a_member_that_runs_past_the_time_cap_stops_before_the_root(
    database, run_dirs, tmp_path
):
    """A member's timed-out rebase ends the task as the root's would --
    `capped_out`, `time_capped` -- and the root is never rebased after it."""
    row, root, member = await _workspace(database, run_dirs, tmp_path)
    _move_member_origin(tmp_path)
    _commit(Path(row["repo"]), "root_moved.txt", "root moved\n", "the root's main moves")
    root_head = git_read(root, "rev-parse", "HEAD")
    hooks = Path(git_read(member, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-rebase").write_text("#!/bin/sh\nsleep 30\n")
    (hooks / "pre-rebase").chmod(0o755)
    hit = caps.Hit(scope="", field="time_cap_minutes", minutes=0, remaining_s=1.0)

    status = await _mr_rebase(
        database,
        run_dirs,
        row,
        root,
        time_cap=caps.Deadline(at=caps.monotonic() + 1.0, hit=hit),
    )

    assert status == caps.TIME_CAPPED
    session = database.read(
        lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
    )
    assert session["status"] == "capped_out"
    # The member's timeout, named -- not the root's own, run out of time after it.
    log = (run_dirs.logs / "s1.log").read_text()
    assert f"git rebase timed out after 1s for {member}" in log
    assert git_read(root, "rev-parse", "HEAD") == root_head


def _move_root_pointer(tmp_path, root_source):
    """The root's own base moves `repos/pkg`'s pointer to a member commit the
    item's rebased member does not descend from."""
    pkg = tmp_path / "pkg"
    _git(pkg, "checkout", "-qb", "side")
    side = _commit(pkg, "side.txt", "side\n", "a member commit off main")
    _git(pkg, "checkout", "-q", "main")
    _git(root_source, "update-index", "--cacheinfo", f"160000,{side},repos/pkg")
    _git(root_source, "commit", "-qm", "the root's main moves the pointer")


async def test_a_root_gitlink_conflict_says_how_to_resolve_it(database, run_dirs, tmp_path):
    """Kraft-xvwye: the root's base moved the same member pointer the pre-MR
    repoint commit moves, so the root's rebase conflicts on that gitlink alone.
    git's text stays; one sentence says what happened and what to do."""
    row, root, _ = await _workspace(database, run_dirs, tmp_path)
    _move_member_origin(tmp_path)
    _move_root_pointer(tmp_path, Path(row["repo"]))

    with pytest.raises(kraft_builtins.RebaseConflict) as raised:
        await _mr_rebase(database, run_dirs, row, root)

    message = str(raised.value)
    assert "CONFLICT" in message
    assert "also moved member repos/pkg's pointer" in message
    assert f"git -C repos/pkg checkout {store.branch_for(row)}" in message


async def test_a_root_file_conflict_gets_no_gitlink_sentence(database, run_dirs, tmp_path):
    """A moved member alongside a root conflict on its own file is git's
    conflict alone: the sentence is only for a conflict on the moved pointers."""
    row, root, _ = await _workspace(database, run_dirs, tmp_path)
    _commit(root, "root.txt", "ours\n", "root change")
    _move_member_origin(tmp_path)
    _commit(Path(row["repo"]), "root.txt", "theirs\n", "the root's main moves")

    with pytest.raises(kraft_builtins.RebaseConflict, match="CONFLICT") as raised:
        await _mr_rebase(database, run_dirs, row, root)

    assert "also moved member" not in str(raised.value)
