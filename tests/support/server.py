from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from support import harness

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Server:
    proc: subprocess.Popen
    base: str
    port: int
    client: httpx.Client

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


_bd_stub: Path | None = None

#: What the stub says, so a child's log shows why its intake failed.
BD_STUB_MESSAGE = (
    "bd is stubbed out for this server child (tests/support/server.py, Kraft-vrcw3): "
    "a unit test must not spawn the real bd. Mark the test e2e('bd') if it needs one."
)


def _bd_stub_dir() -> Path:
    """A directory holding a `bd` that refuses loudly: it prints
    `BD_STUB_MESSAGE` and exits 127. Built once per process.
    `KRAFT_BD_STUB_LOG`, when set, collects each call's argv."""
    global _bd_stub
    if _bd_stub is None:
        d = Path(tempfile.mkdtemp(prefix="kraft-bd-stub-"))
        (d / "bd").write_text(
            "#!/bin/sh\n"
            'if [ -n "$KRAFT_BD_STUB_LOG" ]; then echo "$*" >> "$KRAFT_BD_STUB_LOG"; fi\n'
            f"echo {BD_STUB_MESSAGE!r} >&2\n"
            "exit 127\n"
        )
        (d / "bd").chmod(0o755)
        atexit.register(shutil.rmtree, d, ignore_errors=True)
        _bd_stub = d
    return _bd_stub


def child_env(env: dict | None = None) -> dict:
    """The environment a `python -m kraft` child starts with: this process's
    own, with `env` on top. Every test that spawns Kraft goes through here
    (tests/test_suite_config.py checks).

    The in-process beads fake (tests/conftest.py) cannot reach a child
    process, so a unit test's child finds the loud stub `bd` first on PATH
    instead of the real one (Kraft-vrcw3): a failed `bd` is what Kraft
    already degrades on. `harness.REAL_BD` says which tier this is -- the
    conftest turns it off for every test the fake covers, and leaves it on
    for `e2e("bd")` tests, whose child gets the real bd."""
    child = {**os.environ, **(env or {})}
    if not harness.REAL_BD:
        child["PATH"] = os.pathsep.join([str(_bd_stub_dir()), child.get("PATH", "")])
    return child


def _try_start(run_dir: Path, templates_dir: Path, bd_cwd: Path, env: dict | None):
    """One attempt at a live server. Returns a `Server`, or the child's returncode
    if it exited before it ever served.

    `_free_port` closes its socket before uvicorn binds the number, so anything
    else on the machine can take the port in between. That shows up as an
    immediate exit, and a fresh port is all it needs.
    """
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "kraft"],
        cwd=_REPO_ROOT,
        env=child_env(
            {
                "KRAFT_RUN_DIR": str(run_dir),
                "KRAFT_TEMPLATES_DIR": str(templates_dir),
                "KRAFT_BD_CWD": str(bd_cwd),
                "KRAFT_PORT": str(port),
                **(env or {}),
            }
        ),
    )
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base, timeout=10.0)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            client.close()
            return proc.returncode
        with contextlib.suppress(httpx.TransportError):
            if client.get("/api/health").status_code == 200:
                return Server(proc, base, port, client)
        time.sleep(0.02)
    client.close()
    proc.kill()
    proc.wait()
    raise RuntimeError("server did not become healthy in 15s")


@contextlib.contextmanager
def running_server(*, run_dir: Path, templates_dir: Path, bd_cwd: Path, env: dict | None = None):
    codes = []
    for _ in range(3):
        result = _try_start(run_dir, templates_dir, bd_cwd, env)
        if isinstance(result, Server):
            srv = result
            break
        codes.append(result)
    else:
        raise RuntimeError(f"server exited early on every attempt, rc={codes}")
    try:
        yield srv
    finally:
        srv.client.close()
        if srv.proc.poll() is None:
            srv.proc.kill()
            srv.proc.wait()
