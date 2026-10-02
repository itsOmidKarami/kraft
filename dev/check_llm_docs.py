"""Fail if the docs the site hands to LLMs carry links that resolve nowhere.

`dev/build_docs_site.sh` builds two channels, and each has two copies of its
pages meant for a model rather than a browser: `raw/<page>.md` (the target of
llms.txt, "Copy page" and "Open in Claude/ChatGPT") and `llms-full.txt`. The
lychee step in docs.yml reads neither, and both once shipped hundreds of dead
links: root-relative `/reference/x` in raw/, which resolve outside `/kraft/`,
and same-page `#anchor` links in llms-full.txt that landed on the landing page.
docsite/server/plugins/llm-links.ts is what keeps them out; this is what
notices when it stops.

    uv run python dev/check_llm_docs.py SITE_DIR      # build_docs_site.sh's OUT_DIR

Checks SITE_DIR as the stable channel and SITE_DIR/next as main's when it is
there. Exits 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ORIGIN = "https://itsomidkarami.github.io"
# A markdown link, or the href/src/to of raw HTML, to a path from the domain
# root. `//` is a protocol-relative URL, which is another site's.
ROOT_RELATIVE = re.compile(r"\]\((/(?!/)[^)\s]*)|\s(?:href|src|to)=\"(/(?!/)[^\"]*)")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def root_relative_links(text: str) -> list[tuple[int, str]]:
    """Each link in a raw page that is relative to the domain root."""
    return [
        (number, found.group(1) or found.group(2))
        for number, line in enumerate(text.splitlines(), 1)
        for found in ROOT_RELATIVE.finditer(line)
    ]


def landing_anchors(text: str, base: str, allowed: set[str]) -> list[str]:
    """Each `<base>/#frag` link in llms-full.txt that the landing page cannot answer.

    Every page's own `#frag` link once became `<base>/#frag`, the landing page.
    One is only right when the landing page has that anchor (`allowed`).
    """
    prefix = f"{base}/#"
    return sorted(
        {
            link
            for line in text.splitlines()
            for link in LINK.findall(line)
            if link.startswith(prefix) and link[len(prefix) - 1 :] not in allowed
        }
    )


def check_channel(root: Path, base: str) -> list[str]:
    """Findings for one built channel: `root` is its output directory, `base` its URL."""
    problems = []
    raw = root / "raw"
    pages = sorted(raw.rglob("*.md")) if raw.is_dir() else []
    if not pages:
        return [f"{raw}: no raw/*.md pages, so the build is not what this check expects"]
    for page in pages:
        for number, link in root_relative_links(page.read_text(encoding="utf-8")):
            problems.append(
                f"{page}:{number}: root-relative link {link} (resolves outside {base}/)"
            )
    full = root / "llms-full.txt"
    if not full.is_file():
        return [*problems, f"{full}: missing"]
    landing = raw / "index.md"
    allowed = (
        set(re.findall(r"\]\((#[^)\s]+)\)", landing.read_text(encoding="utf-8")))
        if landing.is_file()
        else set()
    )
    for link in landing_anchors(full.read_text(encoding="utf-8"), base, allowed):
        problems.append(f"{full}: {link} points a page's own anchor at the landing page")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    site = Path(argv[1])
    channels = [(site, f"{ORIGIN}/kraft")]
    if (site / "next").is_dir():
        channels.append((site / "next", f"{ORIGIN}/kraft/next"))
    problems = [p for root, base in channels for p in check_channel(root, base)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} dead link(s) in the LLM copies of the docs", file=sys.stderr)
        return 1
    print(f"ok: no dead links in raw/ or llms-full.txt under {site}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
