"""What the server does to existing state as it starts."""

import asyncio
import json
import subprocess
import sys

import psutil
import pytest
from support.harness import IN_PLACE, git, make_item, make_repo, v1_named_chain, write

from kraft import db, store
from kraft.adapters import hook_install
from kraft.paths import RunDirs

_OLD = [sys.executable, "-m", "kraft", "admin", "permission-hook", "cursor"]


@pytest.fixture
def old_cursor_hook(tmp_path):
    """A worktree whose `.cursor/hooks.json` an earlier Kraft wrote, before
    the server starts: its entry ran the hook with plain `-m`."""
    path = tmp_path / "run/worktrees/w1/.cursor/hooks.json"
    path.parent.mkdir(parents=True)
    entry = {"command": hook_install.command_of(_OLD), "timeout": 10, "failClosed": True}
    path.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [entry]}}))
    return path


def test_start_up_gives_an_earlier_krafts_cursor_hook_safe_path(old_cursor_hook, client):
    """An upgrade must reach worktrees that already exist, or a session adopted
    across the restart keeps a hook a planted module can answer."""
    (entry,) = json.loads(old_cursor_hook.read_text())["hooks"]["preToolUse"]
    assert entry["command"] == hook_install.command_of([sys.executable, "-I", *_OLD[1:]])


@pytest.fixture
def refresh_raises(monkeypatch):
    def boom(_worktrees):
        raise RecursionError("a hooks file built to break the parser")

    monkeypatch.setattr(hook_install, "refresh_cursor_hooks", boom)


def test_a_failed_hook_refresh_never_stops_the_server_starting(refresh_raises, client):
    """Every file the refresh reads is one a worker wrote: one that broke it
    must not stop every later start until someone deletes it."""
    assert client.get("/api/health").status_code == 200


@pytest.fixture
def stale_scratch(tmp_path, request):
    """What a server killed mid-rebase left under `run/scratch`: a throwaway
    worktree holding an in-place item's branch, one git no longer knows, and a
    link someone put there. `request.param`: the repository's git dir is
    apart from its checkout, as a submodule's is."""
    repo = make_repo(tmp_path)
    if getattr(request, "param", False):
        git(repo, "init", "-q", f"--separate-git-dir={tmp_path / 'apart.git'}")
    git(repo, "branch", "kraft/w1")
    scratch = tmp_path / "run/scratch"
    git(repo, "worktree", "add", "-q", str(scratch / "w1-0dead0ff"), "kraft/w1")
    (scratch / "w2-0dead0ff").mkdir()
    # A link to a worktree of the person's: never Kraft's to remove.
    git(repo, "worktree", "add", "-q", "-b", "theirs", str(tmp_path / "mine"))
    write(tmp_path / "mine", "keep.txt", "the person's\n")
    (scratch / "w3-0dead0ff").symlink_to(tmp_path / "mine")
    return repo


@pytest.mark.parametrize(
    "stale_scratch", [False, True], ids=["plain", "git-dir-apart"], indirect=True
)
def test_start_up_removes_the_rebase_worktrees_a_killed_server_left(stale_scratch, client):
    """One would pin its item's branch until that item's next rebase."""
    assert client.get("/api/health").status_code == 200
    assert git(stale_scratch, "worktree", "list", "--porcelain").count("worktree ") == 2
    scratch = stale_scratch.parent / "run/scratch"
    assert [p.name for p in scratch.iterdir()] == ["w3-0dead0ff"], "a link is never Kraft's"
    assert (stale_scratch.parent / "mine/keep.txt").exists()


@pytest.fixture
def left_running(tmp_path, repo):
    """An in-place agent a server stopped between its pause's write and its
    kill: still running, its session written paused."""
    agent = subprocess.Popen(["sleep", "30"], start_new_session=True)
    started = psutil.Process(agent.pid).create_time()

    async def seed():
        run_dirs = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(run_dirs.db)
        try:
            chain = v1_named_chain(run_dirs.base / "t")
            it = await make_item(
                database, run_dirs, chain, "implementation", repo=repo, target=IN_PLACE
            )
            await it.session("s1", "implementation.main.implement", running=(agent.pid, started))
            await database.write(lambda c: store.pause_work_item(c, it.id, ["s1"]))
        finally:
            await database.close()

    asyncio.run(seed())
    yield agent
    agent.kill()
    agent.wait()


