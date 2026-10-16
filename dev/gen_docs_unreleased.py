"""Write the docs' "Unreleased" page from the `_Added in next_` notes.

The notes already say what is new since the last release, on the page or the
section that is new. This lists them, so nobody keeps a second list by hand:

    python3 dev/gen_docs_unreleased.py [CONTENT_DIR]

It writes `6.project/3.unreleased.md` under CONTENT_DIR (default: this
checkout's docsite/content, where it is gitignored) and prints, as JSON, every
page that carries a note, by route, as `new` (the page) or `updated` (some
sections). nuxt.config.ts runs it for the
`next` build and for a local one, and puts the routes in the sidebar's marks
and the page in the version banner.

With no `next` note it writes nothing, removes a page it wrote before, and
prints `{"page": null, "routes": []}`: a release stamps every note into a
number (dev/stamp_docs_added_in.py), so a released site has no such page.
Standard library plus the checker's own parser.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_docs_added_in import Page, parse, slug  # noqa: E402
from check_docs_redirects import CONTENT  # noqa: E402

PAGE = "6.project/3.unreleased.md"
ROUTE = "/project/unreleased"

_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)


def _title(text: str) -> str:
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    found = _TITLE.search(head)
    return found.group(1).strip("'\"") if found else ""


def _section(content: Path, name: str) -> str:
    nav = content / name.split("/")[0] / ".navigation.yml"
    found = _TITLE.search(nav.read_text(encoding="utf-8")) if nav.exists() else None
    return found.group(1).strip("'\"") if found else name.split("/")[0]


def _news(page: Page) -> tuple[bool, list[str]]:
    """Whether the page itself is new, and the headings that carry a note."""
    new_page = page.note == "next"
    headings = {h.line: h for h in page.headings}
    found: list[str] = []
    for line, version in page.notes:
        if version != "next" or new_page:
            continue
        # The heading a note sits under or inside (a table cell's note has the
        # section it is in); a note above every heading is the page's own.
        above = [n for n in headings if n < line]
        if not above:
            continue
        text = headings[max(above)].text
        if text not in found:
            found.append(text)
    return new_page, found


def build(content: Path) -> tuple[str | None, dict[str, str]]:
    sections: dict[str, list[str]] = {}
    routes: dict[str, str] = {}
    for path in sorted(content.rglob("*.md")):
        name = str(path.relative_to(content))
        if name == PAGE:
            continue
        text = path.read_text(encoding="utf-8")
        page = parse(name, text)
        new_page, headings = _news(page)
        if not new_page and not headings:
            continue
        routes[page.route] = "new" if new_page else "updated"
        title = _title(text) or page.route
        if new_page:
            entry = f"- [{title}]({page.route}): a new page."
        else:
            links = "\n".join(f"  - [{h}]({page.route}#{slug(h)})" for h in headings)
            entry = f"- [{title}]({page.route})\n{links}"
        sections.setdefault(_section(content, name), []).append(entry)
    if not routes:
        return None, {}
    count = len(routes)
    lines = [
        "---",
        "title: Unreleased",
        "description: What is new in the docs on main since the latest release.",
        "---",
        "_Added in next_",
        "",
        f"These docs are for unreleased main. {count} page{'s' if count != 1 else ''} below "
        "carry something the latest release does not have. The list is built from the "
        'docs\' own "Added in next" notes, so it is as current as the pages.',
        "",
    ]
    for section, entries in sections.items():
        lines += [f"## {section}", "", *entries, ""]
    return "\n".join(lines), routes


def main(argv: list[str]) -> int:
    content = Path(argv[1]) if len(argv) > 1 else CONTENT
    text, routes = build(content)
    target = content / PAGE
    if text is None:
        target.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(json.dumps({"page": ROUTE if text else None, "routes": routes}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
