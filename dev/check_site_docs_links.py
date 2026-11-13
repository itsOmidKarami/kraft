"""Fail if a marketing page links to a docs page the published site doesn't have.

The marketing pages (`docsite/app/pages/*.vue`, drawn by
`docsite/app/components/<page>/` and the shared `components/site/`) are site
code: they ship from `main` and show only at the root of kraft-sdlc.dev. The
root's docs are the latest stable release's (dev/docs_versions.py), so a page
that links to a docs page or heading newer than that release links nowhere
until the next one is tagged.

This reads every `/docs/path#heading` string in those files and checks it
against the latest release's `docsite/content` (and its map of moved pages),
the same way dev/check_docs_redirects.py checks this checkout's.

Run directly: `python3 dev/check_site_docs_links.py`. Needs the release tags
(a clone with `fetch-depth: 0`). Exits 1 on any finding.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_docs_redirects import (  # noqa: E402
    MAP_NAME,
    exists,
    forward,
    pages,
    read_map,
    site_pages,
)
from docs_versions import _has_docs, versions  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "docsite" / "app"
# A quoted site path: '/guides/x', "/project/y#z".
_LINK = re.compile(r"""["'`](/[a-z][a-z0-9/._-]*(?:#[a-z0-9._-]+)?)["'`]""")
# Files beside the pages, not pages.
_FILE_ROOTS = ("/assets/", "/site/", "/demo/", "/schemas/", "/diagrams/", "/_")


def marketing_files(app: Path) -> list[Path]:
    """The files that draw the marketing pages: each page, the component
    folder of the `<XPage>` it draws, the shared `components/site/`, and
    `utils/siteNav.ts` (the header, menus and footer)."""
    files = sorted((app / "pages").glob("*.vue"))
    folders = {app / "components" / "site"}
    for page in files:
        for name in re.findall(r"<([A-Z][a-z]+)Page\b", page.read_text(encoding="utf-8")):
            folders.add(app / "components" / name.lower())
    for folder in sorted(folders):
        files += sorted(folder.rglob("*.vue"))
    return [*files, app / "utils" / "siteNav.ts"]


def docs_links(files: list[Path], root: Path) -> list[tuple[str, str]]:
    """Every docs address in `files`, with where it is (`file:line`)."""
    found = []
    for file in files:
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            for address in _LINK.findall(line):
                if not address.startswith(_FILE_ROOTS):
                    found.append((f"{file.relative_to(root)}:{number}", address.rstrip("/")))
    return found


def check(
    links: list[tuple[str, str]], site: dict[str, set[str]], entries: dict[str, str], tag: str
) -> list[str]:
    return [
        f"{where}: {address} is not a page and heading in {tag}'s docs, which the site's "
        "root serves: it links nowhere until a release that has it"
        for where, address in links
        if not exists(forward(address, entries), site)
    ]


def latest_tag() -> str:
    tags = subprocess.run(
        ["git", "-C", str(ROOT), "tag", "--list"], capture_output=True, text=True, check=True
    ).stdout.split()
    found = versions(tags, _has_docs, os.environ.get("KRAFT_DOCS_STABLE_TAG"))
    return found[0]["tag"] if found and found[0]["id"] == "latest" else ""


def release_site(tag: str, into: Path) -> tuple[dict[str, set[str]], dict[str, str]]:
    """The release's docs pages and map of moved pages, plus this checkout's
    marketing pages (site code, at the root whatever the release)."""
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", tag, "docsite/content"],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")
    content = into / "docsite" / "content"
    entries, _ = read_map(content / MAP_NAME)
    return {**pages(content), **site_pages(APP / "pages", APP / "components")}, entries


def main() -> int:
    tag = latest_tag()
    if not tag:
        print("no stable release tag: the root serves main's docs, nothing to check")
        return 0
    links = docs_links(marketing_files(APP), ROOT)
    if not links:
        # Zero is the pattern failing, not the pages having no docs links.
        print("found no docs links on the marketing pages", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        site, entries = release_site(tag, Path(tmp))
    problems = check(links, site, entries, tag)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} marketing-page docs link(s) not in {tag}", file=sys.stderr)
        return 1
    print(f"ok: all {len(links)} docs links on the marketing pages resolve in {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
