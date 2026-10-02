from __future__ import annotations

import atexit
import contextlib
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from support import harness, real_binaries

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
    `BD_STUB_MESSAGE` and exits 127. Built once per process. Each call's argv
    goes to `KRAFT_BD_STUB_LOG` when the child has it (`running_server`
    reports those), and to the real-binary guard's log otherwise, which fails
    the test at teardown (`support.real_binaries`)."""
    global _bd_stub
    if _bd_stub is None:
        d = Path(tempfile.mkdtemp(prefix="kraft-bd-stub-", dir=harness.PROCESS_TMP))
        guard_log = shlex.quote(str(real_binaries.log_path()))
        (d / "bd").write_text(
            "#!/bin/sh\n"
            'if [ -n "$KRAFT_BD_STUB_LOG" ]; then echo "$*" >> "$KRAFT_BD_STUB_LOG"; '
            f'else printf \'bd\\t%s\\t%s\\n\' "$PWD" "$*" >> {guard_log}; fi\n'
            f"echo {BD_STUB_MESSAGE!r} >&2\n"
            "exit 127\n"
        )
        (d / "bd").chmod(0o755)
        atexit.register(shutil.rmtree, d, ignore_errors=True)
        _bd_stub = d
    return _bd_stub


def _holds_bd(directory: str) -> bool:
    candidate = Path(directory) / "bd"
    return candidate.is_file() and os.access(candidate, os.X_OK)


def child_env(env: dict | None = None, *, bd: bool = True) -> dict:
    """The environment a `python -m kraft` child starts with: this process's
    own, with `env` on top. Every test that spawns Kraft goes through here
    (tests/test_suite_config.py checks).

    A monkeypatch in this process cannot reach a child, so the child's `PATH`
    carries the guards instead. The real-binary guard's stubs go first
    (`support.real_binaries`), even over a `PATH` that `env` sets, so the child
    resolves `claude`, `gh` and the rest to a stub that refuses and fails the
    test. The in-process beads fake cannot reach it either, so a unit test's
    child finds the loud stub `bd` next (Kraft-vrcw3). `harness.REAL_BD` says
    which tier this is -- the conftest turns it off for every test the fake
    covers, and leaves it on for `e2e("bd")` tests, whose child gets the real
    bd.

    `bd=False` is a child with no bd at all: no `KRAFT_BD_CWD`, no stub, and no
    `PATH` directory holding a `bd`, so intake takes the "bd is not installed"
    path every machine without bd takes instead of making a call to refuse."""
    child = {**os.environ, **(env or {})}
    path = child.get("PATH", "")
    if not bd:
        child.pop("KRAFT_BD_CWD", None)
        path = os.pathsep.join(p for p in path.split(os.pathsep) if p and not _holds_bd(p))
    elif not harness.REAL_BD:
        path = os.pathsep.join([str(_bd_stub_dir()), path])
    child["PATH"] = real_binaries.stubbed_path(path)
    return child


def output_of(proc: subprocess.Popen) -> str:
    """Everything `proc` printed to its captured stdout, once it is dead. A
    live child never sends EOF on its pipe, so reading it first would block
    for as long as the child runs; `kill` is a no-op on one already gone."""
    proc.kill()
    out, _ = proc.communicate()
    return out if isinstance(out, str) else (out or b"").decode(errors="replace")


def _try_start(run_dir: Path, templates_dir: Path, bd_cwd: Path | None, env: dict | None):
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
                **({"KRAFT_BD_CWD": str(bd_cwd)} if bd_cwd is not None else {}),
                "KRAFT_PORT": str(port),
                **(env or {}),
            },
            bd=bd_cwd is not None,
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


class BdStubRefused(AssertionError):
    """A server child ran `bd` and the stub refused it (Kraft-vrcw3). Kraft
    degrades on that, so the test would pass on a call the unit tier must
    never make; `running_server` raises this instead, with every call."""


def report_stub_calls(calls: list[str]) -> None:
    """Raise `BdStubRefused` naming `calls`, the argv lines the stub logged."""
    if calls:
        raise BdStubRefused(
            f"the server child ran bd {len(calls)}x, refused by the stub: {calls}. Start "
            f"it with no bd workspace (`running_server(bd_cwd=None)`) unless the test is "
            f"about beads, which makes it e2e('bd')."
        )


@contextlib.contextmanager
def running_server(
    *, run_dir: Path, templates_dir: Path, bd_cwd: Path | None = None, env: dict | None = None
):
    """A live `python -m kraft` on a free port, killed on exit. `bd_cwd` is its
    `KRAFT_BD_CWD`; None, the default, starts it with no bd at all
    (`child_env(bd=False)`). A bd call the stub refused fails the test as
    `BdStubRefused` once the block exits."""
    stub_log = Path(tempfile.mkdtemp(prefix="kraft-bd-stub-log-")) / "calls.log"
    env = {"KRAFT_BD_STUB_LOG": str(stub_log), **(env or {})}
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
        stub_calls = stub_log.read_text().splitlines() if stub_log.is_file() else []
        shutil.rmtree(stub_log.parent, ignore_errors=True)
    # Past `finally`, so only a block that exited cleanly gets here: a test
    # already failing keeps its own error.
    report_stub_calls(stub_calls)
