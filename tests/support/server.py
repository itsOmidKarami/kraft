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


@contextlib.contextmanager
def running_server(*, run_dir: Path, templates_dir: Path, bd_cwd: Path, env: dict | None = None):
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
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early rc={proc.returncode}")
            with contextlib.suppress(httpx.TransportError):
                if client.get("/health").status_code == 200:
                    break
            time.sleep(0.2)
        else:
            raise RuntimeError("server did not become healthy in 15s")
        yield Server(proc, base, port, client)
    finally:
        client.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
