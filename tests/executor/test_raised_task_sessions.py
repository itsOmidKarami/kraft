"""A task that raises leaves a session row saying so (Kraft-s7c04.55).

`measure_node` folds the exception into the node's verdict either way; this is
about the record a human reads -- `kraft view logs` and the Tasks tab -- which
used to have no row at all for a task that raised before creating one, and a
row stuck `running` for one that raised after."""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft import builtins as _builtins
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext

NO_SETUP = LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)
_BUILTIN = {"id": "scopes", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
_FORGE = {"id": "sync", "kind": "forge", "target": "mr.sync"}


def _raise(exc):
    def boom(*_a, **_kw):
        raise exc

    return boom


@pytest.mark.parametrize(
    ("task", "target", "exc", "status"),
    [
        # Raised before any row existed: one is made.
        (_BUILTIN, "_select_scopes", _builtins.RebaseConflict("CONFLICT (content)"), "conflict"),
        (_BUILTIN, "_select_scopes", OSError("disk gone"), "failed"),
        # Raised after the forge started its row: that row is closed, not
        # stranded `running`, and no second one is made.
        (_FORGE, "base_branch", RuntimeError("git fetch died"), "failed"),
    ],
    ids=["builtin-conflict-before-its-row", "builtin-error-before-its-row", "forge-after-its-row"],
)
async def test_a_task_that_raises_leaves_its_session_and_the_reason(
    item_on, monkeypatch, task, target, exc, status
):
    it = await item_on([{"id": "implementation", "kind": "exec", "tasks": [task]}])
    owner = dispatch if target == "_select_scopes" else _builtins
    monkeypatch.setattr(owner, target, _raise(exc))
    node = it.chain.chain.nodes[0]

    with pytest.raises(type(exc)):
        await dispatch.dispatch_node(
            it.database,
            it.run_dirs,
            node.steps[0].tasks[0],
            node,
            it.row(),
            it.repo,
            launch=NO_SETUP,
        )

    [row] = it.sessions("implementation")
    assert (row["hook_point"], row["status"]) == (f"implementation.main.{task['id']}", status)
    assert f"{type(exc).__name__}: {exc}" in Path(row["log_path"]).read_text()
