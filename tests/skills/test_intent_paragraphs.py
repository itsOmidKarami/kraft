"""The intent process rides four shipped methods, each one paragraph that is
inert unless the launch context names a tree (intent-process design §5)."""

import pytest

from kraft import skill
from kraft.adapters import agent

TRIGGER = "If your instructions name an intent tree"
SKILLS = ["spec", "plan", "code-review", "work-brief"]


def _paragraph(name: str) -> str:
    text = skill.read(None, name)
    return text[text.index(TRIGGER) :].split("\n\n")[0]


@pytest.mark.parametrize("name", SKILLS)
def test_the_skill_carries_the_intent_paragraph(name):
    assert TRIGGER in skill.read(None, name)


@pytest.mark.parametrize("name", SKILLS)
def test_the_skill_leaves_the_format_to_the_readme(name):
    assert not any(line.startswith("## REQ") for line in skill.read(None, name).splitlines())


def test_the_code_review_intent_cases_are_important():
    assert "`important`" in _paragraph("code-review")


def test_the_trigger_names_what_the_context_block_is_called():
    assert "intent tree" in agent.INTENT_HEADING.lower()
