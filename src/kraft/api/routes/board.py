from __future__ import annotations

import json
import math
from datetime import datetime

from fastapi import HTTPException, Request

from kraft import caps, events, executor, store
from kraft import config as config_mod
from kraft import policy as policy_mod
from kraft import progress as progress_mod
from kraft.adapters import forge as forge_mod
from kraft.api import api_router, deps
from kraft.cap_levels import SCOPE_CAP_FIELDS
from kraft.vocab import COMMANDS_MAY_START, STOPPED, WorkItemStatus


def _pending_gate(st, wid: str) -> str | None:
    return executor.pending_gate(st.db, wid)


def _last_review_sha(st, wid: str) -> str | None:
    rev = st.db.read(lambda c: store.last_review(c, wid))
    return rev["head_sha"] if rev else None


def fix_target(st, row, gate: str, node: str | None = None) -> dict:
    """Where a rejection at `gate` restarts the chain, for the review page's sentence.

    `node` is the index `executor.reject_target` picks, the function
    `apply_rejection` runs, so the sentence and the action cannot disagree.
    `then` is the nodes between it and the gate; `round` is the one a rejection
    now would start, against the cap the counter snapshotted (the policy's when
    no rejection has happened yet). Raises ValueError for a `node` not before
    the gate, as `reject_target` does.
    """
    nodes = store.effective_nodes(executor.chain_of(row), store.node_overrides_of(row))
    idx = executor.gate_node_index(nodes, gate)
    target = executor.reject_target(nodes, idx, node)
    key = store.reject_loop_key(gate)
    counter = st.db.read(lambda c: store.read_counter(c, row["id"], key))
    if counter:
        cap = counter["cap_attempts"]
    else:
        cap = st.policy.cap_for(key).attempts if st.policy else None
    return {
        "gate": gate,
        "node": nodes[target].id,
        "then": [n.id for n in nodes[target + 1 : idx]],
        "round": None
        if cap is None
        else {"n": (counter["count"] if counter else 0) + 1, "max": cap},
    }


def _reject_default(st, row, gate: str | None) -> str | None:
    return fix_target(st, row, gate)["node"] if gate else None


#: The event types that bound a `work_item_needs_human` stop, newest wins —
#: the same shape of boundary `_pending_gate` models. A stop needs one because
#: `store.request_gate` also sets status 'needs_human' while appending no
#: `work_item_needs_human`: without this set a first-match scan reads an
#: answered-and-resumed stop, or a pending gate, as a live one forever.
_STOP_BOUNDARY = (
    "work_item_needs_human",
    "gate_requested",
    "gate_rejected",
    "work_item_resumed",
    "work_item_retried",
    "work_item_completed",
    "work_item_rate_limited",
)


#: How long an `escalation_message` with no session row yet still reads as a
#: turn that is starting: the message is recorded before the turn launches, and
#: a launch that fails writes its own (failed) session row.
_LAUNCH_WINDOW_S = 60.0


def _turn_live(session_status: str | None, message_at: str) -> bool:
    """Whether the escalation turn an `escalation_message` announced is still
    working: its session is pending or running, or has no row yet and the
    message is new. A turn that exited (failed, done, asked a question) is
    not live: the item is back with the person."""
    if session_status is not None:
        return session_status in ("pending", "running")
    age = datetime.fromisoformat(store._now()) - datetime.fromisoformat(message_at)
    return age.total_seconds() < _LAUNCH_WINDOW_S


def _stop_episode(st, wid: str) -> tuple[dict | None, bool]:
    """The `work_item_needs_human` payload of the stop the item is *currently*
    sitting on (or None if anything in `_STOP_BOUNDARY` superseded it), and
    whether an escalation turn is working on it: an `escalation_message` landed
    after that boundary event (an escalation from an earlier, already-superseded
    stop must not read as live) and that turn's session has not exited. One
    event scan for both, so `display_status` costs the detail route nothing
    beyond what `_current_stop` already read."""
    evs = st.db.read(lambda c: events.read_after(c, 0, wid))
    boundary = None
    for e in reversed(evs):
        if e["type"] in _STOP_BOUNDARY:
            boundary = e
            break
    stop = (
        boundary["payload"]
        if boundary is not None and boundary["type"] == "work_item_needs_human"
        else None
    )
    last = next((e for e in reversed(evs) if e["type"] == "escalation_message"), None)
    escalated = False
    if boundary is not None and last is not None and last["seq"] > boundary["seq"]:
        session = st.db.read(
            lambda c: c.execute(
                "SELECT status FROM worker_sessions WHERE id = ?",
                (last["payload"].get("session_id"),),
            ).fetchone()
        )
        escalated = _turn_live(session["status"] if session else None, last["created_at"])
    return stop, escalated


def _current_stop(st, wid: str) -> dict | None:
    """The `work_item_needs_human` payload of the stop the item is *currently*
    sitting on, or None if anything in `_STOP_BOUNDARY` superseded it."""
    return _stop_episode(st, wid)[0]


def display_status(row, stop_kind: str | None, escalated: bool, pending_gate: str | None) -> str:
    """The design vocabulary's status badge (Kraft Design Decisions §1, §14):
    exactly one of `archived`, `done`, `cancelled`, `paused`, `running`,
    `waiting`, `needs_you`, `escalated`, `failed`. The stored `status` column
    (`kraft.store.work_items`) is unchanged by this -- `display_status` is a
    read-side view over it, `stop_kind`, whether an escalation is live, and
    whether a gate is pending.

    `infra` shows as `failed`, not `needs_you`: a daemon restart
    that lost a session, a git refresh that failed, or a forge that 403'd
    three times in a row (Decisions §14's own example) are all stops where no
    handler applies and no timer will change that -- auto-escalation did not
    or cannot run for them (Item States requirement 1). A `stuck` stop still
    counts as `needs_you` until its escalation turn actually starts, at which
    point `escalated` takes over.
    """
    if row["archived_at"] is not None:
        return "archived"
    status = row["status"]
    if status == "completed":
        return "done"
    if status == "abandoned":
        return "cancelled"
    if status == "paused":
        return "paused"
    if status == "active":
        return "running"
    if status in ("waiting", "rate_limited"):
        return "waiting"
    if status == "needs_human":
        if pending_gate:
            return "needs_you"
        if escalated:
            return "escalated"
        if stop_kind in ("failed", "config", "infra"):
            return "failed"
        return "needs_you"
    return "running"


