"""Fail if a new docs page, section or CLI verb has no "Added in" note, or a
note is malformed, names a release that does not exist, or outlived its release.

A thing the docs describe that did not exist in the latest release carries a
note naming the minor release that introduced it (`_Added in next_` until the
release stamps it, `_Added in 2.1_` after: dev/stamp_docs_added_in.py). This
compares `docsite/content` with the latest release tag's:

1. Format. A note is exactly `_Added in next_` or `_Added in X.Y_`, on its own
   line directly under a heading (blank lines and comments between are fine) or
   at the end of a table cell. Never in heading text, which leaks into the table
   of contents, search titles and the sidebar, nor mid-sentence.
2. A number names a real minor release: `vX.Y.0` is a tag and Y is not 0 (a
   major starts with no notes, a patch adds no features).
3. Missing notes. A page the tag lacks needs a note under its title; that note
   covers its sections. On a page the tag has, a heading it lacks needs a note
   on it or on a heading above it. A CLI verb is a heading, so it is covered.
   What is not new:
   - a heading with the same text on the same page in the tag;
   - anything `docsite/content/redirects.yml` forwards: a moved page brings its
     headings along, a moved section its one heading;
   - a heading with `<!-- renamed from: Old heading -->` under it, or
     `<!-- renamed from: /page#Old heading -->` for one moved from another page,
     or `<!-- renamed from: /page -->` under a page's first lines for a page
     split or renamed. The old name must exist in the tag, so the comment is a
     claim a reviewer can check, not a free pass; a release drops them.
4. A missed stamp. A `next` note on a page or heading the latest tag already
   has means the release left it unstamped (or the thing is not new). Only a
   warning: between a tag and its stamp pull request merging, every `next` note
   is one, and a pull request opened then is not to blame.

    uv run python dev/check_docs_added_in.py              # this checkout against the latest tag
    uv run python dev/check_docs_added_in.py --ref v2.1.0 # a tag's own content: 1 and 2 only

Needs the repository's tags (CI's lint job checks out with fetch-depth 0).
Exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_docs_redirects import CONTENT, MAP_NAME, ROOT, anchors, read_map, route  # noqa: E402
from stamp_docs_added_in import HATCH, NOTE  # noqa: E402

_STRICT = re.compile(rf"^{NOTE}$")
# Anything that looks like a note, so a misspelt one is reported, not ignored.
_LOOSE = re.compile(r"[_*]Added in\b[^_*]*[_*]")
_CODE_SPAN = re.compile(r"`[^`]*`")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_COMMENT = re.compile(r"^<!--.*-->$")
_STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass
class Heading:
    line: int
    level: int
    text: str
    note: str | None = None  # "next" or "2.1"
    renamed_from: list[str] = field(default_factory=list)
    not_new: bool = False


@dataclass
class Page:
    name: str  # path under docsite/content
    route: str
    headings: list[Heading] = field(default_factory=list)
    note: str | None = None  # the note under the title
    note_line: int = 0
    renamed_from: list[str] = field(default_factory=list)
    not_new: bool = False
    notes: list[tuple[int, str]] = field(default_factory=list)  # every valid note
    problems: list[str] = field(default_factory=list)


def parse(name: str, text: str) -> Page:
    """A page's headings, its notes and the placement problems among them."""
    page = Page(name, route(Path(name)))
    lines = text.splitlines()
    first = 0
    if lines[:1] == ["---"] and "---" in lines[1:]:
        first = lines.index("---", 1) + 1
    fence = ""
    under_heading = True  # only blanks and comments since the last heading
    current: Heading | None = None
    for number in range(first, len(lines)):
        line = lines[number].strip()
        at = f"{name}:{number + 1}"
        marker = _FENCE.match(line)
        if marker and not fence:
            fence = marker.group(1)
        elif marker and marker.group(1).startswith(fence):
            fence = ""
        if fence or marker:
            under_heading = False
            continue
        heading = _HEADING.match(line)
        if heading:
            if _LOOSE.search(_CODE_SPAN.sub("", heading.group(2))):
                page.problems.append(
                    f"{at}: a note in a heading's text; put it on its own line under the heading"
                )
            current = Heading(number + 1, len(heading.group(1)), heading.group(2))
            page.headings.append(current)
            under_heading = True
            continue
        if not line:
            continue
        hatch = HATCH.fullmatch(line)
        if hatch:
            kind, target = hatch.groups()
            titled = current is None or current.level == 1
            owner = page if titled or kind == "renamed from" and target.startswith("/") else current
            if kind == "not new":
                owner.not_new = True
            else:
                owner.renamed_from.append(target)
            continue
        if _COMMENT.match(line):
            continue
        body = _CODE_SPAN.sub("", line)
        if _STRICT.match(line):
            if not under_heading:
                page.problems.append(f"{at}: a note belongs directly under a heading, not here")
            else:
                version = line[len("_Added in ") : -1]
                titled = current is None or current.level == 1
                holder = page if titled else current
                holder.note = version
                if titled:
                    page.note_line = number + 1
                page.notes.append((number + 1, version))
            under_heading = False
            continue
        under_heading = False
        for found in _LOOSE.finditer(body):
            cell = line.startswith("|") and body[found.end() :].strip().startswith("|")
            if cell and _STRICT.match(found.group()):
                page.notes.append((number + 1, found.group()[len("_Added in ") : -1]))
            else:
                page.problems.append(
                    f"{at}: {found.group()} is not a note: write it exactly `_Added in next_` "
                    "or `_Added in X.Y_`, on its own line under a heading or at the end of "
                    "a table cell"
                )
    return page


