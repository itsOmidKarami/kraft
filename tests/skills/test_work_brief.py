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
        "What is unresolved",
    ],
    ids=["asked", "changed", "verified", "reviewed", "unresolved"],
)
def test_the_skill_asks_for_each_section_the_gate_needs(text, section):
    assert f"**{section}.**" in text


def test_the_skill_leaves_out_the_diff_and_the_final_review_brief(text):
    leaves_out = text.split("## What it leaves out", 1)[1]
    assert "**The diff.**" in leaves_out
    assert "review brief" in leaves_out
