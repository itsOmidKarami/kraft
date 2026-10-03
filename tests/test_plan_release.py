"""The version and notes a hand-cut release computes from its merged PRs."""

from __future__ import annotations

import importlib.util
import json
import re
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


def test_every_bullet_of_a_group_carries_the_prs_number():
    """R10h-06: a ten-bullet group put `(#484)` on its last bullet alone. Each
    bullet at the margin gets it, at the end of its own text: after a wrapped
    line, ahead of a nested list."""
    body = (
        "## Changelog\n\n"
        "- Restart exits 1\n  with no server.\n"
        "- Disconnect is refused:\n"
        "  - while an item is open;\n"
        "  - paused ones included.\n"
        "* Doctor warns.\n"
    )
    assert plan_release.changelog_entry(pr(484, "patch", body)) == (
        "- Restart exits 1\n  with no server. (#484)\n"
        "- Disconnect is refused: (#484)\n"
        "  - while an item is open;\n"
        "  - paused ones included.\n"
        "* Doctor warns. (#484)"
    )


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
    assert plan_release.release_notes(prs).startswith(
        "### Highlights\n\n- fix (#3)\n\n- break (#9)\n\n### Breaking changes\n\n"
    )


def test_a_highlighted_major_stays_under_breaking_changes_too():
    prs = [pr(2, "major", title="old"), pr(9, "major", title="break", highlight=True)]
    assert plan_release.release_notes([*prs, pr(11, "patch", title="fix", highlight=True)]) == (
        "### Highlights\n\n- break (#9)\n\n- fix (#11)\n\n"
        "### Breaking changes\n\n- old (#2)\n\n- break (#9)\n"
    )


@pytest.mark.parametrize("label", ["Notes::Highlight", "notes::highlight ", "notes::higlight"])
def test_a_near_miss_notes_label_fails_the_release_by_number(label):
    near_miss = pr(4, "minor")
    near_miss["labels"].append(label)
    with pytest.raises(ValueError, match=f"#4 has the label {re.escape(repr(label))}"):
        plan_release.release_impact([pr(1, "patch"), near_miss])


def test_an_unlabelled_highlight_is_told_to_take_a_shipping_label():
    unlabelled = {"number": 7, "title": "t", "body": "", "labels": ["notes::highlight"]}
    with pytest.raises(ValueError, match="#7 has no release:: label.*not release::none"):
        plan_release.release_impact([unlabelled])


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
    """Ten 1.5.0 PRs in the shape release.yml's jq writes, their bodies abridged
    around a verbatim `## Changelog`, and the notes the pre-highlight code writes
    (since R10h-06, with the PR's number on every bullet of a group)."""
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


_PRE = "This is a pre-release. Install it with"


@pytest.mark.parametrize(
    ("tag", "line"),
    [
        (
            "v1.5.0rc2",
            f"{_PRE} `kraft admin update --channel rc`, "
            'or `uv tool install --force "kraft-sdlc==1.5.0rc2"`.',
        ),
        (
            "v1.5.0b1",
            f"{_PRE} `kraft admin update --channel beta`, "
            "or `uv tool install --force` the wheel attached below.",
        ),
        (
            "v1.5.0a3",
            f"{_PRE} `kraft admin update --channel alpha`, "
            "or `uv tool install --force` the wheel attached below.",
        ),
    ],
)
def test_a_pre_release_says_how_to_install_it_above_its_notes(tag, line):
    """Only an rc is on PyPI; a beta or alpha points at its release's own wheel."""
    notes = "### Highlights\n\n- the headline (#1)\n\n### Fixes\n\n- a fix (#2)\n"
    assert plan_release.release_body(tag, notes) == f"{line}\n\n{notes}"


def test_every_pre_release_channel_is_one_update_accepts():
    from kraft.update import CHANNELS

    for kind, mark in plan_release.PRE_MARKS.items():
        body = plan_release.release_body(f"v1.5.0{mark}1", "- x (#1)\n")
        assert f"`kraft admin update --channel {kind}`" in body
        assert kind in CHANNELS


