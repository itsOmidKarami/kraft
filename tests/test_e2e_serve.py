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
