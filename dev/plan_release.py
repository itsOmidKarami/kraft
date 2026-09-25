"""Plan a release from the pull requests merged since the last one.

A release is cut by hand (`just release`), not by a merge. Whatever merged
since the previous tag goes out together, and the bump is the largest impact
any of those pull requests declared: two minors and three patches is a minor.
The notes are each pull request's `## Changelog` section, grouped by impact.

Usage:
  python3 dev/plan_release.py plan <previous-tag-or-empty> <prs.json> <notes-out>
      Prints the tag to create, or nothing when every PR is `release::none`.
      prs.json is a list of {number, title, body, labels: [name, ...]}.
  python3 dev/plan_release.py pre <tag> <alpha|beta|rc>  < tag-list
      Prints <tag> as its next pre-release, numbered past the tags on stdin.
  python3 dev/plan_release.py changelog <version> <notes-file>
      Writes the notes into CHANGELOG.md as the `## <version>` section.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from next_tag import IMPACTS, PREFIX, impact_from_labels, next_tag, pre_tag

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

#: Largest first. `IMPACTS` is already in this order; the index is the weight.
GROUPS = {"major": "Breaking changes", "minor": "New", "patch": "Fixes"}

_SECTION = re.compile(r"^##\s+Changelog\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def impact_of(pr: dict) -> str:
    """One pull request's declared impact.

    Unlike a merge-time check there is no author left to ask, but the label is
    still editable after merge, so an undeclared PR fails the release by
    number instead of shipping under a guessed weight.
    """
    impact = impact_from_labels(",".join(pr["labels"]))
    if impact is None:
        raise ValueError(
            f"#{pr['number']} has no {PREFIX} label; label it and run the release again"
        )
    return impact


def release_impact(prs: list[dict]) -> str:
    """The largest impact among `prs`, `none` when there are none."""
    return min((impact_of(pr) for pr in prs), key=IMPACTS.index, default="none")


def changelog_entry(pr: dict) -> str:
    """The PR's `## Changelog` section as a list item, or its title without one."""
    match = _SECTION.search(pr.get("body") or "")
    text = _COMMENT.sub("", match.group(1)).strip() if match else ""
    text = text or pr["title"]
    if not text.startswith(("- ", "* ")):
        text = f"- {text}"
    return f"{text} (#{pr['number']})"


def release_notes(prs: list[dict]) -> str:
    """Markdown for every PR that ships something, largest impact first."""
    parts = []
    for impact, heading in GROUPS.items():
        entries = [changelog_entry(pr) for pr in prs if impact_of(pr) == impact]
        if entries:
            parts.append(f"### {heading}\n\n" + "\n\n".join(entries))
    return "\n\n".join(parts) + "\n" if parts else ""


def write_changelog(version: str, notes: str, path: Path = CHANGELOG) -> None:
    """Insert `## <version>` above the newest section, below the preamble."""
    text = path.read_text()
    if re.search(rf"^## {re.escape(version)}$", text, re.MULTILINE):
        raise ValueError(f"{path.name} already has a ## {version} section")
    section = f"## {version}\n\n{notes.strip()}\n\n"
    first = re.search(r"^## ", text, re.MULTILINE)
    at = first.start() if first else len(text)
    path.write_text(text[:at] + section + text[at:])


def main(argv: list[str]) -> None:
    if len(argv) == 4 and argv[0] == "plan":
        previous, prs = argv[1] or None, json.loads(Path(argv[2]).read_text())
        tag = next_tag(previous, release_impact(prs))
        Path(argv[3]).write_text(release_notes(prs))
        if tag:
            print(tag)
    elif len(argv) == 3 and argv[0] == "pre":
        print(pre_tag(argv[1], argv[2], sys.stdin.read().split()))
    elif len(argv) == 3 and argv[0] == "changelog":
        write_changelog(argv[1], Path(argv[2]).read_text())
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
