from __future__ import annotations

import json

from kraft import builtins as _builtins
from kraft import config as _config
from kraft import policy as _policy
from kraft import store
from kraft.executor import entry, gates, walk
from kraft.executor.context import RATE_LIMITED, WAITING, LaunchContext, OnApprove
from kraft.templates import Registry

#: Statuses that let the chain advance. `done_with_concerns` is deliberately
#: here: the agent finished the work — its doubts are information for the human
#: at the next gate, not a control-flow change.
_ADVANCING = ("done", "done_with_concerns")


async def reconcile_current_node(
    db,
    run_dirs,
    work_item_id,
    node,
    row,
    registry,
    worktree,
    adopted,
    *,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
) -> str:
    node_id = node["id"]
    sessions = db.read(
        lambda c: c.execute(
            "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )

    if node.get("fix_loop") or node.get("on_failure"):
        # A node that can remediate itself is its own reconciliation: re-entering
        # `walk_node` re-measures it, and a failure then reaches the repair the
        # template declared. The session-count check below would instead read
        # the crash as "did not resolve cleanly" and stop for a human with the
        # repair never tried (Kraft-rv6i).
        #
        # A fix_loop node that has run >=1 cycle keeps cycle-0's permanently-failed
        # measuring session plus extra fix / re-measure sessions, so the
        # len(final)==len(tasks) & all-done clean-check below is structurally
        # unsatisfiable and would wrongly escalate on every crash-recovery. The
        # loop is its own reconciliation: await any adopted in-flight session,
        # then re-enter walk_node. The surviving retry_counters row continues the
        # wall-clock budget from its original started_at (spec §2.C, §6.4, §9).
        for s in sessions:
            task = adopted.get(s["id"])
            if task is not None:
                await task
        return (
            "ok"
            if await walk.walk_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                policy=policy,
                launch=launch,
            )
            == "ok"
            else "needs_human"
        )

    if not sessions:
        # crash landed between enter_node and the first create_session; safe to
        # re-dispatch because current_node_id only advances with node_completed.
        return (
            "ok"
            if await walk.walk_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                policy=policy,
                launch=launch,
            )
            == "ok"
            else "needs_human"
        )

    for s in sessions:
        task = adopted.get(s["id"])
        if task is not None:
            await task

    # Scoped to this node's own declared tasks, latest attempt only
    # (Kraft-s15p0) -- not every worker_sessions row this node has ever
    # accumulated across every escalation and every earlier failed attempt.
    # See `store.latest_session_per_task`'s own docstring for the observed
    # history this fixes.
    final = db.read(
        lambda c: store.latest_session_per_task(c, work_item_id, node_id, node["tasks"])
    )
    # ponytail: single-task-node resume only. A crash mid-fan-out of a multi-task
    # node (fewer sessions than tasks, none failed) -> needs_human, no partial
    # re-dispatch. Upgrade with per-task session reconciliation if multi-task
    # nodes ship.
    # Nodes declaring `on_failure` never reach here; they took the re-measuring
    # branch above.
    if len(final) == len(node["tasks"]) and all(r["status"] in _ADVANCING for r in final):
        await db.write(lambda c: store.complete_node(c, work_item_id, node_id))
        return "ok"
    await db.write(
        lambda c: store.mark_needs_human(
            c, work_item_id, node_id, "resume: current-node session did not resolve cleanly"
        )
    )
    return "needs_human"


async def resume_once(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    adopted: dict,
    bd_cwd: str | None = None,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    cur = row["current_node_id"]

    if cur is None:
        # crash between create_work_item and the first load_chain; nothing ran.
        # Recorded before ensure_worktree below for the same reason `run` records
        # it first: a worktree that can never be created must not leave the item
        # looking like it crashed before load_chain ever happened.
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))
        cur = nodes[0]["id"]

    # Before the first dispatch, not inside the `env_setup` node: `default.yaml`
    # runs `spec` and `plan` first, and both need a checkout — and, for `plan`'s
    # attached-spec fallback to have anything to find, attachments already
    # copied in — to write into.
    #
    # See the matching comment in `kraft.executor.walk.run`: a git failure here
    # is attributed to the current node rather than left to
    # `kraft.api.deps.guard`'s bare crash handler.
    try:
        worktree = await _builtins.ensure_worktree(
            db,
            run_dirs,
            repo=row["repo"],
            work_item_id=work_item_id,
            attachments=entry.attachments_of(row),
            local_files=(launch.repo_entry or {}).get("local_files") if launch else None,
        )
    except (RuntimeError, _config.ConfigError) as exc:
        reason = str(exc)
        await db.write(lambda c: store.enter_node(c, work_item_id, cur))
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, cur, reason))
        return "needs_human"

    start = next(i for i, n in enumerate(nodes) if n["id"] == cur)

    node0 = nodes[start]
    gate0 = node0.get("gate_after")
    if gate0 and gates.gate_cleared(db, work_item_id, gate0):
        # the gate was approved before the crash; do not reconcile or re-request it.
        start += 1
        if start >= len(nodes):
            await db.write(lambda c: store.mark_completed(c, work_item_id))
            await entry.close_beads(db, row, bd_cwd)
            return "completed"
        # fall through: reconcile from the post-gate node instead

    if (
        await reconcile_current_node(
            db,
            run_dirs,
            work_item_id,
            nodes[start],
            row,
            registry,
            worktree,
            adopted,
            policy=policy,
            launch=launch,
        )
        == "needs_human"
    ):
        return "needs_human"

    if await gates.maybe_gate(db, work_item_id, nodes[start]):
        return "awaiting_gate"

    for node in nodes[start + 1 :]:
        tail_result = await walk.walk_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            registry,
            worktree,
            policy=policy,
            launch=launch,
        )
        if tail_result == "needs_human":
            return "needs_human"
        if tail_result == RATE_LIMITED:
            return RATE_LIMITED
        if tail_result == WAITING:
            return WAITING
        if await gates.maybe_gate(db, work_item_id, node):
            return "awaiting_gate"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    await entry.close_beads(db, row, bd_cwd)
    return "completed"


async def resume(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    adopted: dict,
    bd_cwd: str | None = None,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
    on_approve: OnApprove | None = None,
) -> str:
    status = await resume_once(
        db,
        run_dirs,
        work_item_id=work_item_id,
        registry=registry,
        adopted=adopted,
        bd_cwd=bd_cwd,
        policy=policy,
        launch=launch,
    )
    status = await gates.review_gates(
        status,
        db,
        run_dirs,
        work_item_id=work_item_id,
        registry=registry,
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )
    return await gates.auto_escalate_stuck(
        status,
        db,
        run_dirs,
        work_item_id=work_item_id,
        registry=registry,
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )
