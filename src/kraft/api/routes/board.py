from __future__ import annotations

import json

from fastapi import Request

from kraft import config as config_mod
from kraft import events, executor, store
from kraft import policy as policy_mod
from kraft import progress as progress_mod
from kraft.api import api_router, deps


def _pending_gate(st, wid: str) -> str | None:
    return executor.pending_gate(st.db, wid)


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


def _current_stop(st, wid: str) -> dict | None:
    """The `work_item_needs_human` payload of the stop the item is *currently*
    sitting on, or None if anything in `_STOP_BOUNDARY` superseded it."""
    for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid))):
        if e["type"] in _STOP_BOUNDARY:
            return e["payload"] if e["type"] == "work_item_needs_human" else None
    return None


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
            "SELECT * FROM work_items WHERE (? OR status != 'abandoned') "
            "AND (archived_at IS NOT NULL) = ? ORDER BY created_at",
            (include_abandoned, archived),
        ).fetchall()
        cursor = c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        # The latest gate_* event per item, in one pass — the board renders a gate
        # prompt per row and must not offer Approve on a rejected gate.
        gates = c.execute(
            "SELECT work_item_id, type, payload FROM events WHERE seq IN ("
            "  SELECT MAX(seq) FROM events"
            "  WHERE type IN ('gate_requested', 'gate_approved', 'gate_rejected')"
            "  GROUP BY work_item_id)"
        ).fetchall()
        return rows, cursor, gates

    rows, cursor, gate_rows = st.db.read(_read)
    pending = {
        g["work_item_id"]: json.loads(g["payload"])["gate"]
        for g in gate_rows
        if g["type"] == "gate_requested"
    }
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
            "chain_definition": store.chain_view(r),
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
        }
        for r in rows
    ]
    return {"items": items, "cursor": cursor}


def _board_progress(st, row) -> dict | None:
    """The board's share of `progress`: position and title, not the task list."""
    p = progress_mod.for_item(st.db, row, st.run_dirs.worktrees / row["id"])
    return {k: p[k] for k in ("current", "total", "title")} if p else None


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
    if rows := st.db.read(lambda c: store.repos_for(c, wid)):
        return next((r["mr_ref"] for r in rows if r["role"] == "root"), None)
    for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid))):
        if e["type"] == "mr_opened":
            return {"number": e["payload"]["number"], "url": e["payload"]["url"]}
    return None


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
    if row["status"] != "rate_limited" or row["current_node_id"] is None:
        return None
    counter = st.db.read(
        lambda c: store.read_counter(c, row["id"], f"rate_limit:{row['current_node_id']}")
    )
    cap = st.policy.rate_limit_retries if st.policy else 5
    return {"count": counter["count"] if counter else 0, "cap": cap}


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
    # Over both chain shapes, so a V1 item's stage bar is *correct* rather than
    # merely not crashing. `steerable` below reads the frozen snapshot directly.
    chain = store.chain_view(row)
    node_overrides = store.node_overrides_of(row)
    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    cap_usd, cap_source = store.effective_work_item_cap(row, budget)
    spent_usd, _daily = st.db.read(lambda c: store.budget_spend(c, wid))
    return {
        **{k: row[k] for k in row.keys()},
        "chain_definition": chain,
        # The Config tab's "effective chain" (UI v2 · 04 point 3): node
        # overrides folded over the template-shaped chain, plus the raw
        # override layer itself and its count, so the tab can both render the
        # merged YAML and mark which lines are `# override`.
        "effective_chain": store.effective_chain(chain, node_overrides),
        "node_overrides": node_overrides,
        "node_overrides_count": len(node_overrides),
        # The item's own policy override (Kraft-ab1bh), decoded from its column.
        "policy_override": json.loads(row["policy_override"] or "null"),
        # The Config tab's "$5.00 · $2.41 used" and "policy default" / "item"
        # source line (point 4). Deliberately not `budget` -- that key is
        # `item.budget` client-side, `{scope, spent_usd, cap_usd} | null`,
        # derived purely from a `work_item_needs_human` event's payload
        # (`store.applyEvent`) and used by `deriveState`/`BudgetCard` to mean
        # "a spend cap is what stopped this item right now". This is a
        # different, always-present question -- the item's effective cap and
        # where it comes from -- and reusing the name would make every GET
        # response's truthy `budget` object read as "the item is stopped for
        # budget" even when it is running fine under its cap.
        "budget_cap": {"cap_usd": cap_usd, "source": cap_source, "spent_usd": spent_usd},
        "rate_limit": _rate_limit_retries(st, row),
        "attachments": json.loads(row["attachments"]) if row["attachments"] else [],
        "worker_sessions": [{k: s[k] for k in s.keys()} for s in sessions],
        "usage": st.db.read(lambda c: store.usage_rollup(c, wid)),
        # Where the implementer is in its plan ("3 of 6 · title"), or None off the
        # implementation node or for a plan with no `## Task N` headings.
        "progress": progress_mod.for_item(st.db, row, st.run_dirs.worktrees / wid),
        # empty on a single-repo item; the detail's repos panel is multi-repo only
        "repos": st.db.read(lambda c: store.repos_for(c, wid)),
        # One entry per escalation thread this item has had, oldest first
        # (Kraft-dkb6g) -- so the UI can render thread headers without
        # scanning every session/event itself.
        "escalation_threads": st.db.read(lambda c: store.escalation_threads(c, wid)),
        # local-only: the checkout the agents are editing, for "Open worktree"
        "worktree_path": str(st.run_dirs.worktrees / wid),
        # What the *diff on screen* is, so the gate can tell a measurement taken
        # on this commit from one taken three commits ago (Kraft-lu2).
        # `git_read` returns None for a worktree that does not exist yet.
        "head_sha": config_mod.git_read(st.run_dirs.worktrees / wid, "rev-parse", "HEAD"),
        # The gate actually waiting on a person. Inferring it client-side from
        # "the node has a gate_after and its sessions are done" cannot see a
        # rejection, and offers Approve on a gate the API will 409 (Kraft).
        "pending_gate": pending,
        # The document the gate is a decision about — the spec at
        # spec_approval, the plan at plan_approval. The detail screen offers
        # "Review spec" only when this is set.
        "gate_artifact": _gate_artifact(st, row, pending),
        # Why the item is stopped, when it is: the detail screen has to tell a
        # loop escalation from an unrelated crash on the same node (Kraft-esc).
        "stop_reason": _stop_reason(st, wid),
        # What the chain or a repair concluded a person should do about that
        # stop -- `{action: skip|retry|abandon, reason}` -- or None
        # (Kraft-s7c04.27). Each action is one existing verb.
        "suggested_action": (_current_stop(st, wid) or {}).get("suggested_action"),
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


@api_router.get("/work-items/{wid}/events")
async def get_events(wid: str, request: Request, after_seq: int = 0):
    st = request.app.state
    deps._work_item_row(st, wid)
    return st.db.read(lambda c: events.read_after(c, after_seq, wid))


@api_router.get("/work-items/{wid}/documents")
async def get_work_item_documents(wid: str, request: Request):
    st = request.app.state
    deps._work_item_row(st, wid)  # 404s on an unknown work item
    return {"work_item_id": wid, "documents": st.indexer.documents_for_work_item(wid)}
