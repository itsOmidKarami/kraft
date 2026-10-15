"""Which versions of the docs the site publishes, and which tag each is built from.

The latest stable release is at `/`, each older major that has docs is frozen
at `/vN/` (built from that major's last exact `vX.Y.Z` tag with a
`docsite/content`), and main is at `/next/`. A new major turns the one before it
into `/vN/` with no code change. `dev/build_docs_site.sh` builds from this and
hands the same list to every build, for the version menu.

    python3 dev/docs_versions.py           # id, base, tag per line, in build order
    python3 dev/docs_versions.py --json    # the list as the site reads it, menu order

Prints nothing when the repo has no stable tag.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The same rule as release.yml: exact vX.Y.Z, so rc/alpha/beta tags never count.
STABLE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def versions(
    tags: Iterable[str], has_docs: Callable[[str], bool], latest: str | None = None
) -> list[dict[str, str]]:
    """The versions to publish, in menu order: latest, older majors newest first, next.

    `latest` names the tag for `/` when it is not the highest stable one.
    """
    stable = {t: tuple(map(int, m.groups())) for t in tags if (m := STABLE.match(t))}
    if latest:
        if not (m := STABLE.match(latest)):
            raise ValueError(f"{latest!r} is not a vX.Y.Z tag")
        stable[latest] = tuple(map(int, m.groups()))
    if not stable:
        return []
    top = latest or max(stable, key=stable.__getitem__)
    older: dict[int, str] = {}
    # Ascending, so the last tag of a major to have docs is its highest.
    for tag, (major, *_) in sorted(stable.items(), key=lambda item: item[1]):
        if major < stable[top][0] and has_docs(tag):
            older[major] = tag
    return [
        _entry("latest", f"{top[1:]} (latest)", "/", top),
        *(_entry(f"v{m}", f"{m}.x", f"/v{m}/", older[m]) for m in sorted(older, reverse=True)),
        {
            "id": "next",
            "label": "next",
            "base": "/next/",
            "tag": "",
            "name": "the next docs, for unreleased main",
        },
    ]


def _entry(id: str, label: str, base: str, tag: str) -> dict[str, str]:
    return {"id": id, "label": label, "base": base, "tag": tag, "name": f"the {tag[1:]} docs"}


def _has_docs(tag: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{tag}:docsite/content"],
            capture_output=True,
        ).returncode
        == 0
    )


def main(argv: list[str]) -> int:
    tags = subprocess.run(
        ["git", "-C", str(ROOT), "tag", "--list"], capture_output=True, text=True, check=True
    ).stdout.split()
    found = versions(tags, _has_docs, os.environ.get("KRAFT_DOCS_STABLE_TAG"))
    if "--json" in argv:
        print(json.dumps(found, separators=(",", ":")) if found else "[]")
    else:
        # Main first and the latest last, so the latest build's 404.html and
        # robots.txt are the ones at the root.
        for v in reversed(found):
            print(v["id"], v["base"], v["tag"] or "-")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
