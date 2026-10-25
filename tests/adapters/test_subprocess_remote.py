"""run_task with a runner: the stand-in is the step's process, and the rest follows."""

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import pytest
from support.chain_builders import v1_node
from support.fake_backend import Remote
from support.harness import entry_of, v1_chain
from support.remote import attach, make_rig, workers_file

from kraft import caps
from kraft.adapters import hook_install
from kraft.adapters import subprocess as sp
from kraft.remote import launch, protocol, registry
from kraft.remote import runs as runs_mod
from kraft.remote.machine import Machine
from kraft.remote.runs import Reserved
from kraft.worker import backends
from kraft.worker.backends.docker import OomKill


def _args(rig, sid, cmd, **kw):
    kw.setdefault("repo_entry", entry_of({"setup_command": ""}))
    return dict(
        session_id=sid,
        work_item_id=rig.wid,
        node_id="build",
        hook_point="build.main.a",
        cmd=cmd,
        cwd=rig.repo,
        poll_s=0.02,
        **kw,
    )


async def _run(rig, sid, cmd, **kw):
    return await sp.run_task(rig.database, rig.run_dirs, **_args(rig, sid, cmd, **kw))


def _row(rig, sid):
    return rig.database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = ?", (sid,)).fetchone()
    )


async def test_a_remote_step_is_a_session_row_with_its_machine_and_the_standin_as_its_process(rig):
    status = await _run(rig, "s1", [sys.executable, "-c", "print('on the machine')"], runner="gpu")
    row = _row(rig, "s1")
    assert status == "done" and row["runner"] == "gpu" and row["sandbox"] is None and row["pid"]
    assert "on the machine" in row["command"] and "attach" not in row["command"]
    assert rig.files("s1").log.read_text() == "on the machine\n"


async def test_a_step_given_up_on_ends_its_session_unknown(rig):
    task = asyncio.create_task(_run(rig, "s1", ["sh", "-c", "sleep 30"], runner="gpu"))
    while not (run := rig.runs.find(rig.wid, "s1")) or run.state != "running":
        await asyncio.sleep(0.02)
    await rig.runs.give_up(run, "gpu has been unreachable for 2h")
    assert await asyncio.wait_for(task, 20) == "unknown"
    assert _row(rig, "s1")["status"] == "unknown"


async def test_a_cap_the_machine_enforced_ends_the_session_time_capped(rig, monkeypatch):
    from dataclasses import replace

    from kraft.remote import launch

    build = launch.build
    # the server's own deadline is far off; the machine is told a short one
    monkeypatch.setattr(launch, "build", lambda **kw: replace(build(**kw), deadline_s=0.3))
    cap = caps.Deadline(caps.monotonic() + 1000, caps.Hit("build.main.a", "time_cap_minutes", 5, 0))
    status = await _run(rig, "s1", ["sh", "-c", "sleep 30"], runner="gpu", time_cap=cap)
    assert status == caps.TIME_CAPPED and _row(rig, "s1")["status"] == "capped_out"
    event = rig.database.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type = 'time_cap_reached' AND work_item_id = ?",
            (rig.wid,),
        ).fetchall()
    )
    assert len(event) == 1 and "time_cap_minutes" in event[0]["payload"]


@pytest.mark.parametrize(
    ("runner", "kwargs", "says"),
    [
        ("ghost", {}, "'ghost' is not a machine in workers.yaml"),
    ],
    ids=["unknown-machine"],
)
async def test_a_placement_that_cannot_hold_is_a_config_error_naming_why_and_runs_nothing(
    rig, runner, kwargs, says
):
    assert await _run(rig, "s1", ["true"], runner=runner, **kwargs) == "config_error"
    assert says in rig.files("s1").log.read_text() and rig.calls == []
    assert rig.hub.slots_used("gpu") == 0
    assert _row(rig, "s1")["status"] == "config_error"


async def test_a_machine_without_the_agent_cli_is_a_config_error_not_a_failure(rig):
    assert await _run(rig, "s1", ["claude-not-installed"], runner="gpu") == "config_error"
    assert "claude-not-installed" in rig.files("s1").log.read_text()


async def test_a_step_waiting_for_a_full_machine_has_no_session_row_and_never_runs_elsewhere(rig):
    held = [await rig.run(["sh", "-c", "sleep 30"], sid=f"h{i}") for i in (1, 2)]  # capacity 2
    task = asyncio.create_task(_run(rig, "s2", [sys.executable, "-c", "print('ok')"], runner="gpu"))
    await asyncio.sleep(1.0)
    assert _row(rig, "s2") is None and not task.done() and rig.files("s2").log.exists() is False
    task.cancel()  # a cancel while it waits leaves nothing behind
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert _row(rig, "s2") is None and rig.hub.slots_used("gpu") == 2
    for run in held:
        await rig.runs.signal(run, "KILL")
        await rig.done(run)


