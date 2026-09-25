#!/usr/bin/env python
"""Fill a running dev instance with work items in every interesting state.

Runs against the HTTP API rather than writing rows: the data is then whatever
the real executor produces, so it cannot drift from the schema or from the
states the code can actually reach. Expects `just dev` to be running with the
fake agent on PATH — see docs/superpowers/specs/2026-09-04-packaging-and-dev-execution-design.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

HOME = Path(os.environ.get("KRAFT_HOME") or ".dev").resolve()
REPO = HOME / "repo"
BASE = f"http://127.0.0.1:{os.environ.get('KRAFT_PORT', '8765')}"

CALC = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"

#: title, template, expected end state. The instruction the agent receives is the
#: title, so KRAFT_FAIL / KRAFT_SLOW in a title steer that item's fake agent
#: without touching the ones running beside it.
ITEMS = [
    ("Fix add() so the tests pass", "quick-task", "completed"),
    ("Rewrite the parser KRAFT_FAIL", "quick-task", "failed"),
    ("Design the caching layer", "default", "gate"),
    ("Investigate the flaky import test KRAFT_SLOW", "quick-task", "paused"),
]


def git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True)


def build_repo() -> None:
    """A throwaway repo with the bug fake-claude.sh knows how to fix."""
    if REPO.exists():
        print(f"seed: reusing {REPO}")
        return
    REPO.mkdir(parents=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "dev@kraft.local")
    git("config", "user.name", "Kraft Dev")
    (REPO / "calc.py").write_text(CALC)
    (REPO / "test_calc.py").write_text(TEST)
    git("add", "-A")
    git("commit", "-qm", "initial")
    # Intake calls `bd create` in KRAFT_BD_CWD; without a workspace here it would
    # fall through to whichever beads DB the launch directory belongs to. Without
    # bd at all, intake files no bead and the item runs anyway (executor.entry),
    # so a seed that needs no beads -- the VS Code integration tests in CI, which
    # keeps bd out of every job but e2e-cli -- skips it.
    if shutil.which("bd"):
        subprocess.run(
            ["bd", "init", "--non-interactive", "--prefix", "devseed"],
            cwd=REPO,
            # Pinned: REPO may sit inside a checkout that tracks its own .beads/,
            # whose config bd init would otherwise inherit.
            env={**os.environ, "BEADS_DIR": str(REPO / ".beads")},
            check=True,
            capture_output=True,
        )
    print(f"seed: built {REPO}")


def state(client: httpx.Client, wid: str) -> str:
    """The end state a seeded item is in, in the vocabulary ITEMS uses.

    A pending gate and a dead agent are both `needs_human` on the row; only the
    event stream tells them apart.
    """
    status = client.get(f"/work-items/{wid}").raise_for_status().json()["status"]
    if status != "needs_human":
        return status
    events = client.get(f"/work-items/{wid}/events").raise_for_status().json()
    return "gate" if any(e["type"] == "gate_requested" for e in events) else "failed"


def settle(client: httpx.Client, wid: str, want: str, timeout: float = 90.0) -> str:
    deadline = time.monotonic() + timeout
    seen = "active"
    while time.monotonic() < deadline:
        seen = state(client, wid)
        if seen == want:
            return seen
        time.sleep(0.5)
    return seen


def pause_mid_flight(client: httpx.Client, wid: str, timeout: float = 30.0) -> str:
    """Pause only applies to a running item, so wait for a live agent first.

    The item's title carries KRAFT_SLOW, which keeps its fake agent asleep long
    enough for this to be a wait rather than a race.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = client.get(f"/work-items/{wid}").raise_for_status().json()
        if any(s["status"] == "running" for s in item["worker_sessions"]):
            client.post(f"/work-items/{wid}/pause").raise_for_status()
            return settle(client, wid, "paused", timeout=15)
        time.sleep(0.3)
    return state(client, wid)


def settle_order(created: list) -> list:
    """Paused items first: one can only be paused while its KRAFT_SLOW agent is
    still asleep, and `pause_mid_flight` waits on that running session itself.
    Settled after the others, it waited behind their settle timeouts instead."""
    return sorted(created, key=lambda item: item[2] != "paused")


def main() -> int:
    build_repo()
    # `just dev` builds the repo before the server starts: KRAFT_BD_CWD points at
    # it, and intake shells out to `bd` there on the first work item.
    if "--repo-only" in sys.argv:
        return 0
    # Every JSON route lives under /api/ (Kraft-psuq); baked into base_url so
    # every call below stays a bare "/work-items"-style path.
    client = httpx.Client(base_url=f"{BASE}/api", timeout=30.0)
    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError as exc:
        sys.exit(f"seed: no dev server on {BASE} ({exc}). Start one with `just dev`.")

    # The default chain's verification refuses to guess a test command, and its
    # merge-request half needs a forge: `fake` is the dev-only in-process one
    # (Ruling 147). `uv run` finds this checkout's venv above `.dev/`.
    resp = client.post(
        "/repos",
        json={
            "path": str(REPO),
            "setup_command": "",
            "test_command": "uv run pytest -q",
            "forge": "fake",
        },
    )
    if resp.status_code not in (201, 409):
        resp.raise_for_status()
    if resp.status_code == 409:
        # An older `.dev` connected the repo before it declared a forge and a test
        # command; keeping that entry leaves the default chain unable to verify or
        # open a merge request, and the rows below read BAD with no hint why.
        entry = next(
            (r for r in client.get("/repos").json()["repos"] if r["path"] == str(REPO)), {}
        )
        if entry.get("forge") != "fake" or not entry.get("test_command"):
            print("seed: the dev repo predates forge: fake -- run `just dev-reset` first")

    created = []
    for title, template, want in ITEMS:
        wid = (
            client.post(
                "/work-items", json={"title": title, "repo": str(REPO), "chain_template": template}
            )
            .raise_for_status()
            .json()["id"]
        )
        created.append((wid, title, want))

    rows, ok = [], True
    for wid, title, want in settle_order(created):
        got = pause_mid_flight(client, wid) if want == "paused" else settle(client, wid, want)
        ok &= got == want
        rows.append((wid[:8], title, want, got))

    width = max(len(r[1]) for r in rows)
    for wid, title, want, got in rows:
        mark = "ok " if want == got else "BAD"
        print(f"{mark} {wid}  {title:<{width}}  want={want:<9} got={got}")
    print(f"seed: {BASE}")
    # A seed that quietly produces four identical rows is worse than no seed.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