def _stop_reason(st, wid: str) -> str | None:
    stop = _current_stop(st, wid)
    return stop["reason"] if stop is not None else None


def _needs_context_stop(st, wid: str) -> bool:
    """True iff the item's current stop is a `needs_context` — a
    `work_item_needs_human` reason of the form `needs_context: <question>`
    (kraft.executor.dispatch.needs_context_question). Answerable via /steer and
    /resume the same as a pause, unlike any other needs_human reason."""
    reason = _stop_reason(st, wid)
    return reason is not None and reason.startswith("needs_context:")


def _gate_node_index(nodes, gate: str) -> int:
    return executor.gate_node_index(nodes, gate)


def _gate_artifact(st, row, gate: str | None) -> str | None:
    """The document the pending gate decides, from the gate's own `artifact`
    field. `st.registry` is gone from the call: the artifact kind used to be
    scanned out of the *preceding* node's hook bindings, and a V1 gate declares
    it itself (`gate-owns-gate-behaviour`)."""
    return executor.gate_artifact(st.run_dirs, row, gate)


# B10.
@api_router.get("/budget/today")
async def budget_today(request: Request):
    """The instance's spend since local midnight against `policy.budget.daily_usd`,
    the same window and cap the daily budget stop already uses
    (`intake.py`, `executor/stops.py`), surfaced for a UI with no single work
    item in view."""
    st = request.app.state
    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    since = store.local_midnight_utc()
    _item, spent_usd = st.db.read(lambda c: store.budget_spend(c, "", since=since))
    return {"spent_usd": spent_usd, "cap_usd": budget.daily_usd}


@api_router.get("/work-items")
async def list_work_items(request: Request):
    st = request.app.state
    # Read outside `_read`: that closure runs on the database thread and has no
    # request to ask.
    include_abandoned = request.query_params.get("include_abandoned") == "true"
    # Board counts and the default list exclude archived items entirely
    # (UI v2 · 03); ?archived=true flips to *only* archived, for the
    # Archived view. There is no third state ("both") -- nothing needs it.
    archived = request.query_params.get("archived") == "true"

    def _read(c):
        rows = c.execute(
            "SELECT * FROM work_items WHERE (? OR status != ?) "
            "AND (archived_at IS NOT NULL) = ? ORDER BY created_at",
            (include_abandoned, WorkItemStatus.ABANDONED, archived),
        ).fetchall()
        cursor = c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        # The latest gate request or gate closer per item, in one pass — the
        # board renders a gate prompt per row and must not offer Approve on a
        # rejected gate. The closers are `executor.pending_gate`'s, so a gate
        # `kraft item skip` passed, or one left by an item that ended, reads
        # as closed here too, as it does on the item's own page.
        gate_types = ("gate_requested", *executor.GATE_CLOSED)
        gates = c.execute(
            "SELECT work_item_id, type, payload FROM events WHERE seq IN ("
            "  SELECT MAX(seq) FROM events"
            f"  WHERE type IN ({','.join('?' * len(gate_types))})"
            "  GROUP BY work_item_id)",
            gate_types,
        ).fetchall()
        # The latest fallback switch and session start per item: the card
        # marks an item whose last launch ran on a fallback (Kraft-0a3h8).
        launches = c.execute(
            "SELECT work_item_id, type, payload FROM events WHERE seq IN ("
            "  SELECT MAX(seq) FROM events"
            "  WHERE type IN ('launch_fallback', 'worker_session_started')"
            "  GROUP BY work_item_id, type)"
        ).fetchall()
        # The latest `_STOP_BOUNDARY` event per item, and the latest
        # `escalation_message` per item -- `display_status`/`stop` (B.4) need
        # both, and the list reads no sessions per row, so this is the one
        # grouped query that stands in for `_stop_episode`'s per-item scan.
        boundaries = c.execute(
            "SELECT work_item_id, type, payload, seq FROM events WHERE seq IN ("
            f"  SELECT MAX(seq) FROM events WHERE type IN ({','.join('?' * len(_STOP_BOUNDARY))})"
            "  GROUP BY work_item_id)",
            _STOP_BOUNDARY,
        ).fetchall()
        escalations = c.execute(
            "SELECT e.work_item_id, e.seq, e.created_at, s.status AS session_status FROM events e "
            "LEFT JOIN worker_sessions s ON s.id = json_extract(e.payload, '$.session_id') "
            "WHERE e.seq IN (SELECT MAX(seq) FROM events WHERE type = 'escalation_message' "
            "GROUP BY work_item_id)"
        ).fetchall()
        # `mr_ref` and the step's task: the same grouped-query trade as the
        # rest, so the list stays one pass instead of `_mr_ref`'s per-item scan.
        mr_events = c.execute(
            "SELECT work_item_id, payload FROM events WHERE seq IN ("
            "  SELECT MAX(seq) FROM events WHERE type = 'mr_opened' GROUP BY work_item_id)"
        ).fetchall()
        roots = c.execute(
            "SELECT work_item_id, mr_ref FROM work_item_repos WHERE role = 'root'"
        ).fetchall()
        tasks = c.execute(
            "SELECT s.work_item_id, s.hook_point FROM worker_sessions s"
            " JOIN work_items w ON w.id = s.work_item_id AND w.current_node_id = s.node_id"
            " WHERE s.hook_point != 'escalation' ORDER BY s.created_at"
        ).fetchall()
        return rows, cursor, gates, launches, boundaries, escalations, mr_events, roots, tasks

    (
        rows,
        cursor,
        gate_rows,
        launch_rows,
        boundary_rows,
        escalation_rows,
        mr_event_rows,
        root_rows,
        task_rows,
    ) = st.db.read(_read)
    latest: dict[tuple[str, str], dict] = {
        (e["work_item_id"], e["type"]): json.loads(e["payload"]) for e in launch_rows
    }
    pending = {
        g["work_item_id"]: json.loads(g["payload"])["gate"]
        for g in gate_rows
        if g["type"] == "gate_requested"
    }
    boundary_by_item = {b["work_item_id"]: b for b in boundary_rows}
    escalation_by_item = {e["work_item_id"]: e for e in escalation_rows}
    mr_event_by_item = {e["work_item_id"]: json.loads(e["payload"]) for e in mr_event_rows}
    root_mr_by_item = {r["work_item_id"]: r["mr_ref"] for r in root_rows}
    task_by_item = {t["work_item_id"]: t["hook_point"] for t in task_rows}  # latest wins

    def _list_escalated(wid: str) -> bool:
        boundary, last = boundary_by_item.get(wid), escalation_by_item.get(wid)
        return (
            boundary is not None
            and last is not None
            and last["seq"] > boundary["seq"]
            and _turn_live(last["session_status"], last["created_at"])
        )

    chains = {r["id"]: store.chain_view(r) for r in rows}
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "repo": r["repo"],
            "status": r["status"],
            "chain_template": r["chain_template"],
            # `store.chain_view`, not the raw column: a V1 row's
            # `chain_definition` is `"{}"`, and the board draws its stage bar
            # and names the current node from `chain_definition.nodes`.
            "chain_definition": chains[r["id"]],
            "current_node_id": r["current_node_id"],
            "bead_id": r["bead_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "pending_gate": pending.get(r["id"]),
            "attachments": json.loads(r["attachments"]) if r["attachments"] else [],
            "retry_at": r["retry_at"],
            "archived_at": r["archived_at"],
            "archived_by": r["archived_by"],
            "progress": _board_progress(st, r),
            "fallback": _ran_on_fallback(latest, r["id"]),
            "display_status": display_status(
                r,
                _stop_kind(
                    r["stop_kind"],
                    r["status"],
                    pending.get(r["id"]),
                    _boundary_payload(boundary_by_item.get(r["id"])),
                ),
                _list_escalated(r["id"]),
                pending.get(r["id"]),
            ),
            "stop": _list_stop(r, pending.get(r["id"]), boundary_by_item.get(r["id"])),
            "step": _step_of(r, chains[r["id"]], task_by_item.get(r["id"]), with_task=True),
            "mr_ref": _pick_mr_ref(
                r["id"] in root_mr_by_item,
                root_mr_by_item.get(r["id"]),
                mr_event_by_item.get(r["id"]),
            ),
        }
        for r in rows
    ]
    return {"items": items, "cursor": cursor}


