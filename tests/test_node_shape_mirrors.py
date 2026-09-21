"""A chain node's field set lives in `templates.ChainNodeIn`. Four other files
restate it: the skill a headless agent writes nodes from, two docsite pages,
and two TypeScript interfaces. Every one has drifted at least once, and three
produced filed bugs this month.

Nothing here generates a mirror -- the prose in each is worth writing by hand.
These only make the next drift fail at the commit that causes it, which is the
one moment the fix is cheap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kraft.templates import NODE_CARRYOVER_FIELDS, ChainNode, ChainNodeIn

ROOT = Path(__file__).resolve().parents[1]
FIELDS: tuple[str, ...] = tuple(ChainNodeIn.model_fields)


def block(path: str, start: str, end: str) -> str:
    """The declaring block of `path`, from `start` up to the next `end`.

    Scoped deliberately: a whole-file substring check passes falsely.
    `docsite/configuration.md` contains `auto_escalate_delay_s`, but in the
    `policy.yaml` table, three sections from the node schema it describes.
    """
    text = (ROOT / path).read_text()
    i = text.index(start)
    return text[i : text.index(end, i)]


#: path -> (what the block is, start marker, end marker)
MARKDOWN = {
    "src/kraft/skills/chain-review/SKILL.md": (
        "the node shape the agent writes chain nodes from",
        "Every node you write is exactly:",
        "\nThe field above",
    ),
    "docsite/concepts.md": (
        "the node field table",
        "A node's work is one or more",
        "## Hook point and adapter",
    ),
    "docsite/configuration.md": (
        "the chain template section's field list",
        "## Chain templates",
        "## `registry.yaml`",
    ),
}


@pytest.mark.parametrize("path", sorted(MARKDOWN))
def test_every_node_field_is_named_in_the_markdown_mirror(path: str) -> None:
    what, start, end = MARKDOWN[path]
    text = block(path, start, end)
    missing = [f for f in FIELDS if f not in text]
    assert not missing, (
        f"ChainNodeIn has {missing} and {path} does not name them "
        f"({what}, the block starting {start!r}). Add them there."
    )


#: path -> (interface name, what it is)
INTERFACES = {
    "frontend/src/types/work_item.ts": ("ChainNode", "the board's node type"),
    "frontend/src/types/settings.ts": ("TemplateNode", "the Settings editor's node type"),
}


def _interface_body(path: str, name: str) -> str:
    text = (ROOT / path).read_text()
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", text, re.S)
    assert match, f"{path} no longer declares `export interface {name}`"
    return match.group(1)


@pytest.mark.parametrize("path", sorted(INTERFACES))
def test_every_node_field_is_declared_or_named_in_the_interface(path: str) -> None:
    """A field may be absent from the keys only if the block says why.

    `settings.ts`'s `TemplateNode` leaves out `rebase_bounce_to` on purpose: it
    carries `[key: string]: unknown`, so the serializer round-trips keys the
    form does not render, and a comment names that exact field. That is a
    better answer than listing it, so the rule is "declared or explained".
    """
    name, what = INTERFACES[path]
    body = _interface_body(path, name)
    keys = set(re.findall(r"^\s{2}(\w+)\??:", body, re.M))
    missing = [f for f in FIELDS if f not in keys and f not in body]
    assert not missing, (
        f"ChainNodeIn has {missing} and {path}'s `{name}` ({what}) neither "
        "declares them nor mentions them in a comment. Add the fields, or a "
        "comment saying why they are left out."
    )


#: Fields a node type may carry beyond `ChainNodeIn`, per interface. The
#: board's `ChainNode` is what `store.chain_view` projects a V1 materialized
#: chain into, and that projection adds `kind` -- how a V1 gate node is told
#: apart from an execution node (`gate-is-an-ordered-node`). The Settings
#: editor's `TemplateNode` still edits the legacy template shape, so nothing.
PROJECTED: dict[str, frozenset[str]] = {
    "frontend/src/types/work_item.ts": frozenset({"kind"}),
    "frontend/src/types/settings.ts": frozenset(),
}


@pytest.mark.parametrize("path", sorted(INTERFACES))
def test_the_interface_declares_no_field_the_model_dropped(path: str) -> None:
    name, what = INTERFACES[path]
    keys = set(re.findall(r"^\s{2}(\w+)\??:", _interface_body(path, name), re.M))
    surplus = sorted(keys - set(FIELDS) - PROJECTED[path])
    assert not surplus, (
        f"{path}'s `{name}` ({what}) declares {surplus}, which ChainNodeIn "
        "does not have. Remove them, or add them to the model."
    )


def test_every_carryover_field_is_a_real_node_field() -> None:
    """`NODE_CARRYOVER_FIELDS` is still a hand-written tuple. A name in it the
    model does not have carries nothing forward, silently -- the splice reads
    that key off the old node, finds nothing, and writes nothing."""
    unknown = sorted(set(NODE_CARRYOVER_FIELDS) - set(FIELDS))
    assert not unknown, (
        f"NODE_CARRYOVER_FIELDS names {unknown}, which ChainNodeIn does not "
        "have; those carry nothing forward across a chain-review splice."
    )


def test_chain_node_owns_the_derived_carryover_fields() -> None:
    assert NODE_CARRYOVER_FIELDS == ChainNode.carryover_fields
