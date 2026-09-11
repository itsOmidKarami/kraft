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
        "auto_gate, budget_set, budget_usd, node_overrides) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ),
    )
    events.append(
        conn,
        id,
        "work_item_created",
        {"title": title, "repo": repo, "chain_template": chain_template},
    )
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
) -> None:
    """`capped` carries {cycles, attempts} when a loop cap is what stopped the item.

    The UI shows capped-out as a glyph *plus* the words "capped n/n", and the
    board never fetches sessions per row — so the numbers have to ride the
    event rather than be re-derived client-side.

    `budget` carries {scope, spent_usd, cap_usd} when a spend cap is what stopped
    the item: the card shows the figures and the board never fetches sessions per
    row, so the numbers ride the event for the same reason.
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


def mark_completed(conn: sqlite3.Connection, work_item_id) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'completed', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_completed", {})


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


def resume_work_item(conn: sqlite3.Connection, work_item_id: str, steer: str | None) -> None:
    """Back to active. Deliberately does NOT touch retry_counters (02 §10.2): a
    human-initiated interruption is not a plugin failure."""
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_resumed", {"steer": steer})
