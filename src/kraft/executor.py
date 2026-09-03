from __future__ import annotations

import asyncio
import json
import logging
import uuid

from kraft import builtins as _builtins
from kraft import events, store
from kraft import policy as _policy
from kraft.adapters import agent as _agent
from kraft.adapters import beads
from kraft.adapters import subprocess as _subprocess
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.templates import Registry, Template, materialize

logger = logging.getLogger(__name__)

_FIX_PROMPT = (
    "The checks in node {node_id} failed for this work item. Fix the code so they "
    "pass. Make no unrelated changes. Failing hook points: {failed}"
)


async def intake(
    db,
    run_dirs,
    *,
    title: str,
    repo: str,
    template: Template,
    bd_cwd: str | None = None,
) -> str:
    work_item_id = uuid.uuid4().hex
    bead_id = await beads.intake(title, cwd=bd_cwd)
    chain_definition = json.dumps(materialize(template))
    await db.write(
        lambda c: store.create_work_item(
            c,
            id=work_item_id,
            bead_id=bead_id,
            title=title,
            repo=repo,
            chain_template=template.id,
            chain_definition=chain_definition,
        )
    )
    return work_item_id


async def _dispatch(
    db,
    run_dirs,
    task_hook,
    node,
    work_item_row,
    registry: Registry,
    worktree,
    *,
    instruction_override: str | None = None,
) -> str:
    binding = registry.hooks[task_hook]
    session_id = uuid.uuid4().hex
    kind = binding["kind"]
    common = dict(
        session_id=session_id,
        work_item_id=work_item_row["id"],
        node_id=node["id"],
    )
    if kind == "builtin" and binding.get("handler") == "env_setup":
        return await _builtins.env_setup(db, run_dirs, repo=work_item_row["repo"], **common)
    if kind == "builtin" and binding.get("handler") == "noop":
        return await _builtins.noop(db, run_dirs, hook_point=task_hook, **common)
    if kind == "agent":
        return await _agent.run_agent_task(
            db,
            run_dirs,
            hook_point=task_hook,
            command=binding["command"],
            title=work_item_row["title"],
            task_instruction=instruction_override or work_item_row["title"],
            repo_path=work_item_row["repo"],
            cwd=worktree,
            **common,
        )
    if kind == "subprocess":
        return await _subprocess.run_task(
            db,
            run_dirs,
            hook_point=task_hook,
            cmd=list(binding["command"]),
            cwd=worktree,
            # The fix loop re-runs the test command after an agent edits source in
            # the same worktree. A .pyc written on an earlier cycle has the same
            # second-resolution mtime and (often) size as the fixed source, so
            # CPython would import the stale bytecode and the re-measure would
            # never see the fix. Never writing bytecode keeps every cycle honest.
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            **common,
        )
    raise RuntimeError(
        f"unhandled binding for {task_hook!r}: kind={kind!r} handler={binding.get('handler')!r}"
    )


async def _measure_node(
    db, run_dirs, work_item_id, node, row, registry, worktree
) -> tuple[str, list[str], list[BaseException]]:
    await db.write(lambda c, node=node: store.enter_node(c, work_item_id, node["id"]))
    tasks = node["tasks"]
    results = await asyncio.gather(
        *(_dispatch(db, run_dirs, t, node, row, registry, worktree) for t in tasks),
        return_exceptions=True,
    )
    failed = [
        tasks[i] for i, r in enumerate(results) if isinstance(r, BaseException) or r == "failed"
    ]
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node["id"], exc)
    if failed:
        return "failed", failed, excs
    return "ok", [], []


async def _walk_node(
    db,
    run_dirs,
    work_item_id: str,
    node: dict,
    row,
    registry: Registry,
    worktree,
    *,
    policy: _policy.Policy | None = None,
) -> str:
    key = node.get("fix_loop")

    if not key:
        verdict, failed, excs = await _measure_node(
            db, run_dirs, work_item_id, node, row, registry, worktree
        )
        if verdict == "failed":
            reason = f"task failed in node {node['id']}: {', '.join(failed)}"
            if excs:
                reason += f" ({', '.join(repr(e) for e in excs)})"
            await db.write(lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason))
            return "needs_human"
        await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
        return "ok"

    if policy is None:
        raise RuntimeError(f"node {node['id']!r} has fix_loop but no policy was provided")

    cap = _policy.resolve_cap(policy, key)
    while True:
        verdict, failed, _excs = await _measure_node(
            db, run_dirs, work_item_id, node, row, registry, worktree
        )
        if verdict == "ok":
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
            return "ok"

        # bump_counter returns the cap snapshotted on the row (spec §2.C: written
        # once at first fire, not re-resolved per attempt). Across a restart with
        # an edited policy.yaml, `cap` here is the freshly-resolved one; the row's
        # snapshot is authoritative for the breach check.
        count, started_at, cap = await db.write(
            lambda c, cap=cap: store.bump_counter(c, work_item_id, key, cap)
        )
        if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
            reason = f"{key} exhausted after {count - 1} fix cycle(s)"
            await db.write(
                lambda c: store.mark_sessions_capped_out(
                    c, work_item_id, node["id"], list(node["tasks"])
                )
            )
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"

        payload = {"node_id": node["id"], "cycle": count, "failed_tasks": failed}
        await db.write(
            lambda c, payload=payload: events.append(c, work_item_id, "fix_cycle_started", payload)
        )
        await _dispatch(
            db,
            run_dirs,
            "on.implementation.start",
            node,
            row,
            registry,
            worktree,
            instruction_override=_FIX_PROMPT.format(node_id=node["id"], failed=", ".join(failed)),
        )
        # fix task status is not branched on; loop re-measures


