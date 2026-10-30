"""`kraft admin start` on an address something else holds: the probe that
refuses one already answering, and a port lost after that probe."""

from __future__ import annotations

import logging
import socket
import subprocess
import sys

import pytest
from support.harness import fake_templates_dir, v1_node
from support.server import child_env

from kraft import cli


@pytest.mark.parametrize(
    "squatter_host",
    # the wildcard is the exact Kraft-kquf shape: an existing daemon bound
    # *:PORT, and we are about to bind the specific loopback address next to it
    ["127.0.0.1", "0.0.0.0"],
    ids=["same-address", "wildcard-probed-from-loopback"],
)
def test_serve_refuses_when_the_address_already_answers(tmp_path, monkeypatch, squatter_host):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(fake_templates_dir(tmp_path, "true")))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind((squatter_host, 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        monkeypatch.setenv("KRAFT_PORT", str(port))
        with pytest.raises(SystemExit, match="already answering"):
            cli.admin._serve()


@pytest.mark.slow
async def test_a_start_that_loses_its_port_leaves_running_sessions_alone(
    item_on, run_dirs, tmp_path
):
    """Bound but not listening, the squatter passes the address probe and
    takes the port from uvicorn's bind. The start exits on it before its
    startup marks a live session unknown and stops its item."""
    it = await item_on([v1_node()], status="active")
    await it.session("s1", "build.main.build")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        proc = subprocess.run(
            [sys.executable, "-m", "kraft", "admin", "start"],
            env=child_env(
                {
                    "KRAFT_RUN_DIR": str(run_dirs.base),
                    "KRAFT_CONFIG_DIR": str(fake_templates_dir(tmp_path, "true")),
                    "KRAFT_HOST": "127.0.0.1",
                    "KRAFT_PORT": str(squatter.getsockname()[1]),
                }
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "address already in use" in proc.stderr, "what the troubleshooting page says"
    assert [s["status"] for s in it.sessions()] == ["pending"]
    assert it.status() == "active"


def test_serve_probes_127_0_0_1_when_its_own_bind_is_a_wildcard(tmp_path, monkeypatch):
    """The other direction: we are about to bind wildcard ourselves, and
    something already sits on loopback -- connecting to 0.0.0.0 itself is
    not a reliable client target, so the probe has to translate."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    templates_dir = fake_templates_dir(tmp_path, "true")
    # A wildcard bind is refused outright without a password (_bind, unrelated
    # to this test) -- set one so the address check is what actually runs.
    (templates_dir / "access.yaml").write_text("password_hash: x\n")
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(templates_dir))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        monkeypatch.setenv("KRAFT_HOST", "0.0.0.0")
        monkeypatch.setenv("KRAFT_PORT", str(port))
        with pytest.raises(SystemExit, match="already answering"):
            cli.admin._serve()


def test_serve_proceeds_when_the_address_is_free(tmp_path, monkeypatch):
    """A free ephemeral port (nothing bound it) must not be refused."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(fake_templates_dir(tmp_path, "true")))
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # freed before _serve ever runs
    monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
    monkeypatch.setenv("KRAFT_PORT", str(port))
    started = []
    monkeypatch.setattr(
        cli.admin._SignalLoggingServer,
        "run",
        lambda self, *a, **k: started.append(True),
    )
    cli.admin._serve()
    assert started == [True]


def _has_ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as s:
            s.bind(("::1", 0))
    except OSError:
        return False
    return any(i[0] == socket.AF_INET6 for i in socket.getaddrinfo("localhost", 0))


_IPV6 = pytest.mark.skipif(not _has_ipv6_loopback(), reason="no IPv6 loopback for localhost")


def _infos(*addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", a) for a in addresses]


#: Any 127/8 address binds on Linux: a name's second address on loopback.
_LINUX = pytest.mark.skipif(sys.platform != "linux", reason="127.0.0.2 binds on Linux")
#: An address no interface here has (TEST-NET-1): its bind is EADDRNOTAVAIL.
_ELSEWHERE = ("192.0.2.1", 0)
#: The port a listening socket holds on 127.0.0.2 when the row runs.
_HELD = -1


@pytest.mark.parametrize(
    ("host", "found", "want"),
    [
        pytest.param("127.0.0.1", None, {("127.0.0.1", False)}, id="one-address"),
        pytest.param(
            "two-addresses",
            _infos(("127.0.0.1", 0), ("127.0.0.2", 0)),
            {("127.0.0.1", False), ("127.0.0.2", False)},
            id="a-name-with-two-addresses",
            marks=_LINUX,
        ),
        pytest.param(
            "localhost", None, {("127.0.0.1", False), ("::1", True)}, id="localhost", marks=_IPV6
        ),
        pytest.param("::", None, {("::", True)}, id="ipv6-wildcard-is-ipv6-only", marks=_IPV6),
        pytest.param(
            "moved",
            _infos(("127.0.0.1", 0), _ELSEWHERE),
            {("127.0.0.1", False)},
            id="an-address-not-on-this-machine-is-skipped",
        ),
        # Never started with nothing to serve: a `bind:` address DHCP has moved.
        pytest.param(
            "moved", _infos(_ELSEWHERE), "could not bind on any address", id="no-address-binds"
        ),
        pytest.param(
            "taken",
            _infos(("127.0.0.1", _HELD), ("127.0.0.2", _HELD)),
            "address already in use",
            id="a-taken-address-closes-the-ones-bound",
            marks=_LINUX,
        ),
    ],
)
def test_a_start_listens_on_every_address_its_host_names(monkeypatch, host, found, want):
    """As asyncio's `create_server` binds, which uvicorn's own start used: one
    socket per address the host resolves to, an IPv6 one never taking IPv4
    too, an address this machine lacks skipped, each listening before the
    lifespan runs. One that cannot bind, or none at all, exits 3 with every
    socket it bound closed."""
    with socket.socket() as squatter:
        bound_first = None
        if found is not None and found[0][4][1] == _HELD:
            squatter.bind(("127.0.0.2", 0))
            squatter.listen(1)
            port = squatter.getsockname()[1]
            found = _infos(*((address, port) for address, _ in (i[4] for i in found)))
            bound_first = found[0][4]
        if found is not None:
            monkeypatch.setattr(cli.admin.socket, "getaddrinfo", lambda *a, **k: found)
        if isinstance(want, str):
            logged = []
            monkeypatch.setattr(logging.getLogger("uvicorn.error"), "error", logged.append)
            with pytest.raises(SystemExit) as stopped:
                cli.admin._listen(host, 0, backlog=8)
            assert stopped.value.code == 3
            assert want in str(logged[0])
            if bound_first is not None:
                with socket.socket() as again:  # what it bound first was closed
                    again.bind(bound_first)
            return
        sockets = cli.admin._listen(host, 0, backlog=8)
    try:
        got = set()
        for s in sockets:
            assert s.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
            assert not s.get_inheritable()
            v6only = s.family == socket.AF_INET6 and bool(
                s.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
            )
            got.add((s.getsockname()[0], v6only))
        assert got == want
    finally:
        for s in sockets:
            s.close()
