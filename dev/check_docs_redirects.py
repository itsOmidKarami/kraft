"""Fail if a docs address stops working: a bad entry in the map of moved pages,
or a docs link inside Kraft that lands nowhere.

The site has no server to redirect with, so a page or section that moves
leaves a forwarding address in `docsite/content/redirects.yml` (that file
documents the format; `docsite/nuxt.config.ts` turns it into forwarding pages).
This checks, against `docsite/content/` as it is in this checkout:

- every map entry: its target is a real page (and heading), its source is not
  (or the entry would never be reached), and it does not lead to another entry;
- every docs address Kraft links to (the CLI, the web UI, the VS Code
  extension, the plugins, the README, install.sh): it is a real page and
  heading, or the map forwards it to one. A copy of Kraft already installed
  keeps opening those addresses, so moving a section without an entry fails
  here.

A `/kraft/next/...` link and a `/kraft/...` one are both read against this
checkout's pages: they are what the next release publishes at `/kraft/`.

Run directly: `uv run python dev/check_docs_redirects.py`. Exits 1 on any
finding.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONTENT = ROOT / "docsite" / "content"
MAP_NAME = "redirects.yml"

# Where Kraft's own links to the docs live, and what one looks like.
LINK_SOURCES = ["src", "frontend/src", "plugins", "vscode", "install.sh", "README.md"]
LINK = r"itsomidkarami\.github\.io/kraft/[a-zA-Z0-9/#._-]*"

# A site path with an optional anchor. Narrower than the pattern in
# docsite/nuxt.config.ts on purpose: every line this accepts, the site reads,
# and a line the site would read differently or skip is reported here.
_ADDRESS = r"/[a-z0-9/._-]*[a-z0-9](?:#[a-z0-9._-]+)?"
ENTRY = re.compile(rf"^({_ADDRESS}):\s+({_ADDRESS})$")

# Addresses Kraft links to that resolve to nothing today. Each one is a dead
# link a user can reach; fix the link or the page and delete its line. The
# check fails on an entry here that resolves, so this list can only shrink.
KNOWN_BROKEN: dict[str, str] = {}

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def route(relative: Path) -> str:
    """The address Docus serves a content file at: number prefixes and `.md`
    dropped, an `index.md` standing for its folder."""
    parts = [re.sub(r"^\d+\.", "", part) for part in relative.with_suffix("").parts]
    if parts[-1] == "index":
        parts.pop()
    return "/" + "/".join(parts)


def anchors(markdown: str) -> set[str]:
    """The `#anchor` of every heading on a page, as the site writes its ids.

    github-slugger's rule: lower-case the heading's text, drop everything but
    letters, digits, spaces, hyphens and underscores (so a code span loses its
    backticks), turn spaces into hyphens, and number a repeat
    (`-1`, `-2`). Nuxt MDC then squeezes a run of hyphens into one ("Without
    `--json`" is `without-json`), trims them from the ends, and puts `_` before
    an id that starts with a digit ("2. Connect a repo" is `_2-connect-a-repo`).
    Checked against every heading id of a built site when this was written.

    ponytail: reads the Markdown source, not rendered text, so a heading with
    a link in it (`## See [x](/y)`) would get the URL in its anchor. No page
    has one; strip the link syntax here when one does.
    """
    found: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if not heading:
            continue
        slug = re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-")
        slug = re.sub(r"-+", "-", slug).strip("-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        if count:
            slug = f"{slug}-{count}"
        found.add(f"_{slug}" if slug[:1].isdigit() else slug)
    return found


def pages(content: Path) -> dict[str, set[str]]:
    """Every page's address, and the anchors on it."""
    return {
        route(path.relative_to(content)): anchors(path.read_text(encoding="utf-8"))
        for path in sorted(content.rglob("*.md"))
    }


def read_map(path: Path) -> tuple[dict[str, str], list[str]]:
    """The map's entries, and a problem for each line that is not one.

    A missing file is an empty map: a release tag from before the map existed
    has none, and the site builds its content all the same.
    """
    entries: dict[str, str] = {}
    problems = []
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entry = ENTRY.match(line.strip())
        if not entry:
            problems.append(
                f"{path.name}:{number}: not an entry (`/old/page: /new/page`, "
                f"lower-case paths, no trailing slash): {line.strip()}"
            )
        elif entry.group(1) in entries:
            problems.append(f"{path.name}:{number}: {entry.group(1)} has two entries")
        else:
            entries[entry.group(1)] = entry.group(2)
    return entries, problems


def exists(address: str, site: dict[str, set[str]]) -> bool:
    """Whether an address is a real page and, if it names one, a heading on it."""
    path, _, anchor = address.partition("#")
    return path in site and (not anchor or anchor in site[path])


def forward(address: str, entries: dict[str, str]) -> str:
    """Where the site sends a reader who opens `address`: the same one hop the
    forwarding page and app/plugins/moved-sections.ts make."""
    path, _, anchor = address.partition("#")
    if address in entries:
        return entries[address]
    if path in entries:
        target = entries[path]
        return target if "#" in target or not anchor else f"{target}#{anchor}"
    return address


def check_map(entries: dict[str, str], site: dict[str, set[str]]) -> list[str]:
    problems = []
    for source, target in entries.items():
        path, _, anchor = source.partition("#")
        if source == target:
            problems.append(f"{source} forwards to itself")
        elif forward(target, entries) != target:
            problems.append(
                f"{source} forwards to {target}, which another entry forwards again: "
                "point it at the final address"
            )
        elif not exists(target, site):
            problems.append(f"{source} forwards to {target}, which is not a page and heading")
        if not anchor and path in site:
            problems.append(f"{source} is still a real page, so its entry is never reached")
        elif anchor and path not in site and path not in entries:
            problems.append(
                f"{source} is on a page that neither exists nor has an entry, "
                "so its entry is never reached"
            )
        elif anchor and anchor in site.get(path, ()):
            problems.append(
                f"{source} is still a heading on its page, which its entry would make unreachable"
            )
    return problems


def check_links(
    links: list[str],
    entries: dict[str, str],
    site: dict[str, set[str]],
    known_broken: dict[str, str],
) -> list[str]:
    problems = []
    for address in sorted(set(links)):
        target = forward(address, entries)
        if exists(target, site):
            if address in known_broken:
                problems.append(f"{address} resolves now: delete it from KNOWN_BROKEN")
        elif address not in known_broken:
            via = "" if target == address else f" (forwarded to {target})"
            problems.append(
                f"Kraft links to {address}{via}, which is not a page and heading. "
                f"If it moved, add an entry to docsite/content/{MAP_NAME}"
            )
    return problems


def site_path(url: str) -> str:
    """A docs URL as a site path: no base, no `/next`, no trailing slash."""
    # A URL that ends a sentence takes the full stop with it.
    address = url.partition("/kraft")[2].rstrip(".")
    address = re.sub(r"^/next(?=/|#|$)", "", address)
    path, hash_, anchor = address.partition("#")
    return (path.rstrip("/") or "/") + hash_ + anchor


def kraft_links(root: Path) -> list[str]:
    """Every docs address Kraft links to, as a site path without the base."""
    found = subprocess.run(
        ["git", "grep", "-h", "-o", "-E", LINK, "--", *LINK_SOURCES],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return [site_path(url) for url in found]


def check(root: Path) -> list[str]:
    content = root / "docsite" / "content"
    site = pages(content)
    entries, problems = read_map(content / MAP_NAME)
    links = kraft_links(root)
    if not links:
        # Zero is the grep failing, not Kraft having no docs links.
        problems.append(f"found no docs links under {', '.join(LINK_SOURCES)}")
    return problems + check_map(entries, site) + check_links(links, entries, site, KNOWN_BROKEN)


def main() -> int:
    problems = check(ROOT)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} docs address problem(s)", file=sys.stderr)
        return 1
    print("ok: every moved address and every docs link in Kraft resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
