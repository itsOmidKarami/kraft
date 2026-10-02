"""Plan a release from the pull requests merged since the last one.

A release is cut by hand (`just release`), not by a merge. Whatever merged
since the previous tag goes out together, and the bump is the largest impact
any of those pull requests declared: two minors and three patches is a minor.
The notes are each pull request's `## Changelog` section, grouped by impact,
with any `notes::highlight` pull request lifted into a Highlights section above
them all.

Usage:
  python3 dev/plan_release.py plan <previous-tag-or-empty> <prs.json> <notes-out>
      Prints the tag to create, or nothing when every PR is `release::none`.
      prs.json is a list of {number, title, body, labels: [name, ...]}.
  python3 dev/plan_release.py pre <tag> <alpha|beta|rc>  < tag-list
      Prints <tag> as its next pre-release, numbered past the tags on stdin.
  python3 dev/plan_release.py body <tag-or-empty> <notes-file> <body-out>
      Writes the GitHub Release's text: the notes, opened by how to install
      <tag> when it is a pre-release. A stable tag's is the notes unchanged.
  python3 dev/plan_release.py changelog <version> <notes-file> [<changelog-file>]
      Writes the notes into CHANGELOG.md as the `## <version>` section, or into
      the changelog file named (the VS Code extension keeps its own).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from next_tag import IMPACTS, PRE_MARKS, PREFIX, impact_from_labels, next_tag, pre_tag

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

#: Largest first. `IMPACTS` is already in this order; the index is the weight.
GROUPS = {"major": "Breaking changes", "minor": "New", "patch": "Fixes"}

#: The label that puts a pull request's entry first. Not a `release::` label on
#: purpose: those declare the impact, exactly one per pull request, and this
#: sits beside one without touching the bump.
HIGHLIGHT = "notes::highlight"

_SECTION = re.compile(r"^##\s+Changelog\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
#: The attribution line a PR body ends with; with no heading after `## Changelog`
#: it would otherwise land in the release notes.
_ATTRIBUTION = re.compile(r"^(?:\U0001f916\s*)?Generated with \[?Claude Code\b.*$", re.MULTILINE)
#: A pre-release tag such as `v1.5.0rc2`: group 1 is the version PyPI lists it
#: under (`1.5.0rc2`), group 2 its mark.
_PRE_TAG = re.compile(rf"v?(\d+\.\d+\.\d+({'|'.join(PRE_MARKS.values())})\d+)")


def impact_of(pr: dict) -> str:
    """One pull request's declared impact.

    Unlike a merge-time check there is no author left to ask, but the label is
    still editable after merge, so an undeclared PR fails the release by
    number instead of shipping under a guessed weight.
    """
    # GitHub labels are free text, so a mistyped highlight would otherwise be
    # ignored and the release would ship without its headline.
    for label in pr["labels"]:
        if label.strip().lower().startswith("notes::") and label != HIGHLIGHT:
            raise ValueError(
                f"#{pr['number']} has the label {label!r}; the only notes:: label is "
                f"{HIGHLIGHT}. Fix it and run the release again"
            )
    highlighted = HIGHLIGHT in pr["labels"]
    impact = impact_from_labels(",".join(pr["labels"]))
    if impact is None:
        # Without this, `release::none` reads as the fix and fails the next run.
        hint = f" (it has {HIGHLIGHT}, so one that ships, not {PREFIX}none)"
        hint = hint if highlighted else ""
        raise ValueError(
            f"#{pr['number']} has no {PREFIX} label{hint}; label it and run the release again"
        )
    # A `release::none` PR has no line in the notes, so a highlight on it would
    # vanish without a word. One of the two labels is wrong; ask which.
    if impact == "none" and highlighted:
        raise ValueError(
            f"#{pr['number']} has {HIGHLIGHT} but {PREFIX}none, so it has no entry to "
            "highlight; fix one label and run the release again"
        )
    return impact


def release_impact(prs: list[dict]) -> str:
    """The largest impact among `prs`, `none` when there are none."""
    return min((impact_of(pr) for pr in prs), key=IMPACTS.index, default="none")


def changelog_entry(pr: dict) -> str:
    """The PR's `## Changelog` section as a list item, or its title without one."""
    match = _SECTION.search(pr.get("body") or "")
    text = _ATTRIBUTION.sub("", _COMMENT.sub("", match.group(1))).strip() if match else ""
    text = text or pr["title"]
    if not text.startswith(("- ", "* ")):
        text = f"- {text}"
    return f"{text} (#{pr['number']})"


