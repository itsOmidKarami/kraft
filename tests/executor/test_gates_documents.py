"""Which document a gate is about: the item's `docs/` copy, else the one an
older run left in its worktree's `.engineering/` (spec "Item files and IDs",
part 2). `test_gates.py` is over its line budget, so this is its own file."""

from __future__ import annotations

import pytest
from support.harness import write

from kraft import item_paths
from kraft.executor import gates

#: A node that writes the spec, and the gate that decides it.
_CHAIN = [
    {
        "id": "spec",
        "kind": "exec",
        "tasks": [
            {"id": "write", "kind": "agent", "harness": "fake", "prompt": "p", "produces": "spec"}
        ],
    },
    {"id": "spec_approval", "kind": "gate", "artifact": "spec"},
]


@pytest.mark.parametrize(
    ("written", "name"),
    [
        (["items/{w}/docs/spec.md", ".engineering/specs/{w}.md"], "items/{w}/docs/spec.md"),
        ([".engineering/specs/{w}.md"], ".engineering/specs/{w}.md"),
        ([], None),
    ],
    ids=["new-place", "older-worktree", "nothing-yet"],
)
async def test_the_gate_names_the_document_where_it_is(item_on, run_dirs, written, name):
    it = await item_on(_CHAIN, "spec_approval")
    for each in written:
        rel = each.format(w=it.id)
        write(item_paths.base_for(run_dirs, it.id, rel), rel, "# spec\n")

    want = name.format(w=it.id) if name else None
    assert gates.gate_artifact(run_dirs, it.row(), "spec_approval") == want
