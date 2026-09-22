"""The code-review skill tells a headless agent what to write and at what
severity. The severity names live in code (`findings.SEVERITIES`) and the
subset that burns a fix cycle lives in policy (`policy.DEFAULT_LOOP_SEVERITIES`).
If the skill drifts from either, it teaches the agent to emit findings the
loop silently ignores, or to spend a fix cycle on a nitpick.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from kraft.findings import SEVERITIES
from kraft.policy import DEFAULT_LOOP_SEVERITIES

SKILL = (
    Path(__file__).resolve().parents[2] / "src" / "kraft" / "skills" / "code-review" / "SKILL.md"
)
LIBRARY = Path(__file__).resolve().parents[2] / "templates" / "library.yaml"


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text()


def test_the_skill_exists_and_has_frontmatter(text):
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: code-review" in head
    assert "description:" in head


def test_it_names_every_severity(text):
    """The agent picks a severity per finding; a level it was never told about
    is a level it will not use, or will invent."""
    missing = {s for s in SEVERITIES if f"`{s}`" not in text}
    assert not missing, f"skill does not mention severities {missing}"


def test_it_says_which_severities_burn_a_fix_cycle(text):
    """`policy.loop_severities` decides what re-runs implementation. A skill
    that does not say so teaches the agent that severity is cosmetic."""
    for s in DEFAULT_LOOP_SEVERITIES:
        assert f"`{s}`" in text
    assert "fix cycle" in text.lower()


def test_it_documents_the_result_file_keys(text):
    """The findings parser drops any finding missing one of these
    (`findings._one`), silently — so the skill has to name all three."""
    for key in ("severity", "message", "source_plugin"):
        assert f"`{key}`" in text


def test_the_seeded_code_review_task_selects_this_skill():
    """V1 selects a skill by name from a task (`skill: kraft:code-review`); the
    seeded in-loop reviewer is the task that does."""
    tasks = yaml.safe_load(LIBRARY.read_text())["tasks"]
    assert tasks["code_review"]["skill"] == "kraft:code-review"


def test_it_names_no_legacy_hook_point(text):
    """V1 has no hook points; an `on.x.y` name in the method text points the
    agent at a chain shape that no longer exists (Ruling 72)."""
    assert re.findall(r"`(on\.[\w.]+)`", text) == []