def load(files: dict[str, str]) -> dict[str, Page]:
    pages = [parse(name, text) for name, text in files.items()]
    return {page.route: page for page in pages}


def slug(heading: str) -> str:
    """The anchor a heading gets on a page of its own."""
    return next(iter(anchors(f"## {heading}")), "")


def inherited(base: dict[str, Page], entries: dict[str, str], cur: dict[str, Page]):
    """What the latest release already had, as it stands in the current pages.

    Returns the (route, heading) pairs and the routes that are not new: the
    release's own, plus whatever `redirects.yml` forwards them to.
    """
    headings = {(p.route, h.text) for p in base.values() for h in p.headings}
    routes = set(base)
    for old, new in entries.items():
        path, _, anchor = old.partition("#")
        target, _, target_anchor = new.partition("#")
        if path not in base:
            continue
        if not anchor:
            routes.add(target)
            headings |= {(target, h.text) for h in base[path].headings}
        elif target in cur:
            moved = [h for h in base[path].headings if slug(h.text) == anchor]
            wanted = [h for h in cur[target].headings if slug(h.text) == target_anchor]
            headings |= {(target, h.text) for h in wanted if moved}
    return headings, routes


def claimed(old: str, page: Page, headings: set[tuple[str, str]], routes: set[str]) -> bool:
    """Whether a `renamed from:` comment names something the release had."""
    if old.startswith("/"):
        path, _, text = old.partition("#")
        return (path, text) in headings if text else path in routes
    return (page.route, old) in headings


def check_notes(pages: dict[str, Page], tags: set[str], newest: str | None) -> list[str]:
    """Checks 1 and 2: every note is well formed and names a release that exists."""
    problems = []
    for page in pages.values():
        problems += page.problems
        for line, version in page.notes:
            at = f"{page.name}:{line}"
            if version == "next":
                continue
            major, minor = (int(n) for n in version.split("."))
            if not minor:
                problems.append(
                    f"{at}: `Added in {version}` names a major release, which starts with no notes"
                )
            elif f"v{version}.0" not in tags:
                problems.append(f"{at}: there is no release v{version}.0 to have added this")
            elif newest and _version(f"v{version}.0") > _version(newest):
                problems.append(f"{at}: v{version}.0 is newer than this content's own {newest}")
    return problems


def _version(tag: str) -> tuple[int, ...]:
    return tuple(int(n) for n in tag.lstrip("v").split("."))


