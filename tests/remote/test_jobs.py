"""A job on the machine: its environment, its log and its signals."""

import asyncio
import base64
import sys
import time

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
    await asyncio.sleep(0.3)
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
