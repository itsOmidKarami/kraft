"""Fail if a docsite page has a wall of text: a paragraph or list item over
80 words, or a table cell over 40.

A reader skims a docs page, and a long block is where skimming stops. Front
matter, fenced code, headings, MDC component lines (`::name`) and the YAML
block that opens a component are not prose and are not counted. A list item
is counted with its continuation lines.

Mechanical only: it counts words, it cannot tell whether a short block reads
well.

    uv run python dev/check_docs_walls.py            # all of docsite/content/
    uv run python dev/check_docs_walls.py PAGE.md    # one page, or a folder

Exits 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).parent.parent
DOCSITE = ROOT / "docsite" / "content"

LIMITS = {"paragraph": 80, "list item": 80, "table cell": 40}

#: Walls that stay, as (page under docsite/content/, a fragment of the block's
#: text), each with its reason. The fragment, not a line number: a line moves
#: with every edit above it. An entry that matches no wall fails the check, so
#: fixing the page means deleting its entry here.
ALLOWED: list[tuple[str, str]] = [
    # Five quoted error strings that share one cause and one fix. They stay in
    # one first-column cell so a reader who searches for any of them lands on
    # the row that answers it.
    (
        "4.troubleshooting/index.md",
        "`workspace member <path> of <worktree> has no checkout Kraft made",
    ),
]

_LIST_ITEM = re.compile(r"(?:[-*]|\d+\.)\s+(.*)")
_COMPONENT = re.compile(r":{2,}[\w-]")
_TABLE_RULE = set("|-: ")


class Block(NamedTuple):
    line: int
    kind: str
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


def blocks(text: str) -> list[Block]:
    """Each paragraph, list item and table cell of a page, with its first line."""
    out: list[Block] = []
    current: tuple[int, str, list[str]] | None = None
    in_fence = in_yaml = False
    # A `---` opens a YAML block only as the page's first line (front matter)
    # or on the line after a component opens (its settings).
    yaml_may_open = True

    def flush() -> None:
        nonlocal current
        if current:
            out.append(Block(current[0], current[1], " ".join(current[2])))
        current = None

    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if in_yaml:
            in_yaml = line != "---"
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        if line == "---" and yaml_may_open:
            in_yaml, yaml_may_open = True, False
            continue
        yaml_may_open = bool(_COMPONENT.match(line))
        item = _LIST_ITEM.match(line)
        if not line or line.startswith(("#", "::")):
            flush()
        elif line.startswith("|"):
            flush()
            if not set(line) <= _TABLE_RULE:
                out.extend(Block(number, "table cell", c) for c in line.strip("|").split("|"))
        elif item:
            flush()
            current = (number, "list item", [item.group(1)])
        elif current:
            current[2].append(line)
        else:
            current = (number, "paragraph", [line])
    flush()
    return out


def walls(text: str) -> list[Block]:
    """The blocks of a page that are over their limit."""
    return [b for b in blocks(text) if b.words > LIMITS[b.kind]]


def check(
    pages: Iterable[Path], allowed: list[tuple[str, str]] | None = None, docsite: Path = DOCSITE
) -> list[str]:
    """Findings for `pages`: each wall not in `allowed`, and each `allowed`
    entry for one of these pages (or for a page that is gone) that let nothing
    through."""
    allowed = ALLOWED if allowed is None else allowed
    docsite = docsite.resolve()
    problems = []
    checked = set()
    used = set()
    for page in pages:
        resolved = page.resolve()
        name = (
            resolved.relative_to(docsite).as_posix() if resolved.is_relative_to(docsite) else None
        )
        shown = resolved.relative_to(ROOT) if resolved.is_relative_to(ROOT) else page
        checked.add(name)
        for wall in walls(page.read_text(encoding="utf-8")):
            entry = next((e for e in allowed if e[0] == name and e[1] in wall.text), None)
            if entry:
                used.add(entry)
                continue
            problems.append(
                f"{shown}:{wall.line}: {wall.kind} of {wall.words} words"
                f" (limit {LIMITS[wall.kind]})"
            )
    for entry in allowed:
        name, fragment = entry
        if entry not in used and (name in checked or not (docsite / name).is_file()):
            problems.append(
                f"{name}: ALLOWED entry {fragment!r} matches no wall;"
                " remove it from dev/check_docs_walls.py"
            )
    return problems


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [DOCSITE]
    pages = sorted(p for t in targets for p in (t.rglob("*.md") if t.is_dir() else [t]))
    if not pages:
        # Zero pages would pass vacuously: a moved docsite must not read as clean.
        print(f"no Markdown pages under {', '.join(map(str, targets))}", file=sys.stderr)
        return 1
    problems = check(pages)
    for problem in problems:
        print(problem)
    if problems:
        print(
            f"{len(problems)} wall(s) of text. Split a paragraph or list item into shorter"
            " ones; cut a table cell to one sentence and move the rest to a subsection"
            " below the table.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: no walls of text in {len(pages)} page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
