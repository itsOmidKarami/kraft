"""Fail if a section landing page does not list what its sidebar group holds.

A folder under `docsite/content/` is a sidebar group, and its `index.md` is
the group's landing page. A reader picks a page from the landing page's "In
this section" list, so the list names exactly the pages the sidebar shows for
the group, in the sidebar's order. A link to a page of another section goes
under a different heading.

A sub-group with no landing page of its own has nowhere to be linked, so its
pages are listed in its place (the Guides index lists every guide).

The sidebar sorts by file name, so `10.x` sorts before `2.y`. Number prefixes
in one folder must all have the same number of digits, or the numeric order
this check reads is not the order the reader sees.

    uv run python dev/check_docs_landings.py

Exits 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCSITE = ROOT / "docsite" / "content"

_PREFIX = re.compile(r"(\d+)\.")
_SECTION = re.compile(r"^## In this section\n(.*?)(?=^## |\Z)", re.S | re.M)
_LINK = re.compile(r"\]\((/[^)#\s]*)")


def route(rel: Path) -> str:
    """The address a page under the content folder is published at."""
    parts = [_PREFIX.sub("", part, count=1) for part in rel.with_suffix("").parts]
    if parts[-1] == "index":
        parts.pop()
    return "/" + "/".join(parts)


def children(folder: Path) -> list[Path]:
    """A folder's pages and sub-folders, in the sidebar's order."""
    found = [
        p
        for p in folder.iterdir()
        if not p.name.startswith(".") and p.name != "index.md" and (p.is_dir() or p.suffix == ".md")
    ]
    return sorted(found, key=lambda p: p.name)


def expected(content: Path, folder: Path) -> list[str]:
    """What a landing page for `folder` should list, in order."""
    out: list[str] = []
    for child in children(folder):
        if child.is_dir() and not (child / "index.md").exists():
            out += expected(content, child)
        else:
            out.append(
                route((child / "index.md" if child.is_dir() else child).relative_to(content))
            )
    return out


def listed(text: str) -> list[str] | None:
    """The addresses linked under "In this section", or None with no such heading."""
    section = _SECTION.search(text)
    if section is None:
        return None
    return list(dict.fromkeys(_LINK.findall(section.group(1))))


def check(content: Path = DOCSITE) -> list[str]:
    problems: list[str] = []
    for folder in sorted(p for p in content.rglob("*") if p.is_dir()):
        widths = {len(m.group(1)) for p in children(folder) if (m := _PREFIX.match(p.name))}
        if len(widths) > 1:
            problems.append(
                f"{folder.relative_to(content)}/: number prefixes differ in length, "
                "so the sidebar does not sort them in numeric order"
            )
        index = folder / "index.md"
        if not index.exists():
            continue
        where = index.relative_to(content)
        want, got = expected(content, folder), listed(index.read_text())
        if got is None:
            problems.append(f'{where}: no "## In this section" list')
            continue
        for address in want:
            if address not in got:
                problems.append(f"{where}: {address} is in this section and not listed")
        for address in got:
            # A page deeper in one of the group's own sub-folders is still here.
            if not any(address == a or address.startswith(a + "/") for a in want):
                problems.append(
                    f'{where}: {address} is listed under "In this section" and lives elsewhere'
                )
        if [a for a in got if a in want] != [a for a in want if a in got]:
            problems.append(f"{where}: the list is not in the sidebar's order")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    if problems:
        print(
            f'\n{len(problems)} landing page problem(s). "In this section" lists the '
            "pages of the page's own folder, in file-name order; link other pages "
            "under another heading."
        )
        return 1
    print("ok: every section landing page lists what its sidebar group holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
