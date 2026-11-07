"""Walking a slice group (`templates.models.GroupNode`).

A group is one node in the parent's line. Entering it the parent *decides*
whether to split, runs a *prelude* of shared groundwork when the plan has one,
*forks*: files one child work item per slice, cut from the parent's head, and
parks itself `waiting` holding no slot -- then, when every child has ended,
*joins* (`executor.join`) their commits onto its own branch. The group's phase
is stored on the parent (`store.GroupState`) so a crash, restart or retry that
re-enters the group knows where it was.

Words: user-facing text says "slice" and "child"; `fork` is a code word here
only (`RunFork` already names something else in `templates.forks`).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from kraft import events, ids, store
from kraft import policy as _policy
from kraft.config import git_read
from kraft.executor import entry, stops
from kraft.executor.context import WAITING, LaunchContext, Steer
from kraft.progress import read_attached
from kraft.slices import SliceRefusal, SliceSplit, parse_slices, prelude_note
from kraft.slices_docs import slice_attachments
from kraft.store.slices import ChildRow, GroupState
from kraft.templates.models import Chain, GroupNode, ResolvedChain, ResolvedNode
from kraft.vocab import ENDED, HOLDS_SLOT, StopKind, WorkItemEvent, WorkItemStatus

__all__ = ["children_ended", "walk_group"]

#: What a group answers when its nodes ran: the string `walk_node` answers.
DONE = "ok"


def _row(db, work_item_id: str):
    return db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )


def children_ended(db, parent_id: str, state: GroupState) -> bool:
    """Whether every child of the current generation has ended: completed, or
    dropped (abandoned, which a cancel ends as well)."""
    children = db.read(lambda c: store.children_of(c, parent_id, state.generation))
    return bool(children) and all(c["status"] in ENDED for c in children)


async def walk_group(db, run_dirs, work_item_id, node, row, worktree, **kw) -> str:
    """`_walk_group`, and the group itself completed once its nodes have: a
    rejection back to it then reads as rework (`progress.is_rework`)."""
    result = await _walk_group(db, run_dirs, work_item_id, node, row, worktree, **kw)
    if result == DONE:
        await db.write(lambda c: store.complete_node(c, work_item_id, node.id))
    return result


async def _walk_group(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    *,
    policy: _policy.Policy | None = None,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    start_step: int = 0,
) -> str:
    """One entry into a group, by its stored phase. Answers what `walk_node`
    does -- `ok`, or the status the walk stops with -- and `waiting` when it
    parked the parent to wait for its children."""
    state = db.read(lambda c: store.group_state(c, work_item_id)) or GroupState(group=node.id)
    # Where the walk found the item: inside the group's body, if it stopped in one.
    stopped_in = row["current_node_id"]
    await db.write(lambda c: store.enter_node(c, work_item_id, node.id))
    if state.phase == "joining":
        return await _join(db, run_dirs, work_item_id, node, row, worktree, state, launch=launch)
    if state.phase == "forked":
        return await _forked(db, run_dirs, work_item_id, node, row, worktree, state, launch=launch)
    if state.phase == "joined":
        return await _unforked(
            db, run_dirs, work_item_id, node, worktree, policy, steer, launch, start_step
        )
    if state.phase == "prelude":
        split = _decide(db, run_dirs, row, worktree, policy, work_item_id, node, state)
        if not isinstance(split, SliceSplit):  # the plan changed under a restart
            return await _not_forked(
                db, run_dirs, work_item_id, node, worktree, state, policy, steer, launch,
                start_step, split,
            )  # fmt: skip
        return await _prelude_then_fork(
            db, run_dirs, work_item_id, node, row, worktree, state, split, policy, steer, launch,
            resume_at=(stopped_in, start_step),
        )  # fmt: skip
    decided = _decide(db, run_dirs, row, worktree, policy, work_item_id, node, state)
    if not isinstance(decided, SliceSplit):
        return await _not_forked(
            db, run_dirs, work_item_id, node, worktree, state, policy, steer, launch,
            start_step, decided,
        )  # fmt: skip
    return await _prelude_then_fork(
        db, run_dirs, work_item_id, node, row, worktree, state, decided, policy, steer, launch
    )


def _decide(db, run_dirs, row, worktree, policy, work_item_id, node, state) -> SliceSplit | str:
    """The split to fork, or why this group runs in the parent."""
    from kraft.executor.walk import chain_of

    chain = chain_of(row)
    if chain.target.kind == "workspace":
        return "a workspace item is not sliced"
    if chain.target.in_place:
        return "an in-place item is not sliced"
    text = read_attached(run_dirs, row, Path(worktree), "plan")
    if text is None:
        return "the plan could not be read"
    split = parse_slices(text, max_slices=policy.max_slices if policy else 6)
    if split is None:
        return "the plan has no ## Slices section"
    if isinstance(split, SliceRefusal):
        return f"the plan's ## Slices section is refused: {split.reason}"
    return split


async def _not_forked(
    db, run_dirs, work_item_id, node, worktree, state, policy, steer, launch, start_step, why
) -> str:
    await db.write(
        lambda c: events.append(
            c, work_item_id, WorkItemEvent.SLICES_NOT_FORKED, {"group": node.id, "reason": why}
        )
    )
    return await _unforked(
        db, run_dirs, work_item_id, node, worktree, policy, steer, launch, start_step
    )


async def _unforked(
    db, run_dirs, work_item_id, node, worktree, policy, steer, launch, start_step
) -> str:
    """The group's `unforked` body nodes, run in the parent, once. The group is
    then spent: a later entry (a rejection back to it) runs them again without
    deciding."""
    group: GroupNode = node.node
    wanted = group.unforked_ids()
    result = await _walk_body(
        db,
        run_dirs,
        work_item_id,
        [b for b in node.body if b.node.id in wanted],
        worktree,
        policy,
        steer,
        launch,
        start_step,
    )
    if result == DONE:
        state = db.read(lambda c: store.group_state(c, work_item_id)) or GroupState(group=node.id)
        if state.phase != "joined":
            state.phase = "joined"
            await db.write(lambda c: store.set_group_state(c, work_item_id, state))
    return result


async def _walk_body(
    db, run_dirs, work_item_id, body, worktree, policy, steer, launch, start_step=0
) -> str:
    from kraft.executor.walk import walk_node

    first = True
    for inner in body:
        now = db.read(
            lambda c: c.execute(
                "SELECT status FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        if now["status"] not in HOLDS_SLOT:
            return "paused"
        row = _row(db, work_item_id)
        result = await walk_node(
            db,
            run_dirs,
            work_item_id,
            inner,
            row,
            worktree,
            policy=policy,
            steer=steer,
            launch=launch,
            start_step=start_step if first else 0,
        )
        first = False
        if result != DONE:
            return result
    return DONE


async def _prelude_then_fork(
    db, run_dirs, work_item_id, node, row, worktree, state, split, policy, steer, launch,
    resume_at: tuple[str | None, int] = (None, 0),
) -> str:  # fmt: skip
    if split.prelude and not state.prelude_done:
        body, step = list(node.body), 0
        if state.phase == "prelude":
            # Resuming: at the node the prelude stopped in, not the first one.
            stopped_in, start_step = resume_at
            at = next((i for i, b in enumerate(body) if b.id == stopped_in), None)
            if at is not None:
                body, step = body[at:], start_step
        if state.phase != "prelude":
            state.phase = "prelude"
            await db.write(lambda c: store.set_group_state(c, work_item_id, state))
        # The parent's approved plan never changes, so the implementer is told by a
        # note which tasks are the prelude's; every body node runs, tests and review
        # included, so each slice starts from groundwork that has been tested.
        carried = steer.take() if steer is not None else None
        result = await _walk_body(
            db,
            run_dirs,
            work_item_id,
            body,
            worktree,
            policy,
            Steer(
                "\n\n".join(filter(None, [carried, prelude_note(split)])),
                source=steer.source if steer is not None and carried else "seeded",
            ),
            launch,
            step,
        )
        if result != DONE:
            return result
        state.prelude_done = True
        await db.write(lambda c: store.set_group_state(c, work_item_id, state))
    return await _fork(db, run_dirs, work_item_id, node, row, worktree, state, split, launch)


def _mint_child_id(db, run_dirs, repo: str) -> str:
    return ids.mint(
        ids.WORK_ITEM_LENGTH,
        lambda c: (
            entry.work_item_id_taken_on_disk(run_dirs, repo, c)
            or db.read(lambda conn: store.work_item_id_taken(conn, c))
        ),
    )


def _child_chain(parent_chain, group: GroupNode):
    """The parent's frozen chain with only the group's body as its nodes: the
    child inherits the target, policy layers, sandbox and runner, and a chain
    draft applied to the parent later never reaches it."""
    from kraft.policy import FROZEN

    authored = parent_chain.chain.chain
    body = Chain.model_validate_json(
        authored.model_copy(update={"nodes": list(group.nodes)}).model_dump_json(),
        context=FROZEN,
    )
    resolved = ResolvedChain.from_chain(
        body, steering=parent_chain.chain.steering, addons=parent_chain.chain.addons
    )
    return dataclasses.replace(parent_chain, chain=resolved, untrimmed=None)


async def _fork(db, run_dirs, work_item_id, node, row, worktree, state, split, launch) -> str:
    from kraft.executor.walk import chain_of

    group: GroupNode = node.node
    parent_chain = chain_of(row)
    if launch is not None and launch.slice_room is not None:
        why = launch.slice_room(len(split.slices))
        if why is not None:
            await db.write(
                lambda c: store.mark_needs_human(
                    c, work_item_id, node.id, why, kind=StopKind.FAILED
                )
            )
            return "needs_human"
    fork_commit = git_read(Path(worktree), "rev-parse", "HEAD")
    plan_text = read_attached(run_dirs, row, Path(worktree), "plan") or ""
    spec_text = read_attached(run_dirs, row, Path(worktree), "spec")
    chain_json = _child_chain(parent_chain, group).to_json()
    overrides = json.loads(row["node_overrides"] or "null") or None
    if overrides:
        prefix = f"{node.id}."
        overrides = {k.removeprefix(prefix): v for k, v in overrides.items()}
    generation = state.generation + 1
    children = []
    for s in split.slices:
        child_id = _mint_child_id(db, run_dirs, row["repo"])
        children.append(
            ChildRow(
                id=child_id,
                title=f"{row['title']} · slice {s.key}: {s.title}",
                slice_key=s.key,
                materialized_chain=chain_json,
                base_ref=fork_commit,
                attachments=slice_attachments(
                    run_dirs,
                    child_id,
                    spec_text=spec_text,
                    plan_text=plan_text,
                    keep=frozenset(s.tasks),
                    repo=row["repo"],
                ),
                repo=row["repo"],
                chain_template=row["chain_template"],
                node_overrides=overrides,
                policy_override=json.loads(row["policy_override"] or "null") or None,
                auto_gate=bool(row["auto_gate"]),
                budget_set=bool(row["budget_set"]),
                budget_usd=row["budget_usd"],
                agent_overrides=row["agent_overrides"],
                submodules=json.loads(row["submodules"] or "null") or None,
                root_merge_policy=row["root_merge_policy"],
            )
        )
    forked = dataclasses.replace(
        state, phase="forked", generation=generation, fork_commit=fork_commit, group=node.id
    )
    await db.write(lambda c: store.file_children(c, work_item_id, forked, children))
    return await _forked(db, run_dirs, work_item_id, node, row, worktree, forked, launch=launch)


async def _forked(db, run_dirs, work_item_id, node, row, worktree, state, *, launch) -> str:
    """Queue any child never started, then join if every child has ended, else
    park the parent again."""
    children = db.read(lambda c: store.children_of(c, work_item_id, state.generation))
    for child in children:
        if (
            child["status"] == WorkItemStatus.PAUSED
            and child["current_node_id"] is None
            and db.read(lambda c, cid=child["id"]: store.never_queued(c, cid))
        ):
            # Filed paused by the fork and never queued (a crash between): a child a
            # person paused before it started has been queued, and stays paused.
            await db.write(
                lambda c, cid=child["id"]: store.queue_work_item(
                    c,
                    cid,
                    verb="start",
                    body={},
                    headers={},
                    from_statuses=[WorkItemStatus.PAUSED],
                )
            )
    if children_ended(db, work_item_id, state):
        state.phase = "joining"
        await db.write(lambda c: store.set_group_state(c, work_item_id, state))
        return await _join(db, run_dirs, work_item_id, node, row, worktree, state, launch=launch)
    await db.write(lambda c: store.mark_waiting_on_slices(c, work_item_id, node.id))
    return WAITING


async def _join(db, run_dirs, work_item_id, node, row, worktree, state, *, launch) -> str:
    from kraft.executor import join

    return await join.join(db, run_dirs, row, Path(worktree), node, launch=launch)


async def release_ready_parents(app) -> list[str]:
    """Claim, with a capacity check, every parent waiting on slices whose current
    generation's children have all ended, and re-enter it to join. Run at the
    start of each start-queue pass, before the queued items: a parent finishing
    its own work comes first. Never through `waits.tick`, which moves `waiting`
    to `active` without checking `max_concurrent`. No claim (every slot busy):
    the parent stays waiting and the next pass tries again."""
    from kraft import executor
    from kraft.api import deps

    st = app.state
    limit = st.policy.max_concurrent if st.policy else 1
    ready = [
        r
        for r in st.db.read(
            lambda c: c.execute(
                "SELECT * FROM work_items WHERE status = 'waiting' AND stop_kind = 'slices'"
            ).fetchall()
        )
        if _ready(st.db, r)
    ]
    released: list[str] = []
    for row in ready:
        wid = row["id"]
        async with stops.claimed_or_stopped(
            st.db,
            wid,
            row["current_node_id"],
            reason="the start queue claimed this item to join its slices but could not start",
            handed_off=lambda wid=wid: deps.task_is_live(app, wid),
        ):
            claimed = await st.db.write(
                lambda c, wid=wid: store.claim_for_run(
                    c, wid, from_statuses=[WorkItemStatus.WAITING], limit=limit
                )
            )
            if not claimed:
                continue
            try:
                deps.spawn(
                    app,
                    wid,
                    deps.guard(
                        st.db,
                        wid,
                        executor.run(
                            st.db,
                            st.run_dirs,
                            work_item_id=wid,
                            bd_cwd=deps.bd_cwd(),
                            policy=st.policy,
                            launch=deps.launch(st, row["repo"]),
                        ),
                    ),
                )
            except deps.AlreadyRunning:
                continue
            released.append(wid)
    return released


def _ready(db, row) -> bool:
    state = db.read(lambda c: store.group_state(c, row["id"]))
    return state is not None and state.phase == "forked" and children_ended(db, row["id"], state)


# ── the family cascades ──────────────────────────────────────────────────────
# Outside the API routes, so the time-cap poller (`caps.tick`, which holds only a
# database) can run them too. Abandon and cancel are the routes' own: removing a
# worktree is theirs.


def unended_children(db, parent_id: str) -> list[dict]:
    """The children of the parent's current generation that have not ended."""
    state = db.read(lambda c: store.group_state(c, parent_id))
    if state is None:
        return []
    return [
        c
        for c in db.read(lambda c: store.children_of(c, parent_id, state.generation))
        if c["status"] not in ENDED
    ]


