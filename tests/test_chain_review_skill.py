"""The chain-review skill tells a headless agent which gate names and hook
points are legal. Those two sets live in code (`templates.GATE_NAMES`) and in
config (`templates/registry.yaml`). If they drift apart, the skill teaches the
agent to emit a chain the validator rejects — and nothing else would catch it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kraft.templates import GATE_NAMES, load_registry

SKILL = Path(__file__).parent.parent / "src" / "kraft" / "skills" / "chain-review" / "SKILL.md"
REGISTRY = Path(__file__).parent.parent / "templates" / "registry.yaml"


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text()


def test_the_skill_exists_and_has_frontmatter(text):
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: chain-review" in head
    assert "description:" in head


def test_every_gate_it_names_is_a_real_gate(text):
    named = set(re.findall(r"`(\w*(?:approval|finalized))`", text))
    assert named, "skill names no gates at all"
    assert named <= GATE_NAMES, f"skill names gates the validator rejects: {named - GATE_NAMES}"


def test_it_names_the_whole_gate_set(text):
    """It claims 'these four are the entire set' — so it must list all four."""
    missing = {g for g in GATE_NAMES if f"`{g}`" not in text}
    assert not missing, f"gate set grew; skill does not mention {missing}"


def test_every_hook_point_it_cites_is_registered(text):
    cited = set(re.findall(r"`(on\.[\w.]+)`", text))
    assert cited, "skill cites no hook points"
    known = set(load_registry(REGISTRY).hooks)
    assert cited <= known, f"skill cites unregistered hooks: {sorted(cited - known)}"


def test_it_states_the_node_schema_fields(text):
    for field in ("id", "tasks", "gate_after", "fix_loop"):
        assert f"{field}" in text, f"node schema field {field!r} undocumented"
