from __future__ import annotations

import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store._common import _span_ms
from kraft.usage import Usage


def create_session(
    conn: sqlite3.Connection,
    *,
    id,
    work_item_id,
    node_id,
    hook_point,
    log_path,
    result_path,
    round: int = 0,
    head_sha: str | None = None,
    reuse_if_waiting: bool = False,
) -> tuple[str, str, str]:
    """`round` is the fix-cycle index this session was dispatched in (0 = first pass).

    `head_sha` is the worktree HEAD this session was dispatched against
    (Kraft-lu2) — None for a builtin/agent task that has no meaningful sha of
    its own to report.

    Returns `(id, log_path, result_path)` -- normally the caller's own `id`
    and paths, echoed back. `reuse_if_waiting` (Kraft-ivh1) changes that: a
    session already sitting at `status='waiting'` for this exact (work item,
    node, hook point, round) is the same wait episode, not a new attempt --
    its id/log_path/result_path are returned instead, and no row is
    inserted. `on.ci.poll` is the only caller that sets this; every other
    hook keeps minting a fresh row every dispatch, unchanged.
    """
    if reuse_if_waiting:
        existing = conn.execute(
            "SELECT id, log_path, result_path FROM worker_sessions WHERE work_item_id = ? "
            "AND node_id = ? AND hook_point = ? AND round = ? AND status = 'waiting' "
            "ORDER BY created_at DESC LIMIT 1",
            (work_item_id, node_id, hook_point, round),
        ).fetchone()
        if existing is not None:
            return existing["id"], existing["log_path"], existing["result_path"]
    # The attempt is the count of this (work item, node, hook point)'s sessions,
    # computed in the INSERT rather than passed in: no caller knows better than the
    # table does, and two callers would each re-implement the same query
    # (Kraft-kq8m). Every write goes through Database.write — one connection,
    # serialised — so the count cannot race a concurrent insert.
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at, "
        "round, head_sha) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending', "
        "(SELECT COUNT(*) + 1 FROM worker_sessions "
        "WHERE work_item_id = ? AND node_id = ? AND hook_point = ?), ?, NULL, ?, ?)",
        (
            id,
            work_item_id,
            node_id,
            hook_point,
            log_path,
            result_path,
            work_item_id,
            node_id,
            hook_point,
            _now(),
            round,
            head_sha,
        ),
    )
    (attempt,) = conn.execute("SELECT attempt FROM worker_sessions WHERE id = ?", (id,)).fetchone()
    # Announce the session here, where every session is born, rather than in
    # session_running — only the subprocess adapter calls that, so a builtin hook
    # (create_session -> session_exited) never told the SPA the session existed.
    # worker_session_exited carries no node_id/hook_point, so the client could not
    # build the row from it either: it saw no session for the current node, inferred
    # no gate was awaiting, and rendered no Approve button until a reload (Kraft-dce).
    events.append(
        conn,
        work_item_id,
        "worker_session_created",
        {
            "session_id": id,
            "node_id": node_id,
            "hook_point": hook_point,
            "round": round,
            "attempt": attempt,
        },
    )
    return id, log_path, result_path


def sessions_for_round(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, round: int
) -> list[sqlite3.Row]:
    """Every session this node ran in one fix cycle — the round's result files."""
    return list(
        conn.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND round = ? ORDER BY created_at",
            (work_item_id, node_id, round),
        )
    )


