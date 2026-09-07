"""Stand up a hermetic Kraft orchestrator for the Playwright e2e.

Run from the repo root:

    uv run python frontend/e2e/serve.py

It builds nothing (run `cd frontend && npm run build` first), boots
`python -m kraft` against throwaway fixtures, waits for /health, then prints:

    KRAFT_E2E_REPO=<path>

Copy that into the playwright command:

    cd frontend && KRAFT_E2E_REPO=<path> npx playwright test

Ctrl-C to tear the server down. The temp dir is left behind for inspection.
"""

from __future__ import annotations

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
    make_repo_with_engineering,
)

PORT = os.environ.get("KRAFT_PORT", "8765")


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
    """Refuse to start when something already holds the port.

    Without this the health poll below is satisfied by whoever answers, and a
    stale orchestrator from an earlier session answers instantly — before our
    child has even attempted its bind. serve.py then prints "server up" while
    the child dies, and the whole suite runs against the squatter's database,
    templates and index (Kraft-dnv).

    SO_REUSEADDR matches what uvicorn will do, so a socket lingering in
    TIME_WAIT does not cause a spurious refusal. It does not let this bind
    succeed over a live listener, which is the case being detected.
    """
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", int(port)))
    except OSError as exc:
        pids = _listeners_on(port)
        held = f" (held by PID {', '.join(pids)})" if pids else ""
        sys.exit(
            f"port {port} is already in use{held}: {exc}\n"
            f"  a leftover orchestrator would answer /health and impersonate this "
            f"fixture server.\n"
            f"  inspect: lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  or run on another port: KRAFT_PORT=<free port> "
            f"uv run python frontend/e2e/serve.py"
        )
    finally:
        probe.close()


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

    ensure_port_free(PORT)

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

    health = f"http://127.0.0.1:{PORT}/health"
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
            f"http://127.0.0.1:{PORT}/health still answers — another server holds "
            f"the port; nothing below would have been the fixture instance"
        )

    print(f"\n  server up on http://127.0.0.1:{PORT}  (temp: {tmp})")
    print(f"  KRAFT_E2E_REPO={repo}\n", flush=True)
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
