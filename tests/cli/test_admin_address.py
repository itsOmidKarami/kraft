"""`kraft admin start` on an address something else holds: the probe that
refuses one already answering, and a port lost after that probe."""

from __future__ import annotations

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