def reusable_session(
    conn: sqlite3.Connection,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
    head_sha: str | None,
) -> sqlite3.Row | None:
    """The most recent session for this exact (node, hook point, round) that a
    crash/resume re-entry may stand in for instead of redispatching
    (Kraft-gl9d): status `done`, dispatched against the worktree's current HEAD.

    `done` is the only reusable verdict -- not `done_with_concerns` alongside
    it (its concerns text is for a human at the next gate, not license to
    silently drop it), and not `failed`/`config_error`/`rate_limited`/anything
    still running, none of which are a known-good result to begin with. A null
    `head_sha` never matches, on either side: the caller's own (checked here,
    short-circuiting before the query) and a stored NULL (`head_sha = ?`
    against any bound value is false in SQL, never true, so a stored NULL
    always falls through on its own without needing a second check) --
    absent provenance is not matching provenance.

    Also excluded: a session whose node was *completed* since it ran. `round`
    resets to 0 on every fresh `walk_node` entry -- not just a crash/resume,
    also a gate rejection that walks back to an already-finished node -- and
    an agent task that makes no further change leaves `head_sha` exactly
    where a genuinely stale session left it too (confirmed against
    `test_rejecting_the_final_gate_re_enters_at_implementation`, whose second
    pass over `implementation` never touches the worktree, so the first
    pass's session would otherwise match on every key this function checks).
    A `node_completed` event for this node_id proves, when it comes after the
    candidate session, that the session belongs to a bygone, already-closed
    attempt, not the in-flight one a crash interrupted.

    `store.chain.complete_node` only ever writes that event *once* per node
    (Kraft-gbt / Kraft-126) -- a second and later pass's own completion is a
    silent no-op, so the check above only ever catches the FIRST time a node
    closes out. A gate rejected a second time (`reject_to` with no
    `fix_loop`, so `round` stays 0 across every pass) leaves the second
    pass's session with no newer `node_completed` to exclude against, and it
    would otherwise be reused right back -- the agent that should carry the
    human's new rejection note never runs.

    So also excluded: a session whose node was *re-entered* since it ran --
    another `node_started` for this node_id, itself preceded (also after the
    session) by a `node_started` for some *other* node_id. That "left for
    another node, then came back" shape only happens on a genuine fresh pass
    (walking forward past this node and later back to it), never on a
    crash/resume of the same still-open pass -- which re-enters this exact
    node without ever having gone anywhere else in between, and must still
    reuse a task that already finished (Kraft-gl9d).

    Also excluded: a session on a node that has been *rejected back to*
    since it ran. The `node_started` shape above needs a departure to some
    other node in between, which a gate with no `reject_to` never produces:
    `gates.reject_target` falls back to the gate node itself, so `spec`,
    `plan` and `chain_review` (`templates/default.yaml`) re-enter their own
    node with nothing in between, and their artifacts live in gitignored
    `.engineering/`, so `head_sha` does not move either. On a second
    rejection of such a gate every other key here still matches the first
    pass's session, it is handed back, the agent never runs and the human's
    new rejection note is silently dropped. A `gate_rejected` after the
    session says a new pass began, whichever node it re-entered -- and no
    rejection can land between a session finishing and a crash-recovery of
    that same still-open pass, so this never costs the reuse Kraft-gl9d is
    for.

    Also excluded: a session with a still-open sibling. `dispatch_node`'s
    subprocess branch mints one session per matching `test_scope`, all
    sharing this exact (node, hook point, round, head_sha) -- a crash
    partway through that per-scope loop leaves the scopes that already ran
    `done` and the scope it was mid-run on stuck `pending`/`running`
    (`create_session` inserts each scope's row before that scope runs).
    Without this check, the query above -- filtered to `status = 'done'` --
    simply skips that stuck sibling and hands back the last scope that *did*
    finish, so the caller reads "done" for a hook whose remaining scopes
    never ran at all. A sibling still `pending`/`running` proves this exact
    (round, head_sha) attempt never finished dispatching every scope; a
    sibling that is merely `failed`/`rate_limited`/etc. is a distinct,
    already-closed-out attempt (Kraft-s15p0's same shape) and must not block
    reuse of a later one that succeeded.
    """
    if head_sha is None:
        return None
    return conn.execute(
        "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
        "AND hook_point = ? AND round = ? AND status = 'done' AND head_sha = ? "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM events WHERE events.work_item_id = worker_sessions.work_item_id "
        "  AND events.type = 'node_completed' "
        "  AND json_extract(events.payload, '$.node_id') = worker_sessions.node_id "
        "  AND events.created_at > worker_sessions.created_at"
        ") "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM events reentry "
        "  WHERE reentry.work_item_id = worker_sessions.work_item_id "
        "  AND reentry.type = 'node_started' "
        "  AND json_extract(reentry.payload, '$.node_id') = worker_sessions.node_id "
        "  AND reentry.created_at > worker_sessions.created_at "
        "  AND EXISTS ("
        "    SELECT 1 FROM events departed "
        "    WHERE departed.work_item_id = worker_sessions.work_item_id "
        "    AND departed.type = 'node_started' "
        "    AND json_extract(departed.payload, '$.node_id') != worker_sessions.node_id "
        "    AND departed.created_at > worker_sessions.created_at "
        "    AND departed.created_at < reentry.created_at"
        "  )"
        ") "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM events rejected "
        "  WHERE rejected.work_item_id = worker_sessions.work_item_id "
        "  AND rejected.type = 'gate_rejected' "
        "  AND rejected.created_at > worker_sessions.created_at"
        ") "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM worker_sessions sibling "
        "  WHERE sibling.work_item_id = worker_sessions.work_item_id "
        "  AND sibling.node_id = worker_sessions.node_id "
        "  AND sibling.hook_point = worker_sessions.hook_point "
        "  AND sibling.round = worker_sessions.round "
        "  AND sibling.head_sha = worker_sessions.head_sha "
        "  AND sibling.status IN ('pending', 'running')"
        ") "
        "ORDER BY created_at DESC LIMIT 1",
        (work_item_id, node_id, hook_point, round, head_sha),
    ).fetchone()


