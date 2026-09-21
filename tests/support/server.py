from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

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


def _try_start(run_dir: Path, templates_dir: Path, bd_cwd: Path, env: dict | None):
    """One attempt at a live server. Returns a `Server`, or the child's returncode
    if it exited before it ever served.

    `_free_port` closes its socket before uvicorn binds the number, so anything
    else on the machine can take the port in between. That shows up as an
    immediate exit, and a fresh port is all it needs.
    """
    port = _free_port()
    child_env = {
        **os.environ,
        "KRAFT_RUN_DIR": str(run_dir),
        "KRAFT_TEMPLATES_DIR": str(templates_dir),
        "KRAFT_BD_CWD": str(bd_cwd),
        "KRAFT_PORT": str(port),
        **(env or {}),
    }
    proc = subprocess.Popen([sys.executable, "-m", "kraft"], cwd=_REPO_ROOT, env=child_env)
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
