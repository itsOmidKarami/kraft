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

SKILL = Path(__file__).parent.parent / "src" / "kraft" / "skills" / "code-review" / "SKILL.md"
REGISTRY = Path(__file__).parent.parent / "templates" / "registry.yaml"


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


def test_the_hook_is_bound_to_this_skill():
    hooks = yaml.safe_load(REGISTRY.read_text())["hooks"]
    assert hooks["on.review.local.run"] == {
        "kind": "agent",
        "command": "claude",
        "skill": "code-review",
        "steering": ["never-signal-processes-you-didnt-start"],
    }


def test_the_mr_hook_is_left_alone():
    """Kraft-7eqc reframes on.review.mr.run as reading MR comments; it must not
    be rebound to this skill by a well-meaning edit."""
    hooks = yaml.safe_load(REGISTRY.read_text())["hooks"]
    assert hooks["on.review.mr.run"] == {"kind": "builtin", "handler": "noop"}


def test_every_hook_point_it_cites_is_registered(text):
    cited = set(re.findall(r"`(on\.[\w.]+)`", text))
    registered = set(yaml.safe_load(REGISTRY.read_text())["hooks"])
    assert cited <= registered, f"skill cites unregistered hooks: {cited - registered}"