def latest_session_per_task(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, tasks: list[str]
) -> list[sqlite3.Row]:
    """The most recent worker_sessions row for each of `node_id`'s own
    declared `tasks`, one per hook_point that has run at least once
    (Kraft-s15p0).

    `reconcile_current_node`'s non-fix_loop clean-check used to select every
    worker_sessions row for (work_item_id, node_id), with no filter on
    hook_point or attempt: an `escalation` session is not one of the node's
    own tasks at all, and every earlier failed attempt of the same task
    stayed in that set forever. A node that ever escalated or retried
    therefore always had more rows than tasks, so the caller's own
    `len(final) == len(tasks)` could never be satisfied again -- which is
    exactly what discarded a completed, paid `plan` session on this very
    work item (Kraft-s15p0's own observed history: `on.plan.requested
    failed` attempt 1, `escalation failed` attempt 1, `escalation done`
    attempt 2, `on.plan.requested done` attempt 2 -- 4 rows against 1 task).

    A hook_point with no session at all is simply absent from the returned
    list -- the caller's own `len(...) == len(tasks)` still catches that
    unchanged, the same "a task with no session at all still stops for a
    human" behavior the ponytail note on that check already documents.

    A still-open sibling wins the tiebreak over recency. `dispatch_node`'s
    subprocess branch mints one session per matching `test_scope`, all
    sharing this hook_point and round -- a crash partway through that
    per-scope loop leaves earlier scopes `done` and the one it was mid-run
    on stuck `pending`/`running` (`create_session` inserts each scope's row
    before that scope runs). Bare `ORDER BY created_at DESC LIMIT 1` would
    hand back whichever scope finished last, which is `done` if the crash
    landed between scopes, and the caller's `len(final) == len(tasks) and
    all(... in _ADVANCING)` check would then read a hook whose remaining
    scopes never ran as a clean, completed node. Preferring a `pending`/
    `running` row over a `done` one surfaces that stuck sibling instead, so
    the caller's check still fails closed -- while an old `failed` attempt
    superseded by a later `done` one (Kraft-s15p0's own shape) is unaffected,
    since `failed` is no more `pending`/`running` than `done` is.
    """
    rows = []
    for hook_point in tasks:
        row = conn.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND hook_point = ? "
            "ORDER BY (status IN ('pending', 'running')) DESC, created_at DESC LIMIT 1",
            (work_item_id, node_id, hook_point),
        ).fetchone()
        if row is not None:
            rows.append(row)
    return rows


def session_running(conn: sqlite3.Connection, session_id, pid, pid_start_time) -> None:
    # `status != 'paused'`: a pause that landed while this session was still
    # pending must not be undone by the launch finishing.
    conn.execute(
        "UPDATE worker_sessions SET status = 'running', pid = ?, pid_start_time = ?, "
        "started_at = ? WHERE id = ? AND status != 'paused'",
        (pid, pid_start_time, _now(), session_id),
    )
    row = conn.execute(
        "SELECT work_item_id, node_id, hook_point, round, attempt FROM worker_sessions "
        "WHERE id = ?",
        (session_id,),
    ).fetchone()
    events.append(
        conn,
        row["work_item_id"],
        "worker_session_started",
        {
            "session_id": session_id,
            "node_id": row["node_id"],
            "hook_point": row["hook_point"],
            "round": row["round"],
            "attempt": row["attempt"],
            "pid": pid,
        },
    )


def session_progress(conn: sqlite3.Connection, session_id, usage: Usage) -> None:
    """Live token counts and model for a session that is still running (Kraft-54dk).

    Deliberately no event. One of these lands every few seconds per running
    worker, and an event fans out to the WebSocket, the indexer and the
    notifier for a number nobody subscribes to. `usage_rollup` reads the row,
    not the event stream, so "tokens this node" starts being true the moment
    the row is.

    Cost and wall time stay with `session_exited`, which writes the
    authoritative final figures and corrects any live drift. `status =
    'running'` guards the update: a paused or finished session's numbers are
    settled, and a late progress write must not reopen them.
    """
    conn.execute(
        "UPDATE worker_sessions SET model = ?, tokens_in = ?, tokens_out = ? "
        "WHERE id = ? AND status = 'running'",
        (usage.model, usage.tokens_in, usage.tokens_out, session_id),
    )


