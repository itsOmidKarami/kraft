"""Compute the tag a merge should produce, from the merge request's label.

The version comes from the git tag, so "bumping the version" is "making a tag" -
and a tag nobody makes fails silently forever. Every merge request declares its
release impact, and merging is what tags.

Usage: python3 dev/next_tag.py <previous-tag-or-empty> <labels>
Prints the tag to create, or nothing when the impact is `none`.
"""

from __future__ import annotations

import sys

PREFIX = "release::"
IMPACTS = ("major", "minor", "patch", "none")

#: Where a first release starts, per declared impact. There is no previous tag
#: to increment, so the impact names the component the project begins at.
FIRST = {"major": (1, 0, 0), "minor": (0, 1, 0), "patch": (0, 0, 1)}


def impact_from_labels(labels: str) -> str | None:
    """The declared impact, or `None` when the MR did not declare one.

    GitLab's scoped labels guarantee at most one `release::` label per MR, so
    the first match is the only match.
    """
    for label in labels.split(","):
        name = label.strip()
        if name.startswith(PREFIX) and name[len(PREFIX) :] in IMPACTS:
            return name[len(PREFIX) :]
    return None


def next_tag(previous: str | None, impact: str) -> str | None:
    """The next tag, or `None` for `none`.

    A malformed previous tag raises rather than guessing: tagging the wrong
    version is worse than a failed job somebody has to look at.
    """
    if impact not in IMPACTS:
        raise ValueError(f"unknown release impact {impact!r}; expected one of {IMPACTS}")
    if impact == "none":
        return None
    if not previous:
        major, minor, patch = FIRST[impact]
        return f"v{major}.{minor}.{patch}"
    parts = previous.lstrip("v").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"cannot read a version out of {previous!r}")
    major, minor, patch = (int(p) for p in parts)
    if impact == "major":
        major, minor, patch = major + 1, 0, 0
    elif impact == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    previous, labels = sys.argv[1] or None, sys.argv[2]
    impact = impact_from_labels(labels)
    if impact is None:
        raise SystemExit(f"no {PREFIX} label; expected one of {IMPACTS}")
    tag = next_tag(previous, impact)
    if tag:
        print(tag)
