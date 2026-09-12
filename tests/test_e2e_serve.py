"""Guards on the e2e fixture server's port ownership (Kraft-dnv).

serve.py polls /health until something answers. When a stale orchestrator
already holds the port, that something is the squatter: the spawned child loses
the bind and dies, but the poll has already succeeded, so serve.py reports
success and every spec runs against the wrong database, templates and index.
Observed live — the squatter served `invalid_templates: {pricing}` and no
embeddings while the fixture instance it displaced had vector search working.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_serve():
    """serve.py is a script, not a package module."""
    path = REPO / "frontend" / "e2e" / "serve.py"
    spec = importlib.util.spec_from_file_location("e2e_serve", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_serve"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def serve():
    return _load_serve()


@pytest.fixture
def taken_port():
    """A real listening socket — the failure is about actual port ownership, so
    a mock would not reproduce it."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_a_free_port_is_accepted(serve):
    serve.ensure_port_free(_free_port())


def test_an_occupied_port_is_refused(serve, taken_port):
    with pytest.raises(SystemExit) as exc:
        serve.ensure_port_free(taken_port)
    assert str(taken_port) in str(exc.value)


def test_the_refusal_says_how_to_find_the_squatter(serve, taken_port):
    """A bare 'address in use' sends someone hunting. Name the PID and the
    command, because the whole point is that the process is a leftover nobody
    remembers starting."""
    with pytest.raises(SystemExit) as exc:
        serve.ensure_port_free(taken_port)
    msg = str(exc.value)
    assert "lsof" in msg
    assert str(taken_port) in msg


def test_checking_a_port_twice_does_not_leak_the_probe_socket(serve):
    """The probe must not leave the port bound, or the child it is protecting
    could not start either."""
    port = _free_port()
    serve.ensure_port_free(port)
    serve.ensure_port_free(port)


@pytest.fixture
def wildcard_taken_port():
    """A listener on every interface (`bind(("", 0))`) — the exact shape a
    `SO_REUSEADDR` bind probe on 127.0.0.1 could not tell from free. The
    Kraft-vhxe case: the daemon holds `*:8765` and the old bind probe passed."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def test_a_wildcard_listener_is_refused(serve, wildcard_taken_port):
    """Regression for Kraft-vhxe: a bind-based probe on 127.0.0.1 succeeds
    right over a `*:PORT` listener; a connect-based probe must not."""
    with pytest.raises(SystemExit) as exc:
        serve.ensure_port_free(wildcard_taken_port)
    assert str(wildcard_taken_port) in str(exc.value)


def test_default_port_is_ephemeral(serve, monkeypatch):
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    port = serve.resolve_port()
    assert 0 < int(port) < 65536


def test_default_path_never_probes(serve, monkeypatch):
    """§3: probing only makes sense against a port a human pinned — the
    default path has nothing to collide with."""
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    calls = []
    monkeypatch.setattr(serve, "ensure_port_free", lambda p: calls.append(p))
    serve.resolve_port()
    assert calls == []


def test_pinned_port_is_still_probed(serve, monkeypatch, taken_port):
    monkeypatch.setenv("KRAFT_PORT", str(taken_port))
    with pytest.raises(SystemExit):
        serve.resolve_port()


def test_inherited_daemon_port_is_not_treated_as_pinned(serve, monkeypatch, taken_port):
    """A worker spawned by a Kraft daemon inherits the daemon's `KRAFT_PORT`
    (adapters/subprocess.py builds worker envs as `{**os.environ, ...}`). If
    `KRAFT_DAEMON_PORT` names that same value, it was inherited, not pinned
    by a human, so resolve_port must not probe it (and must not collide with
    the daemon that is answering on it)."""
    monkeypatch.setenv("KRAFT_PORT", str(taken_port))
    monkeypatch.setenv("KRAFT_DAEMON_PORT", str(taken_port))
    calls = []
    monkeypatch.setattr(serve, "ensure_port_free", lambda p: calls.append(p))
    port = serve.resolve_port()
    assert calls == []
    assert port != str(taken_port)


def test_two_ephemeral_resolutions_do_not_collide_once_the_first_is_bound(serve, monkeypatch):
    """Two `serve.py` instances started back to back must land on different
    ports, not both default to 8765 (Kraft-f8u3, Kraft-m1e8)."""
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    port_a = serve.resolve_port()
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", int(port_a)))
    holder.listen(1)
    try:
        port_b = serve.resolve_port()
        assert port_b != port_a
    finally:
        holder.close()