def _boundary_payload(boundary) -> dict | None:
    """The `work_item_needs_human` payload of a list row's latest stop-boundary
    event, or None when that event is not a stop."""
    if boundary is not None and boundary["type"] == "work_item_needs_human":
        return json.loads(boundary["payload"])
    return None


def _list_stop(row, pending_gate: str | None, boundary) -> dict | None:
    """`stop` on the list (B.4): like the detail's, minus `facts`, `task` and
    `attempt` -- the board reads no sessions per row, and `boundary` is this
    item's latest `_STOP_BOUNDARY` row from the one grouped query `_read`
    already ran, not a per-item scan."""
    status = row["status"]
    if status not in STOPPED:
        return None
    payload = _boundary_payload(boundary)
    reason = payload["reason"] if payload else None
    return {
        "kind": _stop_kind(row["stop_kind"], status, pending_gate, payload),
        "node": row["current_node_id"],
        "resume_at": row["retry_at"],
        "reason": reason,
    }


def _ran_on_fallback(latest: dict, wid: str) -> dict | None:
    """The `launch_fallback` payload whose launch is the item's latest session
    start, or None: the item's current or last launch ran on a fallback."""
    switch = latest.get((wid, "launch_fallback"))
    started = latest.get((wid, "worker_session_started"))
    if switch is None or started is None or switch.get("to") is None:
        return None
    return switch if started.get("session_id") == switch.get("session_id") else None


def _board_progress(st, row) -> dict | None:
    """The board's share of `progress`: position and title, not the task list."""
    p = progress_mod.for_item(st.db, row, st.run_dirs.worktrees / row["id"])
    return {"current": p.current, "total": p.total, "title": p.title} if p else None


def _completed_nodes(st, wid: str) -> set[str]:
    return {
        e["payload"].get("node_id")
        for e in st.db.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "node_completed"
    }


def _deferred_findings(st, wid: str) -> list[dict]:
    """Findings that never entered the loop, for the human at the gate.

    A roll-up nobody reads is a silent discard, so these are rendered at the
    gate rather than merely recorded. The computation lives in
    `executor.deferred_findings` because the review brief needs the same list
    (Kraft-s7c04.4) and the gate and the brief must not be able to disagree
    about what was deferred.
    """
    loop_severities = getattr(st.policy, "loop_severities", policy_mod.DEFAULT_LOOP_SEVERITIES)
    return executor.deferred_findings(st.db, wid, loop_severities)


def _judge_stop_notes(st, wid: str) -> list[dict]:
    """Findings a judge chose to stop chasing (`stop_downgrade`), for the
    human at review.

    Distinct from `_deferred_findings`: these are `critical`/`important`
    findings a judge decided not to keep looping on, not the genuinely
    `minor` ones that never entered the loop at all -- conflating the two
    would make a judge-skipped real bug look like routine minor-finding
    triage.

    Bounded at the last resolved gate, the same way `_concerns` below is and
    for the same reason (Kraft-ub2): once a human approves the gate that
    showed this note, re-posing it at every later gate the item reaches asks
    them to answer for it again.
    """
    out: list[dict] = []
    for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid))):
        if e["type"] in ("gate_approved", "gate_rejected"):
            break
        if e["type"] == "judge_verdict" and e["payload"].get("verdict") == "stop_downgrade":
            out.append(
                {
                    "node_id": e["payload"].get("node_id"),
                    "reasoning": e["payload"].get("reasoning", ""),
                    "findings": e["payload"].get("findings", []),
                }
            )
    out.reverse()  # oldest first
    return out


