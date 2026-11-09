"""A job on the machine: its environment, its log and its signals."""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import pytest
from support.acp import agent_argv
from support.acp import spec as acp_spec

from kraft.remote.jobs import JobRunner, JobSpec


def _spec(argv, *, env=None, setup=None, dirs=(), **extra):
    return JobSpec(
        work_item_id="w1",
        session_id="s1",
        node_id="n",
        hook_point="n.main.a",
        argv=argv,
        cwd="/srv/wt",
        env={"KRAFT_SESSION_ID": "w1/s1", **(env or {})},
        repo_env={},
        passthrough=extra.pop("passthrough", []),
        setup_command=setup,
        branch="kraft/x",
        head="h",
        base="b",
        identity={},
        server_run_dir="/srv/run",
        server_worktree="/srv/wt",
        dirs=list(dirs),
        rw=[],
        **extra,
    )


def test_job_spec_ignores_unknown_fields():
    frame = {**_spec(["true"]).to_frame(), "a_field_from_a_newer_server": 1}
    assert JobSpec.from_frame(frame) == _spec(["true"])


def test_job_spec_round_trips_its_acp_field():
    spec = _spec(["agent", "acp"])
    spec.acp = {"spec": "/srv/run/items/w/sessions/s/acp-launch.json"}
    assert JobSpec.from_frame(spec.to_frame()).acp == spec.acp
    assert JobSpec.from_frame({**spec.to_frame(), "acp": None}).acp is None


def _runner(tmp_path, spec, **kw):
    (tmp_path / "area" / "items" / "w1" / "repo").mkdir(parents=True)
    return JobRunner(spec, area=tmp_path / "area", api_port=4242, **kw)


def _text(frames) -> str:
    return b"".join(base64.b64decode(f["data"]) for f in frames if f["t"] == "log").decode()


async def _run(runner):
    frames = []

    async def emit(frame):
        frames.append(frame)

    return await runner.run(emit), frames


async def test_the_log_streams_in_order_and_the_exit_code_comes_back(tmp_path):
    spec = _spec([sys.executable, "-c", "print('one'); print('two'); raise SystemExit(3)"])
    out, frames = await _run(_runner(tmp_path, spec))
    logs = [f for f in frames if f["t"] == "log"]
    assert out.code == 3 and out.rejected is None and frames[0]["t"] == "started"
    assert _text(frames) == "one\ntwo\n" and logs[0]["offset"] == 0
    assert [f["offset"] for f in logs] == sorted(f["offset"] for f in logs)


async def test_the_server_paths_in_argv_and_env_become_the_machines(tmp_path):
    code = "import os,sys; print(sys.argv[1], os.environ['KRAFT_RESULT_PATH'], os.getcwd())"
    spec = _spec(
        [sys.executable, "-c", code, "/srv/run/items/w1/docs"],
        env={"KRAFT_RESULT_PATH": "/srv/run/items/w1/sessions/s1/out/result.json"},
    )
    out, frames = await _run(_runner(tmp_path, spec))
    area = str((tmp_path / "area").resolve())
    text = _text(frames)
    assert f"{area}/run/items/w1/docs" in text
    assert f"{area}/run/items/w1/sessions/s1/out/result.json" in text
    assert "/srv/" not in text and f"{area}/items/w1/repo" in text


async def test_nothing_of_the_servers_own_environment_or_credentials_reaches_the_command(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "machine-home"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = (
        "import os; print(os.environ['HOME'], os.environ.get('KRAFT_PORT'), "
        "os.environ.get('ANTHROPIC_API_KEY'), os.environ.get('KRAFT_DAEMON_PORT'))"
    )
    spec = _spec(
        [sys.executable, "-c", code],
        env={
            "HOME": "/Users/laptop",
            "ANTHROPIC_API_KEY": "sk-secret",
            "KRAFT_DAEMON_PORT": "9999",
        },
    )
    out, frames = await _run(_runner(tmp_path, spec))
    assert _text(frames).split() == [str(tmp_path / "machine-home"), "4242", "None", "None"]


async def test_a_missing_agent_cli_never_starts_and_says_which(tmp_path):
    out, frames = await _run(_runner(tmp_path, _spec(["claude-not-installed", "-p", "x"])))
    assert out.outcome == "config_error" and "claude-not-installed" in out.rejected
    assert not [f for f in frames if f["t"] == "started"]


async def test_a_failing_setup_stops_the_step_before_the_command(tmp_path):
    marker = tmp_path / "ran"
    spec = _spec(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w')"], setup="echo boom >&2; exit 4"
    )
    out, frames = await _run(_runner(tmp_path, spec))
    assert out.outcome == "failed" and "boom" in out.rejected and not marker.exists()


async def test_int_pauses_and_kill_ends_even_a_command_that_ignores_both(tmp_path):
    runner = _runner(
        tmp_path, _spec(["sh", "-c", "trap 'echo got-int; exit 130' INT; sleep 30 & wait"])
    )
    frames = []

    async def emit(frame):
        frames.append(frame)

    task = asyncio.create_task(runner.run(emit))
    while not any(f["t"] == "started" for f in frames):
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)
    runner.signal("INT")
    assert (await asyncio.wait_for(task, 5)).code == 130 and "got-int" in _text(frames)

    stubborn = _runner(
        tmp_path / "second", _spec(["sh", "-c", "trap '' INT TERM; sleep 30"]), kill_grace_s=0.2
    )
    task = asyncio.create_task(stubborn.run(emit))
    while sum(f["t"] == "started" for f in frames) < 2:  # a KILL before it starts is dropped
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # time for the shell to set its trap
    stubborn.signal("KILL")
    assert (await asyncio.wait_for(task, 5)).code == 137


