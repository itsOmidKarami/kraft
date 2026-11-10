#!/usr/bin/env python
"""Stage a fresh dev instance for the README's loop, then record it
(`just readme-gif`, after `just dev-reset` and `just dev`).

The board gets a small library, `calc`, with neighbours that fit it. Its fake
agent takes a couple of seconds a step, so the chain graph shows durations a
reader believes; dev/readme_gif.mjs fast-forwards them and says so on screen.
Arguments go to the recorder: --out DIR.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import seed
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = seed.HOME / "calc"
#: title, chain, keyword for the fake agent (in the description, off the board), end state
NEIGHBOURS = [
    ("Pin CI to Python 3.12 and 3.13", "quick-task", "", "completed"),
    ("Document how round() treats halves", "quick-task", "", "completed"),
    ("Add divide() that rejects a zero divisor", "quick-task", "KRAFT_HANG", "running"),
]


def main() -> int:
    seed.build_repo(REPO)
    client = httpx.Client(base_url=f"{seed.BASE}/api", timeout=30.0)
    seed.connect(client, REPO)
    # Read on every launch, so no restart: the env the repo's agents run with.
    path = seed.HOME / "config" / "repos.yaml"
    config = yaml.safe_load(path.read_text())
    for entry in config["repos"]:
        if entry["path"] == str(REPO):
            entry["env"] = {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "2"}
    path.write_text(yaml.safe_dump(config, sort_keys=False))

    filed = [
        (
            client.post(
                "/work-items",
                json={
                    "title": title,
                    "repo": str(REPO),
                    "chain_template": chain,
                    "autostart": True,
                    **({"description": keyword} if keyword else {}),
                },
            )
            .raise_for_status()
            .json()["id"],
            want,
        )
        for title, chain, keyword, want in NEIGHBOURS
    ]
    for wid, want in filed:
        if seed.settle(client, wid, want, timeout=60) != want:
            return 1
    return subprocess.run(
        ["node", str(ROOT / "dev" / "readme_gif.mjs"), *sys.argv[1:]],
        cwd=ROOT / "frontend",
        env=os.environ,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