def check_new(
    base: dict[str, Page], cur: dict[str, Page], entries: dict[str, str], latest: str
) -> list[str]:
    """Checks 3 and 4: what is new has a note, and only that has a `next` one."""
    problems = []
    headings, routes = inherited(base, entries, cur)
    for page in cur.values():
        for old in page.renamed_from:
            if old in base:  # a page split or renamed: its old headings are not new here
                routes.add(page.route)
                headings |= {(page.route, h.text) for h in base[old].headings}
    for page in cur.values():
        credited = set()
        for line, text, olds in [(1, None, page.renamed_from)] + [
            (h.line, h.text, h.renamed_from) for h in page.headings
        ]:
            for old in olds:
                if not claimed(old, page, headings, routes):
                    problems.append(
                        f"{page.name}:{line}: `renamed from: {old}` names nothing in {latest}; "
                        "delete the comment or write the heading as it was there"
                    )
                elif text:
                    credited.add((page.route, text))
        is_new = page.route not in routes and not page.not_new
        if is_new and not page.note:
            problems.append(
                f"{page.name}: a page {latest} did not have; put `_Added in next_` on its own "
                "line under the title (or `<!-- renamed from: /old/page -->` for an old page "
                "moved or split)"
            )
        if page.note == "next" and not is_new:
            problems.append(
                f"warning: {page.name}:{page.note_line}: `Added in next` on a page {latest} "
                "already has; "
                "the release left it unstamped, or it is not new"
            )
        stack: list[Heading] = []
        for h in page.headings:
            while stack and stack[-1].level >= h.level:
                stack.pop()
            existing = (page.route, h.text) in headings | credited or h.not_new or page.not_new
            if h.note == "next" and existing:
                problems.append(
                    f"warning: {page.name}:{h.line}: `Added in next` on a heading {latest} "
                    "already has; "
                    "the release left it unstamped, or it is not new"
                )
            if not (existing or h.note or any(a.note for a in stack) or (is_new and page.note)):
                problems.append(
                    f"{page.name}:{h.line}: `{h.text}` is new since {latest}: put "
                    "`_Added in next_` on its own line under it, or "
                    "`<!-- renamed from: Old heading -->` if it is an old heading under a new name"
                )
            stack.append(h)
    return problems


def stable_tags(tags: set[str]) -> list[str]:
    return sorted((t for t in tags if _STABLE_TAG.match(t)), key=_version)


def git(*args: str, binary: bool = False):
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)
    if out.returncode:
        raise SystemExit(f"git {' '.join(args)}: {out.stderr.decode().strip()}")
    return out.stdout if binary else out.stdout.decode()


def content_at(ref: str) -> tuple[dict[str, str], str]:
    """Every page of `docsite/content` at `ref`, keyed by path under it, and its redirects."""
    archive = git("archive", ref, "docsite/content", binary=True)
    files = {}
    redirects = ""
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith("docsite/content/"):
                continue
            name = member.name[len("docsite/content/") :]
            data = tar.extractfile(member).read().decode("utf-8")
            if name.endswith(".md"):
                files[name] = data
            elif name == MAP_NAME:
                redirects = data
    return files, redirects


def content_here() -> dict[str, str]:
    return {
        str(p.relative_to(CONTENT)): p.read_text(encoding="utf-8")
        for p in sorted(CONTENT.rglob("*.md"))
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--ref", help="check a tag's or commit's own content (checks 1 and 2 only)")
    args = ap.parse_args(argv)
    tags = set(git("tag", "--list").split())
    releases = stable_tags(tags)
    if not releases:
        print("no vX.Y.Z release tag in this clone: git fetch --tags", file=sys.stderr)
        return 1
    if args.ref:
        files, _ = content_at(args.ref)
        problems = check_notes(load(files), tags, args.ref if args.ref in tags else None)
    else:
        latest = releases[-1]
        base_files, _ = content_at(latest)
        cur = load(content_here())
        entries, _ = read_map(CONTENT / MAP_NAME)
        problems = check_notes(cur, tags, None) + check_new(load(base_files), cur, entries, latest)
    for problem in problems:
        print(problem)
    problems = [p for p in problems if not p.startswith("warning: ")]
    if problems:
        print(f"{len(problems)} 'Added in' problem(s)", file=sys.stderr)
        return 1
    print("ok: every new page, section and verb has its note, and every note is well formed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
