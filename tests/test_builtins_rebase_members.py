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
from support.workspace import ROOT_EMAIL, only_the_root_has_an_identity, workspace_item

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


async def test_a_pending_member_whose_origin_has_not_moved_is_not_reported_as_moved(
    database, run_dirs, tmp_path
):
    """Kraft-jypzx follow-up: `commits_ahead` sends a member with an ordinary
    pending commit into `refresh_worktree_base`, which now reports the
    member's own base head even when the member's branch already contains it
    as an ancestor -- true here, since the member's origin never moved. That
    is not a rebase and must not be counted in `moved`, or `has_rebase_bounce`
    would bounce `BASE_MOVED` on every ordinary run of a workspace item with
    any pending member."""
    row, root, member = await _workspace(database, run_dirs, tmp_path)
    before = git_read(member, "rev-parse", "HEAD")

    status = await _mr_rebase(database, run_dirs, row, root, has_rebase_bounce=True)

    assert status == "done"
    assert git_read(member, "rev-parse", "HEAD") == before


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


async def test_a_members_rebase_commits_as_the_root_and_writes_no_identity_into_its_repository(
    database, run_dirs, tmp_path, monkeypatch
):
    """Kraft-ju36l (J3): the member is a worktree of its connected repository,
    whose config Kraft never writes. With no identity there or in the global
    config, Kraft's rebase of the member still commits, as the root's
    identity, handed to that one command; the worker's authorship stays."""
    row, root, member = await _workspace(database, run_dirs, tmp_path)
    connected = tmp_path / "pkg-connected"
    only_the_root_has_an_identity(monkeypatch, tmp_path, row)
    before = (connected / ".git" / "config").read_text()
    moved = _move_member_origin(tmp_path)

    status = await _mr_rebase(database, run_dirs, row, root)

    assert status == "done"
    assert git_read(member, "merge-base", "--is-ancestor", moved, "HEAD") == ""
    assert git_read(member, "log", "-1", "--format=%ae %ce") == f"t@t {ROOT_EMAIL}"
    assert (connected / ".git" / "config").read_text() == before


# -- Kraft-xngty in a member: its admin dir is the worker's to write too --

from types import SimpleNamespace  # noqa: E402

from kraft.adapters.forge import git as forge_git  # noqa: E402
from kraft.adapters.forge.run import _point_at_merged_members  # noqa: E402


def _refs(*repos) -> list[str]:
    """Each repository's branches and `refs/stash`: what an operator owns.
    Remote-tracking refs move with Kraft's own fetch."""
    fmt = "--format=%(refname) %(objectname)"
    return [git_read(r, "for-each-ref", fmt, "refs/heads", "refs/stash") for r in repos]


class _Merged:
    def __init__(self, sha):
        self.sha = sha

    async def find_mr(self, *, repo, branch):
        return SimpleNamespace(state="merged", merged_sha=self.sha)


async def _door(door, database, run_dirs, row, root, member, branch):
    if door == "mr_rebase":
        return await _mr_rebase(database, run_dirs, row, root)
    if door == "restore_branch":
        return kraft_builtins.restore_branch(member, branch, "main")
    if door == "commit_stragglers":
        (member / "calc.py").write_text("left behind\n")
        return await forge_git.commit_stragglers(member, branch=branch, base="main", message="w")
    await database.write(
        lambda c: c.execute(
            "UPDATE work_item_repos SET merge_state = 'merged' WHERE role = 'submodule'"
        )
    )
    sha = git_read(member, "rev-parse", "HEAD~1")
    return await _point_at_merged_members(_Merged(sha), database, root, branch, row["id"])


@pytest.mark.parametrize(
    ("kind", "door"),
    [
        pytest.param("rebase-merge", "mr_rebase", id="rebase-merge-member"),
        pytest.param("rebase-apply", "mr_rebase", id="rebase-apply-member"),
        pytest.param("HEAD", "mr_rebase", id="planted-head-member"),
        pytest.param(
            "MERGE_AUTOSTASH", "restore_branch", id="MERGE_AUTOSTASH-restore_branch-member"
        ),
        pytest.param(
            "MERGE_AUTOSTASH", "commit_stragglers", id="MERGE_AUTOSTASH-commit_stragglers-member"
        ),
        pytest.param("MERGE_AUTOSTASH", "pointer_bump", id="MERGE_AUTOSTASH-pointer_bump-member"),
    ],
)
async def test_planted_state_in_a_members_admin_dir_never_moves_an_operator_ref(
    database, run_dirs, tmp_path, kind, door
):
    """Plan 1.1 `[member]`: a member is a worktree of its connected repository,
    so its admin dir -- HEAD and any operation state -- sits in that
    repository, and the worker writes it. Planted there, naming the member
    repository's own `main`, a Kraft rebase, checkout or commit in the member
    stops for a person and moves no ref of the member repository or its
    origin."""
    row, root, member = await _workspace(database, run_dirs, tmp_path)
    connected, branch = tmp_path / "pkg-connected", store.branch_for(row)
    _commit(connected, "operator.txt", "the operator's own\n", "operator work")
    _move_member_origin(tmp_path)
    admin = Path(git_read(member, "rev-parse", "--absolute-git-dir"))
    main, orig = (
        git_read(connected, "rev-parse", "main"),
        git_read(connected, "rev-parse", "main~1"),
    )
    if kind in ("rebase-merge", "rebase-apply"):
        (admin / kind).mkdir()
        for name, text in {"head-name": "refs/heads/main", "orig-head": orig, "onto": main}.items():
            (admin / kind / name).write_text(f"{text}\n")
        (admin / kind / "interactive").write_text("")
    else:
        # An operator branch at the member's own commit, so nothing but the
        # guard stands between Kraft and moving it.
        _git(connected, "branch", "side", branch)
    if kind == "MERGE_AUTOSTASH":
        (member / "calc.py").write_text("the worker's stash\n")
        stash = git_read(member, "stash", "create")
        _git(member, "checkout", "--", "calc.py")
        (admin / "MERGE_AUTOSTASH").write_text(f"{stash}\n")
    if kind == "HEAD" or door == "restore_branch":
        (admin / "HEAD").write_text("ref: refs/heads/side\n")
    before = _refs(connected, tmp_path / "pkg")

    try:
        status = await _door(door, database, run_dirs, row, root, member, branch)
    except RuntimeError:
        status = "raised"

    assert _refs(connected, tmp_path / "pkg") == before
    assert status in ("raised", "config_error")
