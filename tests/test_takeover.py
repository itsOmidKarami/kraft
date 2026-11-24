"""`kraft.takeover`: the lock a person holds on a stopped item's agent session,
what refuses it, and what the exit leaves behind."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import psutil
import pytest
from support.harness import git, write

from kraft import executor, harness, store, takeover
from kraft.executor import dispatch

CHAIN = """
- id: work
  kind: exec
  tasks:
    - {id: impl, kind: agent, harness: fake, prompt: Build it.}
"""

TWO_TASKS = """
- id: work
  kind: exec
  tasks:
    - {id: impl, kind: agent, harness: fake, prompt: Build it.}
    - {id: docs, kind: agent, harness: fake, prompt: Document it.}
"""

TASK = "work.main.impl"
LAUNCH = executor.LaunchContext(repo_entry=None)
ME = (os.getpid(), psutil.Process().create_time())


def _dead_pid() -> tuple[int, float]:
    """A pid that ran and is gone, with the start time it had."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    started = psutil.Process(proc.pid).create_time()
    proc.wait()
    return proc.pid, started


async def _item(item_on, *, status="paused", chain=CHAIN, session="paused", worktree=True):
    """A stopped item on `chain`, its worktree a real git worktree on the item's branch."""
    it = await item_on(chain, "work", status=status)
    if session is not None:
        await it.session("s0", TASK, session)
    if worktree:
        git(it.repo, "worktree", "add", "-q", "-b", store.branch_for(it.row()), str(it.worktree))
    return it


def _log_init(it, sid, provider_id):
    log = it.run_dirs.logs / f"{sid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"type": "system", "subtype": "init", "session_id": provider_id}))


def _prepare(it, *, pid_of=ME, starting=(), walk_running=False):
    return takeover.prepare(
        it.database,
        it.run_dirs,
        it.row(),
        harness.load(None),
        launch=LAUNCH,
        pid=pid_of[0],
        pid_start=pid_of[1],
        starting=set(starting),
        walk_running=walk_running,
    )


def _lock(it):
    return it.database.read(lambda c: store.takeover_lock(c, it.id))


async def _sandboxed(it, monkeypatch):
    monkeypatch.setattr(dispatch, "item_sandbox", lambda row, launch: {"kind": "docker"})


async def _remote(it, monkeypatch):
    await it.database.write(lambda c: c.execute("UPDATE worker_sessions SET runner = 'box'"))


async def _no_session(it, monkeypatch):
    await it.database.write(lambda c: c.execute("DELETE FROM worker_sessions"))


async def _gemini(it, monkeypatch):
    await it.database.write(lambda c: c.execute("UPDATE worker_sessions SET harness = 'gemini'"))


async def _taken(it, monkeypatch):
    await it.database.write(
        lambda c: store.take_takeover_lock(
            c,
            it.id,
            pid=ME[0],
            pid_start=ME[1],
            session_id="U0",
            resumed_from=None,
            branch="b",
            tip="t",
        )
    )


async def _worker_alive(it, monkeypatch):
    await it.session("s1", TASK, running=ME)


async def _escalating(it, monkeypatch):
    await it.session("e1", "escalation", running=(1, 0.0))


@pytest.mark.parametrize(
    ("setup", "kwargs", "chain", "status", "message"),
    [
        pytest.param(
            None,
            {},
            CHAIN,
            "active",
            "work item is active; takeover needs paused or needs_human",
            id="wrong-status",
        ),
        pytest.param(
            _sandboxed,
            {},
            CHAIN,
            "paused",
            "doesn't support sandboxed items yet \\(docker\\)",
            id="sandboxed",
        ),
        pytest.param(
            _remote,
            {},
            CHAIN,
            "paused",
            "this step ran on box; takeover needs a local worktree",
            id="remote",
        ),
        pytest.param(
            _no_session,
            {},
            CHAIN,
            "paused",
            "nothing has run on this step yet; resume it instead",
            id="no-session",
        ),
        pytest.param(
            _gemini,
            {},
            CHAIN,
            "paused",
            "takeover isn't supported for gemini yet",
            id="no-interactive-block",
        ),
        pytest.param(
            None,
            {},
            TWO_TASKS,
            "paused",
            r"this step has several tasks \(work.main.impl, work.main.docs\)",
            id="several-tasks",
        ),
        pytest.param(
            _taken, {}, CHAIN, "paused", "a person has this item's session open", id="already-live"
        ),
        pytest.param(
            _worker_alive, {}, CHAIN, "paused", "last session is still stopping", id="live-worker"
        ),
        pytest.param(
            None,
            {"walk_running": True},
            CHAIN,
            "paused",
            "last session is still stopping",
            id="walk-running",
        ),
        pytest.param(
            None,
            {"starting": {"w1"}},
            CHAIN,
            "paused",
            "last session is still stopping",
            id="start-queue",
        ),
        pytest.param(
            _escalating,
            {},
            CHAIN,
            "paused",
            "an escalation is running on this item",
            id="escalation",
        ),
    ],
)
async def test_prepare_refuses(item_on, monkeypatch, setup, kwargs, chain, status, message):
    it = await _item(item_on, status=status, chain=chain)
    if setup is not None:
        await setup(it, monkeypatch)
    before = _lock(it)

    with pytest.raises(takeover.TakeoverRefused, match=message):
        await _prepare(it, **kwargs)

    # Nothing was taken, and the status is the one it had.
    after = _lock(it)
    assert (after and after["takeover_session_id"]) == (before and before["takeover_session_id"])
    assert it.status() == status
    assert not it.events("takeover_started")


