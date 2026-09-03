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
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from support.harness import fake_templates_dir, isolated_bd, make_repo  # noqa: E402

PORT = os.environ.get("KRAFT_PORT", "8765")


def main() -> int:
    dist = REPO / "frontend" / "dist"
    if not (dist / "index.html").exists():
        sys.exit(f"missing {dist}/index.html — run `cd frontend && npm run build` first")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kraft-e2e-"))
    repo = make_repo(tmp)
    tracker = isolated_bd(tmp)
    templates = fake_templates_dir(
        tmp, f"{sys.executable} {REPO / 'tests' / 'support' / 'fake_agent.py'}"
    )

    env = {
        **os.environ,
        "KRAFT_PORT": PORT,
        "KRAFT_RUN_DIR": str(tmp / "run"),
        "KRAFT_TEMPLATES_DIR": str(templates),
        "KRAFT_BD_CWD": str(tracker),
        "KRAFT_FRONTEND_DIST": str(dist),
        "KRAFT_FAKE_AGENT": "fix",
    }
    proc = subprocess.Popen([sys.executable, "-m", "kraft"], cwd=REPO, env=env)

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
        proc.kill()
        sys.exit("server did not become healthy in 30s")

    print(f"\n  server up on http://127.0.0.1:{PORT}  (temp: {tmp})")
    print(f"  KRAFT_E2E_REPO={repo}\n", flush=True)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
