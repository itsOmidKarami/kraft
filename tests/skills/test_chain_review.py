"""The chain-review skill: what it tells the agent that writes a
`chain_revision` change set (Kraft-oydes). Its examples are held to the parser
that reads the agent's output, so the two cannot drift apart."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kraft.templates import revision

SKILLS = Path(__file__).resolve().parents[2] / "src" / "kraft" / "skills"


@pytest.fixture(scope="module")
def text() -> str:
    return (SKILLS / "chain-review" / "SKILL.md").read_text()


def _examples(text: str) -> list[str]:
    return re.findall(r"^```json\n(.*?)\n```$", text, re.DOTALL | re.MULTILINE)


def test_the_skill_exists_and_has_frontmatter(text):
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: chain-review" in head
    assert "description:" in head


def test_its_no_change_example_is_the_empty_change_set_kraft_accepts(text):
    no_change = _examples(text)[-1]

    changes = revision.parse(f"---\nwork_item_ids: [w1]\n---\n```json\n{no_change}\n```\n")

    assert changes.empty


def test_its_full_example_uses_exactly_the_keys_kraft_reads(text):
    full = json.loads(_examples(text)[0])

    assert set(full) == set(revision.ChangeSet.model_fields)
    assert set(full["skip"][0]) == set(revision.Skip.model_fields)
    assert set(full["add"][0]) == set(revision.Add.model_fields)


def test_it_names_every_override_value_kraft_accepts_and_no_other(text):
    bullet = text[text.index("- **`overrides`**") : text.index("| Evidence")]
    named = set(re.findall(r"`([a-z_]+)`", bullet)) - {"overrides", "rationale"}

    assert named == set(revision.Override.model_fields) - {"evidence"}


def test_it_keeps_the_change_nothing_and_tail_only_rules(text):
    assert "## The default is to change nothing" in text
    assert "## The hard rule: only the tail" in text
    assert "**A gate never changes.**" in text


def test_it_speaks_no_legacy_chain_format(text):
    for legacy in (
        "hook_point",
        "registry",
        "revised_chain_nodes",
        "gate_after",
        "proposed_node_overrides",
        "chain_finalized",
    ):
        assert legacy not in text, legacy


def test_gate_review_guidance_covers_a_chain_revision_gate():
    guidance = (SKILLS / "gate-review" / "SKILL.md").read_text()

    assert "**A gate about a chain revision**" in guidance
