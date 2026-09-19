"""A chain node's field set lives in `templates.ChainNodeIn`. Four other files
restate it: the skill a headless agent writes nodes from, two docsite pages,
and two TypeScript interfaces. Every one has drifted at least once, and three
produced filed bugs this month.

Nothing here generates a mirror -- the prose in each is worth writing by hand.
These only make the next drift fail at the commit that causes it, which is the
one moment the fix is cheap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft.templates import ChainNodeIn

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
