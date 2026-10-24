"""run_task with a runner: the stand-in is the step's process, and the rest follows."""

import asyncio
import contextlib
import json
import sys

import pytest
from support.chain_builders import v1_node
from support.fake_backend import Remote
from support.harness import entry_of, v1_chain

from kraft import caps
from kraft.adapters import subprocess as sp
from kraft.remote import launch
from kraft.remote import runs as runs_mod
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
