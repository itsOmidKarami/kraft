"""Stand up a hermetic Kraft orchestrator for the Playwright e2e.

Run from the repo root:

    uv run python frontend/e2e/serve.py

It builds nothing (run `cd frontend && npm run build` first), boots
`python -m kraft` against throwaway fixtures, waits for /api/health, then prints:

    KRAFT_E2E_REPO=<path>
    KRAFT_E2E_BASE=http://127.0.0.1:<port>

Copy those into the playwright command:

    cd frontend && KRAFT_E2E_REPO=<path> KRAFT_E2E_BASE=http://127.0.0.1:<port> npx playwright test

Ctrl-C to tear the server down. The temp dir is left behind for inspection.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from support.harness import (  # noqa: E402
    fake_templates_dir,
    isolated_bd,
    make_repo,
    make_repo_with_engineering,
)


def _listeners_on(port: int | str) -> list[str]:
    """PIDs holding the port, best effort — purely for the error message."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.split()
    except OSError, subprocess.SubprocessError:
        return []


def ensure_port_free(port: int | str) -> None:
    """Refuse to start when something already answers on the port.

    Only ever called for a `KRAFT_PORT` a human pinned explicitly —
    `resolve_port`'s default (ephemeral) path has nothing to collide with.

    Connects rather than binds: a `SO_REUSEADDR` bind on 127.0.0.1 succeeds
    even while something else holds `*:PORT` (0.0.0.0), which is exactly the
    live-listener case this exists to catch (Kraft-vhxe). A connect that
    succeeds means someone is there; a connect that fails — refused, because
    nothing is listening — means the port is free.
    """
    probe = socket.socket()
    try:
        probe.settimeout(0.5)
        probe.connect(("127.0.0.1", int(port)))
    except OSError:
        return  # nothing answered: the port is free
    else:
        pids = _listeners_on(port)
        held = f" (held by PID {', '.join(pids)})" if pids else ""
        sys.exit(
            f"port {port} is already in use{held}\n"
            f"  a leftover orchestrator would answer /api/health and impersonate this "
            f"fixture server.\n"
            f"  inspect: lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  or run on another port: KRAFT_PORT=<free port> "
            f"uv run python frontend/e2e/serve.py"
        )
    finally:
        probe.close()


def _ephemeral_port() -> int:
    """Bind 127.0.0.1:0, read the port the kernel picked, release it.

    ponytail: TOCTOU between this close and the child's own bind in `main` —
    another process can take the port in that window. Acceptable: the window
    is milliseconds and a lost race fails loudly at the health poll, where the
    alternative (teaching the daemon to publish its bound port) belongs to the
    daemon-identity work, not here.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def resolve_port() -> str:
    """`KRAFT_PORT` wins if a human pinned one, and only then must it be free.
    Unset picks a fresh ephemeral port every run, so two `serve.py` instances
    never fight over 8765 (Kraft-f8u3, Kraft-m1e8).

    A worker launched by a Kraft daemon inherits that daemon's own `KRAFT_PORT`
    (adapters/subprocess.py builds worker envs as `{**os.environ, ...}`), which
    looks identical to a human's pin. `KRAFT_DAEMON_PORT` is the daemon telling
    its workers which port is its own (cli/admin.py); when the two agree, the
    value is inherited, not pinned, so fall back to an ephemeral port instead
    of colliding with the very daemon that spawned this worker."""
    pinned = os.environ.get("KRAFT_PORT")
    if pinned and pinned != os.environ.get("KRAFT_DAEMON_PORT"):
        ensure_port_free(pinned)
        return pinned
    return str(_ephemeral_port())


def _raise_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def _terminate(proc: subprocess.Popen) -> None:
    """Signal the whole process group so detached agent/git children die too."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError, PermissionError:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError, PermissionError:
            proc.kill()
        proc.wait()


def main() -> int:
    dist = REPO / "frontend" / "dist"
    if not (dist / "index.html").exists():
        sys.exit(f"missing {dist}/index.html — run `cd frontend && npm run build` first")

    PORT = resolve_port()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kraft-e2e-"))
    # .engineering/ content so the indexer has something to find: the search
    # overlay spec needs real documents, not just a failing test (Kraft-bj9.6).
    repo = make_repo_with_engineering(
        tmp,
        {
            ".engineering/specs/ws.md": (
                "---\ntitle: WS transport design\nowner: omid\n---\n"
                "reconnect backoff schedule caps at ten seconds\n"
            ),
            ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
        },
    )
    tracker = isolated_bd(tmp)
    # fixtures/fake-claude.sh, not tests/support/fake_agent.py: the planning
    # hooks (on.spec.requested/on.plan.requested) carry an `artifact:` contract
    # (write + commit a document into the worktree), and only fake-claude.sh
    # honours it. Same binary also does fake_agent.py's calc.py `fix` trick, so
    # one command covers on.implementation.start too.
    templates = fake_templates_dir(
        tmp, str(REPO / "fixtures" / "fake-claude.sh"), planning_hooks=True
    )

    env = {
        **os.environ,
        "KRAFT_PORT": PORT,
        "KRAFT_RUN_DIR": str(tmp / "run"),
        "KRAFT_TEMPLATES_DIR": str(templates),
        "KRAFT_BD_CWD": str(tracker),
        "KRAFT_FRONTEND_DIST": str(dist),
        "KRAFT_FAKE_CLAUDE": "fix",
        "KRAFT_INDEX_REPOS": str(repo),
    }
    # Own process group: the orchestrator spawns detached agent/git children,
    # and terminating only the direct child orphans them (Kraft-2ih).
    proc = subprocess.Popen(
        [sys.executable, "-m", "kraft"], cwd=REPO, env=env, start_new_session=True
    )

    health = f"http://127.0.0.1:{PORT}/api/health"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return proc.returncode or 1
        try:
            with urllib.request.urlopen(health, timeout=1) as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.3)
    else:
        _terminate(proc)
        sys.exit("server did not become healthy in 30s")

    # The poll above proves something answered, not that it was ours. If the
    # child is gone by now, whatever replied is not the fixture server.
    if proc.poll() is not None:
        sys.exit(
            f"the orchestrator exited (code {proc.returncode}) but "
            f"http://127.0.0.1:{PORT}/api/health still answers — another server holds "
            f"the port; nothing below would have been the fixture instance"
        )

    # With no repo connected the board is the fresh-install empty state (design
    # 08), where the header's New work item is disabled. Connect a separate repo
    # so specs get the normal board, and KRAFT_E2E_REPO stays unconnected for
    # regression.spec's "connect a repo".
    connected = make_repo(tmp, name="connected")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/repos",
        data=json.dumps({"path": str(connected)}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10).close()

    print(f"\n  server up on http://127.0.0.1:{PORT}  (temp: {tmp})")
    print(f"  KRAFT_E2E_REPO={repo}")
    print(f"  KRAFT_E2E_BASE=http://127.0.0.1:{PORT}\n", flush=True)
    # Ctrl-C sends SIGINT; a CI runner or `kill` sends SIGTERM. Handle both, or
    # the orchestrator and its detached children outlive this script.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        proc.wait()
    except KeyboardInterrupt:
        _terminate(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
