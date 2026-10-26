"""The local worker's job table starts, reaps, records and holds sessions."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time

import psutil
import pytest
from support.local_worker import bare_env

from kraft.remote.local_jobs import LocalJobs, SpawnRefused


def _wait(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_spawn_runs_the_argv_with_exactly_the_given_env(tmp_path):
    jobs = LocalJobs(tmp_path / "state")
    log = tmp_path / "session.log"
    log.write_bytes(b"")
    jobs.spawn(
        "w1/s1",
        ["/bin/sh", "-c", 'echo "$ONLY"; echo "${HOME-unset}"'],
        cwd=str(tmp_path),
        env={"ONLY": "yes", "PATH": os.environ["PATH"]},
        log_path=str(log),
    )
    assert _wait(lambda: jobs.reap() == ["w1/s1"] or not jobs.running())
    assert log.read_text().split() == ["yes", "unset"]
    assert jobs.running() == []
    assert list((tmp_path / "state" / "jobs").glob("*.json")) == []


def test_spawn_records_and_reap_drops_the_session(tmp_path):
    jobs = LocalJobs(tmp_path)
    log = tmp_path / "l"
    log.write_bytes(b"")
    spawned = jobs.spawn(
        "w/s", ["sleep", "30"], cwd=str(tmp_path), env=bare_env(), log_path=str(log)
    )
    try:
        assert psutil.Process(spawned.pid).ppid() == os.getpid()
        assert os.getpgid(spawned.pid) == spawned.pid
        assert (tmp_path / "jobs" / "w__s.json").exists()
        assert jobs.running() == ["w/s"] and jobs.reap() == []
    finally:
        os.killpg(spawned.pid, signal.SIGKILL)
    assert _wait(lambda: "w/s" in jobs.reap() or not jobs.running())
    assert not (tmp_path / "jobs" / "w__s.json").exists()


def test_recover_keeps_only_live_identities(tmp_path):
    first = LocalJobs(tmp_path)
    log = tmp_path / "l"
    log.write_bytes(b"")
    alive = first.spawn(
        "w/alive", ["sleep", "30"], cwd=str(tmp_path), env=bare_env(), log_path=str(log)
    )
    dead = first.spawn("w/dead", ["true"], cwd=str(tmp_path), env=bare_env(), log_path=str(log))
    first._children["w/dead"].wait()
    try:
        second = LocalJobs(tmp_path)  # a restarted worker: no children of its own
        assert second.recover() == ["w/alive"]
        assert second.running() == ["w/alive"]
        assert not (tmp_path / "jobs" / "w__dead.json").exists()
        assert dead.pid != alive.pid
    finally:
        os.killpg(alive.pid, signal.SIGKILL)
        first._children["w/alive"].wait()


@pytest.mark.parametrize(
    "start_shift, held",
    [(0.0, True), (-3600.0, False)],
    ids=["same-process-adopted", "pid-reused-by-another-process"],
)
def test_hold_adopts_an_unknown_live_process_only_by_identity(tmp_path, start_shift, held):
    outside = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        start = psutil.Process(outside.pid).create_time() + start_shift
        jobs = LocalJobs(tmp_path)
        assert jobs.hold("w/s", pid=outside.pid, pid_start_time=start) is held
        assert (jobs.running() == ["w/s"]) is held
        assert (tmp_path / "jobs" / "w__s.json").exists() is held
    finally:
        outside.kill()
        outside.wait()


@pytest.mark.parametrize(
    "cwd_is, missing",
    [("absent", "working directory"), ("a-file", None)],
    ids=["missing-working-directory", "cwd-is-a-file"],
)
def test_spawn_refused(tmp_path, cwd_is, missing):
    cwd = tmp_path / "nope"
    if cwd_is == "a-file":
        cwd.write_text("x")
    log = tmp_path / "l"
    log.write_bytes(b"")
    jobs = LocalJobs(tmp_path / "state")
    with pytest.raises(SpawnRefused) as caught:
        jobs.spawn(
            "w/s", [sys.executable, "-c", "pass"], cwd=str(cwd), env=bare_env(), log_path=str(log)
        )
    assert caught.value.missing == missing
    assert str(caught.value)
    assert jobs.running() == []


async def test_stop_kills_what_the_session_backgrounded_even_when_it_ignores_sigterm(tmp_path):
    jobs = LocalJobs(tmp_path)
    log = tmp_path / "l"
    log.write_bytes(b"")
    pidfile = tmp_path / "member"
    script = f"(trap '' TERM; exec sleep 30) & echo $! > {pidfile}; wait"
    leader = jobs.spawn(
        "w/s", ["/bin/sh", "-c", script], cwd=str(tmp_path), env=bare_env(), log_path=str(log)
    )
    assert _wait(lambda: pidfile.exists() and pidfile.read_text().strip())
    member = psutil.Process(int(pidfile.read_text()))
    try:
        assert await jobs.stop("w/s", grace=0.3) is True
        # macOS answers `killpg` for a process still tearing down as if it were gone, so under
        # load the member can be seen briefly after the stop returns.
        assert _wait(lambda: not member.is_running() or member.status() == psutil.STATUS_ZOMBIE)
        assert jobs.running() == []
    finally:
        with contextlib.suppress(OSError):
            os.killpg(leader.pid, signal.SIGKILL)


def test_a_key_that_is_running_is_not_started_twice(tmp_path):
    jobs = LocalJobs(tmp_path)
    log = tmp_path / "l"
    log.write_bytes(b"")
    first = jobs.spawn("w/s", ["sleep", "30"], cwd=str(tmp_path), env=bare_env(), log_path=str(log))
    try:
        with pytest.raises(SpawnRefused, match="already running"):
            jobs.spawn("w/s", ["sleep", "30"], cwd=str(tmp_path), env=bare_env(), log_path=str(log))
        assert jobs.running() == ["w/s"] and jobs._children["w/s"].pid == first.pid
    finally:
        os.killpg(first.pid, signal.SIGKILL)
        jobs._children["w/s"].wait()


def test_two_tables_removing_one_record_do_not_fail(tmp_path):
    a = LocalJobs(tmp_path)
    b = LocalJobs(tmp_path)
    log = tmp_path / "l"
    log.write_bytes(b"")
    a.spawn("w/s", ["true"], cwd=str(tmp_path), env=bare_env(), log_path=str(log))
    a._children["w/s"].wait()
    b.recover()  # sees a dead process: deletes the record
    assert _wait(lambda: a.reap() == ["w/s"])


@pytest.mark.parametrize(
    "key",
    ["../x", "w/../../x", "w/s/t", "w", ""],
    ids=["parent", "nested-parent", "three-parts", "no-slash", "empty"],
)
def test_a_key_that_is_not_a_session_ref_never_becomes_a_path(tmp_path, key):
    log = tmp_path / "l"
    log.write_bytes(b"")
    jobs = LocalJobs(tmp_path / "state")
    with pytest.raises(SpawnRefused):
        jobs.spawn(key, ["true"], cwd=str(tmp_path), env=bare_env(), log_path=str(log))
    assert jobs.hold(key, pid=os.getpid(), pid_start_time=None) is False
    assert list((tmp_path / "state" / "jobs").iterdir()) == []
