"""`kraft.worker.reattach`: what a restart does with every session it finds
pending or running -- adopt a live child, resolve a dead one from its files,
or declare it unknown and stop the item for a human."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager

import psutil
import pytest
from support.harness import fails_once, fake_docker_bin

from kraft import events, store
from kraft.worker import reattach

#: An agent node, then a subprocess node: the two kinds `_adopted_status`
#: tells apart.
_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]
- id: verify
  kind: exec
  tasks:
    - {id: check, kind: subprocess, command: "true"}
    - {id: review, kind: agent, harness: fake, prompt: Review it.}
"""
IMPLEMENT = "implementation.main.implement"
#: A definitely-dead pid, with a start time.
DEAD = (2_000_000_000, 123.0)

#: What a Claude Code worker leaves as its log's last line. Cache reads are
#: input tokens that were billed, kept apart from the 100 uncached ones
#: (Ruling 211).
_ENVELOPE = json.dumps(
    {
        "type": "result",
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 300},
        "modelUsage": {"claude-opus-5": {}},
        "total_cost_usd": 1.25,
    }
)


@pytest.fixture
async def item(item_on):
    """Work item `w1` standing at `implementation`."""
    return await item_on(_CHAIN, "implementation")


@contextmanager
def _child(argv, **popen):
    """A live, detached child and its create_time, reaped on the way out."""
    proc = subprocess.Popen(argv, start_new_session=True, **popen)
    try:
        yield proc, psutil.Process(proc.pid).create_time()
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()


