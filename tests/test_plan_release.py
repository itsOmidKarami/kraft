"""The version and notes a hand-cut release computes from its merged PRs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# `dev/` is not a package; plan_release imports next_tag as a sibling script.
_DEV = Path(__file__).resolve().parents[1] / "dev"
sys.path.insert(0, str(_DEV))
_SPEC = importlib.util.spec_from_file_location("plan_release", _DEV / "plan_release.py")
plan_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plan_release)


def pr(number, impact, body="", title="a title"):
    return {"number": number, "title": title, "body": body, "labels": ["bug", f"release::{impact}"]}


@pytest.mark.parametrize(
    ("impacts", "expected"),
    [
        (["minor", "minor", "patch", "patch", "patch"], "minor"),
        (["patch", "none"], "patch"),
        (["none", "major", "patch"], "major"),
        (["none", "none"], "none"),
        ([], "none"),
    ],
)
def test_the_largest_impact_wins(impacts, expected):
    assert plan_release.release_impact([pr(i, x) for i, x in enumerate(impacts)]) == expected


def test_an_unlabelled_pr_fails_the_release_by_number():
    with pytest.raises(ValueError, match="#7 has no release:: label"):
        unlabelled = {"number": 7, "title": "t", "body": "", "labels": []}
        plan_release.release_impact([pr(1, "patch"), unlabelled])


def test_changelog_section_is_the_entry():
    body = (
        "Why.\n\n## Changelog\n\n<!-- hint -->\nFix: the thing works.\n\n## Release impact\n\nstuff"
    )
    assert plan_release.changelog_entry(pr(12, "patch", body)) == "- Fix: the thing works. (#12)"


def test_no_changelog_section_falls_back_to_the_title():
    body = "## Changelog\n\n<!-- left empty -->\n"
    assert plan_release.changelog_entry(pr(3, "patch", body, title="fix: x")) == "- fix: x (#3)"


def test_notes_group_largest_first_and_skip_none():
    notes = plan_release.release_notes(
        [pr(1, "patch", title="p"), pr(2, "none", title="n"), pr(3, "minor", title="m")]
    )
    assert notes == "### New\n\n- m (#3)\n\n### Fixes\n\n- p (#1)\n"


def test_changelog_goes_above_the_newest_section(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nPreamble.\n\n## 1.0.0\n\n- old\n")
    plan_release.write_changelog("1.1.0", "### New\n\n- new (#4)\n", path)
    assert path.read_text() == (
        "# Changelog\n\nPreamble.\n\n## 1.1.0\n\n### New\n\n- new (#4)\n\n## 1.0.0\n\n- old\n"
    )
    with pytest.raises(ValueError, match="already has"):
        plan_release.write_changelog("1.1.0", "- again\n", path)
