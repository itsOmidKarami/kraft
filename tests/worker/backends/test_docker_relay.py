"""`network:` on the docker backend: the relay a session's worker joins, the
proxy environment it gets instead of the daemon's, the probe that picks a
socket per session or, on a runtime in a VM, the TLS transport's two relays,
and refuses when it cannot tell."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft.worker import ca, shim
from kraft.worker.backends import docker
from kraft.worker.sandbox import SandboxNotReady

POLICED = {"kind": "docker", "image": "img", "network": {"runtime": {"allow": ["a.io"]}}}
SOCK = Path("/run/sn/0123456789abcdef/s.sock")


@pytest.fixture
def calls(monkeypatch):
    """Every `docker_call` a test makes, answered `(0, "")` unless the test
    says otherwise by name (`answers["run"] = (125, "")`); the image's
    `Config.Env` holds `PATH=/img/bin`."""
    made: list[tuple[str, ...]] = []
    answers: dict[str, tuple[int, str] | None] = {"image": (0, '["PATH=/img/bin"]\n')}

    async def call(*args, timeout=None):
        made.append(args)
        return answers.get(args[0], (0, ""))

    monkeypatch.setattr(docker, "docker_call", call)
    return SimpleNamespace(made=made, answers=answers)


def test_a_launch_under_network_joins_its_relay_and_mounts_the_shim(monkeypatch):
    """The daemon's own proxy is where the *proxy* goes out, never the
    worker: under `network:` the worker's only route is its relay. And every
    such launch, whatever its policy, mounts its hook's command: a missing
    one runs the call (codex treats an unrunnable hook as allow)."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    backend = docker.DockerBackend()

    policed = backend.wrap(["true"], "/w", POLICED, None, session_id="s1")
    open_ = backend.wrap(["true"], "/w", {"kind": "docker", "image": "img"}, None, session_id="s1")

    assert "--network=container:kraft-relay-s1" in policed
    assert "HTTPS_PROXY" not in policed
    assert policed[policed.index(f"{shim.HOST_DIR}:/opt/kraft/bin:ro") - 1] == "-v"
    assert not any(a.startswith("--network") for a in open_)
    assert open_[open_.index("HTTPS_PROXY") - 1] == "-e"
    assert not any("/opt/kraft/bin" in a for a in open_)


@pytest.mark.parametrize(
    ("answer", "path"),
    [
        ((0, json.dumps(["A=b", "PATH=/opt/x:/usr/bin"])), "/opt/kraft/bin:/opt/x:/usr/bin"),
        ((0, "null"), f"/opt/kraft/bin:{docker.DEFAULT_PATH}"),
        ((1, ""), None),
        (None, None),
    ],
    ids=["image-path", "image-sets-none", "not-pulled", "no-answer"],
)
async def test_the_shim_goes_first_on_the_image_own_path(calls, monkeypatch, answer, path):
    """`-e PATH` replaces the image's, so it is read off the image; one that
    cannot be read is refused before any relay starts."""
    monkeypatch.setattr(docker, "socket_channel", lambda image: True)
    calls.answers["image"] = answer
    if path is None:
        with pytest.raises(SandboxNotReady, match="PATH"):
            await docker.DockerBackend().open_session("s1", POLICED, SOCK)
        assert [c[0] for c in calls.made] == ["image"]
    else:
        env = await docker.DockerBackend().open_session("s1", POLICED, SOCK)
        assert env["PATH"] == path
    assert calls.made[0] == ("image", "inspect", "--format", "{{json .Config.Env}}", "img")


def test_a_network_sandbox_never_runs_without_its_relay():
    with pytest.raises(docker.SandboxRefused, match="relay"):
        docker.docker_argv(["true"], "/w", POLICED, None)