def _concerns(st, wid: str) -> list[str]:
    """`done_with_concerns` text from every session that reported one, oldest
    first — the same shape of thing as `_deferred_findings` (something a
    machine noticed and owes a human before approval), read from the event log
    `adapters.subprocess.run_task` stamps at session exit, never from
    `result_path` on disk.

    Bounded at the last resolved gate, the way `_stop_reason` is: spec §2 puts a
    concern at the *next* gate, and the detail screen now passes concerns to
    every gate rather than only `human_review_approval`. Without a boundary one
    concern would be re-posed at every later gate the item reaches, long after
    the human who approved that gate already answered for it (Kraft-ub2).

    Excludes judge sessions the same way `walk._diagnosis_bundle` does: a judge
    is told `concerns` is required on every verdict (JUDGE_PROMPT/SKILL.md), so
    a plain `continue` verdict's reasoning would otherwise show up here as a
    concern the human owes an answer for, rather than the judge's own routine
    chatter.
    """
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    snapshot = store.materialized_chain_of(row) if row is not None else None
    # A judge's session is recorded under its canonical path
    # (`<node>.fix_loop.judge`), the key `walk._diagnosis_bundle` skips too.
    judges = [n.judge.path for n in snapshot.chain.nodes if n.judge] if snapshot else []
    judge_session_ids = st.db.read(
        lambda c: {
            r["id"]
            for r in c.execute(
                "SELECT id FROM worker_sessions WHERE work_item_id = ? AND hook_point IN "
                f"({','.join('?' * len(judges))})",
                (wid, *judges),
            ).fetchall()
        }
    )
    out: list[str] = []
    for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid))):
        if e["type"] in ("gate_approved", "gate_rejected"):
            break
        if (
            e["type"] == "worker_session_exited"
            and e["payload"].get("concerns")
            and e["payload"].get("session_id") not in judge_session_ids
        ):
            out.append(e["payload"]["concerns"])
    out.reverse()  # oldest first
    return out


def _mr_ref(st, wid: str) -> dict | None:
    """The most recent `mr_opened` event's `{number, url}`, or None before
    `open_mr` has ever run.

    A single-repo item gets no `work_item_repos` row (`repos_for`'s own
    docstring), so its merge request has nowhere to live but the event log --
    `adapters.forge.run_task` emits `mr_opened` for exactly this reason
    (Kraft-d2sq: "an event, not a work-item column"). Reversed scan, first hit
    wins, same as `_stop_reason`: a retried `open_mr` that reused the existing
    MR still logs a fresh event, so this always names the current one, not a
    stale first-open URL for a since-force-pushed branch.

    A multi-repo item's `open_mr` node emits one `mr_opened` per target repo
    (root and every declared submodule), and the event carries no repo
    identifier to tell them apart, so the events are never read for one: the
    latest would as often name a submodule's merge request as the root's.
    Its root's own row records the root's merge request (Kraft-mjsf), and
    that is the item's; a root with none has no one merge request to link.
    """
    repo_rows = st.db.read(lambda c: store.repos_for(c, wid))
    root = next((r["mr_ref"] for r in repo_rows if r["role"] == "root"), None)
    last = next(
        (
            e["payload"]
            for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid)))
            if e["type"] == "mr_opened"
        ),
        None,
    )
    return _pick_mr_ref(bool(repo_rows), root, last)


def _pick_mr_ref(multi_repo: bool, root_ref, last_event: dict | None) -> dict | None:
    """`_mr_ref`'s rule on data already read: a multi-repo item's root row is
    its merge request (a JSON string on the list's raw rows, a dict from
    `repos_for`); otherwise the latest `mr_opened` payload."""
    if multi_repo:
        return json.loads(root_ref) if isinstance(root_ref, str) else root_ref
    return {"number": last_event["number"], "url": last_event["url"]} if last_event else None


async def _mr_state(st, row) -> str | None:
    """The item's merge request's live state, for the cancel preview and the
    close-on-cancel check: the recorded ref is only `{number, url}`, so this is
    one `find_mr` call. None when the forge no longer knows it. Raises
    `ForgeError`; the preview catches it, the close reports it."""
    worktree = st.run_dirs.worktrees / row["id"]
    repo_entry = deps.launch(st, row["repo"]).repo_entry
    backend = forge_mod.backend_for("auto", repo_entry.forge if repo_entry else None)
    found = await forge_mod.resolve(backend).find_mr(repo=worktree, branch=store.branch_for(row))
    return found.state if found else None


def _running_session(st, wid: str) -> dict | None:
    """The session a cancel would stop (D.2's `running`): the same row
    `store.running_sessions_for_node` finds, read for the fields the preview
    shows rather than the ones a signal needs."""
    s = st.db.read(
        lambda c: c.execute(
            "SELECT s.node_id, s.hook_point, s.attempt, s.started_at FROM worker_sessions s "
            "JOIN work_items w ON w.id = s.work_item_id "
            "WHERE s.work_item_id = ? AND s.status IN ('running', 'pending') "
            "AND (s.node_id = w.current_node_id OR s.hook_point = 'escalation') "
            "ORDER BY s.created_at DESC LIMIT 1",
            (wid,),
        ).fetchone()
    )
    return (
        {
            "node": s["node_id"],
            "task": s["hook_point"],
            "attempt": s["attempt"],
            "started_at": s["started_at"],
        }
        if s
        else None
    )


def _open_thread_count(st, wid: str) -> int:
    threads = st.db.read(lambda c: store.threads_for(c, wid))
    return sum(1 for t in threads if t["state"] != "resolved")


