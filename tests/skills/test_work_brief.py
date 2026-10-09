"""The work-brief skill writes what the pre-draft gate shows (Ruling 87). The
one line the gate's own message does not carry is what approving does: it
publishes the work as a draft merge request and starts CI, and a human who
does not know that may approve casually."""

from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / "src" / "kraft" / "skills" / "work-brief" / "SKILL.md"


@pytest.fixture(scope="module")
def text() -> str:
    """The skill with its line wrapping flattened, so a phrase is found
    wherever the prose happens to break."""
    return " ".join(SKILL.read_text().split())


def test_the_skill_says_what_approving_does(text):
    assert "**What approving does.**" in text
    assert "opens a draft merge request on the forge and starts CI**" in text


@pytest.mark.parametrize(
    "section",
    [
        "What was asked",
        "What changed",
        "What was verified",
        "What the review found, and what was done about it",
        "What was deliberately left out",
        "What you are unsure about",
    ],
    ids=["asked", "changed", "verified", "reviewed", "left-out", "unsure"],
)
def test_the_skill_asks_for_each_section_the_gate_needs(text, section):
    assert f"**{section}.**" in text


@pytest.mark.parametrize(
    "instruction",
    [
        "When you found none of them, the brief is the four sections above. A line works the "
        "same way: write what happened, and give no line to what you looked for and did not find",
        "If a check did not run in it, name that check as not run",
        "`kraft view events --json`",
    ],
    ids=["omits-what-was-not-found", "names-a-check-not-run", "reads-events-as-json"],
)
def test_the_skill_carries_the_wording_its_briefs_were_tested_with(text, instruction):
    """Each of these changed what authors wrote when the wording was run on real
    events. Without the first a clean run grows sections that say "nothing"; the
    second is what names a skipped review; without the third every payload is cut
    short before the fields the brief needs."""
    assert instruction in text


def test_the_skill_leaves_out_the_diff_and_the_final_review_brief(text):
    leaves_out = text.split("## What it leaves out", 1)[1]
    assert "**The diff.**" in leaves_out
    assert "review brief" in leaves_out


def test_the_skill_sizes_the_change_with_git_not_kraft(text):
    """A sandboxed worker's `kraft` has no `view diff` (D16); git works anywhere."""
    assert "kraft view diff" not in text and "git diff --stat" in text
