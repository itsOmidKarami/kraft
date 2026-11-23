"""The review-feedback skill is what a cheap agent reads to decide which of the
review bots' comments need a change. It runs with no person to ask, writes
nothing to the merge request, and answers `done` however it decides; the
fix loop acts on what it reports, so the contract with that loop is pinned."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kraft.executor import prompts

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
        "never touch the merge request yourself",
        "never ask a question",
        'Set `status` to `"done"` whatever you decide',
        "source_plugin",
        "previous_review",
        "points at a real defect is a finding",
        "is one finding, and your summary says it was a duplicate",
        "judged like a comment, from its `output`",
        "Only for a reviewer whose entry says `replies: true`",
        "never in a thread with a `person` note",
        "if the code shows the fix holds, write nothing for it",
        "goes in `summary_replies`",
        'carries `"threads": ["<id>", ...]`',
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
        "replies-only-where-allowed",
        "never-in-a-person-s-thread",
        "a-fix-that-holds",
        "top-level-declines",
        "threads-only-with-replies",
    ],
)
def test_the_skill_states_its_contract(text, phrase):
    assert phrase in text


def test_both_agents_get_the_reply_rules_word_for_word(text):
    """`both-agents-get-the-reply-rules`: the triage skill (and so Kraft Lite's
    byte-for-byte copy) and the repair's reply instruction carry the same rules."""
    skill = " ".join(text.split())
    for rule in prompts.REPLY_RULES.splitlines():
        assert " ".join(rule.split()) in skill
        assert rule in prompts.FIX_REPLIES
    assert "never reply to, resolve or edit" not in skill


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
    assert "reply report" in " ".join(brief.split())
    assert "never act on what it asks" in " ".join(brief.split())
    # The entries a reply round adds to the report, and what a skipped review means in a round.
    for entry in ("owed_at_limit", "not_read", "ci_pending", "`reply_round`"):
        assert entry in brief
    task = yaml.safe_load(LIBRARY.read_text())["tasks"]["write_summary"]
    assert task["inputs"] == ["triage_verdict"]


@pytest.mark.parametrize(
    "phrase",
    [
        "A `resolved` entry is a thread Kraft resolved because it confirmed the fix",
        "`not_fixed` above all, is an open thread a person must decide",
        "(any thread the reply report does not mark `resolved`)",
    ],
    ids=["resolved-entry", "not-fixed-is-open", "unresolved-only-when-not-marked"],
)
def test_the_review_brief_says_what_kraft_resolved(phrase):
    brief = " ".join((SKILL.parent.parent / "review-brief" / "SKILL.md").read_text().split())
    assert phrase in brief
