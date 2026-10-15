"""Turn the docs' "Added in" notes into the release's own numbers.

A docs page, section or CLI verb that is new since the last release carries a
note on its own line under its heading, or at the end of a table cell:

    _Added in next_      written by the author, never a number
    _Added in 2.1_       what a release makes of it

`dev/check_docs_added_in.py` is the rule that every new thing has one. This is
the release's half. Notes mark minor releases only:

- X.Y.0 (Y > 0): `next` becomes `X.Y`.
- X.0.0: a new major starts with no notes, so every note goes.
- X.Y.Z (Z > 0): a patch adds no features, so a `next` note means one shipped
  under a patch label. Nothing is written and the exit status is 1, naming the
  pages; relabel the release or take the note off.

Every release also drops the `<!-- renamed from: ... -->` and `<!-- not new: ... -->`
comments that excuse a heading from a note: the next release's comparison starts
from this one, so they have nothing left to excuse.

    python3 dev/stamp_docs_added_in.py VERSION [CONTENT_DIR]   # no leading v

CONTENT_DIR defaults to this checkout's docsite/content. Standard library only:
the release workflow and the docs build call it with a bare python3.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CONTENT = Path(__file__).resolve().parents[1] / "docsite" / "content"

#: The only two spellings of a note.
NOTE = r"_Added in (?:next|\d+\.\d+)_"
#: The comments that excuse a heading (or page) from a note: it is an old one
#: under a new name (`renamed from: Old heading`), or new text about something
#: that already existed (`not new: why`).
HATCH = re.compile(r"<!--\s*(renamed from|not new):\s*(.+?)\s*-->")

_NOTE_LINE = re.compile(rf"\s*{NOTE}\s*")
_NOTE_CELL = re.compile(rf"\s*{NOTE}(?=\s*\|)")
_NEXT = re.compile(r"_Added in next_")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`[^`]*`)")


def _body_lines(lines: list[str]):
    """(index, line) of every line that is prose: not front matter, not fenced."""
    start = lines.index("---", 1) + 1 if lines[:1] == ["---"] and "---" in lines[1:] else 0
    fence = ""
    for index in range(start, len(lines)):
        marker = _FENCE.match(lines[index])
        if marker and not fence:
            fence = marker.group(1)
        elif marker and marker.group(1).startswith(fence):
            fence = ""
        elif not fence:
            yield index, lines[index]


def _outside_code(line: str, pattern: re.Pattern[str], repl) -> str:
    parts = _CODE_SPAN.split(line)
    return "".join(p if i % 2 else pattern.sub(repl, p) for i, p in enumerate(parts))


def stamp_text(text: str, version: str) -> tuple[str, int]:
    """`text` as release `version` leaves it, and how many notes it changed.

    For a patch it still computes the minor's stamp: `stamp()` refuses to write it
    when that changed anything, which is how it knows a `next` note was there."""
    major, minor, patch = (int(n) for n in version.split("."))
    lines = text.split("\n")
    drop: set[int] = set()
    changed = 0
    for index, line in _body_lines(lines):
        if HATCH.fullmatch(line.strip()):
            drop.add(index)
        elif not minor and not patch:
            if _NOTE_LINE.fullmatch(line):
                drop.add(index)
                changed += 1
            else:
                lines[index], n = _NOTE_CELL.subn("", line)
                changed += n
        else:
            lines[index] = _outside_code(line, _NEXT, lambda _: f"_Added in {major}.{minor}_")
            changed += lines[index] != line
    out: list[str] = []
    for index, line in enumerate(lines):
        if index in drop:
            # Take the blank line after it too, or the gap doubles.
            if (
                out
                and not out[-1].strip()
                and index + 1 < len(lines)
                and not lines[index + 1].strip()
            ):
                drop.add(index + 1)
            continue
        out.append(line)
    return "\n".join(out), changed


def stamp(version: str, content: Path = CONTENT) -> list[str]:
    """Stamp every page under `content`; returns the pages a patch would have
    needed to change (and so left alone)."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"{version!r} is not a bare X.Y.Z version (strip the leading v)")
    patch = int(version.rsplit(".", 1)[1]) > 0
    stuck = []
    results = {}
    for page in sorted(content.rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        new, changed = stamp_text(text, version)
        if patch and changed:
            stuck.append(str(page.relative_to(content)))
        elif new != text:
            results[page] = new
    if not stuck:
        for page, new in results.items():
            page.write_text(new, encoding="utf-8")
            print(f"stamped {page.relative_to(content)} for {version}")
    return stuck


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    stuck = stamp(sys.argv[1], Path(sys.argv[2]) if len(sys.argv) == 3 else CONTENT)
    if stuck:
        print(
            f"{sys.argv[1]} is a patch release, which adds no features, but these pages "
            "carry `_Added in next_` notes (a feature shipped under a patch label); "
            "nothing was stamped:\n  " + "\n  ".join(stuck),
            file=sys.stderr,
        )
        sys.exit(1)