async def pause_family(db, parent_id: str, *, by: str, cancel=None) -> list[str]:
    """Pause every child that admits pause (running, waiting, queued, rate
    limited); children already stopped or paused stay as they are. Records which
    the cascade paused, so `resume_family` wakes only those. `by` is a person, or
    the cap that fired, and rides each child's event. `cancel(child_id)` ends the
    child's live walk task, as the pause door does for an item (the route has the
    app; the cap poller hands one in); without it only the sessions stop."""
    from kraft.api.routes.lifecycle import _terminate  # deferred: kraft.api imports this module
    from kraft.vocab import Verb, admitting

    paused: list[str] = []
    for child in unended_children(db, parent_id):
        cid, status = child["id"], child["status"]
        if status not in admitting(Verb.PAUSE):
            continue
        if status in (WorkItemStatus.QUEUED, WorkItemStatus.BLOCKED):
            done = await db.write(lambda c, cid=cid: store.dequeue_work_item(c, cid, why="paused"))
            if not done:
                continue
        else:
            sessions = db.read(lambda c, cid=cid: store.running_sessions_for_node(c, cid))
            await db.write(
                lambda c, cid=cid, ids=[s["id"] for s in sessions]: store.pause_work_item(
                    c, cid, ids
                )
            )
            for s in sessions:
                _terminate(s["pid"], started=s["pid_start_time"])
            if cancel is not None:
                await cancel(cid)
        await db.write(
            lambda c, cid=cid: (
                c.execute("UPDATE work_items SET paused_by_parent = 1 WHERE id = ?", (cid,)),
                events.append(c, cid, WorkItemEvent.SLICE_PAUSED, {"by": by, "parent": parent_id}),
            )
        )
        paused.append(cid)
    return paused