def test_start_up_stops_an_in_place_agent_a_stopped_server_left(left_running, client):
    """Nothing else ends it, and it writes in the checkout with the lease free."""
    assert left_running.wait(timeout=5) is not None


@pytest.fixture
def on_probation(request, tmp_path, monkeypatch):
    """A record an update left: it was installed and the restart began (param
    False: it never got that far), and no helper confirmed the server that came
    up. Placed before the server starts, so the fixture has to come before
    `client` in a test's arguments."""
    from types import SimpleNamespace

    from kraft import when_free
    from kraft.api import deps, startup
    from kraft.worker import reattach

    run_dirs = RunDirs(tmp_path / "run").ensure()
    handed_off = getattr(request, "param", True)
    snapshot = run_dirs.base / f"{when_free.SNAPSHOT_PREFIX}1"
    (snapshot / "config").mkdir(parents=True)
    when_free.write_rollback(
        run_dirs,
        old_pid=1,
        from_version="2.2.0",
        target=None,
        kind="settings",
        config_snapshot=str(snapshot / "config"),
        **({"handed_off": True} if handed_off else {}),
    )
    started, spawned = [], []
    real = startup.start_background
    monkeypatch.setattr(startup, "start_background", lambda app: started.append(1) or real(app))

    async def resumes_one(*_a, **_k):
        return reattach.ReattachSummary(resumed_work_items=["w1"]), {}

    monkeypatch.setattr(reattach, "reattach", resumes_one)
    monkeypatch.setattr(
        deps, "spawn", lambda app, wid, coro: spawned.append(wid) or deps.discard(coro)
    )
    monkeypatch.setattr(when_free, "PROBATION_S", 3600)
    return SimpleNamespace(
        run_dirs=run_dirs,
        started=started,
        spawned=spawned,
        handed_off=handed_off,
        snapshot=snapshot,
    )


def _wait_for(check, seconds=5.0):
    import time

    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_leftover_record(on_probation, monkeypatch, client):
    """A server that starts on a record nobody confirmed answers, and starts no
    walk or poller until `PROBATION_S` passes with it up; then it goes on and
    the record is gone."""
    from kraft import when_free
    from kraft.api import startup

    st = client.app.state
    assert st.probation is True
    assert client.get("/api/health").status_code == 200
    assert [getattr(st, name) for name in startup.BACKGROUND_TASKS] == [None] * len(
        startup.BACKGROUND_TASKS
    )
    assert on_probation.spawned == [] and on_probation.started == []
    assert when_free.read_rollback(on_probation.run_dirs) is not None


@pytest.mark.parametrize(
    "on_probation",
    [
        pytest.param(False, id="record-not-handed-off-starts-no-probation"),
        pytest.param(True, id="handed-off-record-starts-probation"),
    ],
    indirect=True,
)
def test_probation(on_probation, client):
    """Only a restart this feature handed off starts the new server on
    probation; a record that never got that far is a crashed attempt, and a
    manual restart over it starts everything at once and confirms nothing."""
    from kraft import when_free

    handed_off = on_probation.handed_off
    st = client.app.state
    assert st.probation is handed_off
    assert on_probation.started == ([] if handed_off else [1])
    assert on_probation.spawned == ([] if handed_off else ["w1"])
    assert when_free.outcome(st) is None
    assert (when_free.read_rollback(on_probation.run_dirs) is not None) is handed_off
    assert on_probation.snapshot.is_dir() is handed_off


@pytest.fixture
def short_probation(monkeypatch):
    from kraft import when_free

    monkeypatch.setattr(when_free, "PROBATION_S", 0.2)


def test_probation_ends_by_itself(on_probation, short_probation, client):
    from kraft import when_free

    _wait_for(lambda: when_free.read_rollback(on_probation.run_dirs) is None)
    st = client.app.state
    assert st.probation is False
    assert on_probation.started == [1] and on_probation.spawned == ["w1"]
    assert st.queue_task is not None
    assert when_free.outcome(st)["status"] == "applied"


