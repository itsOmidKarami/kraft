from __future__ import annotations

from kraft import policy as _policy
from kraft import store
from kraft.executor import gates, walk
from kraft.executor.context import _ADVANCING, LaunchContext, OnApprove
from kraft.templates.models import ExecNode, ResolvedNode


async def reconcile_current_node(
    db, work_item_id: str, node: ResolvedNode, adopted: dict
) -> str | None:
    """Settle what a crash left running on the current node, and read its
    outcome when the sessions alone can answer it.

    Every session a restart adopted is awaited first, so its row says how it
    ended. Then, for the one shape whose sessions *are* its outcome -- one
    step, no recovery, no fix loop -- all of them advancing completes the node
    (`"ok"`) and a latest attempt that did not stops for a human
    (`"needs_human"`): walking it again would pay to rerun a task whose
    failure is already known. Every other shape answers `None` and is
    re-entered at the item's cursor, because a node that can remediate itself
    is its own reconciliation (Kraft-rv6i), and its cursor says which steps
    already passed (Kraft-c3dab).
    """
    node_id = node.id
    sessions = db.read(
        lambda c: c.execute(
            "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )
    for s in sessions:
        task = adopted.get(s["id"])
        if task is not None:
            await task
    exec_node = node.node if isinstance(node.node, ExecNode) else None
    if (
        exec_node is None
        or exec_node.fix_loop is not None
        or node.on_failure
        or len(node.steps) > 1
        or not sessions
    ):
        return None
    # Scoped to this node's own declared tasks, latest attempt only
    # (Kraft-s15p0) -- not every worker_sessions row this node has ever
    # accumulated across every escalation and every earlier failed attempt.
    measured = [t.path for step in node.steps for t in step.tasks]
    final = db.read(lambda c: store.latest_session_per_task(c, work_item_id, node_id, measured))
    if len(final) == len(measured) and all(r["status"] in _ADVANCING for r in final):
        await db.write(lambda c: store.complete_node(c, work_item_id, node_id))
        return "ok"
    if len(final) < len(measured) and all(r["status"] in _ADVANCING for r in final):
        # A crash between two of the step's tasks starting: the ones with no
        # session yet were never dispatched. Walking the node dispatches them,
        # and reuses the ones that finished (`reusable_session`).
        return None
    # Which session, and how it ended, rides on the card -- appended, so the
    # prefix `analytics._DEFECT_SIGNATURES` matches on is unchanged.
    seen = {r["hook_point"]: r["status"] for r in final}
    detail = ", ".join(
        f"{t.task.id}: {seen.get(t.path, 'no session')}"
        for step in node.steps
        for t in step.tasks
        if seen.get(t.path) not in _ADVANCING
    )
    reason = "resume: current-node session did not resolve cleanly" + (
        f" ({detail})" if detail else ""
    )
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, reason))
    return "needs_human"


class SteerError(ValueError):
    """A steer that cannot land, naming the one field it was refused for."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


def resume_steer(db, row, text: str | None, steers: dict[str, str]) -> dict[str, str | None] | None:
    """Who a resume's steer reaches, by task path.

    `text` reaches every paused agent task of the node the item stands on
    (`steer-defaults-to-all-paused-agent-tasks`); `steers` addresses paused
    agent tasks individually and wins for the task it names
    (`steer-can-address-paused-agent-tasks-individually`). `None` when no agent
    task is paused there -- then `text`, if any, is the unaddressed note the
    next agent launch takes. A path that is not a paused agent task of this
    node is a `SteerError` naming it: a steer never targets a non-agent task.
    """
    from kraft.templates.forks import ChainPath, PathError
    from kraft.templates.models import AgentTask

    chain = store.materialized_chain_of(row)
    if chain is None:
        # A legacy row, which no walk can run: the walk says so, not this.
        return None
    node_id = row["current_node_id"]
    node = next((n for n in chain.chain.nodes if n.id == node_id), None)
    own = [t for s in node.steps for t in s.tasks] if node is not None else []
    agents = [t.path for t in own if isinstance(t.task, AgentTask)]
    latest = db.read(lambda c: store.latest_session_per_task(c, row["id"], node_id, agents))
    paused = [r["hook_point"] for r in latest if r["status"] == "paused"]
    for path in steers:
        field = f"steers.{path}"
        try:
            target = ChainPath.parse(chain, path)
        except PathError as exc:
            raise SteerError(field, str(exc)) from None
        if target.task is None:
            raise SteerError(field, f"{path!r} is not a task")
        if not isinstance(target.task.task, AgentTask):
            raise SteerError(
                field,
                f"{path!r} is a {target.task.task.kind.value} task, and a steer reaches "
                "only an agent task",
            )
        if path not in paused:
            raise SteerError(field, f"{path!r} is not paused")
    if not paused:
        return None
    # A paused task with no text of its own yet maps to None: a steer left
    # earlier through `/steer` is read only once the resume has claimed the item.
    return {p: steers.get(p, text) for p in paused}


async def resume_once(
    db,
    run_dirs,
    *,
    work_item_id: str,
    adopted: dict,
    bd_cwd: str | None = None,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
) -> str:
    """Crash resume: reconcile the current node, then continue through the one
    entry into the walk, `walk.run_once`, from the item's cursor -- or from the
    node after the one reconciliation just completed. Its result is returned
    as it is: a node back to waiting on CI is `waiting`, not `needs_human`, and
    a pause stops the walk rather than being walked past (Kraft-z0hah)."""
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    if row["status"] in store.ENDED:
        return row["status"]  # before reconciliation settles anything (Kraft-dncfg)
    nodes = walk.chain_of(row).chain.nodes
    current = next((i for i, n in enumerate(nodes) if n.id == row["current_node_id"]), None)
    start_index = None
    if current is not None:
        settled = await reconcile_current_node(db, work_item_id, nodes[current], adopted)
        if settled == "needs_human":
            return "needs_human"
        if settled == "ok":
            start_index = current + 1
    return await walk.run_once(
        db,
        run_dirs,
        work_item_id=work_item_id,
        bd_cwd=bd_cwd,
        start_index=start_index,
        policy=policy,
        launch=launch,
    )


async def resume(
    db,
    run_dirs,
    *,
    work_item_id: str,
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
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )
