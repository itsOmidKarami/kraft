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
