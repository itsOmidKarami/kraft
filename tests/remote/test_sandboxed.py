"""A sandboxed step on a machine (`kraft.remote.sandboxed`): the machine builds the item's sandbox
with its own backend in the order a local step does, and a sandbox it cannot build is
`config_error` with nothing run."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.acp import agent_argv
from support.acp import spec as acp_spec
from support.fake_backend import Remote
from support.harness import commit_all, git

from kraft.remote.sandboxed import Host, Sandboxed
from kraft.worker import backends, channel, refstore
from kraft.worker.backends.docker import OomKill
from kraft.worker.egress import EgressProxy
from remote.test_jobs import _run, _runner, _spec

CREDENTIAL = {
    "env": "EXAMPLE_KEY",
    "domain": "api.example.com",
    "header": "authorization",
    "sentinel": "kraft-proxy-managed",
    "format": "Bearer %s",
}
SANDBOX = {"kind": "remote", "network": {"runtime": {"allow": ["api.example.com"]}}}
RESULT = "/srv/run/items/w1/sessions/s1/out/result.json"


@pytest.fixture
def backend(tmp_path, monkeypatch):
    fake = Remote(tmp_path)
    monkeypatch.setitem(backends._BACKENDS, fake.kind, fake)
    monkeypatch.delenv("EXAMPLE_KEY", raising=False)
    return fake


@pytest.fixture
def host(short_run):
    async def api(method, target, caller, kw):
        return 200, {}, b"{}"

    channels = channel.ChannelRegistry(short_run, None, EgressProxy(api=api), record=_noop)
    return Host(machine="gpu", run_dirs=short_run, channels=channels)


async def _noop(*args):
    return None


def _sandboxed(argv, **extra):
    extra.setdefault("sandbox", SANDBOX)
    extra.setdefault("egress", {"phase": "runtime", "allow": ["api.example.com"], "deny": []})
    extra.setdefault("credentials", [CREDENTIAL])
    extra.setdefault("passthrough", ["EXAMPLE_KEY"])
    extra.setdefault("sentinels", {"EXAMPLE_KEY": "kraft-proxy-managed"})
    extra.setdefault("withheld", ["EXAMPLE_KEY"])
    env = {"KRAFT_RESULT_PATH": RESULT, **extra.pop("env", {})}
    return _spec(argv, env=env, **extra)


async def _step(tmp_path, host, spec):
    runner = _runner(tmp_path, spec, host=host)
    out, frames = await _run(runner)
    return out, frames


async def test_the_backend_is_called_in_the_order_a_local_step_uses(
    tmp_path, host, backend, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_KEY", "the-machines-secret")
    backend.refs = (SimpleNamespace(branch="kraft/x", carried=None),)
    out, _ = await _step(tmp_path, host, _sandboxed(["true"]))
    assert out.rejected is None and out.code == 0
    assert backend.touched == [
        "owner_refusal",
        "code_in",
        "probe",
        "prepare",
        "egress_transport",  # the machine's: does a VM's relay need the mTLS listener?
        "egress_transport",  # the channel's own
        "open_session",
        "wrap",
        "collect",
        "oom_killed",
        "close",
        "close_session",
        "code_out",
    ]
    assert backend.kraft_ca is not None  # a managed credential: the CA is made on the machine


async def test_the_workers_env_holds_the_sentinel_never_the_machines_secret(
    tmp_path, host, backend, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_KEY", "the-machines-secret")
    marker = tmp_path / "seen"
    code = f"import os; open({str(marker)!r}, 'w').write(str(os.environ.get('EXAMPLE_KEY')))"
    await _step(tmp_path, host, _sandboxed([sys.executable, "-c", code]))
    assert marker.read_text() == "None"  # withheld from the client; the proxy injects it


@pytest.mark.parametrize(
    ("refusal", "says"),
    [
        ({"unrunnable": "the image kraft/x is not here"}, "image kraft/x is not here"),
        ({"prepared": "no runtime"}, "no runtime"),
    ],
    ids=["probe-refuses", "prepare-cannot"],
)
async def test_a_sandbox_the_machine_cannot_build_stops_the_step_and_runs_nothing(
    tmp_path, host, backend, monkeypatch, refusal, says
):
    monkeypatch.setenv("EXAMPLE_KEY", "v")
    for name, value in refusal.items():
        setattr(backend, name, value)
    marker = tmp_path / "ran"
    spec = _sandboxed([sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    out, frames = await _step(tmp_path, host, spec)
    assert out.outcome == "config_error" and out.rejected.startswith("gpu: ")
    assert says in out.rejected and not marker.exists()
    assert "wrap" not in backend.touched and not [f for f in frames if f["t"] == "started"]


async def test_a_credential_the_machine_lacks_stops_the_step_naming_the_variable(
    tmp_path, host, backend
):
    marker = tmp_path / "ran"
    spec = _sandboxed([sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    out, _ = await _step(tmp_path, host, spec)
    assert out.outcome == "config_error" and "EXAMPLE_KEY" in out.rejected
    assert backend.touched == [] and not marker.exists()


async def test_a_machine_with_no_sandbox_support_never_runs_a_sandboxed_step(tmp_path, backend):
    marker = tmp_path / "ran"
    spec = _sandboxed([sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    out, _ = await _step(tmp_path, None, spec)
    assert out.outcome == "config_error" and not marker.exists()


async def test_an_oom_the_backend_reports_comes_back_with_the_outcome(
    tmp_path, host, backend, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_KEY", "v")
    backend.oom = OomKill("1g", True)
    out, _ = await _step(tmp_path, host, _sandboxed(["true"]))
    assert out.oom == {"memory": "1g", "confirmed": True}


async def test_a_setup_command_runs_in_the_sandbox_too(tmp_path, host, backend, monkeypatch):
    monkeypatch.setenv("EXAMPLE_KEY", "v")
    spec = _sandboxed(["true"], setup="exit 0", setup_sandbox={"egress": None, "credentials": []})
    await _step(tmp_path, host, spec)
    assert backend.touched.count("wrap") == 2 and backend.touched.count("probe") == 1


async def test_a_setup_runtime_that_vanished_before_the_exec_is_a_config_error(
    tmp_path, host, backend, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_KEY", "v")
    backend.wrap = lambda cmd, *a, **kw: ["/no/such/runtime", *cmd]
    spec = _sandboxed(["true"], setup="exit 0", setup_sandbox={"egress": None, "credentials": []})
    out, _ = await _step(tmp_path, host, spec)
    assert out.outcome == "config_error"
    assert out.rejected.startswith("gpu: could not run the setup command: ")


async def test_a_refusal_after_the_ref_store_was_made_leaves_none_behind(
    tmp_path, host, backend, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_KEY", "v")
    runner = _runner(tmp_path, _sandboxed(["true"]), host=host)
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q")
    commit_all(main)
    git(main, "worktree", "add", "-q", "-b", "kraft/x", str(runner.root))
    backend.code_in = lambda base, cwd, branch, **kw: refstore.prepare_stores(
        base, cwd, branch, **kw
    )
    backend.prepared = "no runtime"
    out, _ = await _run(runner)
    assert out.outcome == "config_error" and "no runtime" in out.rejected
    store = host.run_dirs.base / "sandbox-git"
    assert backend.touched[-1] == "prepare"  # the refusal came after the ref store was made
    assert not store.exists() or not list(store.iterdir())


# ── an ACP step: the client outside, the agent alone in the sandbox ──


_PLAIN = dict(egress=None, credentials=[], withheld=[], sentinels={}, passthrough=[])


def _acp_box(tmp_path, host, **extra):
    """A `Sandboxed` over an ACP job whose launch spec (as the machine holds it, on its own paths)
    sits beside the other shipped files; `(box, the spec's machine path)`."""
    shipped = tmp_path / "area/run/items/w1/acp-launch.json"
    shipped.parent.mkdir(parents=True)
    acp_spec(tmp_path, at=shipped)
    job = _sandboxed(agent_argv(), ro=["/srv/run/items/w1/acp-launch.json"], **_PLAIN, **extra)
    job.acp = {"spec": "/srv/run/items/w1/acp-launch.json"}
    runner = _runner(tmp_path, job, host=host)
    return Sandboxed(host, job, runner.pathmap, runner.root, runner.dir), shipped


async def test_an_acp_agent_is_wrapped_with_stdin_kept_and_the_client_is_not(
    tmp_path, host, backend
):
    box, _ = _acp_box(tmp_path, host)
    launch = await box.step(["agent", "acp"], {}, {}, acp=True)
    assert launch.argv[-2:] == ["agent", "acp"] and backend.wrap_kw[-1].get("acp") is True
    assert backend.probed == "agent"


async def test_an_agents_step_that_is_not_acp_is_wrapped_without_the_keyword(
    tmp_path, host, backend
):
    box, _ = _acp_box(tmp_path, host)
    await box.step(["agent"], {}, {})
    assert "acp" not in backend.wrap_kw[-1]


async def test_the_clients_gate_address_never_reaches_the_agents_sandbox(tmp_path, host, backend):
    box, _ = _acp_box(tmp_path, host)
    env = {"KRAFT_HOST": "127.0.0.1", "KRAFT_PORT": "424242-canary"}
    launch = await box.step(["agent", "acp"], env, {"FAKE_ACP_ASK_ALL": "1"}, acp=True)
    sent = backend.wrapped_env[-1]
    assert "KRAFT_PORT" not in sent and "KRAFT_HOST" not in sent
    assert sent["FAKE_ACP_ASK_ALL"] == "1"
    assert "424242-canary" not in " ".join(launch.argv)


async def test_the_launch_spec_is_never_mounted_into_the_agents_sandbox(tmp_path, host, backend):
    box, shipped = _acp_box(tmp_path, host)
    other = shipped.with_name("instruction.md")
    other.write_text("the task")
    box.spec.ro.append("/srv/run/items/w1/instruction.md")
    await box.step(["agent", "acp"], {}, {}, acp=True)
    mounted = [Path(p) for p in backend.wrap_results[-1]]
    assert other in mounted and shipped not in mounted


def _acp_job(tmp_path, *, corrupt=False, **fields):
    """The sandboxed ACP job, the fake agent behind `Remote.wrap`'s prefix. `corrupt`: a shipped
    spec the client cannot read, so it fails before it spawns anything."""
    shipped = tmp_path / "area/run/items/w1/acp-launch.json"
    shipped.parent.mkdir(parents=True)
    acp_spec(tmp_path, at=shipped, cwd="/srv/wt", result_path=RESULT, **fields)
    if corrupt:
        shipped.write_text(json.dumps({**json.loads(shipped.read_text()), "line_limit": "bad"}))
    (tmp_path / "area/run/items/w1/sessions/s1/out").mkdir(parents=True)  # the machine makes it
    job = _sandboxed(agent_argv(), sandbox={"kind": "remote"}, **_PLAIN)
    job.acp = {"spec": "/srv/run/items/w1/acp-launch.json"}
    job.cwd = job.server_worktree = "/srv/wt"
    return job, tmp_path / "area/jobs/w1__s1"


async def test_a_sandboxed_acp_step_runs_the_client_outside_over_the_wrapped_agent(
    tmp_path, host, backend
):
    """The fake backend does not scrub the environment, so the agent here inherits the client's:
    what must not cross is asserted on what `wrap` was given (above) and on the real docker argv
    in the e2e test; here only the spec and the argv."""
    job, job_dir = _acp_job(tmp_path)
    out, frames = await _step(tmp_path, host, job)
    written = json.loads((job_dir / "acp-launch.json").read_text())
    assert (out.code, out.rejected) == (0, None), frames
    assert written["agent_argv"][0] == "env" and written["agent_argv"][2:] == agent_argv()
    # the log shows the agent as shipped, never `Remote.wrap`'s prefix
    assert written["logged_agent_argv"] == agent_argv()
    assert Path(written["started_marker"]).exists()
    assert backend.wrap_kw[-1]["acp"] is True


@pytest.mark.parametrize(
    ("fields", "outcome"),
    [({"corrupt": True}, None), ({}, "config_error")],
    ids=["client-died-before-spawning", "sandbox-never-started"],
)
async def test_a_client_that_died_before_spawning_is_not_a_sandbox_that_never_started(
    tmp_path, host, backend, fields, outcome
):
    backend.never_started = True  # `launch_failed` answers True for every exit, in both rows
    if not fields:  # a wrapped command that exits 1 after the client wrote the marker
        backend.wrap = lambda cmd, *a, **kw: ["sh", "-c", "exit 1"]
    job, _ = _acp_job(tmp_path, **fields)
    out, _ = await _step(tmp_path, host, job)
    assert out.outcome == outcome
    assert (out.rejected is None) == (outcome is None)
    assert out.code != 0 or outcome is not None


async def test_a_137_from_the_wrapped_agent_is_the_steps_exit_code(tmp_path, host, backend):
    """Spec 8 keeps 137 for OOM detection: the client exits as the agent that was killed would."""
    job, _ = _acp_job(tmp_path, agent_env={"KRAFT_FAKE_ACP": "kill"})
    out, _ = await _step(tmp_path, host, job)
    assert out.code == 137 and out.rejected is None