def _sleeper(seconds=2):
    return _child([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def _held_open():
    """A child that lives until its stdin closes: a timed sleep raced the scan
    under a loaded parallel run, and the row tore down instead."""
    return _child([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)


def _result(run_dirs, sid, payload='{"status": "done"}'):
    (run_dirs.results / f"{sid}.json").write_text(payload)


def _usage(it, sid="s1"):
    row = next(r for r in it.sessions() if r["id"] == sid)
    return (
        row["model"],
        row["tokens_in"],
        row["tokens_cache_read"],
        row["tokens_out"],
        row["cost_usd"],
    )


# --- identity ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "pid, start, reason",
    [
        (os.getpid(), None, "no pid_start_time recorded"),
        (DEAD[0], DEAD[1], "pid 2000000000 is not alive"),
    ],
    ids=["no-start-time", "dead-pid"],
)
def test_identity_mismatch_reason(pid, start, reason):
    assert reattach._identity_mismatch_reason(pid, start) == reason


def test_identity_mismatch_reason_create_time_mismatch():
    real_start = psutil.Process(os.getpid()).create_time()
    reason = reattach._identity_mismatch_reason(os.getpid(), real_start - 10_000)
    assert "create_time mismatch" in reason and str(real_start) in reason


def test_identity_mismatch_reason_never_reports_this_process(monkeypatch):
    """`psutil.Process(None)` is not an error -- it is *this* process. Without
    the explicit guard, a row with no pid reports Kraft's own create_time as
    the session's `observed=`, inside the one diagnostic Kraft-s7c04.51 adds
    to make that answer trustworthy."""

    def boom(*a, **kw):
        raise AssertionError("psutil must not be consulted for a None pid")

    monkeypatch.setattr(reattach, "_pid_alive", boom)
    assert reattach._identity_mismatch_reason(None, 123.0) == "no pid recorded"
    assert reattach._identity_mismatch_reason(None, None) == "no pid recorded"


# --- sessions it cannot adopt ---------------------------------------------------------


@pytest.mark.parametrize(
    "node, path",
    [
        ("implementation", IMPLEMENT),
        # Kraft-7xt: `forge.run_task` creates its row before the poll
        # (Kraft-41b) and does its work in-process, so a restart mid-wait finds
        # a row that never left 'pending'. Before that there was no row at all
        # and the node silently re-ran from scratch.
        ("verify", "on.ci.poll"),
    ],
    ids=["never-launched", "forge-poll-mid-wait"],
)
async def test_a_pending_session_becomes_unknown_and_needs_human(
    item, database, run_dirs, node, path
):
    await database.write(lambda c: store.enter_node(c, "w1", node))
    await item.session("s1", path, node=node)  # stays 'pending', pid NULL

    summary, adopted = await reattach.reattach(database, run_dirs)

    assert (adopted, summary.unknown) == ({}, ["s1"])
    assert "w1" not in summary.resumed_work_items
    assert item.status() == "needs_human"
    assert item.events("session_unknown")


async def test_unconfirmed_identity_records_which_check_failed(item, database, run_dirs):
    """Kraft-s7c04.51: the event used to say only "unconfirmed" -- the human
    diagnosing the live incident had to reverse-engineer a restart timestamp
    to learn the pid had simply died. A dead pid with no result file is
    unknown, and the reason is on the event and on the card."""
    await item.session("s1", IMPLEMENT, running=DEAD)

    summary, _ = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert summary.unknown == ["s1"]
    assert item.status() == "needs_human"
    (unknown,) = item.events("session_unknown")
    assert "not alive" in unknown["payload"]["reason"]
    assert "not alive" in item.events("work_item_needs_human")[-1]["payload"]["reason"]


@pytest.mark.parametrize(
    "confirms, old, expected, calls",
    [
        # A session started moments before this scan gets one recheck; one
        # that still fails ends up unknown.
        ([False, False], False, "unknown", 2),
        # The retry's point: unconfirmed on the first look, confirmed on the
        # second, adopted rather than orphaned.
        ([False, True], False, "adopted", 2),
        # Well past the grace window: no retry -- an old, genuinely dead
        # session should not pay for one that exists for the just-dispatched.
        ([False, False], True, "unknown", 1),
    ],
    ids=["young-still-dead-after-retry", "young-confirmed-on-retry", "old-skips-the-retry"],
)
async def test_a_young_ambiguous_session_gets_one_retry(
    item, database, run_dirs, monkeypatch, confirms, old, expected, calls
):
    answers = iter(confirms)
    seen = []

    def fake_identity_ok(pid, pid_start_time):
        seen.append(pid)
        return next(answers)

    monkeypatch.setattr(reattach, "_identity_ok", fake_identity_ok)
    with _sleeper() as (proc, _):
        await item.session("s1", IMPLEMENT, running=(proc.pid, 0.0))
        if old:
            await database.write(
                lambda c: c.execute(
                    "UPDATE worker_sessions SET started_at = '2020-01-01T00:00:00+00:00'"
                )
            )
        summary, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
        await asyncio.gather(*adopted.values(), return_exceptions=True)

    assert getattr(summary, expected) == ["s1"]
    assert len(seen) == calls


async def test_many_young_sessions_share_one_grace_sleep(item, database, run_dirs, monkeypatch):
    """The grace retry waits once for the whole scan, not once per row: a
    restart moments after several sessions started (`kraft admin stop` then
    start) puts all of them in the age window, and a sleep per row would add
    `grace_retry_delay_s` x N to startup exactly when they are most likely
    dead."""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def counting_sleep(delay, *a, **kw):
        sleeps.append(delay)
        return await real_sleep(0, *a, **kw)

    monkeypatch.setattr(reattach, "_identity_ok", lambda pid, pst: False)
    monkeypatch.setattr(reattach.asyncio, "sleep", counting_sleep)
    for n in range(5):
        await item.session(f"s{n}", IMPLEMENT, running=(90000 + n, 123.0))

    summary, _ = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0.01)

    assert sorted(summary.unknown) == ["s0", "s1", "s2", "s3", "s4"]
    assert sleeps == [0.01], f"expected one shared grace sleep, got {len(sleeps)}"


# --- dead pid, resolved from its files --------------------------------------------------


async def test_running_dead_pid_resolves_from_result_file(item, database, run_dirs):
    _result(
        run_dirs, "s1", '{"status": "done", "session_summary_ref": ".engineering/sessions/s1.md"}'
    )
    await item.session("s1", IMPLEMENT, running=DEAD)

    # grace_retry_delay_s=0: a fresh (young) session, but this is the ordinary
    # resolve-from-file path, not Kraft-s7c04.51's retry.
    summary, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert (adopted, summary.resolved_from_file) == ({}, ["s1"])
    (row,) = item.sessions()
    assert (row["status"], row["session_summary_ref"]) == ("done", ".engineering/sessions/s1.md")
    assert item.events("session_reattached") and item.events("worker_session_exited")
    assert summary.resumed_work_items == ["w1"]


@pytest.mark.parametrize(
    ("path", "exit_code", "expected"),
    [
        # Kraft-s7c04.38: a green run Kraft merely cannot confirm pages nobody.
        ("verify.main.check", "0", "done"),
        ("verify.main.check", "3", "failed"),
        # No evidence at all still escalates.
        ("verify.main.check", None, "unknown"),
        # An agent's exit code never speaks for its missing result file.
        ("verify.main.review", "0", "unknown"),
    ],
    ids=["exit-0", "exit-3", "no-exit-file", "agent-exit-0"],
)
async def test_a_dead_unconfirmed_session_reads_its_exit_file_before_paging(
    item_on, database, run_dirs, path, exit_code, expected
):
    it = await item_on(_CHAIN, "verify")
    await it.session("s1", path, running=DEAD)
    if exit_code is not None:
        (run_dirs.results / "s1.exit").write_text(exit_code)

    summary, _ = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert it.sessions()[0]["status"] == expected
    assert (summary.unknown == ["s1"]) is (expected == "unknown")
    assert (it.status() == "needs_human") is (expected == "unknown")


async def test_resolved_from_file_carries_concerns_question_and_usage(item, database, run_dirs):
    """The same `worker_session_exited` payload `run_task` would have stamped:
    the concerns roll-up at the gate and the needs_context question both read
    that event. And a worker that outlived a restart spent real money --
    recording its exit without usage leaves the four columns NULL forever, and
    the run reads as free everywhere Kraft reports money (Kraft-7co4). The
    result file carries no usage block, so the log envelope is the source."""
    _result(
        run_dirs,
        "s1",
        '{"status": "done_with_concerns", "concerns": "the migration is untested",'
        ' "question": "which db?"}',
    )
    (run_dirs.logs / "s1.log").write_text(_ENVELOPE + "\n")
    await item.session("s1", IMPLEMENT, running=DEAD)

    summary, _ = await reattach.reattach(database, run_dirs)

    assert summary.resolved_from_file == ["s1"]
    assert _usage(item) == ("claude-opus-5", 100, 300, 20, 1.25)
    payload = item.events("worker_session_exited")[-1]["payload"]
    assert {
        k: payload[k]
        for k in (
            "concerns",
            "question",
            "model",
            "tokens_in",
            "tokens_cache_read",
            "tokens_out",
            "cost_usd",
        )
    } == {
        "concerns": "the migration is untested",
        "question": "which db?",
        "model": "claude-opus-5",
        "tokens_in": 100,
        "tokens_cache_read": 300,
        "tokens_out": 20,
        "cost_usd": 1.25,
    }


#: A real codex thread's first turn and its resumed second (codex-cli 0.155.0,
#: Kraft-w3kot): usage is the thread's running total, 23 then 28 output tokens.
_CODEX_FIRST = [
    '{"type":"thread.started","thread_id":"01a0cb2d-88c2-7631-b8b3-45debaca3bf6"}',
    '{"type":"turn.completed","usage":{"input_tokens":20458,"cached_input_tokens":12032,'
    '"cache_write_input_tokens":0,"output_tokens":23,"reasoning_output_tokens":0}}',
]
_CODEX_RESUMED = [
    _CODEX_FIRST[0],
    '{"type":"turn.completed","usage":{"input_tokens":46200,"cached_input_tokens":18944,'
    '"cache_write_input_tokens":0,"output_tokens":28,"reasoning_output_tokens":0}}',
]


@pytest.mark.parametrize("path", ["from-file", "adopted"])
async def test_a_re_adopted_codex_session_reads_and_nets_with_codex_s_reader(
    item, database, run_dirs, path
):
    """Kraft-9elw1: the row's harness picks the reader, so a codex session that
    outlived a restart reads codex tokens -- claude's reader finds none in this
    log -- and, resuming an earlier session's thread, records only its own share."""
    (run_dirs.logs / "s0.log").write_text("\n".join(_CODEX_FIRST) + "\n")
    await item.session("s0", IMPLEMENT, "paused", running=DEAD, harness="codex")
    (run_dirs.logs / "s1.log").write_text("\n".join(_CODEX_RESUMED) + "\n")
    _result(run_dirs, "s1")
    if path == "from-file":
        await item.session("s1", IMPLEMENT, running=DEAD, harness="codex")
        await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
    else:
        with _sleeper() as (proc, pst):
            await item.session("s1", IMPLEMENT, running=(proc.pid, pst), harness="codex")
            _, adopted = await reattach.reattach(database, run_dirs)
            await adopted["s1"]

    assert _usage(item)[1:] == (46200 - 18944 - (20458 - 12032), 18944 - 12032, 28 - 23, None)


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (None, ("claude-opus-5", 100, 300, 20, 1.25)),
        ("retired-harness", ("claude-opus-5", 100, 300, 20, 1.25)),
        ("gemini", (None, None, None, None, None)),
    ],
    ids=["older-row", "unknown-harness", "no-reader"],
)
async def test_a_row_s_harness_decides_whether_claude_s_reader_applies(
    item, database, run_dirs, harness, expected
):
    """A row older than the column, or naming a harness no longer loaded, can
    only have been claude's; a harness that declares no log reader parses none."""
    (run_dirs.logs / "s1.log").write_text(_ENVELOPE + "\n")
    _result(run_dirs, "s1")
    await item.session("s1", IMPLEMENT, running=DEAD, harness=harness)

    await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert _usage(item) == expected