@pytest.mark.parametrize("tag", ["v1.5.0", "1.5.0", "", "v1.5.0rc", "v1.5.0.rc1"])
def test_a_stable_release_body_is_its_notes_byte_for_byte(tmp_path, tag):
    """The real 1.5.0 notes, through the command release.yml runs."""
    notes, body = _FIXTURES / "notes-1.5.0.md", tmp_path / "body.md"
    plan_release.main(["body", tag, str(notes), str(body)])
    assert body.read_bytes() == notes.read_bytes()


def test_the_release_is_created_from_the_body_and_the_changelogs_from_the_notes():
    text = (_DEV.parent / ".github" / "workflows" / "release.yml").read_text()
    body = 'plan_release.py" body "$TAG" "$RUNNER_TEMP/notes.md" "$RUNNER_TEMP/body.md"'
    # After the pre-release tag is numbered, and before the dry run blanks it.
    assert text.index('plan_release.py" pre "$TAG"') < text.index(body)
    assert text.index(body) < text.index('cat "$RUNNER_TEMP/body.md"') < text.index("TAG=\n")
    assert '--notes-file "$RUNNER_TEMP/body.md"' in text
    assert text.count('"$RUNNER_TEMP/notes.md" CHANGELOG.md') == 1
    assert 'changelog "$VERSION" "$RUNNER_TEMP/notes.md"\n' in text


def test_changelog_goes_above_the_newest_section(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nPreamble.\n\n## 1.0.0\n\n- old\n")
    plan_release.write_changelog("1.1.0", "### New\n\n- new (#4)\n", path)
    assert path.read_text() == (
        "# Changelog\n\nPreamble.\n\n## 1.1.0\n\n### New\n\n- new (#4)\n\n## 1.0.0\n\n- old\n"
    )
    with pytest.raises(ValueError, match="already has"):
        plan_release.write_changelog("1.1.0", "- again\n", path)


def test_a_changelog_with_no_section_yet_gets_its_first(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nPreamble.\n")
    plan_release.write_changelog("1.1.0", "- new (#4)\n", path)
    assert path.read_text() == "# Changelog\n\nPreamble.\n\n## 1.1.0\n\n- new (#4)\n"
    plan_release.write_changelog("1.2.0", "- newer (#5)\n", path)
    assert path.read_text() == (
        "# Changelog\n\nPreamble.\n\n## 1.2.0\n\n- newer (#5)\n\n## 1.1.0\n\n- new (#4)\n"
    )


def test_the_changelog_command_writes_the_file_it_is_given(tmp_path):
    notes, extension = tmp_path / "notes.md", tmp_path / "vscode-CHANGELOG.md"
    notes.write_text("### Fixes\n\n- fixed (#9)\n")
    extension.write_text("# Changelog\n\nPreamble.\n")
    repository = plan_release.CHANGELOG.read_text()
    plan_release.main(["changelog", "9.9.9", str(notes), str(extension)])
    assert (
        extension.read_text()
        == "# Changelog\n\nPreamble.\n\n## 9.9.9\n\n### Fixes\n\n- fixed (#9)\n"
    )
    assert plan_release.CHANGELOG.read_text() == repository


def test_the_extension_changelog_takes_the_release_notes_the_way_the_root_one_does(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "CHANGELOG.md"
    path.write_text((root / "vscode" / "CHANGELOG.md").read_text())
    plan_release.write_changelog("1.5.0", "### New\n\n- new (#4)\n", path)
    text = path.read_text()
    assert text.startswith("# Changelog\n\nNotable changes to the Kraft VS Code extension")
    assert text.endswith("\n\n## 1.5.0\n\n### New\n\n- new (#4)\n")
    assert "do not\nedit this file by hand" in text


def test_a_stable_release_body_keeps_crlf_notes_byte_for_byte(tmp_path):
    """A PR body written with CRLF line ends reaches the notes as CRLF."""
    notes, body = tmp_path / "notes.md", tmp_path / "body.md"
    notes.write_bytes(b"### Fixes\r\n\r\n- a fix (#2)\r\n")
    plan_release.main(["body", "v1.5.0", str(notes), str(body)])
    assert body.read_bytes() == notes.read_bytes()