def test_confirm_ends_it(on_probation, client):
    from kraft import when_free

    r = client.post("/api/update/confirm")

    assert (r.status_code, r.json()) == (200, {"confirmed": True})
    st = client.app.state
    assert st.probation is False and st.queue_task is not None
    assert on_probation.started == [1] and on_probation.spawned == ["w1"]
    assert when_free.read_rollback(on_probation.run_dirs) is None
    assert when_free.outcome(st)["from"] == "2.2.0"
    assert client.post("/api/update/confirm").status_code == 409


def test_shutdown_during_probation_is_clean(on_probation, client):
    """Nothing was started, so shutdown cancels only what exists; leaving the
    client raises if it does not."""
    assert client.app.state.probation is True


@pytest.fixture
def no_link_installed(monkeypatch):
    """The server starts its own link, as outside a test: no `DirectLink` stands in, and
    the worker it asks for is one on a thread of this test."""
    from support.local_worker import ThreadedWorker

    from kraft.remote import local_link

    local_link.install(None)
    workers = []
    monkeypatch.setattr(
        local_link,
        "_start_worker",
        lambda run_base, install: workers.append(ThreadedWorker(run_base, install)),
    )
    yield workers
    for worker in workers:
        worker.close()


def test_startup_local_link_none_installed_waits_for_the_worker_before_resuming(
    no_link_installed, client
):
    from kraft.remote import local_link

    link = local_link.current()
    assert isinstance(link, local_link.LocalLink)
    assert link.connected, "the lifespan yielded before the worker was up"
    assert len(no_link_installed) == 1
    assert client.app.state.local_link_task is not None


def test_startup_local_link_direct_link_installed_starts_no_worker(client):
    from kraft.remote import local_link

    assert isinstance(local_link.current(), local_link.DirectLink)
    assert client.app.state.local_link_task is None


def test_startup_local_link_boot_fails_after_the_link(no_link_installed, tmp_path, monkeypatch):
    """A boot that fails half-way leaves no link task respawning workers for a server that
    is not there."""
    from support import api as api_support

    from kraft.remote import local_link
    from kraft.worker import reattach

    ended = []

    async def forever(self):
        try:
            await asyncio.Event().wait()
        finally:
            ended.append(True)

    async def boom(*args, **kwargs):
        raise RuntimeError("boot failed after the link")

    monkeypatch.setattr(local_link.LocalLink, "run", forever)
    monkeypatch.setattr(reattach, "reattach", boom)
    # Any exception: the egress proxy a failed boot never closed adds an error of its own.
    with pytest.raises(Exception), api_support._client(tmp_path, monkeypatch):  # noqa: B017
        pass

    assert local_link.current() is None
    assert ended == [True]


def test_boot_deletes_a_stop_now_marker_no_server_read(tmp_path, client_factory):
    """`kraft admin stop --now` writes the marker before the SIGTERM; one that never reached a
    server must not turn the next, clean stop into a `--now`."""
    marker = RunDirs(tmp_path / "run").stop_now
    marker.parent.mkdir(parents=True)
    marker.write_text("")

    with client_factory():
        assert not marker.exists()


@pytest.mark.parametrize(
    ("stop_now", "kept"), [(False, True), (True, False)], ids=["clean-stop-keeps", "stop-now-ends"]
)
def test_a_stop_now_marker_ends_the_local_sessions_a_clean_stop_keeps(
    tmp_path, client_factory, monkeypatch, stop_now, kept
):
    from kraft.adapters import subprocess as sp

    seen = []
    real = sp.server_stopping
    monkeypatch.setattr(sp, "server_stopping", lambda **kw: seen.append(kw) or real(**kw))
    with client_factory() as client:
        marker = client.app.state.run_dirs.stop_now
        if stop_now:
            marker.write_text("")
    assert seen == [{"keep_local": kept}]
    assert not marker.exists()


@pytest.fixture
def client_factory(tmp_path, monkeypatch):
    from support.api import _client

    return lambda: _client(tmp_path, monkeypatch)
