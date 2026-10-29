"""The review-feedback skill is what a cheap agent reads to decide which of the
review bots' comments need a change. It runs with no person to ask, writes
nothing to the merge request, and answers `done` however it decides; the
fix loop acts on what it reports, so the contract with that loop is pinned."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "src" / "kraft" / "skills" / "review-feedback" / "SKILL.md"
LIBRARY = ROOT / "config" / "library.yaml"


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text()


def test_the_skill_exists_and_has_frontmatter(text):
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: review-feedback" in head
    assert "description:" in head


@pytest.mark.parametrize(
    "phrase",
    [
        "$KRAFT_BOT_FEEDBACK",
        "each comment",
        "never reply to, resolve or edit anything on the merge request",
        "never ask a question",
        'Set `status` to `"done"` whatever you decide',
        "source_plugin",
        "previous_review",
        "points at a real defect is a finding",
        "is one finding, and your summary says it was a duplicate",
        "judged like a comment, from its `output`",
    ],
    ids=[
        "reads-the-collection",
        "decides-per-comment",
        "never-writes-to-the-merge-request",
        "never-asks",
        "done-whatever-it-decides",
        "finding-keys",
        "declined-last-round",
        "a-real-defect-is-a-finding-even-when-the-fix-takes-a-choice",
        "a-duplicate-is-one-finding",
        "a-failed-check-is-judged-like-a-comment",
    ],
)
def test_the_skill_states_its_contract(text, phrase):
    assert phrase in text


def test_it_lists_what_it_declined_in_the_summary(text):
    assert "summary" in text and "declined" in text


def test_the_seeded_triage_task_selects_this_skill_and_reads_the_feedback():
    task = yaml.safe_load(LIBRARY.read_text())["tasks"]["triage_review_feedback"]
    assert task["skill"] == "kraft:review-feedback"
    assert task["inputs"] == ["bot_feedback", "previous_review", "pr_rules"]
    assert task["profile"] == "fast"


def test_the_review_brief_is_told_to_report_what_the_triage_declined():
    brief = (SKILL.parent.parent / "review-brief" / "SKILL.md").read_text()
    assert "Review-bot feedback not acted on" in brief
    task = yaml.safe_load(LIBRARY.read_text())["tasks"]["write_summary"]
    assert task["inputs"] == ["triage_verdict"]