# --- live pid, adopted --------------------------------------------------------------------


async def test_live_pid_matching_identity_is_adopted(item, database, run_dirs):
    _result(run_dirs, "s1")
    with _sleeper() as (proc, pst):
        await item.session("s1", IMPLEMENT, running=(proc.pid, pst))

        summary, adopted = await reattach.reattach(database, run_dirs)

        assert summary.adopted == ["s1"] and set(adopted) == {"s1"}
        assert item.events("session_reattached")
        assert item.sessions()[0]["status"] == "running", "running until the child exits"
        await adopted["s1"]
    assert item.sessions()[0]["status"] == "done"


async def test_a_crashed_adopt_marks_the_work_item_needs_human(
    item, database, run_dirs, monkeypatch
):
    """Kraft-mjwz: `reattach()` hands its tasks straight to the caller rather
    than through `api._spawn`'s `_guard`, so a crash in `_adopt` outside its own
    per-tick try used to vanish -- no log, no needs_human, task leaked forever.
    This pins `_guarded_adopt`'s catch. The pid is this test process, alive and
    passing the identity check: the crash is in `_adopt`, not before it."""

    async def _boom(db, session_id, pid, poll_s=0.1, progress_s=5.0, **kw):
        raise ValueError("boom")

    monkeypatch.setattr(reattach, "_adopt", _boom)
    await item.session("s1", IMPLEMENT, running=(os.getpid(), psutil.Process().create_time()))

    summary, adopted = await reattach.reattach(database, run_dirs)
    await asyncio.gather(*adopted.values(), return_exceptions=True)

    assert summary.adopted == ["s1"]
    assert item.status() == "needs_human"
    reason = item.events("work_item_needs_human")[0]["payload"]["reason"]
    assert "reattach crashed" in reason and "boom" in reason


