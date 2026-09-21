from __future__ import annotations

import json
import re
import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks

#: How much of the title goes into the branch name. A Kraft title is a
#: paragraph, not a headline (`forge.mr_title` notes a 360-character one), and
#: a branch name has to stay something a human can read in `git log --oneline`.
BRANCH_SLUG_MAX = 48


def branch_name(title: str, work_item_id: str) -> str:
    """`kraft/<slug>-<id[:8]>` — readable, unique, always a legal git ref.

    The `[a-z0-9-]` charset is what makes the result legal by construction: no
    `..`, no `@{`, no `.lock`, no control characters, no trailing dot. Two items
    collide only if their ids share an 8-hex prefix, and then `git worktree add
    -b` fails loudly rather than silently sharing a branch.

    A title that slugs to nothing — punctuation only, wholly non-ASCII, empty —
    falls back to `kraft/<id>`, which is what every branch looked like before
    Kraft-nhps: ugly, but always valid.
    """
    head = title.strip().splitlines()[0] if title.strip() else ""
    slug = re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-")[:BRANCH_SLUG_MAX].rstrip("-")
    return f"kraft/{slug}-{work_item_id[:8]}" if slug else f"kraft/{work_item_id}"


def branch_for(row) -> str:
    """The branch a work item's worktree lives on. The row is the truth.

    Rows written before the `branch` column existed hold NULL and keep the
    `kraft/<id>` branch their worktree, MR and abandon path already point at.
    """
    return row["branch"] or f"kraft/{row['id']}"


def create_work_item(
    conn: sqlite3.Connection,
    *,
    id,
    bead_id,
    title,
    repo,
    chain_template,
    chain_definition,
    description: str | None = None,
    submodules: list[str] | None = None,
    root_merge_policy: str | None = None,
    attachments: list[dict] | None = None,
    status: str = "active",
    bead_cwd: str | None = None,
    implements_beads: list[str] | None = None,
    #: Whether this item's `auto_escalate` gates may be reviewed by an agent
    #: before a human sees them (Kraft-zr3s). Off unless a human asked for it:
    #: the template says which gates *could* be, this says whether they *may*.
    auto_gate: bool = False,
    #: Intake-time spend cap and per-node overrides (UI v2 · 04 point 6).
    #: `budget_set` follows the same "explicit vs policy default" rule as
    #: `store.budget.set_budget` -- False leaves both columns at their
    #: defaults (policy default applies), True means the intake caller sent
    #: a `budget_usd` (a number, or `None` for an explicit "no cap").
    budget_set: bool = False,
    budget_usd: float | None = None,
    node_overrides: dict[str, dict] | None = None,
    #: Set when this item was filed by the auto-intake poller, not a person
    #: (`intake.py::_start`). Recorded on `work_item_created` only -- never a
    #: column -- so "recent pickups" and "last picked up per repo" (design 31)
    #: can be read back from the event log with no schema change.
    source: str | None = None,
    #: The bead's own priority at pickup time (P0-P4), carried the same way --
    #: Kraft's own row has no priority column, the bead does.
    bead_priority: int | None = None,
    #: `MaterializedChain.to_json()` — the immutable V1 input for this item.
    #: Defaulted so the legacy intake path is unchanged; NULL means this item
    #: runs off `chain_definition` (template schema V1, phase 2).
    materialized_chain: str | None = None,
) -> None:
    """`submodules` are the cross-repo paths chosen at intake (06, design 1g).

    They are stored as JSON rather than a side table: they are chosen once, never
    queried across items, and belong to this item as much as its chain does.

    `attachments` are the spec/plan documents chosen at intake (Kraft-dgh),
    stored as JSON for the same reason `submodules` is: chosen once, never
    queried across items.

    `implements_beads` are the sub-bead ids this item's description names
    (Kraft-p8q1) -- closed alongside `bead_id` on completion.
    """
    now = _now()
    conn.execute(
        "INSERT INTO work_items (id, bead_id, title, description, repo, chain_template, "
        "chain_definition, current_node_id, status, created_at, updated_at, "
        "submodules, root_merge_policy, attachments, bead_cwd, branch, implements_beads, "
        "auto_gate, budget_set, budget_usd, node_overrides, "
        "materialized_chain) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id,
            bead_id,
            title,
            description or None,
            repo,
            chain_template,
            chain_definition,
            status,
            now,
            now,
            json.dumps(submodules) if submodules else None,
            root_merge_policy if submodules else None,
            json.dumps(attachments) if attachments else None,
            bead_cwd,
            branch_name(title, id),
            json.dumps(implements_beads) if implements_beads else None,
            1 if auto_gate else 0,
            1 if budget_set else 0,
            budget_usd,
            json.dumps(node_overrides) if node_overrides else None,
            materialized_chain,
        ),
    )
    payload = {"title": title, "repo": repo, "chain_template": chain_template}
    if source:
        payload["source"] = source
        payload["bead_id"] = bead_id
        if bead_priority is not None:
            payload["priority"] = bead_priority
    events.append(conn, id, "work_item_created", payload)
    if attachments:
        # Its own event, not a field on work_item_created: the timeline has to
        # explain why this item's chain has no spec node.
        events.append(conn, id, "work_item_attachments", {"attachments": attachments})


