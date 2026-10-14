"""Fail if a docsite page's first paragraph only restates its description.

The page shows its front-matter `description` as a lede under the title, so an
opening paragraph that says the same again spends the most valuable lines of
the page twice, on a phone most of all. The paragraph should carry on from the
description or not be there.

Mechanical only: it measures the share of the first sentence's content words (no
stop words, plural `s` dropped) that the description already used. It cannot
tell whether a paragraph with new words reads well.

    uv run python dev/check_docs_ledes.py            # all of docsite/content/
    uv run python dev/check_docs_ledes.py PAGE.md    # one page, or a folder

Exits 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_docs_walls import DOCSITE, ROOT, blocks  # noqa: E402

#: Share of the first sentence's content words that the description already
#: used. Calibrated on the corpus after the rewrite: the highest score of a
#: page whose opener adds something was 0.62, and 24 openers that only
#: restated the description scored 0.67 or more, so 0.65 sits between them.
THRESHOLD = 0.65

#: A paragraph this short is a pointer, not a restatement; one stray shared
#: word would otherwise decide it.
MIN_WORDS = 10

_STOP = set(
    "a an the and or of to in on at by for with from as is are was be it its this that these those"
    " you your what when where how why which who not no do does can will may one each every any"
    " than then so if into out up about also only more most such there have has had but".split()
)
# A full stop closes a sentence only before a space and a capital or backtick/bracket.
_SENTENCE = re.compile(r"(?<=[.!?:])\s+(?=[A-Z`*\[])")
_DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.M)


def words(text: str) -> set[str]:
    """Content words of `text`, lower-cased, with a plural `s` dropped."""
    text = re.sub(r"\]\([^)]*\)", " ", text)  # a link's target is not prose
    found = (w.rstrip("s") for w in re.findall(r"[a-z][a-z0-9_]+", text.lower()))
    return {w for w in found if len(w) > 2 and w not in _STOP}


def description(text: str) -> str:
    front = text.split("---", 2)[1] if text.startswith("---") else ""
    match = _DESCRIPTION.search(front)
    return match.group(1).strip("\"'") if match else ""


def overlap(text: str) -> float | None:
    """Share of the first sentence the description already said, or None when
    the page has no description or no paragraph long enough to judge."""
    desc = description(text)
    # Only a paragraph before the first heading is an opening paragraph.
    opening = re.split(r"^#{1,6} ", text.split("---", 2)[-1], maxsplit=1, flags=re.M)[0]
    first = next((b for b in blocks(opening) if b.kind == "paragraph"), None)
    if not desc or first is None or first.words < MIN_WORDS:
        return None
    mine = words(_SENTENCE.split(first.text, maxsplit=1)[0])
    return len(mine & words(desc)) / len(mine) if mine else None


def check(pages: Iterable[Path]) -> list[str]:
    problems = []
    for page in pages:
        score = overlap(page.read_text(encoding="utf-8"))
        if score is not None and score >= THRESHOLD:
            shown = (
                page.resolve().relative_to(ROOT) if page.resolve().is_relative_to(ROOT) else page
            )
            problems.append(f"{shown}: first paragraph repeats the description ({score:.2f})")
    return problems


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [DOCSITE]
    pages = sorted(p for t in targets for p in (t.rglob("*.md") if t.is_dir() else [t]))
    if not pages:
        print(f"no Markdown pages under {', '.join(map(str, targets))}", file=sys.stderr)
        return 1
    problems = check(pages)
    for problem in problems:
        print(problem)
    if problems:
        print(
            f"{len(problems)} page(s) open by repeating the description, which the page already"
            " shows as its lede. Delete the paragraph, or carry on from where the description"
            " stops.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: no repeated ledes in {len(pages)} page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