async def test_an_adopted_session_records_its_usage(item, database, run_dirs):
    """The `_adopt` path, whose own SELECT has to grow a log_path column -- the
    file-resolved test passes without that and would not catch it."""
    _result(run_dirs, "s1")
    (run_dirs.logs / "s1.log").write_text(_ENVELOPE + "\n")
    with _sleeper() as (proc, pst):
        await item.session("s1", IMPLEMENT, running=(proc.pid, pst))
        summary, adopted = await reattach.reattach(database, run_dirs)
        await adopted["s1"]

    assert summary.adopted == ["s1"]
    assert _usage(item) == ("claude-opus-5", 100, 300, 20, 1.25)


@pytest.mark.parametrize("seam", [None, "read", "write"], ids=["clean", "bad-read", "bad-write"])
async def test_an_adopted_session_carries_live_tokens_before_it_exits(
    item, database, run_dirs, monkeypatch, caplog, seam
):
    """Kraft-jgs6: the child survives the restart (`run_task` starts it
    `start_new_session=True` precisely so it can) and keeps writing its log.
    Before `_adopt` had its own progress loop, tokens sat frozen at whatever
    `run_task` last wrote before the restart. Calls `_adopt` directly to
    shorten `progress_s` -- the default 5s would make this as slow as the bug.

    Kraft-0jpb: a tick that raises, reading the log or writing the row, is
    logged with its traceback, and the next tick still lands -- the same
    contract as `run_task`'s own loop."""
    if seam == "read":
        monkeypatch.setattr(reattach, "_progress_usage", fails_once(reattach._progress_usage))
    elif seam == "write":
        monkeypatch.setattr(store, "session_progress", fails_once(store.session_progress))
    script = (
        'printf \'{"type":"assistant","request_id":"req_1","message":'
        '{"model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":200}}}\\n\'; '
        "sleep 3"
    )
    log_path = run_dirs.logs / "s1.log"
    with (
        open(log_path, "w") as log_file,
        _child(["sh", "-c", script], stdout=log_file) as (proc, pst),
    ):
        await item.session("s1", IMPLEMENT, running=(proc.pid, pst))
        task = asyncio.create_task(
            reattach._adopt(database, "s1", proc.pid, poll_s=0.05, progress_s=0.2)
        )
        live = None
        for _ in range(200):
            if task.done():
                break
            if _usage(item)[1]:
                live = _usage(item)
                break
            await asyncio.sleep(0.05)
        _result(run_dirs, "s1")
        await task

    assert live is not None, "tokens_in never appeared on the row before the child exited"
    assert live[:4] == ("claude-opus-5", 1000, 0, 200)
    failed = [r for r in caplog.records if "usage progress tick failed" in r.getMessage()]
    assert [r.exc_info[1].args for r in failed] == ([] if seam is None else [("poison tick",)])