def mark_needs_human(
    conn: sqlite3.Connection,
    work_item_id,
    node_id,
    reason,
    capped: dict | None = None,
    budget: dict | None = None,
    bundle: dict | None = None,
) -> None:
    """`capped` carries {cycles, attempts} when a loop cap is what stopped the item.

    The UI shows capped-out as a glyph *plus* the words "capped n/n", and the
    board never fetches sessions per row — so the numbers have to ride the
    event rather than be re-derived client-side.

    `budget` carries {scope, spent_usd, cap_usd} when a spend cap is what stopped
    the item: the card shows the figures and the board never fetches sessions per
    row, so the numbers ride the event for the same reason.

    `bundle` carries the stuck-detector's diagnosis (Kraft-39ep): whatever a
    human would otherwise reconstruct by hand from the worktree and the
    event log, gathered once at the moment Kraft gives up rather than asked
    for later.
    """
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', retry_at = NULL, updated_at = ? "
        "WHERE id = ?",
        (_now(), work_item_id),
    )
    payload = {"node_id": node_id, "reason": reason}
    # The reason names the hook that failed, never why -- that is in the failed
    # session's log. Naming the session here is what lets the timeline offer
    # "view log" on the one event a human actually lands on, instead of asking
    # them to notice the worker_session_exited row above it (Kraft-eh6p). Read
    # rather than threaded through every caller: a session id nobody passed is
    # a button nobody gets, and there are eight call sites.
    #
    # The node's *latest* session, and only if that one has something to explain.
    # Scanning for the latest `failed` session instead would hand a
    # needs_context stop -- or a budget stop, where no session ran at all -- the
    # log of some earlier, unrelated failure in the same node, which is worse
    # than no button: it looks like the answer and is not.
    last = conn.execute(
        "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (work_item_id, node_id),
    ).fetchone()
    if last is not None and last[1] in ("failed", "needs_context"):
        payload["session_id"] = last[0]
    if capped is not None:
        payload["capped"] = capped
    if budget is not None:
        payload["budget"] = budget
    if bundle is not None:
        payload["bundle"] = bundle
    events.append(conn, work_item_id, "work_item_needs_human", payload)