# B4.
@api_router.get("/work-items/{wid}/cancel-preview")
async def cancel_preview(wid: str, request: Request):
    """What `/cancel` would do: read-only, so the UI can show it before
    the person commits. Refuses the same way `/cancel` itself does, once the
    item has already ended (`_live_work_item_row`)."""
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    cap_usd, _source = store.effective_work_item_cap(row, budget)
    spent_usd, _daily = st.db.read(lambda c: store.budget_spend(c, wid))
    ref = _mr_ref(st, wid)
    mr = None
    if ref is not None:
        try:
            state = await _mr_state(st, row)
        except forge_mod.ForgeError:
            # Read-only: a forge that cannot answer leaves the state unknown
            # rather than failing the preview of a cancel that needs no forge.
            state = None
        mr = {"ref": ref["number"], "url": ref["url"], "state": state}
    return {
        "running": _running_session(st, wid),
        "kept": {
            "branch": store.branch_for(row),
            "worktree": str(st.run_dirs.worktrees / wid),
            "findings": len(_deferred_findings(st, wid)),
            "threads": _open_thread_count(st, wid),
        },
        "mr": mr,
        "spend": {"spent_usd": spent_usd, "cap_usd": cap_usd},
        # The beads a hand completion closes when asked: the item's own, those it
        # implements, and those its commits' `Fixes`/`Closes` trailers name.
        "beads": executor.named_beads(row, st.run_dirs),
    }


def _needs_context_question(st, wid: str) -> str | None:
    """The agent's question, straight from the `needs_context: <question>`
    reason `_needs_context_stop` already trusts — not a scan of
    `worker_session_exited.question` events, which has no boundary at the
    triggering stop: a later needs_context whose result file omitted the
    field would otherwise resurface an earlier, already-answered question
    instead of falling through to
    `kraft.executor.dispatch.needs_context_question`'s own
    `"(no question given)"` guard, which the reason string always carries.
    """
    reason = _stop_reason(st, wid)
    if reason is None or not reason.startswith("needs_context:"):
        return None
    return reason.removeprefix("needs_context: ")


def _rate_limit_retries(st, row) -> dict | None:
    """06's rate-limited sub-row: 'N of M relaunches used'. Reads the same
    counter `rate_limit_retry._retry_one` bumps rather than keeping a second
    one -- None off a rate-limited item (nothing to show), or when there is
    no current node to key the counter by."""
    if row["status"] != WorkItemStatus.RATE_LIMITED or row["current_node_id"] is None:
        return None
    counter = st.db.read(
        lambda c: store.read_counter(c, row["id"], f"rate_limit:{row['current_node_id']}")
    )
    cap = st.policy.rate_limit_retries if st.policy else 5
    return {"count": counter["count"] if counter else 0, "cap": cap}


def _legacy_needs_human_kind(payload: dict | None) -> str:
    """The kind of a `needs_human` row from before migration 45, which has no
    `stop_kind`: what its `work_item_needs_human` event still says (the
    `needs_context:` reason, a `budget` or `capped` figure), else `failed`: a
    stop nobody can name is one a person has to look at, and `failed` is the
    kind that offers Retry."""
    payload = payload or {}
    if payload.get("kind"):
        return payload["kind"]
    if str(payload.get("reason") or "").startswith("needs_context:"):
        return "question"
    if "budget" in payload:
        return "budget"
    if "capped" in payload:
        return "cap"
    return "failed"


def _stop_kind(
    stop_kind: str | None, status: str, pending_gate: str | None, payload: dict | None = None
) -> str:
    """`stop.kind` (B.3): the stored `stop_kind`, unless a gate is pending
    (`gate` -- a gate stop writes no `stop_kind`, it is `gate_requested`), or
    the row predates migration 45 and carries none. Then the status says
    whether it was a wait or a rate limit, and a `needs_human` row is read off
    its stop event (`payload`): never called a rate limit."""
    if pending_gate:
        return "gate"
    if stop_kind is not None:
        return stop_kind
    if status == "waiting":
        return "wait"
    if status == "needs_human":
        return _legacy_needs_human_kind(payload)
    return "rate_limit"


def _stop_task_and_attempt(sessions, node_id: str | None) -> tuple[str | None, int | None]:
    """The `hook_point`/`attempt` of `node_id`'s latest session that is not an
    escalation turn -- `sessions` ordered oldest first, as `get_work_item`
    already loads them, so the latest match scanning backward is the one."""
    for s in reversed(sessions):
        if s["node_id"] == node_id and s["hook_point"] != "escalation":
            return s["hook_point"], s["attempt"]
    return None, None


def _step_of(row, chain: dict, task: str | None, *, with_task: bool = False) -> dict | None:
    """The current node's step (1-based) out of its steps, when it declares
    more than one and `task` (a `node.step.task` path) is in one of them;
    `with_task` adds the step's id and the task's, which the list row draws
    as `step › task` (the detail's `summary.step` does not)."""
    node = next((n for n in chain.get("nodes") or [] if n["id"] == row["current_node_id"]), None)
    steps = node.get("steps") if node else None
    if not steps or len(steps) < 2:
        return None
    index = next((i for i, group in enumerate(steps) if task in group), None)
    if index is None:
        return None
    step = {"index": index + 1, "count": len(steps)}
    if not with_task:
        return step
    parts = task.split(".", 2)
    # A path with no step or task segment (a hand-written chain) names neither.
    return {**step, "name": parts[1], "task": parts[2]} if len(parts) == 3 else step


def _summary(st, wid: str, row, chain: dict, sessions) -> dict:
    """`summary` on the detail response (H.4): a run's progress at a glance --
    nodes done out of the frozen chain's total, how many of those were gates,
    and the current node's step (1-based) out of its steps when it declares
    more than one.
    """
    nodes = chain.get("nodes") or []
    completed = _completed_nodes(st, wid)
    gates_passed = sum(1 for n in nodes if n["id"] in completed and n.get("gate_after") is not None)
    task, _attempt = _stop_task_and_attempt(sessions, row["current_node_id"])
    step = _step_of(row, chain, task)
    return {
        "nodes_done": len(completed),
        "nodes_total": len(nodes),
        "gates_passed": gates_passed,
        "step": step,
    }