async def test_adopting_a_session_leaves_its_backgrounded_child_alone(
    item, database, run_dirs, tmp_path
):
    """The one exception in §5: a session Kraft adopted after a restart is
    meant to outlive the process that launched it. Killing its group on
    adoption would undo the reattach feature outright."""
    pidfile = tmp_path / "grandchild.pid"
    _result(run_dirs, "s1")
    grandchild = None
    try:
        with _child(["bash", "-c", f"sleep 30 & echo $! > {pidfile}; sleep 2"]) as (proc, pst):
            await item.session("s1", IMPLEMENT, running=(proc.pid, pst))
            for _ in range(50):
                if pidfile.exists():
                    break
                await asyncio.sleep(0.1)
            grandchild = int(pidfile.read_text().strip())
            summary, adopted = await reattach.reattach(database, run_dirs)
            await adopted["s1"]
        assert summary.adopted == ["s1"]
        os.kill(grandchild, 0)  # still alive: no ProcessLookupError
    finally:
        if grandchild:
            try:
                os.kill(grandchild, signal.SIGKILL)
            except ProcessLookupError:
                pass


# --- the deferred self-retry of an escalation turn ---------------------------------------


@pytest.mark.parametrize("alive", [True, False], ids=["adopted", "resolved-from-file"])
async def test_reattach_resumes_a_deferred_self_retry_left_by_an_escalation_turn(
    item, database, run_dirs, monkeypatch, alive
):
    """Kraft-atdbw: `lifecycle.retry_work_item` defers a self-retry from a live
    escalation session by writing `work_item_self_retry_requested`, because the
    caller's own session is still live. Its only consumer runs inside
    `escalate.dispatch`'s awaiting frame -- gone if the server restarts
    mid-turn. So `_adopt`'s tail (a live pid) and the resolved-from-file branch
    (the session exited while the server was down, the exact reported failure)
    must both pick the request back up, or the item sits needs_human and
    `auto_escalate_delay` dispatches a new paid turn on an already-fixed stop.
    The file branch runs it as a background task: `resume_after_escalation`
    awaits a whole `walk.run`, which must not block the server's startup."""
    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    async def fake_refresh(worktree, repo, branch, **_kw):
        return None

    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)
    monkeypatch.setattr("kraft.builtins.refresh_worktree_base", fake_refresh)
    await database.write(
        lambda c: store.mark_needs_human(c, "w1", "implementation", "budget exhausted")
    )
    await item.session("esc1", "escalation")
    await database.write(
        lambda c: events.append(
            c,
            "w1",
            "escalation_message",
            {"session_id": "esc1", "message": "please fix it", "auto": False},
        )
    )
    # As if the agent ran `kraft item retry` on itself mid-turn: exactly what
    # lifecycle.retry_work_item writes.
    await database.write(
        lambda c: events.append(
            c,
            "w1",
            "work_item_self_retry_requested",
            {
                "session_id": "esc1",
                "node_id": "implementation",
                "key": None,
                "gate_key": None,
                "steer": "fixed it",
                "seeded": False,
            },
        )
    )
    _result(run_dirs, "esc1")

    if alive:
        with _held_open() as (proc, pst):
            await database.write(lambda c: store.session_running(c, "esc1", proc.pid, pst))
            summary, adopted = await reattach.reattach(
                database, run_dirs, launch_factory=lambda repo: None
            )
            proc.stdin.close()
            await adopted["esc1"]
    else:
        await database.write(lambda c: store.session_running(c, "esc1", *DEAD))
        summary, adopted = await reattach.reattach(
            database, run_dirs, launch_factory=lambda repo: None
        )
        await adopted["esc1"]

    assert (summary.adopted if alive else summary.resolved_from_file) == ["esc1"]
    assert [kw["steer"] for kw in walk_calls] == ["fixed it"]
    # Retried and re-entered -- not needs_human on the already-fixed stop.
    types = [e["type"] for e in item.events()]
    assert types.index("work_item_self_retry_requested") < types.index("work_item_retried")


