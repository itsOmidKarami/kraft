"""A sandboxed step on a machine (`kraft.remote.sandboxed`): the machine builds the item's sandbox
with its own backend in the order a local step does, and a sandbox it cannot build is
`config_error` with nothing run."""

import sys
from types import SimpleNamespace

import pytest
from support.fake_backend import Remote
from support.harness import commit_all, git

from kraft.remote.sandboxed import Host
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