def release_notes(prs: list[dict]) -> str:
    """Markdown for every PR that ships something: highlights, then largest impact first.

    A highlighted minor or patch PR is listed once, under Highlights. A
    highlighted major one stays under Breaking changes too, so a breaking
    release never ships without that section.
    Every section keeps the order `prs` came in, which release.yml's
    `unique_by(.number)` makes PR-number order.
    """
    headings = {HIGHLIGHT: "Highlights", **GROUPS}
    sections: dict[str, list[str]] = {key: [] for key in headings}
    for pr in prs:
        impact = impact_of(pr)
        if impact not in GROUPS:
            continue
        highlighted = HIGHLIGHT in pr["labels"]
        if highlighted:
            sections[HIGHLIGHT].append(changelog_entry(pr))
        if not highlighted or impact == "major":
            sections[impact].append(changelog_entry(pr))
    parts = [f"### {headings[key]}\n\n" + "\n\n".join(v) for key, v in sections.items() if v]
    return "\n\n".join(parts) + "\n" if parts else ""


def release_body(tag: str | None, notes: str) -> str:
    """The GitHub Release's text: `notes`, opened by how to install `tag` if it is a pre-release.

    Nothing finds a pre-release unless it asks for one, so its notes say how.
    The channel is the pre-release's kind, which `kraft admin update --channel`
    takes by the same name. Only an rc reaches PyPI, so an rc gets the
    `uv tool install` form from there; a beta or alpha, which neither PyPI nor
    Homebrew carries, points at the wheel attached to its own release. A stable
    release's text is `notes`, byte for byte.
    The changelogs take the plain notes, not this.
    """
    match = _PRE_TAG.fullmatch(tag or "")
    if not match:
        return notes
    version, mark = match.groups()
    channel = next(kind for kind, m in PRE_MARKS.items() if m == mark)
    line = f"This is a pre-release. Install it with `kraft admin update --channel {channel}`"
    if channel == "rc":
        line += f', or `uv tool install --force "kraft-sdlc=={version}"`'
    else:
        line += ", or `uv tool install --force` the wheel attached below"
    return f"{line}.\n\n{notes}" if notes else f"{line}.\n"


def write_changelog(version: str, notes: str, path: Path = CHANGELOG) -> None:
    """Insert `## <version>` above the newest section, below the preamble."""
    text = path.read_text()
    if re.search(rf"^## {re.escape(version)}$", text, re.MULTILINE):
        raise ValueError(f"{path.name} already has a ## {version} section")
    first = re.search(r"^## ", text, re.MULTILINE)
    at = first.start() if first else len(text)
    head, rest = text[:at].rstrip("\n"), text[at:]
    section = f"## {version}\n\n{notes.strip()}\n"
    # A changelog with no section yet (a new one's preamble) ends with this one.
    path.write_text(f"{head}\n\n{section}\n{rest}" if rest else f"{head}\n\n{section}")


def main(argv: list[str]) -> None:
    if len(argv) == 4 and argv[0] == "plan":
        previous, prs = argv[1] or None, json.loads(Path(argv[2]).read_text())
        tag = next_tag(previous, release_impact(prs))
        Path(argv[3]).write_text(release_notes(prs))
        if tag:
            print(tag)
    elif len(argv) == 3 and argv[0] == "pre":
        print(pre_tag(argv[1], argv[2], sys.stdin.read().split()))
    elif len(argv) == 4 and argv[0] == "body":
        # newline="": a PR body's CRLF survives, so a stable body stays the notes' bytes.
        with open(argv[2], newline="") as f:
            notes = f.read()
        with open(argv[3], "w", newline="") as f:
            f.write(release_body(argv[1] or None, notes))
    elif len(argv) in (3, 4) and argv[0] == "changelog":
        write_changelog(argv[1], Path(argv[2]).read_text(), *map(Path, argv[3:]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