async def test_a_wait_moves_the_time_cap_on_by_however_long_it_lasted(rig):
    from kraft.remote import launch

    held = [await rig.run(["sh", "-c", "sleep 1"], sid=f"h{i}") for i in (1, 2)]
    cap = caps.Deadline(
        caps.monotonic() + 100.0, caps.Hit("build.main.a", "time_cap_minutes", 1, 0.0)
    )
    got, refusal, moved = await launch.claim(
        rig.database,
        work_item_id=rig.wid,
        session_id="s2",
        node_id="build",
        runner="gpu",
        repo_entry=None,
        time_cap=cap,
    )
    assert refusal is None and got.machine == "gpu"
    assert (
        moved.at - cap.at >= 0.4 and moved.hit == cap.hit
    )  # it waited for a slot; the cap did not
    rig.hub.release(got)
    for run in held:
        await rig.done(run)


async def test_with_no_machines_in_this_process_a_remote_runner_is_a_config_error(
    database, run_dirs, repo, item_on
):
    runs_mod.install(None)
    it = await item_on(v1_chain([v1_node("n")], repo=repo), "n", repo=repo)
    status = await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id=it.id,
        node_id="n",
        hook_point="n.main.a",
        cmd=["true"],
        cwd=repo,
        runner="gpu",
    )
    assert status == "config_error"


SECRET = "s3cr3t-value-of-the-servers-key"
SANDBOX = {
    "kind": "remote",
    "network": {"runtime": {"allow": ["api.example.com"]}},
    "credentials": [
        {
            "env": "EXAMPLE_KEY",
            "inject": [
                {"domain": "api.example.com", "header": "authorization", "format": "Bearer %s"}
            ],
        }
    ],
}


@pytest.fixture
def sandboxed(rig, tmp_path, short_run, monkeypatch):
    """The fake backend registered as the item's sandbox kind -- the machine in this process
    builds it in a run dir of its own -- and every `JobSpec` the launch builds kept."""
    backend = Remote(tmp_path)
    monkeypatch.setitem(backends._BACKENDS, backend.kind, backend)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(short_run.base))
    monkeypatch.setenv("EXAMPLE_KEY", SECRET)
    built = []
    build = launch.build
    monkeypatch.setattr(launch, "build", lambda **kw: built.append(build(**kw)) or built[-1])
    return backend, built


async def test_a_sandboxed_job_carries_its_sandbox_lists_and_credential_declarations(
    rig, sandboxed
):
    _, built = sandboxed
    repo = entry_of({"setup_command": "", "env_passthrough": ["EXAMPLE_KEY"]})
    await _run(rig, "s1", ["true"], runner="gpu", sandbox=SANDBOX, repo_entry=repo)
    (spec,) = built
    assert spec.sandbox == SANDBOX
    assert spec.egress == {"phase": "runtime", "allow": ["api.example.com"], "deny": []}
    assert [(c["env"], c["domain"], c["header"]) for c in spec.credentials] == [
        ("EXAMPLE_KEY", "api.example.com", "authorization")
    ]
    assert spec.withheld == ["EXAMPLE_KEY"] and set(spec.sentinels) == {"EXAMPLE_KEY"}
    assert SECRET not in json.dumps(spec.to_frame())
    assert _row(rig, "s1")["sandbox"] == "remote" and _row(rig, "s1")["runner"] == "gpu"


async def test_a_sandboxed_remote_step_runs_no_backend_on_the_server(rig, sandboxed):
    backend, _ = sandboxed
    repo = entry_of({"setup_command": "", "env_passthrough": ["EXAMPLE_KEY"]})
    status = await _run(rig, "s1", ["true"], runner="gpu", sandbox=SANDBOX, repo_entry=repo)
    assert status == "done", rig.files("s1").log.read_text()
    # every call came from the machine, in its own run dir; none was made in the server's
    assert backend.bases and rig.run_dirs.base not in backend.bases


async def test_a_memory_kill_in_the_machines_sandbox_ends_the_session_config_error(rig, sandboxed):
    backend, _ = sandboxed
    backend.oom = OomKill("1g", True)
    repo = entry_of({"setup_command": "", "env_passthrough": ["EXAMPLE_KEY"]})
    status = await _run(rig, "s1", ["false"], runner="gpu", sandbox=SANDBOX, repo_entry=repo)
    assert status == "config_error"
    assert "killed by its memory limit (1g)" in rig.files("s1").log.read_text()
    kinds = [
        r["type"]
        for r in rig.database.read(lambda c: c.execute("SELECT type FROM events").fetchall())
    ]
    assert kinds.count("sandbox_oom_killed") == 1


