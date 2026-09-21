"""A retry: fork the run at a canonical path and walk the fork."""

from __future__ import annotations

from kraft import policy as _policy
from kraft import store
from kraft.executor import gates, walk
from kraft.executor.context import LaunchContext, OnApprove
from kraft.templates.forks import ChainPath
from kraft.templates.models import GateNode
from kraft.templates.retry import RetryOverride


async def retry(
    db,
    run_dirs,
    *,
    work_item_id: str,
    target: ChainPath | None,
    override: RetryOverride | None = None,
    steer: str | None = None,
    seeded: bool = False,
    escalated: bool = False,
    bd_cwd: str | None = None,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
    on_approve: OnApprove | None = None,
    conflict: str | None = None,
) -> str:
    """Rerun `target` and everything after it on a new run fork; `None`
    restarts the work item from its first node
    (`work-item-restart-reruns-the-complete-chain`).

    The caller has claimed the item and validated `override`
    (`templates.retry.validate_retry_override`). One transaction records the
    retry the way every retry is recorded (`store.retry_after_cap`) and the
    fork itself (`store.fork_run`), which reopens the span's gates and clears
    its counters. The walk then starts at the fork's own start: the retried
    node's first step, the retried step, or the retried task's step -- whose
    completed siblings the walk keeps (`RunFork.preserved`).

    `conflict` is a rebase conflict the caller hit refreshing the worktree:
    the fork's starting node's `on_conflict` handler takes it (`walk.run_once`).
    """
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    node = target.node if target is not None else walk.chain_of(row).chain.nodes[0]
    is_gate = isinstance(node.node, GateNode)
    key = None if is_gate or node.node.fix_loop is None else walk._loop_key(node)
    gate_key = gates.reject_loop_key(node.id) if is_gate else None

    def _record(c):
        store.retry_after_cap(
            c,
            work_item_id,
            node.id,
            key,
            steer,
            gate_key=gate_key,
            escalated=escalated,
            seeded=seeded,
        )
        return store.fork_run(c, work_item_id, target, override)

    fork = await db.write(_record)
    start_index, start_step = fork.start
    return await walk.run(
        db,
        run_dirs,
        work_item_id=work_item_id,
        bd_cwd=bd_cwd,
        start_index=start_index,
        start_step=start_step,
        policy=policy,
        steer=steer,
        steer_source="seeded" if seeded else "human",
        launch=launch,
        on_approve=on_approve,
        conflict=conflict,
    )
