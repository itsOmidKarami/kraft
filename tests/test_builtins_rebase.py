"""`kraft.builtins`' rebase side: `restore_branch`, `refresh_worktree_base`,
`upstream_head` and `mr_rebase` -- a worktree kept on its own branch and moved
onto the base the merge request targets."""

import asyncio
import contextlib
import os
import signal
import subprocess
from pathlib import Path

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


async def test_refresh_worktree_base_returns_head_when_already_up_to_date(database, run_dirs, repo):
    """Kraft-jypzx: "already contains the upstream head" is not "nothing to
    report" -- the caller stores the return as `base_ref`, so a stale value
    (a human rebased the branch by hand, outside Kraft) must still advance."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    upstream_head = git_read(repo, "rev-parse", "HEAD")
    # no origin remote exists here at all -- the already-pushed probe's
    # failure must be swallowed (expected_failure), not raised
    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result == upstream_head


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

    # Origin's main moves on too (Kraft-m4sdz): a move only the local checkout
    # has is invisible to `upstream_head`, so without the push this took the
    # "already up to date" exit and never reached the pushed-branch guard.
    _commit(repo, "moved.txt", "moved on\n", "moved on")
    _git(repo, "push", "-q", "origin", "main")

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


async def test_refresh_worktree_base_advances_stale_base_ref_after_a_hand_rebase(
    database, run_dirs, repo
):
    """Kraft-jypzx: an operator rebases the branch onto the new upstream head
    by hand (outside Kraft) to resolve something, then retries. The worktree's
    branch already contains that head, so there is nothing left to rebase --
    but `base_ref` must still catch up, or Kraft keeps diffing against the
    commit the branch forked from, not the one it is actually built on."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)

    new_head = _commit(repo, "moved.txt", "moved on\n", "moved on")
    # Stand in for the hand-rebase: the worktree's branch is fast-forwarded
    # onto the new upstream head directly, without going through
    # `refresh_worktree_base` -- so `base_ref` in the DB is left stale.
    _git(worktree, "rebase", new_head)

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert result == new_head


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


async def test_mr_rebase_stops_for_a_person_on_an_operation_in_progress(database, run_dirs, repo):
    """Kraft-xngty: refused state is no failure a fix loop may work on."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    _commit(worktree, "work.txt", "work\n", "worktree work")
    _commit(repo, "moved.txt", "moved on\n", "moved on")
    (Path(git_read(worktree, "rev-parse", "--absolute-git-dir")) / "rebase-merge").mkdir()

    status = await kraft_builtins.mr_rebase(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="n",
        hook_point="n.rebase",
        round=0,
        repo=str(repo),
        worktree=str(worktree),
        branch=wtree.branch(database),
    )

    assert status == "config_error"


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


@pytest.fixture
def hung(tmp_path):
    """Where a hanging hook below records the pid of its `sleep`. Each is
    killed once the test is done: git gives up on the hook, but nothing ends
    the hook itself, which would otherwise outlive the test."""
    pids = tmp_path / "hook-pids"
    yield pids
    for pid in pids.read_text().split() if pids.exists() else ():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), signal.SIGKILL)


def _sleep_recorded(pids, seconds: float) -> str:
    """The hook's last lines: the shell becomes the `sleep`, its pid in `pids`."""
    return f'echo $$ >> "{pids}"\nexec sleep {seconds}\n'


def _hanging_pre_rebase_hook(repo, pids, seconds: float) -> None:
    """A real `pre-rebase` hook that sleeps -- `git rebase` runs it before it
    does anything else, so this hangs the rebase itself, the same shape a
    slow custom hook or a smudge/LFS filter would (Kraft-3llig)."""
    hook = repo / ".git" / "hooks" / "pre-rebase"
    hook.write_text("#!/bin/sh\n" + _sleep_recorded(pids, seconds))
    hook.chmod(0o755)


