"""`dispatch_node` and the item's documents (spec "Item files and IDs", part 2):
a re-run keeps the version it revises, and every launch is told where the
item's documents are. `test_dispatch.py` is over its line budget, so this is
its own file."""

from __future__ import annotations

import pytest
from support.harness import write

from executor.test_dispatch import _agent, _dispatch_each_node, _exec
from kraft import executor, item_paths
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext


@pytest.mark.parametrize(
    ("where", "steer"),
    [
        ("items/{w}/docs/spec.md", "tighten section 2"),
        ("items/{w}/docs/spec.md", None),
        (".engineering/specs/{w}.md", "tighten section 2"),
    ],
    ids=["steered", "retried", "older-worktree"],
)
async def test_a_re_dispatch_keeps_the_version_it_revises(
    item_on, run_dirs, fake_agent, monkeypatch, where, steer
):
    """Before a task producing `spec` runs again, the spec it is about to revise
    goes to `history/spec.1.md`, steered or not. The document stays where it
    is, for the agent to revise in place; an older worktree's is seeded into
    `docs/` first, so the revise note names the file the agent is told to
    write."""
    it = await item_on([_exec("spec", _agent("write", produces="spec"))])
    rel = where.format(w=it.id)
    # `_dispatch_each_node` dispatches in `it.repo`, the worktree here.
    write(*item_paths.locate(run_dirs, it.id, rel, worktree=it.repo), "v1\n")
    current = item_paths.docs_dir(run_dirs, it.id) / "spec.md"
    seen = {}

    async def revise(db_, run_dirs_, *, task_instruction, **kw):
        seen["instruction"] = task_instruction
        current.write_text("v2\n")
        return "done"

    monkeypatch.setattr(dispatch._agent, "run_agent_task", revise)
    await _dispatch_each_node(
        it, launch=LaunchContext(repo_entry=None), steer=executor.Steer(steer) if steer else None
    )

    assert (item_paths.history_dir(run_dirs, it.id) / "spec.1.md").read_text() == "v1\n"
    assert current.read_text() == "v2\n"
    if steer:
        assert seen["instruction"].startswith(f"A human read {current} and sent it back")


async def test_a_later_launch_is_told_where_the_document_an_earlier_one_wrote_is(
    item_on, run_dirs, fake_agent
):
    """End to end: the spec node's agent writes where its contract says, in the
    item's `docs/`, and the next node's launch names that file in its context.
    Nothing is left in the worktree to find it by."""
    it = await item_on(
        [_exec("spec", _agent("write", produces="spec")), _exec("implementation", _agent())]
    )

    await _dispatch_each_node(it, launch=LaunchContext(repo_entry=None))

    spec = item_paths.docs_dir(run_dirs, it.id) / "spec.md"
    assert spec.is_file()
    assert not (it.repo / ".engineering").exists()
    first, second = (" ".join(argv) for argv in fake_agent.argv())
    assert f"spec: {spec}" not in first
    assert f"spec: {spec}" in second