async def test_a_remote_standin_is_stopped_by_a_clean_stop(rig):
    """Only a local session is kept: the stand-in is this server's own process, and what it
    stands for is a step on a machine that is told to stop."""
    import psutil

    task = asyncio.create_task(_run(rig, "s1", ["sh", "-c", "sleep 30"], runner="gpu"))
    while not (run := rig.runs.find(rig.wid, "s1")) or run.state != "running":
        await asyncio.sleep(0.02)
    row = _row(rig, "s1")
    sp.server_stopping()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not psutil.pid_exists(row["pid"]) or psutil.Process(row["pid"]).status() == "zombie"


FAKE_CODEX = [sys.executable, str(Path(__file__).parents[1] / "support/fake_codex_app_server.py")]
CODEX_HOOK = {"harness": "codex", "hook": "codex", "exe": FAKE_CODEX, "at": 3}


async def test_a_machine_step_gets_the_hook_flags_its_own_codex_trusted(rig, monkeypatch, tmp_path):
    """Its codex is asked in its copy of the worktree, and the hook it trusts is the machine's
    interpreter's: the flags go in where the server marked, before the rest of the command."""
    monkeypatch.setattr(hook_install, "_codex_flags", {})
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "codex.log"))
    cmd = [sys.executable, "-c", "import sys, json; print(json.dumps(sys.argv[1:]))", "tail"]
    assert await _run(rig, "s1", cmd, runner="gpu", codex_hook=dict(CODEX_HOOK)) == "done"
    args = json.loads(rig.files("s1").log.read_text())
    assert [args[0], args[2], args[4], args[6]] == ["-c", "-c", "-c", "tail"]
    hook, state = args[1], args[5]
    assert f"{sys.executable} -I -m kraft admin permission-hook codex" in hook
    assert state.startswith("hooks.state=") and "trusted_hash=" in state
    assert len((tmp_path / "codex.log").read_text().splitlines()) == 2


@pytest.mark.parametrize(
    ("caps", "mode", "says"),
    [
        ((), "ok", "update Kraft on that machine"),
        (None, "distrust", "did not trust"),
    ],
    ids=["machine-lacks-the-cap", "machine-codex-distrusts"],
)
async def test_a_machine_step_whose_hook_the_machine_cannot_trust_is_refused_and_never_runs(
    rig_offline, monkeypatch, tmp_path, caps, mode, says
):
    """Never the server's codex asked instead, never a launch with the hook skipped."""
    monkeypatch.setattr(hook_install, "_codex_flags", {})
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)
    marker = tmp_path / "ran"
    await attach(rig_offline.hub, rig_offline.machine, **({} if caps is None else {"caps": caps}))
    cmd = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
    status = await _run(rig_offline, "s1", cmd, runner="gpu", codex_hook=dict(CODEX_HOOK))
    log = rig_offline.files("s1").log.read_text()
    assert status == "config_error" and says in log
    assert "'codex' cannot run Kraft's permission hook" in log
    assert not marker.exists() and rig_offline.hub.slots_used("gpu") == 0


# ── the machine's own limit: the slot is asked for before the session exists ──


@pytest.fixture
async def pair(tmp_path, database, run_dirs, item_on):
    """Two machines, `gpu` (whose own limit the test sets through `limit[0]`; none at first) and
    `spare`, both capacity 2 in workers.yaml. Yields `(rig, limit, spare)`."""
    limit = [None]
    workers = workers_file(
        gpu={"capacity": 2},
        spare={"capacity": 2, "token_sha256": registry.hash_token("kwt_spare")},
    )
    rig = await make_rig(
        tmp_path,
        database,
        run_dirs,
        item_on,
        workers=workers,
        machine_kwargs={"limit": lambda: limit[0]},
    )
    spare = Machine(tmp_path / "spare-home", kraft_version="2.3.0")
    await attach(rig.hub, spare, "spare", token="kwt_spare")
    yield rig, limit, spare
    await rig.listener.close()
    runs_mod.install(None)
    await rig.hub.close()


async def _settled(cond, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.02)


async def _hold_gpu_then_lower_its_limit(rig, limit):
    """One step running on `gpu`, then `gpu` sets a limit of one: the server still thinks it has
    a second slot."""
    held = await rig.run(["sh", "-c", "sleep 30"], sid="h1")
    await _settled(lambda: held.state == "running")
    limit[0] = 1
    return held


async def _kill(rig, run):
    await rig.runs.signal(run, "KILL")
    await rig.done(run)


async def test_a_step_a_machine_turns_away_runs_on_another_with_no_session_on_the_first(pair):
    rig, limit, spare = pair
    held = await _hold_gpu_then_lower_its_limit(rig, limit)
    status = await _run(rig, "s2", [sys.executable, "-c", "print('ok')"], runner="any")
    assert status == "done" and _row(rig, "s2")["runner"] == "spare"
    assert f"{rig.wid}/s2" not in rig.machine._jobs  # gpu was asked, and never sent the job
    assert (rig.hub.limit("gpu"), rig.hub.limit_source("gpu")) == (1, "machine")
    await _kill(rig, held)


