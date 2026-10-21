"""`walk_once` and `approve`: one `run_once` pass over a `support.harness.Item`
and a gate approved under it, for the restart suites (`test_base_change`,
`test_merge_rejection`)."""

from __future__ import annotations

from kraft import executor, store
from kraft import policy as _policy
from support.harness import entry_of

#: The repo declaring it needs no preparation: without one no worktree is cut.
NO_SETUP = entry_of({"setup_command": ""})


def walk_once(it, policy=None):
    """`executor.run_once` from the item's own cursor, under `policy` (three
    attempts over an hour for any loop it does not name)."""
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=policy or _policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


async def approve(it, gate: str) -> None:
    """`gate` requested and approved, as a person's decision leaves it."""
    await it.database.write(lambda c: store.request_gate(c, it.id, gate, gate))
    await it.database.write(lambda c: store.approve_gate(c, it.id, gate))