# --- sandboxed sessions' containers ------------------------------------------------------


@pytest.fixture
def docker_rm(tmp_path, monkeypatch):
    """A fake `docker` first on PATH; each `docker rm` name lands in the log."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    log = tmp_path / "docker-rm-log"
    monkeypatch.setenv("FAKE_DOCKER_RM_LOG", str(log))
    return log


async def test_reattach_kills_the_container_of_a_session_it_does_not_adopt(
    item, database, run_dirs, docker_rm
):
    """Kraft-rki: a container is parented by the docker daemon, so it survived
    the restart that orphaned this row. Reattach is the only thing left that
    knows the session is over, so the teardown belongs there."""
    _result(run_dirs, "s1")
    await item.session("s1", IMPLEMENT, running=DEAD)

    summary, _ = await reattach.reattach(database, run_dirs)

    assert summary.resolved_from_file == ["s1"]
    assert docker_rm.read_text().split() == ["kraft-s1"]


async def test_an_adopted_session_kills_its_container_when_it_ends(
    item, database, run_dirs, docker_rm
):
    """The third call path into the same teardown (Kraft-rki): a session
    adopted after a restart ends inside `_guarded_adopt`, not inside
    `run_task`'s `finally`, so its container needs the same hook there."""
    _result(run_dirs, "s1")
    with _held_open() as (proc, pst):
        await item.session("s1", IMPLEMENT, running=(proc.pid, pst))
        _, adopted = await reattach.reattach(database, run_dirs)
        assert not docker_rm.exists(), "still running: nothing to tear down yet"
        proc.stdin.close()
        await adopted["s1"]

    assert docker_rm.read_text().split() == ["kraft-s1"]


# --- what an adopted session's exit reads as ---------------------------------------------


@pytest.mark.parametrize(
    "result, exit_code, is_agent, expected",
    [
        # A green adopted run must not be recorded failed (b5afe84c: 28 min lost).
        (None, "0", False, "done"),
        (None, "3", False, "failed"),
        # Never fabricate a failure: `unknown` is a real status and surfaces as
        # an orphaned session in doctor/render.
        (None, None, False, "unknown"),
        # require_result_file survives: a clean exit without the file is a
        # broken contract, not a success (adapters/agent.py).
        (None, "0", True, "failed"),
        # The result file wins, for a subprocess and an agent alike.
        ({"status": "done_with_concerns"}, "1", False, "done_with_concerns"),
        ({"status": "done_with_concerns"}, "1", True, "done_with_concerns"),
    ],
    ids=[
        "subprocess-exit-0",
        "subprocess-exit-3",
        "subprocess-no-evidence",
        "agent-without-result-file",
        "subprocess-result-file-wins",
        "agent-result-file-wins",
    ],
)
def test_adopted_status(tmp_path, result, exit_code, is_agent, expected):
    result_path = tmp_path / "s.json"
    if result is not None:
        result_path.write_text(json.dumps(result))
    if exit_code is not None:
        (tmp_path / "s.exit").write_text(exit_code)
    assert reattach._adopted_status(result_path, is_agent=is_agent) == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        # Kraft-hwrks: whether an adopted session is an agent was looked up in
        # the legacy registry by `hook_point`, which on V1 is a task path no
        # registry hook has -- so every V1 subprocess session read as an
        # agent, and a green run adopted across a restart was recorded
        # `failed` and fed the fix loop (b5afe84c, back for V1).
        ("verify.main.check", "done"),
        ("verify.main.review", "failed"),
        # The conservative direction stays: an escalation turn is no chain
        # task, and a clean exit without its result file must not read done.
        ("escalation", "failed"),
    ],
    ids=[
        "subprocess-task-reads-its-exit-code",
        "agent-task-needs-its-result-file",
        "outside-the-chain-is-an-agent",
    ],
)
async def test_an_adopted_v1_session_s_kind_decides_its_exit(
    item_on, database, run_dirs, path, expected
):
    """A session whose process already exited 0 without writing a result
    file, adopted the way a restart finds it."""
    it = await item_on(_CHAIN, "verify")
    await it.session("s1", path)
    (run_dirs.results / "s1.exit").write_text("0")
    proc = subprocess.Popen(["true"])
    proc.wait()

    await reattach._adopt(database, "s1", proc.pid, poll_s=0.01)

    assert it.sessions()[0]["status"] == expected
