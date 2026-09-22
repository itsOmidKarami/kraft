"""`kraft.builtins`' rebase side: `restore_branch`, `refresh_worktree_base`,
`upstream_head` and `mr_rebase` -- a worktree kept on its own branch and moved
onto the base the merge request targets."""

import asyncio
import subprocess

import pytest
from support import worktree as wtree
from support.harness import _git, make_repo

from kraft import builtins as kraft_builtins
from kraft import caps, events
from kraft.config import git_read


def _commit(cwd, name, text, message):
    """Write `name` under `cwd` and commit everything; returns the new HEAD."""
    (cwd / name).write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-m", message)
    return git_read(cwd, "rev-parse", "HEAD")


def _porcelain(cwd):
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.splitlines()


def test_restore_branch_recovers_from_a_stranded_mid_merge_diagnostic_branch(repo):
    """Kraft-v5qd: an agent diagnosing a conflict checked out a scratch
    branch, ran a test merge that hit real conflicts, and never checked back
    out -- the worktree sat on the scratch branch, mid-merge, with the
    item's own branch untouched underneath. `restore_branch` is the net for
    exactly that, whatever left the worktree there."""
    readme = repo / "README.md"

    _git(repo, "checkout", "-b", "kraft/w1")
    readme.write_text("item's own change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "item work")
    item_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout

    _git(repo, "checkout", "main")
    readme.write_text("diverging main change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main diverges")

    # An agent's ad-hoc diagnosis: branch off the item's own branch, try a
    # merge, hit a conflict, and stop mid-merge without cleaning up.
    _git(repo, "checkout", "kraft/w1")
    _git(repo, "checkout", "-b", "_conflict_test")
    subprocess.run(["git", "merge", "main"], cwd=repo, capture_output=True, text=True)
    assert any("README.md" in line for line in _porcelain(repo)), (
        "the scenario did not actually conflict"
    )

    kraft_builtins.restore_branch(repo, "kraft/w1", "main")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "kraft/w1"
    assert _porcelain(repo) == [], "the aborted merge left the tree dirty"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert head == item_head, "the item's own commit must be untouched"


def test_restore_branch_is_a_no_op_when_already_on_the_right_branch(repo):
    _git(repo, "checkout", "-b", "kraft/w1")

    kraft_builtins.restore_branch(repo, "kraft/w1", "main")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "kraft/w1"


def test_refresh_worktree_base_returns_none_when_no_worktree(tmp_path, repo):
    worktree = tmp_path / "nope"
    result = asyncio.run(
        kraft_builtins.refresh_worktree_base(worktree, repo, "kraft/w1", base="main")
    )
    assert result is None


async def test_refresh_worktree_base_returns_none_when_already_up_to_date(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    # no origin remote exists here at all -- the already-pushed probe's
    # failure must be swallowed (expected_failure), not raised
    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result is None


async def test_refresh_worktree_base_skips_when_branch_already_pushed(
    tmp_path, database, run_dirs, repo
):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    _git(worktree, "push", "-q", "-u", "origin", branch)
    before = git_read(worktree, "rev-parse", "HEAD")

    _commit(repo, "moved.txt", "moved on\n", "moved on")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result is None
    assert git_read(worktree, "rev-parse", "HEAD") == before


async def test_refresh_worktree_base_rebases_and_returns_new_head(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)

    _commit(worktree, "worktree_work.txt", "done in the worktree\n", "worktree work")

    _commit(repo, "moved.txt", "moved on\n", "moved on")
    new_head = git_read(repo, "rev-parse", "HEAD")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result == new_head
    assert (worktree / "worktree_work.txt").is_file()
    assert (worktree / "moved.txt").is_file()
    assert git_read(worktree, "merge-base", "--is-ancestor", new_head, "HEAD") == ""


def _repo_with_origin(tmp_path):
    """`make_repo` pushed to a bare `origin`, plus a second clone standing in for
    everyone else -- what lands through it reaches origin but not `repo`."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    repo = make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git(other, "config", "user.email", "o@o")
    _git(other, "config", "user.name", "o")
    return repo, other


def _land_upstream(other, name):
    (other / name).write_text("landed upstream\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", f"upstream {name}")
    _git(other, "push", "-q", "origin", "main")
    return git_read(other, "rev-parse", "HEAD")


async def test_ensure_worktree_forks_from_origin_when_the_local_checkout_is_behind(
    tmp_path, database, run_dirs
):
    # Kraft-k647: the connected repo's checkout is only as fresh as its owner's
    # last pull, and Kraft merges on the forge -- forking from it started every
    # item behind the branch its MR targets.
    repo, other = _repo_with_origin(tmp_path)
    upstream = _land_upstream(other, "upstream.txt")

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    assert (worktree / "upstream.txt").is_file()
    assert wtree.base_ref(database) == upstream


async def test_refresh_worktree_base_rebases_onto_origin_when_the_local_checkout_is_behind(
    tmp_path, database, run_dirs
):
    # Kraft-k647: against the stale local HEAD this answered "nothing to
    # rebase" -- the branch already contained it -- on every pre_mr_rebase.
    repo, other = _repo_with_origin(tmp_path)

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    _commit(worktree, "worktree_work.txt", "done in the worktree\n", "worktree work")
    upstream = _land_upstream(other, "upstream.txt")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result == upstream
    assert (worktree / "upstream.txt").is_file()
    assert (worktree / "worktree_work.txt").is_file()


async def test_refresh_worktree_base_falls_back_to_local_head_when_origin_is_unreachable(
    tmp_path, database, run_dirs, repo
):
    # Offline, or credentials the server process cannot reach: the rebase still
    # happens against what the checkout has, rather than failing the resume.
    _git(repo, "remote", "add", "origin", str(tmp_path / "gone.git"))

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    _commit(repo, "moved.txt", "moved on\n", "moved on")
    local = git_read(repo, "rev-parse", "HEAD")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result == local
    assert (worktree / "moved.txt").is_file()


def test_upstream_head_falls_back_to_the_last_fetched_origin_ref_before_local_head(tmp_path):
    # A failed fetch still leaves the last good view of origin, and that -- not
    # whatever the checkout has on it, unpushed commits or another branch -- is
    # what the MR targets.
    repo, _ = _repo_with_origin(tmp_path)
    fetched = git_read(repo, "rev-parse", "origin/main")
    _commit(repo, "local_only.txt", "never pushed\n", "local only")
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    assert asyncio.run(kraft_builtins.upstream_head(repo, "main")) == fetched


def test_upstream_head_sees_origin_even_when_the_fetch_refspec_skips_the_default(tmp_path):
    # A --single-branch clone of some other branch: a bare `fetch origin main`
    # would not update refs/remotes/origin/main, and the tip read back is stale.
    repo, other = _repo_with_origin(tmp_path)
    _git(repo, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
    upstream = _land_upstream(other, "upstream.txt")

    assert asyncio.run(kraft_builtins.upstream_head(repo, "main")) == upstream


async def test_refresh_worktree_base_raises_and_aborts_on_conflict(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)

    _commit(
        worktree,
        "calc.py",
        "def add(a, b):\n    return a - b - 1  # bug: should be +\n",
        "worktree edit",
    )
    worktree_head = git_read(worktree, "rev-parse", "HEAD")

    _commit(
        repo,
        "calc.py",
        "def add(a, b):\n    return a - b - 2  # bug: should be +\n",
        "conflicting edit",
    )

    with pytest.raises(RuntimeError, match="git rebase failed"):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")

    assert git_read(worktree, "status", "--porcelain") == ""
    assert git_read(worktree, "rev-parse", "HEAD") == worktree_head


async def test_mr_rebase_moves_the_base_and_records_a_done_session(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)

    _commit(repo, "moved.txt", "moved on\n", "moved on")
    new_head = git_read(repo, "rev-parse", "HEAD")

    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="pre_mr_rebase",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=branch,
    )
    assert status == "done"
    assert wtree.base_ref(database) == new_head
    assert (worktree / "moved.txt").is_file()
    session = database.read(
        lambda c: c.execute(
            "SELECT hook_point, status FROM worker_sessions WHERE id='s1'"
        ).fetchone()
    )
    assert session["hook_point"] == "on.mr.rebase"
    assert session["status"] == "done"


async def test_mr_rebase_leaves_base_ref_alone_when_nothing_to_rebase(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    before = wtree.base_ref(database)

    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="pre_mr_rebase",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=branch,
    )
    assert status == "done"
    assert wtree.base_ref(database) == before


async def test_mr_rebase_raises_on_conflict(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)

    _commit(
        worktree,
        "calc.py",
        "def add(a, b):\n    return a - b - 1  # bug: should be +\n",
        "worktree edit",
    )

    _commit(
        repo,
        "calc.py",
        "def add(a, b):\n    return a - b - 2  # bug: should be +\n",
        "conflicting edit",
    )

    with pytest.raises(RuntimeError, match="git rebase failed"):
        await kraft_builtins.mr_rebase(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            node_id="pre_mr_rebase",
            hook_point="on.mr.rebase",
            round=0,
            repo=str(repo),
            worktree=str(worktree),
            branch=branch,
        )
    assert wtree.base_ref(database) != git_read(repo, "rev-parse", "HEAD")


async def test_refresh_worktree_base_raises_rebase_conflict_a_runtimeerror_subclass(
    database, run_dirs, repo
):
    """.23: the three callers that can now *act* on a conflict need to tell
    one apart from any other git failure without matching on message text --
    and every existing `except RuntimeError` around this call must keep
    working unchanged, since `RebaseConflict` is a subclass."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)

    _commit(
        worktree,
        "calc.py",
        "def add(a, b):\n    return a - b - 1  # bug: should be +\n",
        "worktree edit",
    )

    _commit(
        repo,
        "calc.py",
        "def add(a, b):\n    return a - b - 2  # bug: should be +\n",
        "conflicting edit",
    )

    with pytest.raises(kraft_builtins.RebaseConflict):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")

    # The compatibility claim itself: a bare `except RuntimeError` --
    # every call site that predates this bead -- still catches it.
    try:
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
        raised = False
    except RuntimeError:
        raised = True
    assert raised


async def test_mr_rebase_reports_a_moved_base_when_the_node_bounces(database, run_dirs, repo):
    """A node that declares `rebase_bounce_to` must not run its later steps
    against a base the rebase just moved. A node with no bounce target wants the
    opposite, which is why the flag decides."""
    from kraft.executor.context import BASE_MOVED

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    _commit(repo, "moved.txt", "moved on\n", "moved on")
    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="open_mr",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=wtree.branch(database),
        has_rebase_bounce=True,
    )
    assert status == BASE_MOVED
    session = database.read(
        lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
    )
    assert session["status"] == "done"


async def test_mr_rebase_reports_done_when_it_moved_nothing(database, run_dirs, repo):
    """No movement, no stop: otherwise every `open_mr` would bounce once."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="open_mr",
        hook_point="on.mr.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=wtree.branch(database),
        has_rebase_bounce=True,
    )
    assert status == "done"


def _hanging_pre_rebase_hook(repo, seconds: float) -> None:
    """A real `pre-rebase` hook that sleeps -- `git rebase` runs it before it
    does anything else, so this hangs the rebase itself, the same shape a
    slow custom hook or a smudge/LFS filter would (Kraft-3llig)."""
    hook = repo / ".git" / "hooks" / "pre-rebase"
    hook.write_text(f"#!/bin/sh\nsleep {seconds}\nexit 0\n")
    hook.chmod(0o755)


async def test_mr_rebase_aborts_and_reports_capped_out_when_the_rebase_hangs(
    database, run_dirs, repo
):
    """Kraft-3llig review fix 1: a hanging pre-rebase hook must not hold the
    worker slot forever. `time_cap` bounds the `git rebase` subprocess
    itself; past it, the rebase is aborted and the stop is recorded the way
    every other time-capped task's is -- `capped_out`, `caps.REACHED`,
    `caps.TIME_CAPPED` -- not a new stop kind."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    _commit(repo, "moved.txt", "moved on\n", "moved on")
    _hanging_pre_rebase_hook(repo, seconds=30)
    hit = caps.Hit(scope="", field="time_cap_minutes", minutes=0, remaining_s=1.0)
    time_cap = caps.Deadline(at=caps.monotonic() + 1.0, hit=hit)

    started = caps.monotonic()
    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="draft_merge_request",
        hook_point="draft_merge_request.rebase.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=wtree.branch(database),
        time_cap=time_cap,
    )
    elapsed = caps.monotonic() - started

    assert status == caps.TIME_CAPPED
    # Stopped near the 1s cap, nowhere near the hook's 30s sleep.
    assert elapsed < 15
    session = database.read(
        lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
    )
    assert session["status"] == "capped_out"
    evts = database.read(lambda c: events.read_after(c, 0, "w1"))
    [reached] = [e for e in evts if e["type"] == caps.REACHED]
    assert reached["payload"]["node_id"] == "draft_merge_request"
    # The rebase was cleanly aborted, not left mid-operation.
    assert git_read(worktree, "status", "--porcelain") == ""
    assert not (worktree / ".git" / "rebase-merge").exists()
    assert not (worktree / ".git" / "rebase-apply").exists()
