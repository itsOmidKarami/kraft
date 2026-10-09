"""The event-type vocabulary: nine families, 110 types, three shared groups."""

from __future__ import annotations

import ast
import re
from enum import StrEnum
from pathlib import Path

import pytest

from kraft.vocab import (
    BY_VALUE,
    EVENT_TRAITS,
    EVENT_TYPES,
    FAMILIES,
    GATE_CLOSED,
    RESTARTS_RUN,
    RUN_BOUNDARY,
    ChainEvent,
    EscalationEvent,
    ForgeEvent,
    GateEvent,
    LimitEvent,
    SandboxEvent,
    SessionEvent,
    SettingsEvent,
    WorkItemEvent,
)
from kraft.vocab.events import by_value

ROOT = Path(__file__).resolve().parents[2]

#: The docs page's `##` heading for each family, and the prefix its member
#: names drop.
SECTIONS = {
    WorkItemEvent: ("Work item", "work_item_"),
    ChainEvent: ("Chain and nodes", ""),
    GateEvent: ("Gates and review", "gate_"),
    SessionEvent: ("Agent sessions", ""),
    LimitEvent: ("Caps, budgets and limits", ""),
    EscalationEvent: ("Escalation", ""),
    ForgeEvent: ("Forge, CI and waits", ""),
    SandboxEvent: ("Sandbox", "sandbox_"),
    SettingsEvent: ("Settings changed", ""),
}


def test_the_families_are_the_nine_in_docs_order_and_hold_every_type():
    assert FAMILIES == tuple(SECTIONS)
    assert [len(f) for f in FAMILIES] == [26, 21, 18, 9, 9, 7, 13, 4, 6]
    assert EVENT_TYPES == tuple(m for f in FAMILIES for m in f)
    assert len(BY_VALUE) == len(EVENT_TYPES) == 113
    assert list(EVENT_TRAITS) == list(EVENT_TYPES)


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.__name__)
def test_a_member_is_named_after_its_value(family):
    prefix = SECTIONS[family][1]
    for member in family:
        assert member.name == member.value.removeprefix(prefix).upper()


def test_two_families_cannot_share_a_value():
    class A(StrEnum):
        X = "same"

    class B(StrEnum):
        Y = "same"

    with pytest.raises(ValueError, match=r"A\.X and B\.Y"):
        by_value((A, B))


def test_the_shared_groups_are_todays_sets():
    assert {m.value for m in RUN_BOUNDARY} == {
        "work_item_retried",
        "work_item_resumed",
        "work_item_created",
        "pause_requested",
        "gate_approved",
        "gate_rejected",
        "work_item_completed",
        "work_item_abandoned",
        "work_item_restored",
    }
    assert {m.value for m in GATE_CLOSED} == {
        "gate_approved",
        "gate_rejected",
        "node_skipped",
        "work_item_completed",
        "work_item_abandoned",
    }
    assert {m.value for m in RESTARTS_RUN} == {
        "work_item_retried",
        "run_forked",
        "base_change_restart",
    }


def _documented(heading: str) -> list[str]:
    """The types in the first table under `## heading`: its header must be
    `Type | When | Key fields`, and a `###` ends the section's own table (the
    Work item section goes on with tables of payload fields and stop kinds)."""
    text = (ROOT / "docsite/content/5.reference/10.events.md").read_text()
    body = re.split(r"^## ", text, flags=re.M)
    section = next(s for s in body if s.split("\n", 1)[0].strip() == heading)
    types: list[str] = []
    header_seen = False
    for line in section.split("\n")[1:]:
        if line.startswith("### "):
            break
        if not line.startswith("|"):
            if types:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header_seen:
            assert cells[:3] == ["Type", "When", "Key fields"], heading
            header_seen = True
        elif not set(cells[0]) <= set("-: "):
            types.append(cells[0].strip("`"))
    return types


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.__name__)
def test_the_docs_page_lists_exactly_each_familys_types(family):
    assert _documented(SECTIONS[family][0]) == [m.value for m in family]


#: `end_work_item` appends the two types it unpacked from `MANUAL_ENDS`,
#: which holds members.
_UNPACKED = {("store/work_items.py", "audit"), ("store/work_items.py", "ordinary")}


def test_every_writer_names_its_type_as_a_family_member():
    families = {f.__name__ for f in FAMILIES}
    src = ROOT / "src" / "kraft"
    calls, bad = 0, []
    for path in sorted(src.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "events"
            ):
                continue
            calls += 1
            kind = node.args[2]
            rel = path.relative_to(src).as_posix()
            if (
                isinstance(kind, ast.Attribute)
                and isinstance(kind.value, ast.Name)
                and kind.value.id in families
            ):
                continue
            if isinstance(kind, ast.Name) and (rel, kind.id) in _UNPACKED:
                continue
            bad.append(f"{rel}:{node.lineno}")
    assert calls > 100, "the walk found no writers: has `events.append` been renamed?"
    assert bad == []
