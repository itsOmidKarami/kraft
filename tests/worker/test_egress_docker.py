"""`sandbox.network` against a real container runtime, docker and podman: a
worker launched by `run_task` shares a network-less relay's namespace, so it
has loopback and nothing else, and reaches out only through the daemon's
egress proxy (`sandbox-egress-is-deny-by-default-when-declared`).

Each runs over the transport the runtime's probe picks: a socket per session
on Linux-native docker and podman, the two-hop mTLS relay on Docker Desktop
and podman machine, where a container cannot connect to a host socket (spike
4.5a). The documented manual run, on a Mac with both:

    KRAFT_E2E_REQUIRE=docker,podman just test tests/worker/test_egress_docker.py --no-testmon

A Linux runner also forces the two-hop transport (`forced-tls`), with the
listener on every address instead of loopback and, for docker, relay B's
gateway name mapped to the bridge's gateway: Linux containers cannot reach
the host's loopback. Only the test does that; the daemon listens on
127.0.0.1.

The container side is all real: the relay, the worker, the proxy env Kraft
gives it, the socket into this process. Only the proxy's last hop is a
seam: `allowed.test` resolves to a private address its exact allow entry
admits, and dialling that address reaches the fake server on this host."""

import asyncio
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from kraft import events, store
from kraft.adapters import subprocess as sp
from kraft.paths import RunDirs, default_templates_dir
from kraft.worker import channel
from kraft.worker.backends import docker
from kraft.worker.egress import SANDBOX_EGRESS_REFUSED, EgressProxy

IMAGE = "docker.io/library/alpine:3"
#: What `allowed.test` resolves to, for the proxy only: private, so only an
#: exact allow entry reaches it.
ALLOWED_ADDRESS = "10.254.254.254"


def _unavailable(cli: str, why: str):
    # Where the runtime is required, an unreachable daemon or registry is a
    # failure, never a quiet skip past KRAFT_E2E_REQUIRE.
    if cli in os.environ.get("KRAFT_E2E_REQUIRE", "").split(","):
        pytest.fail(why)
    pytest.skip(why)


def _pulled(cli: str, image: str) -> None:
    if subprocess.run([cli, "image", "inspect", image], capture_output=True).returncode == 0:
        return
    pulled = subprocess.run([cli, "pull", "-q", image], capture_output=True, text=True)
    if pulled.returncode != 0:
        _unavailable(cli, f"e2e: {cli} could not pull {image}: {pulled.stderr.strip()}")


@pytest.fixture
def short_run(monkeypatch):
    """A run dir short enough for a unix socket path, for the probe
    (`KRAFT_RUN_DIR`) and the channels alike."""
    # Resolved: podman machine shares macOS's /private/tmp, not /tmp.
    base = Path(tempfile.mkdtemp(prefix="kraft-e2e-", dir="/tmp")).resolve()
    monkeypatch.setenv("KRAFT_RUN_DIR", str(base))
    yield RunDirs(base).ensure()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(
    params=[
        pytest.param("docker", marks=pytest.mark.e2e("docker")),
        pytest.param("podman", marks=pytest.mark.e2e("podman")),
    ]
)
def probed(request, monkeypatch, short_run) -> docker.Runtime:
    """The runtime under test made this machine's through `sandbox.yaml`,
    with alpine and the relay image pulled, and its socket probe answered."""
    cli = request.param
    if cli == "podman" and sys.platform == "darwin":
        # podman on macOS is a client of podman machine, whose connection is
        # under the operator's real HOME; KRAFT_HOME still isolates Kraft.
        monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "sandbox.yaml").write_text(f"cli: {cli}\n")
    relay = docker._forward._sandbox_host(os.environ).relay_image
    _pulled(cli, IMAGE)
    _pulled(cli, relay)
    monkeypatch.setattr(docker, "_RUNTIME", docker.detect_runtime())
    if docker.socket_channel(relay) is None:
        _unavailable(cli, f"e2e: {docker.runtime().describe()} could not be probed")
    return docker.runtime()


@pytest.fixture(params=["probed", "forced-tls"])
def runtime(request, probed, monkeypatch) -> docker.Runtime:
    """`probed`, over its own transport or forced onto the two-hop one."""
    if request.param == "probed":
        return probed
    if probed.socket_channel is False:
        pytest.skip("the probe already takes the two-hop transport here")
    # The seam, in the test only: a refused socket, and the runtime saying
    # it runs in a VM, which a Linux runner never does.
    monkeypatch.setattr(docker, "_RUNTIME", replace(probed, socket_channel=False))
    monkeypatch.setattr(docker, "_vm_evidence", lambda host: "forced by the test")
    if not probed.podman:
        # Podman adds host.containers.internal, the host's address, itself.
        real = docker.relay_b_argv

        def relay_b_argv(*args):
            argv = real(*args)
            return [*argv[:2], "--add-host=host.docker.internal:host-gateway", *argv[2:]]

        monkeypatch.setattr(docker, "relay_b_argv", relay_b_argv)
    return docker.runtime()