def _rate_limit_facts(st, row, task_path: str | None) -> dict:
    """B6: a rate-limited stop's `retries`, plus -- when the stopped task's
    frozen chain names a `fallback:` -- that list and the subset its resolved
    policy still allows. Reuses `MaterializedChain.policy_for`, the same
    resolution `TemplateLibrary.lint`'s `fallback-never-escapes-allowed-
    harnesses` check already trusts, rather than re-deriving it."""
    facts: dict = {"retries": _rate_limit_retries(st, row)}
    if task_path is None:
        return facts
    chain = store.materialized_chain_of(row)
    if chain is None:
        return facts
    task = next(
        (t for node in chain.chain.nodes for t in node.tasks() if t.path == task_path), None
    )
    from kraft.templates.models import AgentTask  # deferred: pulls in policy/harness schema

    if not isinstance(getattr(task, "task", None), AgentTask):
        return facts
    fallback = [e.harness for e in task.task.fallback or () if e.harness]
    if not fallback:
        return facts
    allowed = chain.policy_for(task).allowed_harnesses
    facts["fallback"] = fallback
    facts["fallback_allowed"] = (
        fallback if allowed is None else [h for h in fallback if h in allowed]
    )
    return facts


def _test_result(st, row) -> dict | None:
    """`test_result` on the detail response: the latest changed-test-scope
    verification run, one entry per scope that finished, or None when there is
    no such run. An area's setup that passed is not a scope and is left out;
    one that failed is its scopes' result and stays."""
    chain = store.materialized_chain_of(row)
    if chain is None:
        return None
    from kraft.executor import dispatch  # deferred, as `_rate_limit_facts` defers the schema
    from kraft.templates.models import BuiltinAction, BuiltinTask

    verifies = [
        (node, t)
        for node in reversed(chain.chain.nodes)
        for t in node.tasks()
        if isinstance(t.task, BuiltinTask)
        and t.task.ref is BuiltinAction.VERIFY_CHANGED_TEST_SCOPES
    ]
    if not verifies:
        return None
    repo_entry = deps.launch(st, row["repo"]).repo_entry
    for node, t in verifies:
        try:
            results = dispatch.scope_results(st.db, row["id"], node.id, t.path, repo_entry)
        except config_mod.ConfigError:
            # An unreadable repos.yaml costs the scope names, not the detail.
            results = dispatch.scope_results(st.db, row["id"], node.id, t.path, None)
        scopes = [
            {
                "command": r["command"],
                "scope": r.get("scope"),
                "passed": r["passed"],
                "exit_code": r.get("exit_code"),
                "session_id": r["session_id"],
            }
            for r in results
            if not (r.get("setup") and r["passed"])
        ]
        if scopes:
            return {"scopes": scopes, "passed": all(x["passed"] for x in scopes)}
    return None


def _scope_runs(st, row) -> list[dict]:
    """`scope_runs` on the detail response: every command each changed-test-scope
    task ran, over every round and repository (`dispatch.scope_runs`), for the
    node's view to draw a round's scopes beside the round before it. Empty for
    a chain with no such task."""
    chain = store.materialized_chain_of(row)
    if chain is None:
        return []
    from kraft.executor import dispatch  # deferred, as `_test_result` defers it
    from kraft.templates.models import BuiltinAction, BuiltinTask

    verifies = [
        (node, t)
        for node in chain.chain.nodes
        for t in node.tasks()
        if isinstance(t.task, BuiltinTask)
        and t.task.ref is BuiltinAction.VERIFY_CHANGED_TEST_SCOPES
    ]
    if not verifies:
        return []
    launch = deps.launch(st, row["repo"])

    def entry(repository: str | None):
        # A member repository's own table; the item's own for the root and a lone repo.
        return (launch.repositories.get(repository) if repository else None) or launch.repo_entry

    def live(node) -> bool:
        # Whether the walk is still on this node, so a command it picked can still start.
        return row["current_node_id"] == node.id and row["status"] in COMMANDS_MAY_START

    runs = []
    for node, t in verifies:
        try:
            found = dispatch.scope_runs(st.db, row["id"], node.id, t.path, entry, live=live(node))
        except config_mod.ConfigError:
            # An unreadable repos.yaml costs the scope names, not the detail.
            found = dispatch.scope_runs(
                st.db, row["id"], node.id, t.path, lambda _: None, live=live(node)
            )
        runs += [{**r, "node_id": node.id, "hook_point": t.path} for r in found]
    return runs


def _stop(st, row, sessions, pending_gate: str | None, stop_payload: dict | None) -> dict | None:
    """`stop` on the detail response (B.3): `None` unless the item is
    currently `needs_human`, `waiting` or `rate_limited`."""
    status = row["status"]
    if status not in STOPPED:
        return None
    node = row["current_node_id"]
    task, attempt = _stop_task_and_attempt(sessions, node)
    kind = _stop_kind(row["stop_kind"], status, pending_gate, stop_payload)
    facts = dict(stop_payload.get("facts") or {}) if stop_payload else {}
    if kind == "rate_limit":
        facts.update(_rate_limit_facts(st, row, task))
    limit = _stop_limit(row, stop_payload)
    # Which cap stopped a budget stop (`caps.Breach.scope`): `work_item`, the
    # item's own, is the one `/budget/raise` takes; it refuses the rest.
    scope = ((stop_payload or {}).get("budget") or {}).get("scope") if kind == "budget" else None
    return {
        "kind": kind,
        "node": node,
        "task": task,
        "attempt": attempt,
        "resume_at": row["retry_at"],
        "reason": stop_payload["reason"] if stop_payload else None,
        "facts": facts,
        **({"limit": limit} if limit else {}),
        **({"scope": scope} if scope else {}),
    }