async def test_mr_rebase_aborts_and_reports_capped_out_when_the_rebase_hangs(
    database, run_dirs, repo, hung
):
    """Kraft-3llig review fix 1: a hanging pre-rebase hook must not hold the
    worker slot forever. `time_cap` bounds the `git rebase` subprocess
    itself; past it, the rebase is aborted and the stop is recorded the way
    every other time-capped task's is -- `capped_out`, `caps.REACHED`,
    `caps.TIME_CAPPED` -- not a new stop kind."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    _commit(repo, "moved.txt", "moved on\n", "moved on")
    _hanging_pre_rebase_hook(repo, hung, seconds=30)
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


def _hanging_rebase_hook(repo, tmp_path, pids, phases: str, seconds: float) -> None:
    """A `reference-transaction` hook that sleeps `seconds`, once per phase
    named in `phases` ("rebase", "abort"), while a rebase is in progress.
    Kraft-ujep9: `git rebase --abort` fires it (a `post-checkout` hook does
    not, on current git), so a hook like this hangs the abort itself. Once per
    phase, so a regression that drops the abort's timeout still ends."""
    hook = repo / ".git" / "hooks" / "reference-transaction"
    marker = tmp_path / "hook-hung"
    hook.write_text(
        "#!/bin/sh\n"
        '[ -d "$(git rev-parse --git-dir)/rebase-merge" ] || exit 0\n'
        'case "$(ps -ww -o args= -p $PPID)" in\n'
        '  *"rebase --abort"*) phase=abort ;;\n'
        "  *rebase*) phase=rebase ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
        f'case " {phases} " in *" $phase "*) ;; *) exit 0 ;; esac\n'
        f'[ -e "{marker}.$phase" ] && exit 0\n'
        f'touch "{marker}.$phase"\n' + _sleep_recorded(pids, seconds)
    )
    hook.chmod(0o755)


@pytest.mark.parametrize(
    ("phases", "error", "timeout"),
    [
        ("abort", kraft_builtins.RebaseConflict, None),
        ("rebase abort", kraft_builtins.RebaseTimedOut, 1.0),
    ],
    ids=["conflict", "timed_out"],
)
async def test_a_hanging_rebase_abort_is_bounded_and_says_so(
    tmp_path, monkeypatch, database, run_dirs, repo, hung, phases, error, timeout
):
    """Kraft-ujep9: the abort after a conflict or a timed-out rebase runs under
    its own fixed timeout, and past it raises the caller's own error class,
    saying the worktree was left mid-rebase -- not a slot held forever."""
    monkeypatch.setattr(kraft_builtins, "REBASE_ABORT_TIMEOUT_S", 1.0)
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    _commit(worktree, "calc.py", "def add(a, b):\n    return a - b - 1\n", "worktree edit")
    if error is kraft_builtins.RebaseConflict:
        _commit(repo, "calc.py", "def add(a, b):\n    return a - b - 2\n", "conflicting edit")
    else:
        _commit(repo, "moved.txt", "moved on\n", "moved on")
    _hanging_rebase_hook(repo, tmp_path, hung, phases, seconds=10)

    started = caps.monotonic()
    with pytest.raises(error, match="--abort` also timed out after 1s.*left mid-rebase"):
        await kraft_builtins.refresh_worktree_base(
            worktree, repo, wtree.branch(database), base="main", timeout=timeout
        )
    assert caps.monotonic() - started < 8