async def test_a_rewind_resends_the_log_from_the_offset_the_server_names(tmp_path):
    code = "import time; print('abc', flush=True); time.sleep(0.3); print('def', flush=True)"
    runner = _runner(tmp_path, _spec([sys.executable, "-c", code]))
    out, frames = await _run(runner)
    replay = []

    async def emit(frame):
        replay.append(frame)

    await runner.replay(emit, 2)
    assert replay[0]["offset"] == 2 and _text(replay) == "c\ndef\n"


async def test_the_time_cap_stops_the_command_on_the_machine(tmp_path):
    cap = {"scope": "n.main.a", "field": "time_cap_minutes", "minutes": 5, "remaining_s": 0.0}
    spec = _spec(["sh", "-c", "sleep 30"], deadline_s=0.3, cap=cap)
    out, _ = await asyncio.wait_for(_run(_runner(tmp_path, spec)), 5)
    assert out.capped == cap and out.code != 0


async def test_the_time_cap_counts_from_the_job_arriving_not_from_the_command(tmp_path):
    marker = tmp_path / "ran"
    cap = {"scope": "n.main.a", "field": "time_cap_minutes", "minutes": 5, "remaining_s": 0.0}
    spec = _spec(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w')"],
        setup="sleep 0.6",
        deadline_s=0.3,
        cap=cap,
    )
    runner = _runner(tmp_path, spec, received_at=time.monotonic())
    out, frames = await asyncio.wait_for(_run(runner), 5)
    assert out.capped == cap and not marker.exists()
    assert not [f for f in frames if f["t"] == "started"]


async def test_a_command_whose_log_stays_silent_past_idle_s_is_stopped(tmp_path):
    spec = _spec(["sh", "-c", "echo hi; sleep 30"], idle_s=0.3)
    out, _ = await asyncio.wait_for(_run(_runner(tmp_path, spec)), 5)
    assert out.capped["field"] == "idle_minutes" and out.capped["scope"] == "n.main.a"


async def test_a_command_inside_its_caps_is_not_marked_capped(tmp_path):
    spec = _spec(["sh", "-c", "echo hi"], deadline_s=30, cap={"scope": "x"}, idle_s=30)
    out, _ = await _run(_runner(tmp_path, spec))
    assert out.capped is None and out.code == 0


def _acp_job(tmp_path, agent=None, **fields):
    """An ACP job whose shipped launch spec names the server's paths, as `build` leaves it: the
    server's run dir and worktree are `srv/run` and `srv/wt`, the machine's copy of the spec is
    where its path map puts the server's."""
    server = tmp_path / "srv"
    shipped = tmp_path / "area/run/items/w1/acp-launch.json"
    shipped.parent.mkdir(parents=True)
    acp_spec(
        tmp_path,
        at=shipped,
        cwd=str(server / "wt"),
        result_path=str(server / "run/r.json"),
        prompt=f"read {server / 'run/items/w1/instruction.md'}",
        **fields,
    )
    job = _spec(agent if agent is not None else agent_argv())
    job.acp = {"spec": str(server / "run/items/w1/acp-launch.json")}
    job.cwd = job.server_worktree = str(server / "wt")
    job.server_run_dir = str(server / "run")
    return job, shipped.parent.parent.parent.parent / "jobs" / "w1__s1" / "acp-launch.json"


async def test_an_acp_step_runs_the_machines_own_client_over_a_spec_with_the_machines_paths(
    tmp_path,
):
    job, written_at = _acp_job(tmp_path)
    out, frames = await _run(_runner(tmp_path, job))
    area = tmp_path / "area"
    written = json.loads(written_at.read_text())
    assert (out.code, out.rejected) == (0, None), _text(frames)
    assert written["cwd"] == f"{area}/items/w1/repo"
    assert written["result_path"] == f"{area}/run/r.json"
    assert written["prompt"] == f"read {area}/run/items/w1/instruction.md"
    assert written["started_marker"] == str(written_at.with_name("acp-started"))
    assert Path(written["started_marker"]).exists()
    assert written["logged_agent_argv"] == written["agent_argv"] == agent_argv()
    assert oct(written_at.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("sandboxed", [False, True], ids=["unsandboxed", "sandboxed"])
def test_an_acp_steps_client_reaches_the_gate_through_the_machine(tmp_path, sandboxed):
    job = _spec(agent_argv(), sandbox={"kind": "remote"} if sandboxed else None)
    job.acp = {"spec": "/x"}
    env = _runner(tmp_path, job)._env()
    assert (env["KRAFT_HOST"], env["KRAFT_PORT"]) == ("127.0.0.1", "4242")


def test_a_sandboxed_step_that_is_not_acp_has_no_gate_address(tmp_path):
    env = _runner(tmp_path, _spec(agent_argv(), sandbox={"kind": "remote"}))._env()
    # the test process's own KRAFT_PORT may cross as the instance's: only the forwarder's is not
    assert env.get("KRAFT_PORT") != "4242" and env.get("KRAFT_HOST") != "127.0.0.1"


async def test_an_acp_step_whose_agent_is_missing_never_starts_and_names_the_agent(tmp_path):
    job, _ = _acp_job(tmp_path, agent=["opencode-not-installed", "acp"])
    out, frames = await _run(_runner(tmp_path, job))
    assert out.outcome == "config_error" and "opencode-not-installed" in out.rejected
    assert not [f for f in frames if f["t"] == "started"]