def _gateway(cli: str) -> str:
    """The default network's gateway: the host, as a container on it sees it."""
    net, field = ("podman", ".Subnets") if cli == "podman" else ("bridge", ".IPAM.Config")
    fmt = f"{{{{range {field}}}}}{{{{.Gateway}}}}{{{{end}}}}"
    done = subprocess.run([cli, "network", "inspect", net, "--format", fmt], capture_output=True)
    return done.stdout.decode().strip() or "172.17.0.1"


@pytest.fixture
async def launch(probed, runtime, database, short_run, tmp_path, monkeypatch):
    """`launch(script, network)`: `script` run by `run_task` in a sandbox
    with `network`, beside a host server listening on every address. Returns
    `(what the script wrote to ./out, the refusal events, the NetworkMode
    of the relay and of relay B while it ran, and the transport)`; a launch
    that never ran fails with its log."""

    async def serve(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\nhello-from-host")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "0.0.0.0", 0)
    port = server.sockets[0].getsockname()[1]

    async def resolve(host, port_, **kw):
        if host == "allowed.test":
            return [(0, 0, 0, "", (ALLOWED_ADDRESS, port_))]
        return await asyncio.get_running_loop().getaddrinfo(host, port_, **kw)

    async def connect(address, port_):
        if address == ALLOWED_ADDRESS:
            return await asyncio.open_connection("127.0.0.1", port)
        return await asyncio.open_connection(address, port_)

    registry = channel.ChannelRegistry(
        short_run, database, EgressProxy(getaddrinfo=resolve, connect=connect)
    )
    channel.install(registry)
    # Forced on a Linux host, whose containers reach the host at the
    # bridge's gateway, never its loopback.
    forced = probed.socket_channel and not runtime.socket_channel
    listener = channel.TLSListener(registry, short_run, host="0.0.0.0" if forced else "127.0.0.1")
    await listener.start()
    modes: list[tuple[str, ...]] = []
    real_close = docker.DockerBackend.close_session

    async def close_session(self, session_id):
        inspect = [runtime.cli, "inspect", "-f", "{{.HostConfig.NetworkMode}}"]
        relays = (docker.relay_name(session_id), docker.relay_b_name(session_id))
        done = [subprocess.run([*inspect, r], capture_output=True) for r in relays]
        modes.append(tuple(d.stdout.decode().strip() for d in done))
        await real_close(self, session_id)

    monkeypatch.setattr(docker.DockerBackend, "close_session", close_session)
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    work = tmp_path / "work"
    work.mkdir()

    async def go(script: str, network: dict):
        status = await sp.run_task(
            database,
            short_run,
            session_id=f"s{os.getpid()}",
            work_item_id="w1",
            node_id="verify",
            hook_point="on.test.run",
            cmd=["sh", "-c", script.format(gateway=_gateway(runtime.cli), port=port)],
            cwd=work,
            sandbox={"kind": "docker", "image": IMAGE, "network": network},
        )
        refused = [
            e["payload"]
            for e in database.read(lambda c: events.read_after(c, 0, "w1"))
            if e["type"] == SANDBOX_EGRESS_REFUSED
        ]
        out = work / "out"
        log = short_run.logs / f"s{os.getpid()}.log"
        assert status != "config_error", log.read_text() if log.exists() else status
        (row,) = database.read(lambda c: c.execute("SELECT egress FROM worker_sessions").fetchall())
        transport = json.loads(row["egress"])["transport"]
        return out.read_text() if out.exists() else "", refused, modes, transport

    try:
        yield go
    finally:
        await listener.close()
        await registry.close_all()
        channel.install(None)
        server.close()


_ISOLATION = """
echo up: $(ip -o link show up | cut -d: -f2 | cut -d@ -f1 | xargs) > out
for target in "169.254.169.254 80" "1.1.1.1 443" "{gateway} {port}"; do
  if nc -w 3 $target </dev/null >/dev/null 2>&1; then echo "reached $target"; \
else echo "blocked $target"; fi >> out
done
"""


async def test_a_worker_under_network_has_loopback_and_no_route_out(launch):
    """Only `lo`; the metadata address, a public address and the host's
    gateway unreachable directly; the relay itself on no network, and relay
    B, only under the TLS transport, on the default one."""
    out, _, modes, transport = await launch(_ISOLATION, {"runtime": {"allow": ["**"]}})

    lines = out.splitlines()
    assert lines[0] == "up: lo", out
    assert [line.split()[0] for line in lines[1:]] == ["blocked"] * 3, out
    ((relay, relay_b),) = modes
    assert relay == "none"
    assert relay_b not in ("", "none") if transport == "tls" else relay_b == ""


_THROUGH_PROXY = """
wget -q -T 10 -O - http://allowed.test/ > out 2>&1; echo " rc=$?" >> out
for i in 1 2; do wget -q -T 10 -O - http://denied.test/ >> out 2>&1; echo " rc=$?" >> out; done
"""


async def test_a_worker_under_network_reaches_only_its_allowed_hosts(launch):
    """The allowed host through the proxy Kraft set in its environment; a
    host off the list answered 403, refused once however often it is asked."""
    out, refused, *_ = await launch(_THROUGH_PROXY, {"runtime": {"allow": ["allowed.test"]}})

    assert out.startswith("hello-from-host rc=0\n"), out
    assert "403" in out and out.count("rc=1") == 2, out
    assert [(r["host"], r["phase"]) for r in refused] == [("denied.test", "runtime")]
