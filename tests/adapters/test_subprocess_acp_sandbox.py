"""`run_task` for an ACP launch in a sandbox: the backend wraps the agent the client spawns, never
the client itself (ACP part 3)."""

import pytest
from support import acp as acp_support
from support.fake_backend import Remote
from support.worktree import make_item

from kraft import item_paths
from kraft.acp import launch as acp_launch
from kraft.adapters import subprocess as sp
from kraft.worker import backends

SANDBOX = {"kind": "remote"}


@pytest.fixture
def backend(tmp_path, monkeypatch):
    fake = Remote(tmp_path)
    monkeypatch.setitem(backends._BACKENDS, fake.kind, fake)
    return fake


async def _run(database, run_dirs, tmp_path, sid="s1", **fields):
    """The client's command over a launch spec at the session's own folder, as `acp.launch.command`
    leaves it; `(status, spec path, the session's files)`."""
    await make_item(database, str(tmp_path / "repo"))
    files = item_paths.session_files(run_dirs, "w1", sid)
    files.folder.mkdir(parents=True, exist_ok=True)
    fields.setdefault("started_marker", str(acp_launch.marker_path(files)))
    path = acp_support.spec(
        tmp_path,
        at=files.instruction.with_name("acp-launch.json"),
        result_path=str(files.result),
        **fields,
    )
    status = await sp.run_task(
        database,
        run_dirs,
        session_id=sid,
        work_item_id="w1",
        node_id="build",
        hook_point="build.main.a",
        cwd=tmp_path,
        cmd=acp_support.client_argv(path),
        sandbox=SANDBOX,
        reader="acp",
        poll_s=0.02,
    )
    return status, path, files


async def test_only_the_agent_is_wrapped_and_the_client_stays_the_command(
    database, run_dirs, tmp_path, backend
):
    status, path, files = await _run(database, run_dirs, tmp_path)
    wrapped = acp_launch.read_spec(path).agent_argv
    # What `Remote.wrap` returned: its own prefix, then the agent exactly as the spec named it.
    assert wrapped[0] == "env" and wrapped[2:] == acp_support.agent_argv()
    # Wrapping the client's own command would make the agent a client that spawns itself.
    assert "acp-client" not in " ".join(wrapped)
    assert len(backend.wrapped_env) == 1 and backend.wrap_kw[0]["acp"] is True
    row = database.read(
        lambda c: c.execute("SELECT command FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert row["command"].startswith(" ".join(acp_launch.client_prefix()))
    assert status == "done", files.log.read_text()


@pytest.mark.parametrize("wrapped_before", [False, True], ids=["fresh", "spec-already-wrapped"])
async def test_the_probe_targets_the_agent_not_the_client(
    database, run_dirs, tmp_path, backend, wrapped_before
):
    agent = tmp_path / "agent-bin"
    agent.symlink_to(acp_support.agent_argv()[0])
    argv = [str(agent), str(acp_support.FAKE)]
    # A second wrap of the same spec starts from the agent, not from the argv the first left.
    fields = {"agent_argv": argv}
    if wrapped_before:
        fields = {"agent_argv": ["env", "K=V", *argv], "logged_agent_argv": argv}
    _, path, _ = await _run(database, run_dirs, tmp_path, **fields)
    assert backend.probed == str(agent)
    assert backend.wrapped_env and acp_launch.read_spec(path).agent_argv[2:] == argv


async def test_the_entrys_asking_switch_crosses_into_the_sandbox(
    database, run_dirs, tmp_path, backend
):
    await _run(database, run_dirs, tmp_path, agent_env={"FAKE_ACP_ASK_ALL": "1"})
    assert backend.wrapped_env[0]["FAKE_ACP_ASK_ALL"] == "1"


@pytest.mark.parametrize(
    ("fields", "marker_first", "spec_gone", "want"),
    [
        ({"agent_argv": []}, False, False, "failed"),
        ({}, True, False, "config_error"),
        ({"agent_argv": []}, True, False, "failed"),
        ({}, True, True, "config_error"),
    ],
    ids=["client-failed-first", "sandbox-never-started", "stale-marker-is-ignored", "spec-gone"],
)
async def test_a_client_that_fails_before_spawning_is_not_a_sandbox_that_never_started(
    database, run_dirs, tmp_path, backend, fields, marker_first, spec_gone, want
):
    backend.never_started = True  # what `launch_failed` makes of the exit in every row
    if "agent_argv" not in fields:
        # a wrapped command that exits 1 after the client wrote the marker; the late status check
        # reads the spec it read at entry, so one gone (or unreadable) by then is no exception
        spec_file = item_paths.session_files(run_dirs, "w1", "s1").instruction.with_name(
            "acp-launch.json"
        )
        gone = f"rm -f {spec_file}; " if spec_gone else ""
        backend.wrap = lambda cmd, *a, **kw: ["sh", "-c", f"{gone}exit 1"]
    if marker_first and fields:
        files = item_paths.session_files(run_dirs, "w1", "s1")
        files.folder.mkdir(parents=True, exist_ok=True)
        acp_launch.marker_path(files).write_text("stale")
    status, _, _ = await _run(database, run_dirs, tmp_path, **fields)
    assert status == want


async def test_a_wrapped_launch_leaves_no_env_literal_in_the_session_log(
    database, run_dirs, tmp_path, backend, monkeypatch
):
    real = backend.wrap
    monkeypatch.setattr(
        backend,
        "wrap",
        lambda cmd, *a, **kw: ["env", "SECRET=canary-9f3a", *real(cmd, *a, **kw)[2:]],
    )
    _, _, files = await _run(database, run_dirs, tmp_path)
    log = files.log.read_text()
    assert "canary-9f3a" not in log
    assert '"agent":[' in log and str(acp_support.FAKE) in log
