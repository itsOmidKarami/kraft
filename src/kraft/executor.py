from __future__ import annotations

import asyncio
import json
import logging
import uuid

from kraft import builtins as _builtins
from kraft import store
from kraft.adapters import agent as _agent
from kraft.adapters import beads
from kraft.adapters import subprocess as _subprocess
from kraft.templates import Registry, Template, materialize

logger = logging.getLogger(__name__)


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
    db, run_dirs, task_hook, node, work_item_row, registry: Registry, worktree
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
            task_instruction=work_item_row["title"],
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
            **common,
        )
    raise RuntimeError(
        f"unhandled binding for {task_hook!r}: kind={kind!r} handler={binding.get('handler')!r}"
    )


async def _walk_node(
    db, run_dirs, work_item_id: str, node: dict, row, registry: Registry, worktree
) -> str:
    await db.write(lambda c, node=node: store.enter_node(c, work_item_id, node["id"]))
    results = await asyncio.gather(
        *(_dispatch(db, run_dirs, task, node, row, registry, worktree) for task in node["tasks"]),
        return_exceptions=True,
    )
    excs = [r for r in results if isinstance(r, BaseException)]
    if excs or "failed" in results:
        reason = f"task failed in node {node['id']}"
        if excs:
            reason += ": " + ", ".join(repr(e) for e in excs)
        await db.write(
            lambda c, node=node, reason=reason: store.mark_needs_human(
                c, work_item_id, node["id"], reason
            )
        )
        return "needs_human"
    await db.write(lambda c, node=node: store.complete_node(c, work_item_id, node["id"]))
    return "ok"


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
        result = await _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree)
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
    db, run_dirs, work_item_id, node, row, registry, worktree, adopted
) -> str:
    node_id = node["id"]
    sessions = db.read(
        lambda c: c.execute(
            "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )
    if not sessions:
        # crash landed between enter_node and the first create_session; safe to
        # re-dispatch because current_node_id only advances with node_completed.
        return (
            "ok"
            if await _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree) == "ok"
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

    if (
        await _reconcile_current_node(
            db, run_dirs, work_item_id, nodes[start], row, registry, worktree, adopted
        )
        == "needs_human"
    ):
        return "needs_human"

    if await _maybe_gate(db, work_item_id, nodes[start]):
        return "awaiting_gate"

    for node in nodes[start + 1 :]:
        if (
            await _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree)
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
