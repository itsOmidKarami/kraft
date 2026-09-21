"""The board's node shape is `store.node_view`'s projection of a resolved V1
node, and `frontend/src/types/work_item.ts`'s `ChainNode` restates it by hand.
Nothing here generates the mirror; this only makes the next drift fail at the
commit that causes it, which is the one moment the fix is cheap."""

from __future__ import annotations

import re
from pathlib import Path

from kraft import store
from kraft.templates.library import TemplateLibrary

ROOT = Path(__file__).resolve().parents[1]

#: `ChainNode` fields no V1 projection carries: a row filed by the legacy loader
#: still shows its stored `chain_definition` (`store.chain_view`), whose nodes
#: have them.
LEGACY_ROW_ONLY = frozenset({"rebase_bounce_to", "auto_escalate_stuck", "auto_escalate_delay_s"})


def _projected_fields() -> set[str]:
    chain = TemplateLibrary.from_yaml_dir(ROOT / "templates").resolve_chain("default")
    return {key for node in chain.nodes for key in store.node_view(node)}


def _interface_keys() -> set[str]:
    text = (ROOT / "frontend/src/types/work_item.ts").read_text()
    match = re.search(r"export interface ChainNode \{(.*?)\n\}", text, re.S)
    assert match, "work_item.ts no longer declares `export interface ChainNode`"
    return set(re.findall(r"^\s{2}(\w+)\??:", match.group(1), re.M))


def test_chain_node_declares_every_projected_field() -> None:
    missing = sorted(_projected_fields() - _interface_keys())
    assert not missing, f"store.node_view projects {missing}; declare them on ChainNode"


def test_chain_node_declares_no_field_nothing_projects() -> None:
    surplus = sorted(_interface_keys() - _projected_fields() - LEGACY_ROW_ONLY)
    assert not surplus, f"ChainNode declares {surplus}, which store.node_view never projects"
