from __future__ import annotations

import asyncio
import json
import subprocess


async def intake(title: str, *, cwd: str | None = None) -> str:
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "bd",
            "create",
            "--json",
            "--title",
            title,
            "-d",
            "Created by the Kraft orchestrator.",
            "--type",
            "task",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    out = proc.stdout
    # --json prints the issue object to stdout; slice from the first "{" in case
    # bd prepends an advisory line.
    if "{" not in out:
        raise RuntimeError(f"bd create emitted no JSON: {out!r} stderr={proc.stderr!r}")
    return json.loads(out[out.index("{") :])["id"]


async def complete(bead_id: str, *, cwd: str | None = None) -> None:
    await asyncio.to_thread(
        subprocess.run,
        ["bd", "close", bead_id],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


async def search(q: str, *, cwd: str | None = None, limit: int = 5) -> list[dict]:
    """Live bead search — a thin `bd search --json` passthrough (06 §6.1).

    Live, not indexed: the search overlay's own results are a lagging shadow of
    the repo, and this footer strip is the one line in it that is not.

    `--status all` because a completed work item closes its bead: without it the
    finished work is exactly what the strip cannot find (Kraft-evm). Callers get
    the status per row and can say so.

    Best-effort by contract: no `bd` on PATH is a missing footer strip, not a
    500 on the search route (Kraft-9m4).
    """
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["bd", "search", q, "--json", "--status", "all"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError, ValueError:
        return []
    if proc.returncode != 0 or "[" not in proc.stdout:
        return []
    try:
        rows = json.loads(proc.stdout[proc.stdout.index("[") :])
    except json.JSONDecodeError:
        return []
    return [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "status": r.get("status"),
            "issue_type": r.get("issue_type"),
        }
        for r in rows[:limit]
        if isinstance(r, dict)
    ]


async def ready(*, cwd: str | None = None) -> list[dict]:
    """Beads with no unmet blockers — a thin `bd ready --json` passthrough.

    Best-effort: a repo with no beads workspace, a `bd` that is not installed,
    a non-zero exit, or output that is not JSON all answer `[]`. The only
    caller is a poller, and a poller that raises kills its own task and
    silently stops picking work up.
    """
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["bd", "ready", "--json"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError, ValueError:
        # OSError: bd is not installed / cwd does not exist. ValueError: text=True
        # decodes stdout as UTF-8, which raises UnicodeDecodeError (a ValueError
        # subclass) on a bd that emits invalid bytes.
        return []
    if proc.returncode != 0 or "[" not in proc.stdout:
        return []
    try:
        rows = json.loads(proc.stdout[proc.stdout.index("[") :])
    except json.JSONDecodeError:
        return []
    return [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "priority": r.get("priority"),
            "issue_type": r.get("issue_type"),
        }
        for r in rows
        if isinstance(r, dict) and r.get("id") and r.get("title")
    ]
