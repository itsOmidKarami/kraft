#!/usr/bin/env python
"""Fill a running dev instance with work items in every interesting state.

Runs against the HTTP API rather than writing rows: the data is then whatever
the real executor produces, so it cannot drift from the schema or from the
states the code can actually reach. Expects `just dev` to be running, which
puts the fake agent (fixtures/fake-claude.sh) ahead of the real one on PATH.
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
MCP_JSON = '{"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}}\n'

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
        # A dev repo built before the seed committed its own registration.
        if not (REPO / ".mcp.json").exists():
            (REPO / ".mcp.json").write_text(MCP_JSON)
            git("add", ".mcp.json")
            git("commit", "-qm", "register kraft for the fake agent")
        say(f"reusing {REPO}")
        return
    REPO.mkdir(parents=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "dev@kraft.local")
    git("config", "user.name", "Kraft Dev")
    (REPO / "calc.py").write_text(CALC)
    (REPO / "test_calc.py").write_text(TEST)
    # A Claude launch is refused unless something registers Kraft's MCP server
    # (`registration.permission_tool`). Committed here, the seed repo carries its
    # own registration, so a dev instance works on a machine that never ran
    # `kraft admin init` or installed the plugin. The fake agent never calls it.
    (REPO / ".mcp.json").write_text(MCP_JSON)
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
    say(f"built {REPO}")


def say(msg: str) -> None:
    """Flushed, so progress shows up even when stdout is a pipe (`| tee`)."""
    print(f"seed: {msg}", flush=True)


def snapshot(client: httpx.Client, wid: str) -> tuple[str, str]:
    """(state, where): the end state in the vocabulary ITEMS uses, and where
    the item is right now -- its current node and whether an agent is live.

    A pending gate and a dead agent are both `needs_human` on the row; only the
    event stream tells them apart.
    """
    item = client.get(f"/work-items/{wid}").raise_for_status().json()
    node = item.get("current_node_id") or "-"
    live = any(s["status"] == "running" for s in item.get("worker_sessions") or [])
    where = f"node {node}" + (", agent running" if live else "")
    status = item["status"]
    if status != "needs_human":
        return status, where
    events = client.get(f"/work-items/{wid}/events").raise_for_status().json()
    return ("gate" if any(e["type"] == "gate_requested" for e in events) else "failed"), where


def state(client: httpx.Client, wid: str) -> str:
    return snapshot(client, wid)[0]


#: How often `settle` says it is still waiting when nothing has changed.
HEARTBEAT = 10.0


def settle(client: httpx.Client, wid: str, want: str, timeout: float = 90.0) -> str:
    """Poll until the item reaches `want`, saying so on every change of state
    or node, and every HEARTBEAT seconds while nothing moves -- a seed waiting
    on a slow item must not look the same as a hung one."""
    start = time.monotonic()
    deadline = start + timeout
    seen, last, beat = "active", None, start
    while time.monotonic() < deadline:
        seen, where = snapshot(client, wid)
        now = time.monotonic()
        if (seen, where) != last:
            say(f"{wid[:8]} {seen} ({where})")
            last, beat = (seen, where), now
        if seen == want:
            return seen
        if now - beat >= HEARTBEAT:
            say(
                f"{wid[:8]} still {seen} ({where}), want {want}"
                f" -- {now - start:.0f}s of {timeout:.0f}s"
            )
            beat = now
        time.sleep(0.5)
    say(f"{wid[:8]} gave up after {timeout:.0f}s: still {seen}, wanted {want}")
    return seen


def pause_mid_flight(client: httpx.Client, wid: str, timeout: float = 30.0) -> str:
    """Pause only applies to a running item, so wait for a live agent first.

    The item's title carries KRAFT_SLOW, which keeps its fake agent asleep long
    enough for this to be a wait rather than a race.
    """
    deadline = time.monotonic() + timeout
    item: dict = {"worker_sessions": []}
    while time.monotonic() < deadline:
        item = client.get(f"/work-items/{wid}").raise_for_status().json()
        if any(s["status"] == "running" for s in item["worker_sessions"]):
            say(f"{wid[:8]} agent running, pausing it")
            client.post(f"/work-items/{wid}/pause").raise_for_status()
            return settle(client, wid, "paused", timeout=15)
        time.sleep(0.3)
    say(f"{wid[:8]} no agent started within {timeout:.0f}s")
    # Filed paused at capacity, it never ran: its row says "paused" too, but it
    # is a not-started item, not the mid-flight pause this was asked for.
    return state(client, wid) if item["worker_sessions"] else "never started"


def settle_order(created: list) -> list:
    """Paused items first: one can only be paused while its KRAFT_SLOW agent is
    still asleep, and `pause_mid_flight` waits on that running session itself.
    Settled after the others, it waited behind their settle timeouts instead.
    `main` files them in this order too: filed last, a paused item lost the
    capacity race (`max_concurrent`) and was filed paused without ever running."""
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

    say(f"connecting {REPO} to {BASE}")
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
            say("the dev repo predates forge: fake -- run `just dev-reset` first")

    created = []
    rows, ok = [], True
    # A paused item is filed and paused before the rest are filed, so its slot
    # is free again by the time they take theirs.
    for title, template, want in settle_order(ITEMS):
        wid = (
            client.post(
                "/work-items",
                json={
                    "title": title,
                    "repo": str(REPO),
                    "chain_template": template,
                    "autostart": True,
                },
            )
            .raise_for_status()
            .json()["id"]
        )
        say(f"{wid[:8]} filed {title!r} ({template}), want {want}")
        if want == "paused":
            got = pause_mid_flight(client, wid)
            ok &= got == want
            rows.append((wid[:8], title, want, got))
        else:
            created.append((wid, title, want))

    for n, (wid, title, want) in enumerate(created, 1):
        say(f"[{n}/{len(created)}] waiting for {wid[:8]} to reach {want}")
        got = settle(client, wid, want)
        ok &= got == want
        rows.append((wid[:8], title, want, got))

    width = max(len(r[1]) for r in rows)
    for wid, title, want, got in rows:
        mark = "ok " if want == got else "BAD"
        print(f"{mark} {wid}  {title:<{width}}  want={want:<9} got={got}")
    say(BASE)
    # A seed that quietly produces four identical rows is worse than no seed.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