def _mr_rebase(database, run_dirs, repo, worktree, branch):
    return kraft_builtins.mr_rebase(
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


async def test_mr_rebase_moves_the_base_past_untracked_session_notes(database, run_dirs, repo):
    """Every agent session leaves an untracked `.engineering/sessions/` note,
    which nothing ignores in an adopter's repo: counted as uncommitted, it
    skipped every rebase, and the merge request opened on a stale base."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    note = worktree / ".engineering" / "sessions" / "s0.md"
    note.parent.mkdir(parents=True)
    note.write_text("what the agent did\n")
    new_head = _commit(repo, "upstream.txt", "landed meanwhile\n", "upstream change")

    assert await _mr_rebase(database, run_dirs, repo, worktree, branch) == "done"
    assert wtree.base_ref(database) == new_head
    assert git_read(worktree, "merge-base", "--is-ancestor", new_head, "HEAD") == ""
    assert note.read_text() == "what the agent did\n"


@pytest.mark.parametrize("base_commits_one", [True, False], ids=["base-commits-it", "base-not"])
async def test_the_setups_lockfile_is_set_aside_for_the_rebase(
    database, run_dirs, repo, base_commits_one
):
    """Kraft tells a repo with no `uv.lock` to commit one. Once its base
    does, the setup's untracked `uv.lock` in every item's worktree made git
    refuse the rebase ("could not detach HEAD"). The base's committed copy
    wins; with none, the setup's is put back as it was."""
    from kraft.adapters import forge

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    before = await forge.lockfile_digests(worktree)
    (worktree / "uv.lock").write_text("by the setup\n")
    await forge.record_setup_writes(worktree, before)
    if base_commits_one:
        new_head = _commit(repo, "uv.lock", "committed on the base\n", "commit a lockfile")
    else:
        new_head = _commit(repo, "upstream.txt", "landed meanwhile\n", "upstream change")

    result = await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")

    assert result == new_head
    assert git_read(worktree, "merge-base", "--is-ancestor", new_head, "HEAD") == ""
    expected = "committed on the base\n" if base_commits_one else "by the setup\n"
    assert (worktree / "uv.lock").read_text() == expected
    assert _porcelain(worktree) == ([] if base_commits_one else ["?? uv.lock"])


async def test_an_untracked_file_the_base_would_overwrite_stops_for_a_person(
    database, run_dirs, repo
):
    """Not a silent skip, and not a conflict an agent is sent to resolve:
    git refuses to start, nothing moves, the file is kept, and `mr_rebase`
    stops the item for a person with git's own words."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    (worktree / "notes.txt").write_text("the agent's, never added\n")
    _commit(repo, "notes.txt", "the base's\n", "base adds notes.txt")
    head = git_read(worktree, "rev-parse", "HEAD")

    with pytest.raises(kraft_builtins.RebaseBlocked) as blocked:
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    # A stop reason is read by its first line, which ended at git's "would be
    # overwritten by checkout:", with the file on the line after it.
    assert str(blocked.value).splitlines()[0] == (
        "untracked notes.txt would be overwritten by the base branch; move or delete it, then retry"
    )
    assert "could not detach HEAD" in str(blocked.value)
    assert git_read(worktree, "rev-parse", "HEAD") == head
    assert (worktree / "notes.txt").read_text() == "the agent's, never added\n"

    assert await _mr_rebase(database, run_dirs, repo, worktree, branch) == "config_error"
    log = database.read(
        lambda c: c.execute("SELECT log_path FROM worker_sessions WHERE id='s1'").fetchone()
    )["log_path"]
    assert Path(log).read_text().startswith("untracked notes.txt would be overwritten")


async def test_a_lockfile_recorded_with_no_digest_is_never_set_aside(database, run_dirs, repo):
    """A record from 1.5.0rc14 names `uv.lock` with no digest, so an agent's
    edit to it cannot be told from the setup's own. Set aside, the base's
    committed copy would replace it and the agent's edit would be lost:
    git's refusal, a stop for a person, keeps it."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    gitdir = Path(git_read(worktree, "rev-parse", "--absolute-git-dir"))
    (gitdir / "kraft-setup-wrote").write_text("uv.lock\n")
    (worktree / "uv.lock").write_text("the setup's, then the agent's edit\n")
    _commit(repo, "uv.lock", "committed on the base\n", "commit a lockfile")

    with pytest.raises(kraft_builtins.RebaseBlocked, match="untracked uv.lock"):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert (worktree / "uv.lock").read_text() == "the setup's, then the agent's edit\n"


async def test_a_set_aside_a_killed_server_left_is_cleared(database, run_dirs, repo):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    stale = Path(git_read(worktree, "rev-parse", "--absolute-git-dir")) / "kraft-set-aside"
    (stale / "sub").mkdir(parents=True)
    (stale / "sub" / "uv.lock").write_text("left by a SIGKILL\n")
    _commit(repo, "upstream.txt", "landed meanwhile\n", "upstream change")

    await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")

    assert not stale.exists()


async def test_a_failing_put_back_never_hides_the_rebases_own_error(
    database, run_dirs, repo, monkeypatch, caplog
):
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    (worktree / "notes.txt").write_text("the agent's\n")
    _commit(repo, "notes.txt", "the base's\n", "base adds notes.txt")

    def broken(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(kraft_builtins, "_put_back_setup_lockfiles", broken)
    with pytest.raises(kraft_builtins.RebaseBlocked):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    assert "could not put the setup's lockfiles back" in caplog.text