async def test_a_step_named_for_a_machine_at_its_limit_waits_and_runs_there_once_a_slot_frees(pair):
    rig, limit, spare = pair
    held = await _hold_gpu_then_lower_its_limit(rig, limit)
    task = asyncio.create_task(_run(rig, "s2", [sys.executable, "-c", "print('ok')"], runner="gpu"))
    await _settled(lambda: rig.hub.limit("gpu") == 1)  # turned away once
    await asyncio.sleep(0.6)
    assert _row(rig, "s2") is None and not task.done()  # no session row while it waits
    await _kill(rig, held)
    assert await asyncio.wait_for(task, 20) == "done" and _row(rig, "s2")["runner"] == "gpu"


async def test_a_machine_without_the_reserve_cap_is_not_asked(rig_offline, monkeypatch):
    asked = []
    real = rig_offline.runs.reserve
    monkeypatch.setattr(rig_offline.runs, "reserve", lambda *a, **k: asked.append(a) or real(*a))
    await attach(rig_offline.hub, rig_offline.machine, caps=protocol.CAPS - {protocol.RESERVE})
    assert await _run(rig_offline, "s1", ["true"], runner="gpu") == "done"
    assert asked == []


@pytest.mark.parametrize("how", ["spec-fails", "cancelled-at-reserve", "cancelled-at-session"])
async def test_a_launch_that_ends_before_the_run_starts_gives_back_the_slot_and_the_hold(
    pair, monkeypatch, how
):
    rig, limit, _spare = pair
    limit[0] = 2  # the machine holds a slot for every step it is asked about
    if how == "spec-fails":

        def refuse(**kw):
            raise RuntimeError("no such branch")

        monkeypatch.setattr(launch, "build", refuse)
    elif how == "cancelled-at-reserve":

        async def never_answers(frame):
            await asyncio.sleep(60)

        monkeypatch.setattr(rig.machine, "_on_reserve", never_answers)
    else:

        def cancelled():
            raise asyncio.CancelledError

        monkeypatch.setattr(sp.store, "replay_claimed", cancelled)
    task = asyncio.create_task(_run(rig, "s1", ["true"], runner="gpu"))
    if how == "cancelled-at-reserve":
        await _settled(lambda: rig.runs._reserved.get("gpu"))
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 20)
    await _settled(lambda: not rig.machine._reserved and not rig.runs._reserved.get("gpu"))
    assert rig.hub.slots_used("gpu") == 0


async def test_a_reserve_that_is_not_answered_is_not_a_refusal_and_is_asked_again(
    pair, monkeypatch
):
    rig, limit, _spare = pair
    limit[0] = 2
    seen, real, real_reserve = [], rig.machine._on_reserve, rig.runs.reserve

    async def drops_the_first(frame):
        seen.append(frame["rid"])
        if len(seen) > 1:
            await real(frame)

    monkeypatch.setattr(rig.machine, "_on_reserve", drops_the_first)
    monkeypatch.setattr(rig.runs, "reserve", lambda m, k: real_reserve(m, k, timeout=0.2))
    assert await _run(rig, "s1", ["true"], runner="gpu") == "done"
    assert len(seen) == 2 and len(set(seen)) == 2  # each ask has its own rid
    assert rig.hub._entries["gpu"].full_until is None


async def test_a_reconnect_keeps_the_holds_the_server_still_has_and_drops_the_rest(pair):
    rig, limit, _spare = pair
    limit[0] = 3
    assert (await rig.runs.reserve("gpu", f"{rig.wid}/kept")).ok
    assert (await rig.runs.reserve("gpu", f"{rig.wid}/forgotten")).ok
    rig.runs._reserved["gpu"].discard(f"{rig.wid}/forgotten")  # the server no longer holds it
    await rig.reconnect()
    await _settled(lambda: set(rig.machine._reserved) == {f"{rig.wid}/kept"})


async def test_a_refusal_marks_the_machine_full_and_takes_its_limit_whatever_else_it_said(
    pair, monkeypatch
):
    rig, _limit, _spare = pair
    real = rig.runs.reserve

    async def gpu_is_full(machine, key, **kw):
        if machine == "gpu":
            return Reserved(False, 1)
        return await real(machine, key, **kw)

    monkeypatch.setattr(rig.runs, "reserve", gpu_is_full)
    got, refusal, _cap = await launch.claim(
        rig.database,
        work_item_id=rig.wid,
        session_id="s1",
        node_id="build",
        runner="any",
        repo_entry=None,
        time_cap=None,
    )
    assert refusal is None and got.machine == "spare"
    assert not rig.hub.free("gpu", []) and rig.hub.limit("gpu") == 1
    launch.release(got)