def session_exited(
    conn: sqlite3.Connection,
    session_id,
    status,
    summary_ref=None,
    usage: Usage | None = None,
    *,
    concerns: str | None = None,
    question: str | None = None,
) -> None:
    """`concerns` (`done_with_concerns`) and `question` (`needs_context`) are
    free text with no column of their own (Task 1's migration deliberately adds
    none) — this event is the only place they are recorded, so a human-review
    gate or a needs_context stop can read them back without a per-request file
    read (`adapters.subprocess.read_concerns`/`read_question` off disk)."""
    now = _now()
    row = conn.execute(
        "SELECT work_item_id, started_at, created_at, status FROM worker_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    # Pausing SIGTERMs the child, so the adapter — or reattach, adopting a child
    # that dies after a restart — arrives here a moment later with 'failed'. A
    # human's interruption is not a task failure, and the row and the event have to
    # agree: return before either is written.
    if row is None or row["status"] == "paused":
        return
    # COALESCE: a None ref must not erase one an earlier resolution already stored.
    conn.execute(
        "UPDATE worker_sessions SET status = ?, exited_at = ?, "
        "session_summary_ref = COALESCE(?, session_summary_ref) WHERE id = ?",
        (status, now, summary_ref, session_id),
    )
    # Wall time runs from the moment the process started, not from row creation:
    # a session that waited behind a lock did not spend that time working.
    wall_ms = _span_ms(row["started_at"] or row["created_at"], now)
    payload = {"session_id": session_id, "status": status, "wall_ms": wall_ms}
    if concerns:
        payload["concerns"] = concerns
    if question:
        payload["question"] = question
    if usage is not None:
        conn.execute(
            "UPDATE worker_sessions SET model = ?, tokens_in = ?, tokens_out = ?, "
            "cost_usd = ?, wall_ms = ? WHERE id = ?",
            (usage.model, usage.tokens_in, usage.tokens_out, usage.cost_usd, wall_ms, session_id),
        )
        payload |= {
            "model": usage.model,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cost_usd": usage.cost_usd,
        }
    else:
        conn.execute("UPDATE worker_sessions SET wall_ms = ? WHERE id = ?", (wall_ms, session_id))
    # The design calls this event worker_session_completed; this codebase has
    # always called the same moment worker_session_exited, so the usage rides
    # that rather than a second event meaning the same thing.
    events.append(conn, row["work_item_id"], "worker_session_exited", payload)


def stop_escalation_session(conn: sqlite3.Connection, work_item_id: str, session_id: str) -> None:
    """Kill a running escalation turn without touching the item's own status
    (06 'Stop agent') -- the item was, and remains, needs_human; only the turn
    stops. Mirrors the per-session half of `pause_work_item`, minus the
    work_items UPDATE that function also does."""
    now = _now()
    conn.execute(
        "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
        (now, session_id),
    )
    events.append(conn, work_item_id, "worker_session_paused", {"session_id": session_id})


def session_unknown(conn: sqlite3.Connection, session_id) -> None:
    conn.execute(
        "UPDATE worker_sessions SET status = 'unknown', exited_at = ? WHERE id = ?",
        (_now(), session_id),
    )
    row = conn.execute(
        "SELECT work_item_id FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(conn, row["work_item_id"], "session_unknown", {"session_id": session_id})


def session_reattached(conn: sqlite3.Connection, session_id) -> None:
    row = conn.execute(
        "SELECT work_item_id, pid FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(
        conn,
        row["work_item_id"],
        "session_reattached",
        {"session_id": session_id, "pid": row["pid"]},
    )


def session_status(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute("SELECT status FROM worker_sessions WHERE id = ?", (session_id,)).fetchone()
    return row["status"] if row else None


def recent_sessions_for_hook(
    conn: sqlite3.Connection, hook_point: str, limit: int = 5
) -> list[dict]:
    """The Plugins binding detail's "LAST RUNS" (design 28): the most recent
    sessions dispatched for one hook, across every work item — not filtered
    to one item, the same way the binding itself isn't."""
    rows = conn.execute(
        "SELECT work_item_id, node_id, round, status, wall_ms, created_at "
        "FROM worker_sessions WHERE hook_point = ? ORDER BY created_at DESC LIMIT ?",
        (hook_point, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def running_sessions_for_node(conn: sqlite3.Connection, work_item_id: str) -> list[sqlite3.Row]:
    """Every running session on the item's current node — all of them on a
    concurrent node like `verify` — plus any running escalation session
    whatever node it was dispatched on."""
    # 'pending' too: a row is inserted before Popen returns, and a pause landing in
    # that window would otherwise neither signal the child nor mark the row — the
    # task would run to completion under an item that reads paused.
    #
    # Escalation sessions are matched by hook_point rather than node because
    # `escalate.escalation_running` — what `retry`/`resume`/`skip` consult to
    # decide whether a turn is live — is not node-scoped. A row whose node_id
    # has drifted from current_node_id (a stale running row after a server
    # restart) is live to that check and would be missed by this one, so the
    # caller would kill nothing and then rebase and spawn into a worktree an
    # agent is still writing in (code review finding).
    return conn.execute(
        "SELECT s.id, s.pid FROM worker_sessions s JOIN work_items w ON w.id = s.work_item_id "
        "WHERE s.work_item_id = ? AND s.status IN ('running', 'pending') "
        "AND (s.node_id = w.current_node_id OR s.hook_point = 'escalation')",
        (work_item_id,),
    ).fetchall()
