#!/usr/bin/env python
"""Take the docs picture of `kraft console` (`just console-screenshot`).

Runs the console headless against the dev instance (start `just api`, then
`just dev-seed --rich`), puts the cursor on SELECT so the peek shows its spec,
saves Textual's SVG of the screen and renders it to a PNG with the frontend's
Playwright (frontend/e2e/docs-shots/svg-to-png.mjs). The fake agent's spec says
`fake spec body`; the first run writes SPEC over it, in the dev instance's
`run/items/<id>/docs/spec.md`, as the web UI's retake recipe does by hand.

  python dev/console_screenshot.py [--out PATH] [--version X.Y.Z] [--size COLSxROWS]

--out      default .github/assets/console.png
--version  the release the top line names; default the newest stable tag, so a
           dev build's `0.1.devN` and its update notice stay out of the picture
--size     the terminal, default 120x34
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path

from textual.widgets import OptionList

from kraft import client, update
from kraft.console import live, moments
from kraft.console.app import KraftConsole
from kraft.console.screens.board import Board

ROOT = Path(__file__).resolve().parents[1]
#: The item the peek opens on: a gate with a spec to show, in `dev-seed --rich`.
SELECT = "Design the caching layer"
#: Short enough for the peek to show it whole.
SPEC = """
Cache the pricing path's results in process for 60 seconds,
and evict the oldest entry once the cache is full.
"""


def give_it_a_spec(work_item_id: str) -> None:
    """SPEC in place of the fake agent's body, keeping its front matter."""
    home = Path(os.environ.get("KRAFT_HOME") or ROOT / ".dev")
    spec = home / "run" / "items" / work_item_id / "docs" / "spec.md"
    text = spec.read_text()
    if "fake spec body" in text:
        body = text.split("\n---\n", 1)[0] + "\n---\n\n" + SPEC.strip() + "\n"
        spec.write_text(body.replace("title: fake spec", "title: Caching layer"))


def newest_release() -> str:
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    stable = [t[1:] for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not stable:
        raise SystemExit("no release tag here: pass --version (a shallow clone has none)")
    return max(stable, key=lambda v: tuple(map(int, v.split("."))))


async def shoot(svg: Path, size: tuple[int, int]) -> None:
    app = KraftConsole()
    async with app.run_test(size=size) as pilot:
        async with asyncio.timeout(15):
            while app.link is live.Link.RECONNECTING or not app.rows:
                await pilot.pause(0.05)
        row = next((r for r in app.rows if r["title"] == SELECT), None)
        if row is None:
            raise SystemExit(f"no item titled {SELECT!r}: seed with `just dev-seed --rich`")
        give_it_a_spec(row["id"])
        board = app.screen.query_one(Board)
        board.select(row["id"])
        # The peek reads the item once the cursor has stopped; give it time to draw.
        await pilot.pause(2.0)
        # Selecting scrolls the row to the top; from the top, the group's heading shows too.
        board.query_one("#rows", OptionList).scroll_home(animate=False)
        await pilot.pause(0.2)
        app.save_screenshot(filename=svg.name, path=str(svg.parent))
        app.workers.cancel_all()
        await pilot.pause()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / ".github/assets/console.png")
    ap.add_argument("--version")
    ap.add_argument("--size", default="120x34")
    args = ap.parse_args()
    version = args.version or newest_release()
    size = tuple(int(n) for n in args.size.split("x"))

    async def current() -> dict:
        return {"behind": False}

    # The picture names a release, not this checkout's build, and has no update notice,
    # nor the line saying what changed since the console last closed.
    update.installed = lambda: version
    client.update_available = current
    moments.read_seen = lambda: None

    with tempfile.TemporaryDirectory() as tmp:
        svg = Path(tmp) / "console.svg"
        asyncio.run(shoot(svg, size))
        subprocess.run(
            ["node", "e2e/docs-shots/svg-to-png.mjs", str(svg), str(args.out.resolve())],
            cwd=ROOT / "frontend",
            check=True,
        )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