def test_the_relay_has_no_network_and_mounts_the_sockets_directory(monkeypatch):
    """R8: as locked down as a worker, read-only, labelled as a relay, and
    named rather than `--rm` (`close_session` removes it). The directory, not
    the socket file: a daemon restart makes a new socket a file mount would
    never see."""
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(selinux="relabel"))
    argv = docker.relay_argv("s1", SOCK, "socat-img")

    for flag in (
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--log-driver=none",
        "--memory=64m",
        "--pids-limit=256",
    ):
        assert flag in argv
    assert "--rm" not in argv
    assert argv[argv.index("--name") + 1] == "kraft-relay-s1"
    labels = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
    assert labels == [docker.home_label(), docker.RELAY_LABEL]
    assert argv[argv.index("-v") + 1] == f"{SOCK.parent}:{SOCK.parent}:z"
    assert argv[argv.index("socat-img") + 1 :] == [
        "TCP-LISTEN:3128,bind=127.0.0.1,fork,reuseaddr",
        f"UNIX-CONNECT:{SOCK}",
    ]


async def test_open_session_starts_the_relay_and_close_session_removes_it(calls, monkeypatch):
    monkeypatch.setattr(docker, "socket_channel", lambda image: True)
    backend = docker.DockerBackend()

    assert await backend.open_session("s1", {"kind": "docker", "image": "img"}, SOCK) == {}
    assert calls.made == []

    env = await backend.open_session("s1", POLICED, SOCK)
    await backend.close_session("s1")

    assert env == {**docker.RELAY_PROXY_ENV, "PATH": "/opt/kraft/bin:/img/bin"}
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://127.0.0.1:3128"
    assert env["NO_PROXY"] == env["no_proxy"] == ""
    assert calls.made[1][:4] == ("run", "-d", "--name", "kraft-relay-s1")
    assert calls.made[2:] == [
        ("rm", "-f", "kraft-relay-s1", "kraft-relay-b-s1"),
        ("volume", "rm", "-f", "kraft-egress-s1"),
    ]


@pytest.mark.parametrize(
    ("reachable", "said", "transport"),
    [
        (True, "", "unix"),
        (False, "OperatingSystem: Docker Desktop", "tls"),
        (False, "", "does not say it runs in a VM"),
        (None, "", "could not tell"),
    ],
    ids=["linux-native", "vm", "socket-refused-off-a-vm", "inconclusive"],
)
async def test_the_probe_picks_the_transport_and_refuses_when_it_cannot_tell(
    monkeypatch, reachable, said, transport
):
    """The two-hop transport only where the runtime says it runs in a VM:
    a socket refused anywhere else (SELinux, permissions) is a problem to
    fix, and a relay B there could not reach the loopback listener."""
    monkeypatch.setattr(docker, "socket_channel", lambda image: reachable)
    monkeypatch.setattr(docker, "_vm_evidence", lambda host: said)
    if transport in ("unix", "tls"):
        assert await docker.DockerBackend().egress_transport() == transport
    else:
        with pytest.raises(SandboxNotReady, match=transport):
            await docker.DockerBackend().egress_transport()


@pytest.mark.parametrize(
    ("engine", "gateway"), list(zip(("docker", "podman"), ca.GATEWAY_HOSTS, strict=True))
)
async def test_the_tls_transport_joins_two_relays_through_a_volume(
    calls, monkeypatch, engine, gateway
):
    """Relay B: the default network, the session's volume and its own
    certificate directory read-only, nothing else; dialling the listener's
    persisted port by the runtime's gateway name, verifying it. The relay
    the worker joins: no network, the same volume. All of it gone at close."""
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(engine, engine=engine))
    run_dirs = docker._run_dirs()
    run_dirs.ca.mkdir(parents=True)
    (run_dirs.ca / "tls-port").write_text("40123\n")
    certs = ca.session_dir(run_dirs, "s1")
    backend = docker.DockerBackend()

    assert await backend.open_session("s1", POLICED, None) == {
        **docker.RELAY_PROXY_ENV,
        "PATH": "/opt/kraft/bin:/img/bin",
    }

    _, create, relay_b, relay = calls.made
    assert create == (
        "volume", "create", "--label", docker.home_label(),
        "--label", docker.VOLUME_LABEL, "kraft-egress-s1",
    )  # fmt: skip
    assert relay_b[:4] == ("run", "-d", "--name", "kraft-relay-b-s1")
    assert not any(a.startswith("--network") for a in relay_b)
    assert [relay_b[i + 1] for i, a in enumerate(relay_b) if a == "-v"] == [
        "kraft-egress-s1:/tmp",
        f"{certs}:/c:ro",
    ]
    assert relay_b[-2:] == (
        "UNIX-LISTEN:/tmp/s.sock,fork,mode=600,unlink-early",
        f"OPENSSL:{gateway}:40123,cert=/c/client.pem,key=/c/key.pem,cafile=/c/ca.pem,verify=1",
    )
    assert (certs / "client.pem").is_file()
    assert relay[:4] == ("run", "-d", "--name", "kraft-relay-s1")
    assert "--network=none" in relay
    assert relay[relay.index("-v") + 1] == "kraft-egress-s1:/tmp"
    assert relay[-1] == "UNIX-CONNECT:/tmp/s.sock"

    await backend.close_session("s1")
    assert calls.made[4:] == [
        ("rm", "-f", "kraft-relay-s1", "kraft-relay-b-s1"),
        ("volume", "rm", "-f", "kraft-egress-s1"),
    ]
    assert not certs.exists()


