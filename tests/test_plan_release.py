"""The version and notes a hand-cut release computes from its merged PRs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# `dev/` is not a package; plan_release imports next_tag as a sibling script.
_DEV = Path(__file__).resolve().parents[1] / "dev"
sys.path.insert(0, str(_DEV))
_SPEC = importlib.util.spec_from_file_location("plan_release", _DEV / "plan_release.py")
plan_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plan_release)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release"


def pr(number, impact, body="", title="a title", highlight=False):
    labels = ["bug", f"release::{impact}"] + (["notes::highlight"] if highlight else [])
    return {"number": number, "title": title, "body": body, "labels": labels}


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


def test_a_trailing_attribution_line_stays_out_of_the_entry():
    body = "## Changelog\n\nFix: the thing works.\n\n\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)\n\n"
    assert plan_release.changelog_entry(pr(5, "patch", body)) == "- Fix: the thing works. (#5)"


def test_no_changelog_section_falls_back_to_the_title():
    body = "## Changelog\n\n<!-- left empty -->\n"
    assert plan_release.changelog_entry(pr(3, "patch", body, title="fix: x")) == "- fix: x (#3)"


def test_notes_group_largest_first_and_skip_none():
    notes = plan_release.release_notes(
        [pr(1, "patch", title="p"), pr(2, "none", title="n"), pr(3, "minor", title="m")]
    )
    assert notes == "### New\n\n- m (#3)\n\n### Fixes\n\n- p (#1)\n"


def test_a_highlight_leads_the_notes_and_is_not_repeated():
    prs = [pr(1, "patch", title="p"), pr(2, "minor", title="m")]
    notes = plan_release.release_notes([*prs, pr(3, "minor", title="h", highlight=True)])
    assert notes == "### Highlights\n\n- h (#3)\n\n### New\n\n- m (#2)\n\n### Fixes\n\n- p (#1)\n"


def test_highlights_keep_pr_order_whatever_their_impact():
    prs = [
        pr(3, "patch", title="fix", highlight=True),
        pr(5, "minor", title="new"),
        pr(9, "major", title="break", highlight=True),
    ]
    assert plan_release.release_notes(prs) == (
        "### Highlights\n\n- fix (#3)\n\n- break (#9)\n\n### New\n\n- new (#5)\n"
    )


def test_a_highlight_still_counts_toward_the_bump():
    prs = [pr(1, "patch"), pr(2, "minor", highlight=True)]
    assert plan_release.release_impact(prs) == "minor"


def test_a_highlighted_release_none_pr_fails_the_release_by_number():
    prs = [pr(1, "patch"), pr(8, "none", highlight=True)]
    with pytest.raises(ValueError, match="#8 has notes::highlight but release::none"):
        plan_release.release_impact(prs)
    with pytest.raises(ValueError, match="#8 has notes::highlight but release::none"):
        plan_release.release_notes(prs)


def test_no_highlight_leaves_real_notes_byte_identical(tmp_path, capsys):
    """Ten merged 1.5.0 PRs as release.yml's jq writes them, and the notes the
    release wrote for them before highlights existed."""
    notes = tmp_path / "notes.md"
    plan_release.main(["plan", "v1.4.0", str(_FIXTURES / "prs-1.5.0.json"), str(notes)])
    assert capsys.readouterr().out == "v1.5.0\n"
    assert notes.read_bytes() == (_FIXTURES / "notes-1.5.0.md").read_bytes()


def test_a_highlight_lifts_one_real_entry_out_of_new():
    prs = json.loads((_FIXTURES / "prs-1.5.0.json").read_text())
    before = plan_release.release_notes(prs)
    redesign = next(p for p in prs if p["number"] == 400)
    redesign["labels"].append("notes::highlight")
    entry = plan_release.changelog_entry(redesign)
    after = plan_release.release_notes(prs)
    assert after.startswith(f"### Highlights\n\n{entry}\n\n### New\n\n")
    assert after.count(entry) == 1
    assert after.replace(f"### Highlights\n\n{entry}\n\n", "") == before.replace(f"\n\n{entry}", "")


def test_changelog_goes_above_the_newest_section(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nPreamble.\n\n## 1.0.0\n\n- old\n")
    plan_release.write_changelog("1.1.0", "### New\n\n- new (#4)\n", path)
    assert path.read_text() == (
        "# Changelog\n\nPreamble.\n\n## 1.1.0\n\n### New\n\n- new (#4)\n\n## 1.0.0\n\n- old\n"
    )
    with pytest.raises(ValueError, match="already has"):
        plan_release.write_changelog("1.1.0", "- again\n", path)