async def test_a_takeover_lock_round_trips_and_its_session_lands_on_the_worker_row(item_on):
    it = await _item(item_on)
    db = it.database
    assert _lock(it) is None

    await db.write(
        lambda c: store.take_takeover_lock(
            c,
            it.id,
            pid=42,
            pid_start=1.5,
            session_id="U",
            resumed_from="s0",
            branch="kraft/x",
            tip="abc",
        )
    )

    lock = _lock(it)
    assert (lock["takeover_pid"], lock["takeover_pid_start"], lock["takeover_session_id"]) == (
        42,
        1.5,
        "U",
    )
    assert (lock["takeover_resumed_from"], lock["takeover_branch"], lock["takeover_tip"]) == (
        "s0",
        "kraft/x",
        "abc",
    )
    assert lock["takeover_started_at"]
    await db.write(lambda c: store.clear_takeover_lock(c, it.id))
    assert _lock(it) is None

    await db.write(lambda c: store.set_cli_session_id(c, it.id, "s0", "U"))
    assert it.sessions()[0]["cli_session_id"] == "U"


async def test_prepare_forks_the_paused_session(item_on):
    it = await _item(item_on)
    _log_init(it, "s0", "X")

    prepared = await _prepare(it)

    assert prepared.argv[:3] == ["claude", "--resume", "X"]
    assert prepared.argv[-2:] == ["--session-id", prepared.session_id]
    assert prepared.resumed_from == "s0"
    assert prepared.cwd == str(it.worktree)
    lock = _lock(it)
    assert (lock["takeover_pid"], lock["takeover_session_id"]) == (ME[0], prepared.session_id)
    assert lock["takeover_resumed_from"] == "s0"
    assert it.events("takeover_started")[0]["payload"] == {
        "session_id": prepared.session_id,
        "resumed_from": "s0",
    }
    assert it.status() == "paused"


async def test_prepare_starts_fresh_when_nothing_is_resumable(item_on):
    it = await _item(item_on, status="needs_human", session="failed")
    _log_init(it, "s0", "X")

    prepared = await _prepare(it)

    assert prepared.resumed_from is None
    assert "--resume" not in prepared.argv
    assert prepared.argv == ["claude", "--session-id", prepared.session_id]
    assert _lock(it)["takeover_resumed_from"] is None


async def test_only_one_of_two_concurrent_prepares_wins(item_on):
    it = await _item(item_on)

    results = await asyncio.gather(_prepare(it), _prepare(it), return_exceptions=True)

    won = [r for r in results if isinstance(r, takeover.Prepared)]
    refused = [r for r in results if isinstance(r, takeover.TakeoverRefused)]
    assert (len(won), len(refused)) == (1, 1)
    assert _lock(it)["takeover_session_id"] == won[0].session_id


async def test_live_releases_a_stale_lock(item_on):
    """The CLI was killed hard: the next check finds its pid gone and frees the item."""
    it = await _item(item_on)
    await _prepare(it, pid_of=_dead_pid())
    assert _lock(it) is not None

    assert await takeover.live(it.database, it.run_dirs, it.id, harness.load(None)) is None

    assert _lock(it) is None
    assert it.events("takeover_ended")
    # The item is free to be taken over again.
    assert await _prepare(it)


async def test_live_keeps_a_lock_whose_cli_is_running(item_on):
    it = await _item(item_on)
    await _prepare(it)

    assert await takeover.live(it.database, it.run_dirs, it.id, harness.load(None)) is not None
    assert _lock(it) is not None


@pytest.mark.parametrize(
    ("resumed", "written", "recorded"),
    [
        pytest.param(True, True, True, id="forked-and-written"),
        pytest.param(True, False, False, id="forked-but-quit-before-a-turn"),
        pytest.param(False, True, False, id="fresh"),
    ],
)
async def test_release_records(item_on, tmp_path, monkeypatch, resumed, written, recorded):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    it = await _item(
        item_on,
        status="paused" if resumed else "needs_human",
        session="paused" if resumed else "failed",
    )
    _log_init(it, "s0", "X")
    prepared = await _prepare(it)
    if written:
        write(home, f".claude/projects/some-folder/{prepared.session_id}.jsonl", "{}\n")

    released = await takeover.release(it.database, it.run_dirs, it.id, harness.load(None))

    assert released.recorded is recorded
    assert released.status == it.status()
    row = it.sessions()[0]
    assert row["cli_session_id"] == (prepared.session_id if recorded else None)
    assert _lock(it) is None
    assert it.events("takeover_ended")[0]["payload"] == {"recorded": recorded, "warnings": []}


def _uncommit(it):
    write(it.worktree, "scratch.txt", "left behind\n")


def _switch(it):
    git(it.worktree, "checkout", "-q", "-b", "other")


def _rewrite(it):
    git(it.worktree, "commit", "-q", "--amend", "--allow-empty", "-m", "rewritten")


@pytest.mark.parametrize(
    ("change", "warning"),
    [
        pytest.param(_uncommit, "uncommitted changes in the worktree", id="uncommitted"),
        pytest.param(_switch, "HEAD is not on ", id="other-branch"),
        pytest.param(_rewrite, "history was rewritten since the takeover started", id="rewritten"),
    ],
)
async def test_release_warns(item_on, change, warning):
    it = await _item(item_on)
    await _prepare(it)
    change(it)

    released = await takeover.release(it.database, it.run_dirs, it.id, harness.load(None))

    assert len(released.warnings) == 1
    assert released.warnings[0].startswith(warning)


async def test_release_of_a_clean_return_warns_of_nothing(item_on):
    it = await _item(item_on)
    await _prepare(it)
    git(it.worktree, "commit", "-q", "--allow-empty", "-m", "person's work")

    released = await takeover.release(it.database, it.run_dirs, it.id, harness.load(None))

    assert released.warnings == []
