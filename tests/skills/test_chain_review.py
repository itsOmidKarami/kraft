"""The chain-review skill: what it tells a headless agent about the chain
tail it reviews. Its revised-tail format is parked under Template Schema V1
(the skill says so); these pin the text that describes it, so the feature can
come back without relearning the bugs behind each line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL = (
    Path(__file__).resolve().parents[2] / "src" / "kraft" / "skills" / "chain-review" / "SKILL.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text()


def test_the_skill_exists_and_has_frontmatter(text):
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: chain-review" in head
    assert "description:" in head


def test_it_limits_gate_names_to_the_supplied_chain(text):
    assert "already present in the supplied chain" in text


def test_it_documents_carried_over_fields(text):
    """Kraft-eod0: the skill's schema is only 4 of a node's 8 real keys. It
    must say the other 4 (on_failure among them) are preserved for it, or a
    reviewer following the schema to the letter looks like it strips them."""
    for field in ("on_failure", "reject_to", "rebase_bounce_to", "auto_escalate"):
        assert field in text, f"carried-over field {field!r} undocumented"


def test_skill_documents_the_escalation_fields_as_settable(text):
    assert "reject_to" in text.split("Every node you write is exactly")[1][:600]
    assert "proposed_node_overrides" in text
    assert "flags" in text


def test_skill_documents_the_model_effort_dial_table(text):
    assert "proposed_node_overrides" in text
    assert "model" in text and "escalate_model" in text and "effort" in text


def test_skill_documents_permission_surface_as_flag_only(text):
    assert "flags" in text
    assert "never propose" in text.lower() or "read-only" in text.lower()
