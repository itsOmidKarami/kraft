"""Every agent launch is told where its item's documents are (spec "Item files
and IDs", part 2). A later node used to find a gate-written spec by its place
in the worktree; documents now live in the item's `docs/`, outside it, so the
launch names each by absolute path (`agent.documents_note`). That every launch
carries it is `tests/executor/test_dispatch_documents.py`'s."""

from __future__ import annotations

import pytest
from support.harness import write

from kraft import item_paths
from kraft.adapters import agent
from kraft.worker import sandbox

#: A spec node, its gate, and a plan node: the kinds an item's documents can be.
_CHAIN = [
    {
        "id": "spec",
        "kind": "exec",
        "tasks": [
            {"id": "write", "kind": "agent", "harness": "fake", "prompt": "x", "produces": "spec"}
        ],
    },
    {"id": "spec_approval", "kind": "gate", "artifact": "spec"},
    {
        "id": "plan",
        "kind": "exec",
        "tasks": [
            {"id": "write", "kind": "agent", "harness": "fake", "prompt": "x", "produces": "plan"}
        ],
    },
]


@pytest.mark.parametrize(
    ("where", "attachments", "lines"),
    [
        ("items/{w}/docs/spec.md", None, ["spec: {run}/items/{w}/docs/spec.md"]),
        (".engineering/specs/{w}.md", None, ["spec: {wt}/.engineering/specs/{w}.md"]),
        (
            None,
            [{"kind": "plan", "path": "docs/p.md"}],
            ["plan, attached at intake: docs/p.md in the worktree"],
        ),
        (
            "items/{w}/filed/s.md",
            [{"kind": "spec", "path": "docs/s.md", "source": "/gone/filed/s.md"}],
            ["spec, attached at intake: {run}/items/{w}/filed/s.md"],
        ),
        (None, None, []),
    ],
    ids=["new-place", "older-worktree", "attached", "attached-not-in-the-folder", "none-yet"],
)
async def test_every_agent_is_told_where_its_item_s_documents_are(
    item_on, run_dirs, where, attachments, lines
):
    it = await item_on(_CHAIN, attachments=attachments)
    if where is not None:
        rel = where.format(w=it.id)
        write(*item_paths.locate(run_dirs, it.id, rel), "# spec\n")

    note = agent.documents_note(run_dirs, it.row(), worktree=it.worktree)

    want = [line.format(run=run_dirs.base, wt=it.worktree, w=it.id) for line in lines]
    assert note == (f"\n\nThis work item's documents so far, by path:\n{want[0]}\n" if want else "")
    assert "plan:" not in note, "a kind the chain produces but nobody wrote is not listed"


def test_a_launch_is_told_the_folder_its_documents_go_in(run):
    """`$KRAFT_DOCS_DIR`, for a skill that writes a document of its own: the
    item's `docs/`, the one folder of the item's a sandbox mounts read-write,
    and forwarded into the sandbox like the other per-session vars."""
    assert run()["env"]["KRAFT_DOCS_DIR"] == "/kraft/run/items/w1/docs"
    assert "KRAFT_DOCS_DIR" in sandbox.FORWARDED_ENV