def stopped_by_family_cap(db, child: dict) -> bool:
    """Whether `child` stopped on an item-wide spend cap its parent's family
    shares: dollars or tokens (its own launch check refused: `caps.budget_breach`,
    path `""`), or the instance's per-item dollar cap."""
    if child["status"] != WorkItemStatus.NEEDS_HUMAN or child["stop_kind"] != StopKind.BUDGET:
        return False
    found = db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE work_item_id = ? AND type = ? "
            "ORDER BY seq DESC LIMIT 1",
            (child["id"], WorkItemEvent.NEEDS_HUMAN),
        ).fetchone()
    )
    budget = (json.loads(found["payload"]) if found else {}).get("budget") or {}
    return budget.get("scope") == "work_item" or (
        budget.get("scope") in ("usd", "tokens") and budget.get("path") == ""
    )


async def resume_family(db, parent_id: str) -> list[str]:
    """Queue every child the cascade paused (`paused_by_parent`), through the
    start queue like any start, and retry every child the family cap stopped
    (the cap the parent just raised, or retried under). A child a person paused,
    or stopped for any other reason, is left alone."""
    woken: list[str] = []
    for child in unended_children(db, parent_id):
        cid = child["id"]
        if stopped_by_family_cap(db, child):
            queued = await db.write(
                lambda c, cid=cid: store.queue_work_item(
                    c,
                    cid,
                    verb="retry",
                    body={},
                    headers={},
                    from_statuses=[WorkItemStatus.NEEDS_HUMAN],
                )
            )
            if queued:
                woken.append(cid)
            continue
        if child["status"] != WorkItemStatus.PAUSED or not child["paused_by_parent"]:
            continue
        await db.write(
            lambda c, cid=cid: c.execute(
                "UPDATE work_items SET paused_by_parent = 0 WHERE id = ?", (cid,)
            )
        )
        queued = await db.write(
            lambda c, cid=cid: store.queue_work_item(
                c,
                cid,
                verb="start",
                body={},
                headers={},
                from_statuses=[WorkItemStatus.PAUSED],
            )
        )
        if queued:
            woken.append(cid)
    return woken
