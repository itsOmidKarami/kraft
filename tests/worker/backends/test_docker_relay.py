"""`network:` on the docker backend: the relay a session's worker joins, the
proxy environment it gets instead of the daemon's, and the probe that
refuses a runtime whose containers cannot reach a host unix socket."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from kraft.worker.backends import docker
from kraft.worker.sandbox import SandboxNotReady

POLICED = {"kind": "docker", "image": "img", "network": {"runtime": {"allow": ["a.io"]}}}
SOCK = Path("/run/sn/0123456789abcdef/s.sock")


@pytest.fixture
def calls(monkeypatch):
    """Every `docker_call` a test makes, answered `(0, "")` unless the test
    says otherwise by name (`answers["run"] = (125, "")`)."""
    made: list[tuple[str, ...]] = []
    answers: dict[str, tuple[int, str] | None] = {}

    async def call(*args, timeout=None):
        made.append(args)
        return answers.get(args[0], (0, ""))

    monkeypatch.setattr(docker, "docker_call", call)
    return SimpleNamespace(made=made, answers=answers)


def test_network_set_replaces_forwarded_proxies_with_the_relay(monkeypatch):
    """The daemon's own proxy is where the *proxy* goes out, never the
    worker: under `network:` the worker's only route is its relay."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    backend = docker.DockerBackend()

    policed = backend.wrap(["true"], "/w", POLICED, None, session_id="s1")
    open_ = backend.wrap(["true"], "/w", {"kind": "docker", "image": "img"}, None, session_id="s1")

    assert "--network=container:kraft-relay-s1" in policed
    assert "HTTPS_PROXY" not in policed
    assert not any(a.startswith("--network") for a in open_)
    assert open_[open_.index("HTTPS_PROXY") - 1] == "-e"


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

    assert env == docker.RELAY_PROXY_ENV
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://127.0.0.1:3128"
    assert env["NO_PROXY"] == env["no_proxy"] == ""
    assert calls.made[0][:4] == ("run", "-d", "--name", "kraft-relay-s1")
    assert calls.made[1] == ("rm", "-f", "kraft-relay-s1")


@pytest.mark.parametrize(
    ("reachable", "said"),
    [(False, "runs its containers in a VM"), (None, "could not tell")],
    ids=["vm-runtime", "probe-inconclusive"],
)
async def test_a_runtime_that_cannot_carry_the_channel_refuses_network(
    calls, monkeypatch, reachable, said
):
    monkeypatch.setattr(docker, "socket_channel", lambda image: reachable)
    with pytest.raises(SandboxNotReady, match=said):
        await docker.DockerBackend().open_session("s1", POLICED, SOCK)
    assert calls.made == []


async def test_a_relay_that_did_not_start_is_refused_and_removed(calls, monkeypatch):
    monkeypatch.setattr(docker, "socket_channel", lambda image: True)
    calls.answers["run"] = (125, "")
    with pytest.raises(SandboxNotReady, match="could not start the sandbox's egress relay"):
        await docker.DockerBackend().open_session("s1", POLICED, SOCK)
    assert calls.made[-1] == ("rm", "-f", "kraft-relay-s1")


async def test_sweep_keeps_a_live_sessions_relay(calls):
    calls.answers["ps"] = (0, "kraft-live\nkraft-relay-live\nkraft-relay-gone\n")
    assert await docker.DockerBackend().sweep(["live"]) == ["kraft-relay-gone"]


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
    ],
    ids=["docker-desktop", "podman-machine"],
)
async def test_doctor_fails_naming_the_vm_runtime_when_network_is_set(
    healthy_but_relay, monkeypatch, engine, info, said
):
    """Doctor asks the probe again (a launch's cached answer may be from
    before the runtime changed) and names the VM in the runtime's words."""
    monkeypatch.setattr(docker, "_RUNTIME", docker.Runtime(socket_channel=True))
    monkeypatch.setattr(docker, "detect_runtime", lambda: docker.Runtime(engine, engine=engine))
    monkeypatch.setattr(docker, "_probe_socket_channel", lambda host, image: False)
    monkeypatch.setattr(docker, "_run", lambda *argv: f"{info}\n")

    ok, detail = await docker.DockerBackend().health(POLICED)

    assert not ok
    assert f"({said})" in detail and "VM transport is not available yet" in detail