def _stop_limit(row, stop_payload: dict | None) -> dict | None:
    """`stop.limit` on a cap stop: the item-policy field that raises the limit
    that stopped it, `{path, key, value, maximum}`. `maximum` is the
    administrator ceiling now (None where there is none), read off the item's
    frozen policy; the rest was written when the item stopped."""
    limit = stop_payload.get("limit") if stop_payload else None
    chain = store.materialized_chain_of(row) if limit else None
    if limit is None or chain is None:
        return None
    maxima = chain.policy.maxima
    if limit["key"] in SCOPE_CAP_FIELDS:
        bound = maxima.nearest("work_item", limit["key"])
        maximum = bound[1] if bound else None
    else:
        maximum = getattr(maxima, limit["key"])
    return {**limit, "maximum": maximum}


@api_router.get("/work-items/{wid}/fix-target")
async def get_fix_target(
    wid: str, request: Request, node: str | None = None, gate: str | None = None
):
    """Where the work restarts if the reviewer requests changes now.

    With a gate pending this is `fix_target`; with none it is what a gateless
    `request_changes` would target (`review.changes_target`), which costs git
    calls per thread and so is not on the polled item detail. `reason` says why.
    """
    from kraft import review as review_mod
    from kraft.api.routes import gates as gate_routes

    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    pending = _pending_gate(st, wid)
    if gate is not None and gate != pending:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    if pending:
        try:
            return {**fix_target(st, row, pending, node), "reason": "requested" if node else "gate"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    nodes = gate_routes.gate_nodes(st, row)
    current = next((i for i, n in enumerate(nodes) if n.id == row["current_node_id"]), None)
    if current is None:
        raise HTTPException(409, "this work item has no current node")
    if node:
        idx = next((i for i, n in enumerate(nodes) if n.id == node), None)
        if idx is None or idx > current:
            raise HTTPException(
                400, f"cannot request changes at {node!r}: not at or before the current node"
            )
        why = "requested"
    else:
        threads = st.db.read(lambda c: store.threads_for(c, wid))
        runs = st.db.read(lambda c: store.node_run_rows(c, wid))
        try:
            idx, why = review_mod.changes_target(
                st.run_dirs.worktrees / wid, nodes, current, threads, runs
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {
        "gate": None,
        "node": nodes[idx].id,
        "then": [n.id for n in nodes[idx + 1 : current + 1]],
        "round": None,
        "reason": why,
    }


def _item_wide_budget_usd(row) -> float | None:
    """The item-wide `budget_usd` its frozen policy holds it to, the item's own
    override folded in (`caps.budget_breach` reads the same), or None for no
    such cap or a V1 item."""
    chain = store.materialized_chain_of(row)
    cap = chain.work_item_policy().budget_usd if chain is not None else None
    return None if cap in (None, math.inf) else float(cap)


def budget_cap(st, row) -> dict:
    """The item's effective dollar cap, where it comes from, its spend, and the
    instance's spend today against the daily cap.

    Two caps hold an item: its own (`budget_usd`, else the policy's
    `work_item_usd`) and the item-wide `budget_usd` of its chain policy, which
    a stop's Raise cap writes into the item's policy override. Whichever is
    lower stops it first, so that one is reported. `key` names the field a
    `PATCH` changes it with, and `source` is `item` when the item set it."""
    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    cap_usd, source = store.effective_work_item_cap(row, budget)
    key = "budget_usd"
    item_wide = _item_wide_budget_usd(row)
    if item_wide is not None and (cap_usd is None or item_wide < cap_usd):
        override = store.policy_override_of(row)
        own = override is not None and override.budget_usd not in (None, policy_mod.NO_CAP)
        cap_usd, key, source = item_wide, "policy.budget_usd", "item" if own else "policy"
    since = store.local_midnight_utc()
    spent_usd, daily_spent_usd = st.db.read(lambda c: store.budget_spend(c, row["id"], since=since))
    return {
        "cap_usd": cap_usd,
        "source": source,
        "key": key,
        "spent_usd": spent_usd,
        "daily": {"spent_usd": daily_spent_usd, "cap_usd": budget.daily_usd},
    }


@api_router.get("/work-items/{wid}")
async def get_work_item(wid: str, request: Request):
    from kraft.api.routes import lifecycle

    st = request.app.state
    row = deps._work_item_row(st, wid)
    sessions = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at", (wid,)
        ).fetchall()
    )
    pending = _pending_gate(st, wid)
    stop_payload, escalated = _stop_episode(st, wid)
    # Over both chain shapes, so a V1 item's stage bar is *correct* rather than
    # merely not crashing. `steerable` below reads the frozen snapshot directly.
    chain = store.chain_view(row)
    payload = store.work_item_payload(row)
    node_overrides = payload["node_overrides"]
    progress = progress_mod.for_detail(st.db, row, st.run_dirs.worktrees / wid)
    return {
        # The override columns decoded, as every action and gate echo has them.
        **payload,
        "chain_definition": chain,
        # The Config tab's "effective chain" (UI v2 · 04 point 3): node
        # overrides folded over the template-shaped chain, plus the raw
        # override layer itself and its count, so the tab can both render the
        # merged YAML and mark which lines are `# override`.
        "effective_chain": store.effective_chain(chain, node_overrides),
        "node_overrides_count": len(node_overrides),
        # The Config tab's "$5.00 · $2.41 used" and "policy default" / "item"
        # source line (point 4). Deliberately not `budget` -- that key is
        # `item.budget` client-side, `{scope, spent_usd, cap_usd} | null`,
        # derived purely from a `work_item_needs_human` event's payload
        # (`store.applyEvent`) and used by the shipped UI's budget card to mean
        # "a spend cap is what stopped this item right now". This is a
        # different, always-present question -- the item's effective cap and
        # where it comes from -- and reusing the name would make every GET
        # response's truthy `budget` object read as "the item is stopped for
        # budget" even when it is running fine under its cap.
        "budget_cap": budget_cap(st, row),
        # The time budget beside the dollars: what the item-wide running-time
        # cap has measured and allows (`caps.running_time`).
        "running_time": st.db.read(lambda c: caps.running_time(c, row)),
        "rate_limit": _rate_limit_retries(st, row),
        "attachments": json.loads(row["attachments"]) if row["attachments"] else [],
        "worker_sessions": [{k: s[k] for k in s.keys()} for s in sessions],
        "usage": st.db.read(lambda c: store.usage_rollup(c, wid)),
        # Where the implementer is in its plan ("3 of 6 · title"), kept once it
        # stops on the implementation node or finishes it; None before that node
        # or for a plan with no `## Task N` headings.
        "progress": progress.model_dump() if progress else None,
        # empty on a single-repo item; the detail's repos panel is multi-repo only
        "repos": st.db.read(lambda c: store.repos_for(c, wid)),
        # One entry per escalation thread this item has had, oldest first
        # (Kraft-dkb6g) -- so the UI can render thread headers without
        # scanning every session/event itself.
        "escalation_threads": st.db.read(lambda c: store.escalation_threads(c, wid)),
        # local-only: the checkout the agents are editing, for "Open worktree"
        "worktree_path": str(st.run_dirs.worktrees / wid),
        # Whether that directory is there: a reclaimed or deleted worktree
        # leaves the path above pointing at nothing.
        "worktree_exists": (st.run_dirs.worktrees / wid).is_dir(),
        # What the *diff on screen* is, so the gate can tell a measurement taken
        # on this commit from one taken three commits ago (Kraft-lu2).
        # `git_read` returns None for a worktree that does not exist yet.
        "head_sha": config_mod.git_read(st.run_dirs.worktrees / wid, "rev-parse", "HEAD"),
        # The gate actually waiting on a person. Inferring it client-side from
        # "the node has a gate_after and its sessions are done" cannot see a
        # rejection, and offers Approve on a gate the API will 409 (Kraft).
        "pending_gate": pending,
        # A gateless `request_changes` waiting to be honoured at its target
        # node -- by the running node's own completion, the next `/resume`,
        # or the next `/retry` with no path (review threads anywhere §1).
        "pending_rewind": st.db.read(lambda c: store.pending_rewind(c, wid)),
        # The review flow's compare picker and "runs X again" sentence (spec §2).
        "attempts": st.db.read(lambda c: store.gate_attempts(c, wid, pending)) if pending else [],
        "last_review_sha": _last_review_sha(st, wid),
        "reject_default": _reject_default(st, row, pending),
        "fix_target": fix_target(st, row, pending) if pending else None,
        # The document the gate is a decision about — the spec at
        # spec_approval, the plan at plan_approval. The detail screen offers
        # "Review spec" only when this is set.
        "gate_artifact": _gate_artifact(st, row, pending),
        # Why the item is stopped, when it is: the detail screen has to tell a
        # loop escalation from an unrelated crash on the same node (Kraft-esc).
        "stop_reason": stop_payload["reason"] if stop_payload else None,
        # What the chain or a repair concluded a person should do about that
        # stop -- `{action: skip|retry|abandon, reason}` -- or None
        # (Kraft-s7c04.27). Each action is one existing verb.
        "suggested_action": (stop_payload or {}).get("suggested_action"),
        # The board's status badge (Kraft UI v2 · B1), and the stop it names
        # when there is one -- {kind, node, task, attempt, resume_at, reason,
        # facts} -- `None` off `needs_human`/`waiting`/`rate_limited`.
        "display_status": display_status(
            row,
            _stop_kind(row["stop_kind"], row["status"], pending, stop_payload),
            escalated,
            pending,
        ),
        "stop": _stop(st, row, sessions, pending, stop_payload),
        "test_result": _test_result(st, row),
        "scope_runs": _scope_runs(st, row),
        # A run's progress at a glance (Kraft UI v2 · B13): nodes done out of
        # the frozen chain's total, how many were gates, and the current
        # node's step when it declares more than one.
        "summary": _summary(st, wid, row, chain, sessions),
        "deferred_findings": _deferred_findings(st, wid),
        "judge_stop_note": _judge_stop_notes(st, wid),
        "concerns": _concerns(st, wid),
        "needs_context_question": _needs_context_question(st, wid),
        # The root repo's merge request, once `open_mr` has run -- the detail
        # screen's one link out to the forge.
        "mr_ref": _mr_ref(st, wid),
        # Whether a steer given now would be accepted (Kraft-bz9b, Ruling
        # 183): the detail screen uses this to drop the steer box entirely
        # rather than offer text the route would 409 on. A paused item needs
        # a paused agent task; a stranded one an agent task downstream, failing
        # open (True) when the node isn't in its own chain.
        "steerable": lifecycle.steerable(st, row),
    }


#: The window `before_seq` alone returns, with no `limit` given (rule H.3).
_DEFAULT_PAGE = 100


@api_router.get("/work-items/{wid}/events")
async def get_events(
    wid: str,
    request: Request,
    after_seq: int | None = None,
    before_seq: int | None = None,
    limit: int | None = None,
):
    st = request.app.state
    deps._work_item_row(st, wid)
    if after_seq is not None and before_seq is not None:
        raise HTTPException(422, "after_seq and before_seq cannot both be given")
    if limit is not None and not 1 <= limit <= 500:
        raise HTTPException(422, "limit must be between 1 and 500")
    if before_seq is not None:
        return st.db.read(
            lambda c: events.read_before(c, before_seq, wid, limit=limit or _DEFAULT_PAGE)
        )
    return st.db.read(lambda c: events.read_after(c, after_seq or 0, wid, limit=limit))


@api_router.get("/work-items/{wid}/documents")
async def get_work_item_documents(wid: str, request: Request):
    st = request.app.state
    deps._work_item_row(st, wid)  # 404s on an unknown work item
    return {"work_item_id": wid, "documents": st.indexer.documents_for_work_item(wid)}