def mark_rate_limited(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, retry_at: str
) -> None:
    """The item hit an API rate limit; `rate_limit_retry.poller` relaunches it
    once `retry_at` passes, with nobody paged (unlike `mark_needs_human`)."""
    conn.execute(
        "UPDATE work_items SET status = 'rate_limited', retry_at = ?, updated_at = ? WHERE id = ?",
        (retry_at, _now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "work_item_rate_limited", {"node_id": node_id, "retry_at": retry_at}
    )


def mark_waiting(conn: sqlite3.Connection, work_item_id: str, node_id: str, retry_at: str) -> None:
    """The node is waiting on something outside Kraft (today: a pipeline).

    Sibling of `mark_rate_limited`, and deliberately shaped identically: the
    wait is a row the scheduler owns, not a coroutine holding a slot. A
    `waiting` row is not an `active` one, so `active_count` stops counting it
    and the intake slot frees (Kraft-g15w); and with no coroutine in flight
    there is nothing for pause to fail to cancel (Kraft-tnak).
    """
    conn.execute(
        "UPDATE work_items SET status = 'waiting', retry_at = ?, updated_at = ? WHERE id = ?",
        (retry_at, _now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "work_item_waiting", {"node_id": node_id, "retry_at": retry_at}
    )


def mark_blocked_by_dependency(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, blocked_by: list[str]
) -> None:
    """Kraft-tsfpk: `run_once` found an open bd dependency before dispatching
    the next node. `paused`, not a new status -- the board already renders a
    paused card with a Resume button, `active_count` already excludes it, and
    resume/retry already re-enter through `run_once`, so a still-blocked
    resume costs one more `bd blocked` call and re-pauses here, not a full
    agent session that reads the blocker from prose.
    """
    conn.execute(
        "UPDATE work_items SET status = 'paused', retry_at = NULL, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn,
        work_item_id,
        "work_item_blocked_by_dependency",
        {"node_id": node_id, "blocked_by": blocked_by},
    )


def mark_reentered(conn: sqlite3.Connection, work_item_id: str) -> None:
    """Flip a `waiting` (or `rate_limited`) item back to `active` the instant
    its own poller decides to re-enter it, before the spawned run has done
    anything -- so the *next* tick's `WHERE status = 'waiting'` no longer
    matches this row (Kraft-ppk9). No event: `node_started` already narrates
    the re-entry once the walk actually begins; this is bookkeeping to make
    the row stop looking due, not something a human reads.
    """
    conn.execute(
        "UPDATE work_items SET status = 'active', retry_at = NULL, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )


def mark_completed(conn: sqlite3.Connection, work_item_id) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'completed', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_completed", {})


#: An operator's terminal action -> (the write that ends the item, its audit
#: event, the ordinary event every reader of that status already knows). The
#: status is a literal in each write, not a parameter, so no reader of this
#: module (`dev/check_claim_handoff.py`) can take it for a claim to `active`.
MANUAL_ENDS = {
    "complete": (
        "UPDATE work_items SET status = 'completed', retry_at = NULL, updated_at = ? WHERE id = ?",
        "work_item_manually_completed",
        "work_item_completed",
    ),
    "cancel": (
        "UPDATE work_items SET status = 'abandoned', retry_at = NULL, updated_at = ? WHERE id = ?",
        "work_item_cancelled",
        "work_item_abandoned",
    ),
}


def end_work_item(
    conn: sqlite3.Connection,
    work_item_id: str,
    action: str,
    reason: str,
    *,
    session_ids: list[str] | None = None,
) -> None:
    """End an item by an operator's explicit `complete` or `cancel`
    (`manual-completion-is-an-explicit-work-item-terminal-action`,
    `manual-cancellation-is-an-explicit-work-item-terminal-action`).

    One write: the running sessions are marked `paused` before the caller
    signals them (`pause_work_item`'s ordering), the status leaves the running
    set for good -- which is what stops the walk at its next node -- and the
    audit event carries the reason and the node the item stood on.
    """
    ending, audit, ordinary = MANUAL_ENDS[action]
    now = _now()
    for sid in session_ids or []:
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
            (now, sid),
        )
        events.append(conn, work_item_id, "worker_session_paused", {"session_id": sid})
    node_id = conn.execute(
        "SELECT current_node_id FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()[0]
    conn.execute(ending, (now, work_item_id))
    events.append(conn, work_item_id, audit, {"reason": reason, "node_id": node_id})
    events.append(conn, work_item_id, ordinary, {})


def set_bead_id(conn: sqlite3.Connection, work_item_id, bead_id: str) -> None:
    """A late backfill (Kraft-dr3n): bd was down at intake, and a bead only
    exists for this item from completion time on -- record it, same as if
    intake had filed it in the first place."""
    conn.execute(
        "UPDATE work_items SET bead_id = ?, updated_at = ? WHERE id = ?",
        (bead_id, _now(), work_item_id),
    )


def active_count(conn: sqlite3.Connection) -> int:
    """How many work items are running right now.

    One definition, because two callers bound against it: auto-intake decides
    whether to pick anything up, and a manual resume is refused when the last
    slot is taken. Two copies of this query would let those two disagree about
    what "busy" means.
    """
    return conn.execute("SELECT COUNT(*) FROM work_items WHERE status = 'active'").fetchone()[0]


def abandon_work_item(conn: sqlite3.Connection, work_item_id: str) -> None:
    """Terminal. The row stays — its events and sessions are still the record of
    what happened — but it is out of the running set for good, and off the board.
    """
    conn.execute(
        "UPDATE work_items SET status = 'abandoned', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_abandoned", {})


def archive_work_item(conn: sqlite3.Connection, work_item_id: str, by: str) -> None:
    """Marks a completed/abandoned item archived without touching `status`
    (UI v2 · 03): "Ended as" keeps reading completed/abandoned, and every
    board count that filters on status still excludes an archived item only
    because the list query adds its own `archived_at IS NULL` (board.py).

    `by` is `"you"` (the archive route) or `"auto"` (the poller) — shown in
    the Archived view's ARCHIVED column ("today · by you" / "2 days ago ·
    auto").
    """
    now = _now()
    conn.execute(
        "UPDATE work_items SET archived_at = ?, archived_by = ?, updated_at = ? WHERE id = ?",
        (now, by, now, work_item_id),
    )
    events.append(conn, work_item_id, "work_item_archived", {"by": by})


def restore_work_item(conn: sqlite3.Connection, work_item_id: str) -> None:
    """Puts an archived item back under Done (UI v2 · 03) -- clears the two
    columns `archive_work_item` set; `status` never moved, so there is
    nothing else to restore."""
    conn.execute(
        "UPDATE work_items SET archived_at = NULL, archived_by = NULL, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_restored", {})


def pause_work_item(conn: sqlite3.Connection, work_item_id: str, session_ids: list[str]) -> None:
    """Mark the item and its killed sessions paused (02 §10.2).

    The log files are left alone: a paused attempt's output is still the record
    of what it managed to do before a human stopped it.

    `retry_at` is cleared too (Kraft-tnak): a paused item the `ci_wait` poller
    still considers due would be woken straight back up, and the pause would
    look like it worked and then silently undo itself.
    """
    now = _now()
    conn.execute(
        "UPDATE work_items SET status = 'paused', retry_at = NULL, updated_at = ? WHERE id = ?",
        (now, work_item_id),
    )
    events.append(conn, work_item_id, "pause_requested", {"sessions": session_ids})
    for sid in session_ids:
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
            (now, sid),
        )
        events.append(conn, work_item_id, "worker_session_paused", {"session_id": sid})


def pause_for_broken_base(
    conn: sqlite3.Connection, work_item_id: str, *, broken_by: str, follow_up_bead: str | None
) -> None:
    """Kraft-43kw: `post_merge_watch` found this item sitting on a commit
    whose pipeline just came back red, and is warning it the way the bead's
    description asks -- scoped to items actually on the broken commit, not a
    broadcast to every open item.

    A soft pause, unlike a human's own `/pause` (`api/routes/lifecycle.py`,
    which reads `running_sessions_for_node` and SIGTERMs each pid): there is
    no session id to signal from here, deep inside a different item's forge
    node. The running node, if any, finishes what it is doing, and
    `run_once`'s own between-nodes check (Kraft-e7pm, `walk.py`) stops the
    walk at the next node rather than mid-session -- cheap and eventual, the
    same posture Part 1's blocked-by pause takes.
    """
    conn.execute(
        "UPDATE work_items SET status = 'paused', retry_at = NULL, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn,
        work_item_id,
        "paused_by_broken_base",
        {"broken_by": broken_by, "follow_up_bead": follow_up_bead},
    )


def set_steer(conn: sqlite3.Connection, work_item_id: str, text: str) -> None:
    conn.execute(
        "UPDATE work_items SET pending_steer_context = ?, updated_at = ? WHERE id = ?",
        (text, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "steer_context_set", {"steer": text})


def set_description(conn: sqlite3.Connection, work_item_id: str, description: str) -> None:
    """Replace the brief. Emits its own event: the description is prepended to
    every agent instruction, so an edit changes what later nodes are told, and
    `events` is where that has to be answerable from.
    """
    conn.execute(
        "UPDATE work_items SET description = ?, updated_at = ? WHERE id = ?",
        (description or None, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_description_edited", {"description": description})


def set_title(conn: sqlite3.Connection, work_item_id: str, title: str) -> None:
    """Replace the label.

    Its own event type, not one shared with `set_description`: the description
    is prepended to every agent instruction and the title is a label, so the two
    edits mean different things and a `work_item_edited` that covered both would
    answer neither question from the timeline.

    Nothing downstream derives from the title. The worktree branch name was
    derived from it at intake and is deliberately not renamed — a live branch
    cannot be renamed under a running chain, and `base_ref` and the MR already
    point at it. Search indexes documents, not work-item rows, so no reindex.
    """
    conn.execute(
        "UPDATE work_items SET title = ?, updated_at = ? WHERE id = ?",
        (title, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_title_edited", {"title": title})


def set_agent_overrides(
    conn: sqlite3.Connection, work_item_id: str, agent_overrides: str | None
) -> None:
    """Replace a work item's own model/effort override (Kraft-4k6l).
    `agent_overrides` is already-serialized JSON text; `None` clears it back
    to the template's own binding -- the same nullable-column convention
    `set_description` uses. Replaces the whole stored object; there is no
    field-level merge with what was there.
    """
    conn.execute(
        "UPDATE work_items SET agent_overrides = ?, updated_at = ? WHERE id = ?",
        (agent_overrides, _now(), work_item_id),
    )
    events.append(
        conn,
        work_item_id,
        "agent_overrides_changed",
        {"overrides": json.loads(agent_overrides) if agent_overrides else {}},
    )


def set_base_ref(conn: sqlite3.Connection, work_item_id: str, sha: str) -> None:
    """Pin the commit a work item's diff is measured against.

    Written when the worktree is created, and again by
    `refresh_worktree_base` when a paused or retried item resumes onto a
    moved HEAD (Kraft-ulab). A merge-base recomputed later moves when the
    default branch moves, and a diff that changes under an unchanged work
    item is worse than no diff.
    """
    conn.execute(
        "UPDATE work_items SET base_ref = ?, updated_at = ? WHERE id = ?",
        (sha, _now(), work_item_id),
    )


def set_current_step(conn: sqlite3.Connection, work_item_id: str, step: int) -> None:
    """Record the step group the item's current node is about to run, so a
    wait or a retry can resume there instead of at group 0."""
    conn.execute("UPDATE work_items SET current_step = ? WHERE id = ?", (step, work_item_id))


def set_ci_pipeline_ref(conn: sqlite3.Connection, work_item_id: str, ref: str) -> None:
    """Pin `on.ci.poll` to the pipeline it last saw, stored as
    "<head_sha>:<pipeline_id>" (Kraft-ivh1). GitLab only -- GitHub's
    `ci_status` never sets `CIStatus.pipeline_ref`, so this is never called
    for a GitHub-backed item. Read back and compared against the current
    head before the next poll trusts it (a rebase or a repair's push moves
    the head, which invalidates it on its own -- no separate clear needed
    for that case).
    """
    conn.execute(
        "UPDATE work_items SET ci_pipeline_ref = ?, updated_at = ? WHERE id = ?",
        (ref, _now(), work_item_id),
    )


def set_escalation_session(
    conn: sqlite3.Connection, work_item_id: str, cli_session_id: str
) -> None:
    """Save the `claude` CLI's own session id for this item's escalation
    thread, so the next `escalate.dispatch` can `--resume` it.

    Overwritten on every turn with whatever the run just reported, rather
    than written once: `claude --resume` can "start a copy and say so"
    (`claude --help`) instead of truly resuming, and picking up whatever id
    the CLI actually used covers that without Kraft needing to detect it.
    """
    conn.execute(
        "UPDATE work_items SET escalation_session_id = ?, updated_at = ? WHERE id = ?",
        (cli_session_id, _now(), work_item_id),
    )


def take_steer(conn: sqlite3.Connection, work_item_id: str) -> str | None:
    """Read and clear the pending steer — it belongs to one relaunch, not to every
    later one."""
    row = conn.execute(
        "SELECT pending_steer_context FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()
    text = row["pending_steer_context"] if row else None
    if text:
        conn.execute(
            "UPDATE work_items SET pending_steer_context = NULL WHERE id = ?", (work_item_id,)
        )
    return text


def claim_for_run(
    conn: sqlite3.Connection,
    work_item_id: str,
    *,
    from_statuses: list[str],
    to_status: str = "active",
    limit: int | None = None,
) -> bool:
    """Conditionally flip `work_item_id` to `to_status`. True when this caller
    won the claim.

    `UPDATE ... WHERE status IN (...)` is one statement on Kraft's single
    writer connection: of two callers racing to claim the same item, exactly
    one sees `rowcount == 1` and the other sees 0 -- the item can never be
    claimed twice, whatever runs between the check and the write.

    `limit`, when given, folds the `active_count` capacity check into the
    same `UPDATE` instead of a separate `st.db.read` taken before it -- a
    snapshot read isn't serialized against another writer's claim the way
    two writes are against each other, so two items resumed/retried
    milliseconds apart could each read a free slot and each independently
    win (Kraft-m43g, Kraft-nxht). `rowcount == 0` now means either "not in
    an eligible status" or "no slot free"; callers already can't tell those
    apart from the return value alone, so they re-read `active_count()`
    after a failed claim purely to word their own error message.

    `retry_at` is cleared unconditionally, the same convention every other
    flip-to-active function in this module already follows (`mark_reentered`,
    `pause_work_item`, `chain.skip_node`) -- a claim out of `waiting` or
    `rate_limited` must not leave a stale due-timestamp behind it.

    The caller still owns its own event for the transition (`work_item_resumed`,
    `work_item_retried`, `node_skipped`, ...); this owns only the status
    column, so a caller that loses the race can 409 before writing anything
    else.
    """
    placeholders = ",".join("?" for _ in from_statuses)
    capacity_clause = ""
    params: tuple = (to_status, _now(), work_item_id, *from_statuses)
    if limit is not None:
        capacity_clause = " AND (SELECT COUNT(*) FROM work_items WHERE status = 'active') < ?"
        params = (*params, limit)
    cur = conn.execute(
        f"UPDATE work_items SET status = ?, retry_at = NULL, updated_at = ? "
        f"WHERE id = ? AND status IN ({placeholders}){capacity_clause}",
        params,
    )
    return cur.rowcount == 1


def resume_work_item(conn: sqlite3.Connection, work_item_id: str, steer: str | None) -> None:
    """Record the resume. The status flip is `claim_for_run`'s now, called by
    the route before the worktree rebase (Kraft-11e0) -- this only narrates it.
    Deliberately does NOT touch retry_counters: a human-initiated interruption
    is not a plugin failure."""
    events.append(conn, work_item_id, "work_item_resumed", {"steer": steer})


def recent_auto_pickups(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Work items auto-intake started, newest first (design 31 "Recent pickups").
    Only items whose `work_item_created` event carries `source: "auto_intake"` --
    a skipped candidate never got a work item, so a skip never appears here
    (Kraft-e6x0: tracked, not built)."""
    rows = conn.execute(
        "SELECT e.work_item_id, e.payload, e.created_at, w.status "
        "FROM events e JOIN work_items w ON w.id = e.work_item_id "
        "WHERE e.type = 'work_item_created' "
        "AND json_extract(e.payload, '$.source') = 'auto_intake' "
        "ORDER BY e.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        payload = json.loads(r["payload"])
        out.append(
            {
                "work_item_id": r["work_item_id"],
                "bead_id": payload.get("bead_id"),
                "title": payload.get("title"),
                "repo": payload.get("repo"),
                "priority": payload.get("priority"),
                "status": r["status"],
                "at": r["created_at"],
            }
        )
    return out


def last_auto_pickup_at(conn: sqlite3.Connection) -> dict[str, str]:
    """Repo path -> ISO timestamp of its most recent auto-intake start."""
    rows = conn.execute(
        "SELECT json_extract(e.payload, '$.repo') AS repo, MAX(e.created_at) AS at "
        "FROM events e WHERE e.type = 'work_item_created' "
        "AND json_extract(e.payload, '$.source') = 'auto_intake' "
        "GROUP BY repo"
    ).fetchall()
    return {r["repo"]: r["at"] for r in rows if r["repo"]}