def _gate_cleared(db, work_item_id: str, gate: str) -> bool:
    """True iff the most recent gate_* event for the item is gate_approved <gate>."""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["type"] == "gate_approved" and e["payload"].get("gate") == gate
    return False


async def _maybe_gate(db, work_item_id: str, node: dict) -> bool:
    """If the node ends in a gate, request it and return True (caller stops the walk)."""
    gate = node.get("gate_after")
    if not gate:
        return False
    await db.write(lambda c: store.request_gate(c, work_item_id, node["id"], gate))
    return True


async def run(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    bd_cwd: str | None = None,
    start_index: int = 0,
    policy: _policy.Policy | None = None,
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    worktree = run_dirs.worktrees / work_item_id

    if start_index == 0:
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))

    for node in nodes[start_index:]:
        result = await _walk_node(
            db, run_dirs, work_item_id, node, row, registry, worktree, policy=policy
        )
        if result == "needs_human":
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"


async def _reconcile_current_node(
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
) -> str:
    node_id = node["id"]
    sessions = db.read(
        lambda c: c.execute(
            "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )

    if node.get("fix_loop"):
        # A fix_loop node that has run >=1 cycle keeps cycle-0's permanently-failed
        # measuring session plus extra fix / re-measure sessions, so the
        # len(final)==len(tasks) & all-done clean-check below is structurally
        # unsatisfiable and would wrongly escalate on every crash-recovery. The
        # loop is its own reconciliation: await any adopted in-flight session,
        # then re-enter _walk_node. The surviving retry_counters row continues the
        # wall-clock budget from its original started_at (spec §2.C, §6.4, §9).
        for s in sessions:
            task = adopted.get(s["id"])
            if task is not None:
                await task
        return (
            "ok"
            if await _walk_node(
                db, run_dirs, work_item_id, node, row, registry, worktree, policy=policy
            )
            == "ok"
            else "needs_human"
        )

    if not sessions:
        # crash landed between enter_node and the first create_session; safe to
        # re-dispatch because current_node_id only advances with node_completed.
        return (
            "ok"
            if await _walk_node(
                db, run_dirs, work_item_id, node, row, registry, worktree, policy=policy
            )
            == "ok"
            else "needs_human"
        )

    for s in sessions:
        task = adopted.get(s["id"])
        if task is not None:
            await task

    final = db.read(
        lambda c: c.execute(
            "SELECT status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )
    # ponytail: single-task-node resume only. A crash mid-fan-out of a multi-task
    # node (fewer sessions than tasks, none failed) -> needs_human, no partial
    # re-dispatch. Upgrade with per-task session reconciliation if multi-task
    # nodes ship.
    if len(final) == len(node["tasks"]) and all(r["status"] == "done" for r in final):
        await db.write(lambda c: store.complete_node(c, work_item_id, node_id))
        return "ok"
    await db.write(
        lambda c: store.mark_needs_human(
            c, work_item_id, node_id, "resume: current-node session did not resolve cleanly"
        )
    )
    return "needs_human"


async def resume(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    adopted: dict,
    bd_cwd: str | None = None,
    policy: _policy.Policy | None = None,
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    worktree = run_dirs.worktrees / work_item_id
    cur = row["current_node_id"]

    if cur is None:
        # crash between create_work_item and the first load_chain; nothing ran.
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))
        cur = nodes[0]["id"]

    start = next(i for i, n in enumerate(nodes) if n["id"] == cur)

    node0 = nodes[start]
    gate0 = node0.get("gate_after")
    if gate0 and _gate_cleared(db, work_item_id, gate0):
        # the gate was approved before the crash; do not reconcile or re-request it.
        start += 1
        if start >= len(nodes):
            await db.write(lambda c: store.mark_completed(c, work_item_id))
            try:
                await beads.complete(row["bead_id"], cwd=bd_cwd)
            except Exception as exc:  # noqa: BLE001
                logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
            return "completed"
        # fall through: reconcile from the post-gate node instead

    if (
        await _reconcile_current_node(
            db,
            run_dirs,
            work_item_id,
            nodes[start],
            row,
            registry,
            worktree,
            adopted,
            policy=policy,
        )
        == "needs_human"
    ):
        return "needs_human"

    if await _maybe_gate(db, work_item_id, nodes[start]):
        return "awaiting_gate"

    for node in nodes[start + 1 :]:
        if (
            await _walk_node(
                db, run_dirs, work_item_id, node, row, registry, worktree, policy=policy
            )
            == "needs_human"
        ):
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"