async def test_the_tls_transport_refuses_before_the_listener_ever_started(calls):
    with pytest.raises(SandboxNotReady, match="TLS listener has not started"):
        await docker.DockerBackend().open_session("s1", POLICED, None)
    assert [c[0] for c in calls.made] == ["image"]


@pytest.mark.parametrize("sock", [SOCK, None], ids=["unix", "tls"])
async def test_a_relay_that_did_not_start_is_refused_and_removed(calls, sock):
    run_dirs = docker._run_dirs()
    run_dirs.ca.mkdir(parents=True)
    (run_dirs.ca / "tls-port").write_text("40123\n")
    calls.answers["run"] = (125, "")
    with pytest.raises(SandboxNotReady, match="could not start the sandbox's egress relay"):
        await docker.DockerBackend().open_session("s1", POLICED, sock)
    assert calls.made[-2:] == [
        ("rm", "-f", "kraft-relay-s1", "kraft-relay-b-s1"),
        ("volume", "rm", "-f", "kraft-egress-s1"),
    ]
    assert not ca.session_dir(run_dirs, "s1").exists()


async def test_sweep_keeps_a_live_sessions_relays_volume_and_certificate(calls):
    listed = ("kraft-live", "kraft-relay-live", "kraft-relay-b-live", "kraft-relay-gone")
    calls.answers["ps"] = (0, "\n".join(listed))
    calls.answers["volume"] = (0, "kraft-egress-live\nkraft-egress-gone\n")
    run_dirs = docker._run_dirs()
    for sid in ("live", "gone"):
        ca.mint_session_cert(run_dirs, sid)

    assert await docker.DockerBackend().sweep(["live"]) == ["kraft-relay-gone", "kraft-egress-gone"]
    assert calls.made[-1] == ("volume", "rm", "-f", "kraft-egress-gone")
    assert [d.name for d in (run_dirs.ca / "sessions").iterdir()] == ["live"]


def test_the_socket_probe_is_kept_until_doctor_refreshes(monkeypatch):
    """Asked once per runtime, like its limits; an inconclusive answer is
    not kept, and doctor's refresh asks again."""
    answers = iter([None, False, True])
    asked = []

    def probe(host, image):
        asked.append(image)
        return next(answers)

    monkeypatch.setattr(docker, "_probe_socket_channel", probe)
    monkeypatch.setattr(docker, "detect_runtime", docker.Runtime)

    assert [docker.socket_channel("r") for _ in range(3)] == [None, False, False]
    assert len(asked) == 2
    docker.runtime(refresh=True)
    assert docker.socket_channel("r") is True and len(asked) == 3


def test_a_probe_answer_does_not_clobber_a_runtime_refreshed_meanwhile(monkeypatch):
    """A doctor refresh landing mid-probe wins: the answer was about the
    runtime that was probed, and is kept only on that one."""
    monkeypatch.setattr(docker, "detect_runtime", docker.Runtime)
    monkeypatch.setattr(docker, "_RUNTIME", None)

    def probe(host, image):
        docker.runtime(refresh=True)
        return False

    monkeypatch.setattr(docker, "_probe_socket_channel", probe)
    assert docker.socket_channel("r") is False
    assert docker._RUNTIME.socket_channel is None


@pytest.mark.parametrize(
    ("returncode", "answer"),
    [(0, True), (1, False), (125, None), (127, None)],
    ids=["connected", "connect-failed", "docker-failed", "no-socat"],
)
def test_the_probe_believes_only_the_relays_own_answer(monkeypatch, returncode, answer):
    """socat exits 1 when `connect` fails (ENOTSUP in a VM); anything else
    that is not 0 is the runtime failing, which says nothing either way."""
    run_dir = Path(tempfile.mkdtemp(prefix="kraft-probe-", dir="/tmp"))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
    ran = []

    def fake_run(argv, **kw):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr(docker.subprocess, "run", fake_run)
    try:
        assert docker._probe_socket_channel(docker.Runtime(), "socat-img") is answer
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    (argv,) = ran
    assert {"--network=none", "--pull=never", "--cap-drop=ALL"} <= set(argv)
    mounted = argv[argv.index("-v") + 1].split(":")[0]
    assert argv[-1] == f"UNIX-CONNECT:{mounted}/s.sock"


@pytest.fixture
def healthy_but_relay(monkeypatch):
    """`health` on a machine whose daemon answers and whose repository image
    is pulled; `pulled["relay"]` says whether the relay image is."""
    pulled = {"relay": True}
    relay = docker._forward._sandbox_host(os.environ).relay_image

    async def call(*args, timeout=None):
        if args[0] == "version":
            return 0, "29.3.1"
        return (0, "sha") if args[-1] != relay or pulled["relay"] else (1, "")

    monkeypatch.setattr(docker, "docker_call", call)
    monkeypatch.setattr(docker, "detect_runtime", docker.Runtime)
    return SimpleNamespace(pulled=pulled, relay=relay)


async def test_doctor_fails_when_the_relay_image_is_not_pulled(healthy_but_relay, monkeypatch):
    """A launch never pulls it: the probe runs `--pull=never`."""
    monkeypatch.setattr(docker, "socket_channel", lambda image: True)
    backend = docker.DockerBackend()
    ok, detail = await backend.health(POLICED)
    assert ok and f"egress relay {healthy_but_relay.relay}" in detail

    healthy_but_relay.pulled["relay"] = False
    ok, detail = await backend.health(POLICED)
    assert not ok and f"docker pull {healthy_but_relay.relay}" in detail


@pytest.mark.parametrize(
    ("engine", "info", "said"),
    [
        ("docker", "Docker Desktop", "OperatingSystem: Docker Desktop"),
        ("podman", "true", "ServiceIsRemote: true"),
        ("docker", "Ubuntu 24.04", None),
        ("podman", "false", None),
    ],
    ids=["docker-desktop", "podman-machine", "docker-not-a-vm", "podman-not-a-vm"],
)
async def test_doctor_names_the_two_hop_transport_on_a_vm_runtime(
    healthy_but_relay, monkeypatch, engine, info, said
):
    """Not a failure since P4b: doctor asks the probe again (a launch's
    cached answer may be from before the runtime changed) and says which
    transport egress takes, naming the VM in the runtime's words. Where
    the runtime says no VM, the refused socket is a failure."""
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(socket_channel=True))
    monkeypatch.setattr(docker, "detect_runtime", lambda: docker.Runtime(engine, engine=engine))
    monkeypatch.setattr(docker, "_probe_socket_channel", lambda host, image: False)
    monkeypatch.setattr(docker, "_run", lambda *argv: f"{info}\n")

    ok, detail = await docker.DockerBackend().health(POLICED)

    assert ok is (said is not None)
    assert f"two-hop TLS transport ({said})" in detail if said else "SELinux" in detail
