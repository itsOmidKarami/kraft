"""Review flow storage (docs/superpowers/specs/2026-09-27-review-flow-backend-design.md).

`node_runs` pins the commit each node run started and ended on, so any two
attempts stay diffable. Threads, comments and reviews are added in later
sections of this module.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks

__all__ = [
    "CLAIMS",
    "LABELS",
    "OUTCOMES",
    "YOU",
    "add_draft_reply",
    "agent_reply",
    "comment_dict",
    "comment_row",
    "create_thread",
    "delete_draft_comment",
    "delete_draft_thread",
    "finish_run",
    "gate_attempts",
    "is_draft_thread",
    "last_review",
    "next_gate_attempt",
    "node_run_rows",
    "open_must_fix",
    "pin_gate",
    "render_note",
    "set_thread_state",
    "start_run",
    "publish_review",
    "record_review",
    "submit_review",
    "unrecord_review",
    "thread_row",
    "threads_for",
    "unanswered",
    "update_draft_comment",
    "update_draft_thread",
]


def _newest(conn: sqlite3.Connection, wid: str, node_id: str):
    return conn.execute(
        "SELECT attempt, end_sha FROM node_runs WHERE work_item_id = ? AND node_id = ? "
        "ORDER BY attempt DESC LIMIT 1",
        (wid, node_id),
    ).fetchone()


def start_run(conn, wid, node_id, *, start_sha, base_sha) -> int | None:
    last = _newest(conn, wid, node_id)
    if last is not None and last["end_sha"] is None:
        # A resume, or a fix loop re-measuring: the same run, not a new attempt.
        return None
    attempt = 1 if last is None else last["attempt"] + 1
    conn.execute(
        "INSERT INTO node_runs (work_item_id, node_id, attempt, start_sha, base_sha, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (wid, node_id, attempt, start_sha, base_sha, _now()),
    )
    return attempt


def finish_run(conn, wid, node_id, *, end_sha, dirty) -> int | None:
    last = _newest(conn, wid, node_id)
    if last is None or last["end_sha"] is not None:
        return None
    conn.execute(
        "UPDATE node_runs SET end_sha = ?, dirty = ? "
        "WHERE work_item_id = ? AND node_id = ? AND attempt = ?",
        (end_sha, int(dirty), wid, node_id, last["attempt"]),
    )
    return last["attempt"]


def next_gate_attempt(conn, wid, gate, sha) -> int | None:
    """The attempt `pin_gate` would write for `sha`, or None when the newest
    gate row is already at it -- read ahead so the ref can be pinned before the
    `gate_requested` write ends the walk."""
    last = _newest(conn, wid, gate)
    if last is not None and last["end_sha"] == sha:
        # The same gate re-requested at the same commit (a restart): nothing new to review.
        return None
    return 1 if last is None else last["attempt"] + 1


def pin_gate(conn, wid, gate, *, sha, base_sha, dirty) -> int | None:
    attempt = next_gate_attempt(conn, wid, gate, sha)
    if attempt is None:
        return None
    conn.execute(
        "INSERT INTO node_runs (work_item_id, node_id, attempt, start_sha, end_sha, base_sha, "
        "dirty, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, gate, attempt, sha, sha, base_sha, int(dirty), _now()),
    )
    return attempt


def node_run_rows(conn, wid) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM node_runs WHERE work_item_id = ? ORDER BY created_at, attempt", (wid,)
    ).fetchall()


def gate_attempts(conn, wid, gate) -> list[dict]:
    rows = conn.execute(
        "SELECT attempt, end_sha, base_sha, created_at FROM node_runs "
        "WHERE work_item_id = ? AND node_id = ? AND end_sha IS NOT NULL ORDER BY attempt",
        (wid, gate),
    ).fetchall()
    return [
        {"n": r["attempt"], "sha": r["end_sha"], "base_sha": r["base_sha"], "at": r["created_at"]}
        for r in rows
    ]


YOU = "you"
LABELS = frozenset({"must_fix", "question", "nit"})
CLAIMS = frozenset({"fixed", "answered", "should_fix"})
OUTCOMES = frozenset({"approve", "request_changes", "comment"})
_KEEP = object()


def _id() -> str:
    return uuid.uuid4().hex


def create_thread(
    conn,
    *,
    wid,
    gate,
    anchor_sha,
    body,
    node_id=None,
    file_path=None,
    side=None,
    start_line=None,
    end_line=None,
    label=None,
    suggestion=None,
) -> str:
    tid = _id()
    now = _now()
    conn.execute(
        "INSERT INTO review_threads (id, work_item_id, gate, node_id, file_path, side, "
        "start_line, end_line, anchor_sha, label, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tid, wid, gate, node_id, file_path, side, start_line, end_line, anchor_sha, label, now),
    )
    conn.execute(
        "INSERT INTO review_comments (id, thread_id, author, body, suggestion, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (_id(), tid, YOU, body, json.dumps(suggestion) if suggestion else None, now),
    )
    return tid


def thread_row(conn, tid):
    return conn.execute("SELECT * FROM review_threads WHERE id = ?", (tid,)).fetchone()


def comment_row(conn, cid):
    return conn.execute("SELECT * FROM review_comments WHERE id = ?", (cid,)).fetchone()


def _first_comment(conn, tid):
    return conn.execute(
        "SELECT * FROM review_comments WHERE thread_id = ? ORDER BY created_at, rowid LIMIT 1",
        (tid,),
    ).fetchone()


def is_draft_thread(conn, tid) -> bool:
    first = _first_comment(conn, tid)
    return first is not None and first["author"] == YOU and first["review_id"] is None


def update_draft_thread(conn, tid, *, label=_KEEP, body=None, suggestion=_KEEP) -> None:
    if label is not _KEEP:
        conn.execute("UPDATE review_threads SET label = ? WHERE id = ?", (label, tid))
    first = _first_comment(conn, tid)
    if body is not None:
        conn.execute("UPDATE review_comments SET body = ? WHERE id = ?", (body, first["id"]))
    if suggestion is not _KEEP:
        conn.execute(
            "UPDATE review_comments SET suggestion = ? WHERE id = ?",
            (json.dumps(suggestion) if suggestion else None, first["id"]),
        )


def delete_draft_thread(conn, tid) -> None:
    conn.execute("DELETE FROM review_comments WHERE thread_id = ?", (tid,))
    conn.execute("DELETE FROM review_threads WHERE id = ?", (tid,))


def add_draft_reply(conn, tid, *, body, suggestion=None) -> str:
    cid = _id()
    conn.execute(
        "INSERT INTO review_comments (id, thread_id, author, body, suggestion, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (cid, tid, YOU, body, json.dumps(suggestion) if suggestion else None, _now()),
    )
    return cid


def update_draft_comment(conn, cid, *, body, suggestion) -> None:
    conn.execute(
        "UPDATE review_comments SET body = ?, suggestion = ? WHERE id = ?",
        (body, json.dumps(suggestion) if suggestion else None, cid),
    )


def delete_draft_comment(conn, cid) -> None:
    conn.execute("DELETE FROM review_comments WHERE id = ?", (cid,))


def set_thread_state(conn, tid, state: str) -> None:
    conn.execute(
        "UPDATE review_threads SET state = ?, resolved_at = ? WHERE id = ?",
        (state, _now() if state == "resolved" else None, tid),
    )
    wid = thread_row(conn, tid)["work_item_id"]
    events.append(conn, wid, "thread_updated", {"thread_id": tid, "state": state})


def agent_reply(conn, tid, *, author, body, claim, attempt) -> str:
    cid = _id()
    conn.execute(
        "INSERT INTO review_comments (id, thread_id, author, attempt, body, claim, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, tid, author, attempt, body, claim, _now()),
    )
    t = thread_row(conn, tid)
    if claim is not None and t["state"] == "open":
        set_thread_state(conn, tid, "claimed")
    else:
        # An agent claim never reopens a `claimed` or `resolved` thread: report
        # the state the thread is actually in instead.
        events.append(
            conn, t["work_item_id"], "thread_updated", {"thread_id": tid, "state": t["state"]}
        )
    return cid


def comment_dict(r) -> dict:
    d = {k: r[k] for k in r.keys()}
    d["suggestion"] = json.loads(r["suggestion"]) if r["suggestion"] else None
    d["draft"] = r["author"] == YOU and r["review_id"] is None
    return d


def threads_for(conn, wid, gate=None) -> list[dict]:
    q = "SELECT * FROM review_threads WHERE work_item_id = ?"
    args: tuple = (wid,)
    if gate is not None:
        q, args = q + " AND gate = ?", (wid, gate)
    out = []
    for t in conn.execute(q + " ORDER BY created_at, rowid", args).fetchall():
        comments = [
            comment_dict(r)
            for r in conn.execute(
                "SELECT * FROM review_comments WHERE thread_id = ? ORDER BY created_at, rowid",
                (t["id"],),
            ).fetchall()
        ]
        d = {k: t[k] for k in t.keys()}
        d["comments"] = comments
        d["draft"] = bool(comments) and comments[0]["draft"]
        out.append(d)
    return out


def open_must_fix(conn, wid, gate) -> list[str]:
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM review_threads WHERE work_item_id = ? AND gate = ? "
            "AND label = 'must_fix' AND state != 'resolved' ORDER BY created_at, rowid",
            (wid, gate),
        ).fetchall()
    ]


def unanswered(conn, wid, gate) -> list[dict]:
    return [
        t
        for t in threads_for(conn, wid, gate)
        if not t["draft"] and t["state"] != "resolved" and t["comments"][-1]["author"] == YOU
    ]


def record_review(conn, *, wid, gate, outcome, summary, head_sha, base_sha) -> str:
    """The review row, and every draft comment on this gate stamped with it --
    no event yet. The submit route calls this *before* the gate call, so a walk
    the gate call starts never sees its threads as drafts (Kraft-dl5fl), and
    `unrecord_review` undoes it if that call refuses."""
    rid = _id()
    conn.execute(
        "INSERT INTO reviews (id, work_item_id, gate, outcome, summary, head_sha, base_sha, "
        "submitted_at) VALUES (?,?,?,?,?,?,?,?)",
        (rid, wid, gate, outcome, summary, head_sha, base_sha, _now()),
    )
    conn.execute(
        "UPDATE review_comments SET review_id = ? WHERE review_id IS NULL AND author = ? "
        "AND thread_id IN (SELECT id FROM review_threads WHERE work_item_id = ? AND gate = ?)",
        (rid, YOU, wid, gate),
    )
    return rid


def publish_review(conn, rid: str) -> None:
    """`review_submitted`, once the review's gate call has taken."""
    r = conn.execute(
        "SELECT work_item_id, gate, outcome FROM reviews WHERE id = ?", (rid,)
    ).fetchone()
    events.append(
        conn,
        r["work_item_id"],
        "review_submitted",
        {"review_id": rid, "gate": r["gate"], "outcome": r["outcome"]},
    )


def unrecord_review(conn, rid: str) -> None:
    """Undo `record_review`: its comments are drafts again and the row is gone."""
    conn.execute("UPDATE review_comments SET review_id = NULL WHERE review_id = ?", (rid,))
    conn.execute("DELETE FROM reviews WHERE id = ?", (rid,))


def submit_review(conn, *, wid, gate, outcome, summary, head_sha, base_sha) -> str:
    """`record_review` and `publish_review` in one step, for a caller with no
    gate call in between."""
    rid = record_review(
        conn,
        wid=wid,
        gate=gate,
        outcome=outcome,
        summary=summary,
        head_sha=head_sha,
        base_sha=base_sha,
    )
    publish_review(conn, rid)
    return rid


def last_review(conn, wid, gate):
    return conn.execute(
        "SELECT * FROM reviews WHERE work_item_id = ? AND gate = ? "
        "ORDER BY submitted_at DESC, rowid DESC LIMIT 1",
        (wid, gate),
    ).fetchone()


_NOTE_HEAD = (
    "Review threads to address (reply to each with "
    '`kraft item reply <thread-id> --claim fixed|answered --body "..."`):'
)


def render_note(threads: list[dict], summary: str | None) -> str:
    parts = [summary.strip()] if summary and summary.strip() else []
    live = [t for t in threads if t["state"] != "resolved"]
    if live:
        parts.append(_NOTE_HEAD)
    for t in live:
        where = "(whole change)"
        if t["file_path"]:
            where = t["file_path"]
            if t["start_line"] is not None:
                where += f":{t['start_line']}-{t['end_line']}"
        head = f"[{t['id']}] {where}" + (f" ({t['label']})" if t["label"] else "")
        first, *replies = t["comments"]
        lines = [head, first["body"]]
        s = first.get("suggestion")
        if s:
            lines.append(f"Suggested replacement for lines {s['start_line']}-{s['end_line']}:")
            lines += ["    " + ln for ln in s["replacement"].splitlines()]
        lines += [f"  > {r['author']}: {r['body']}" for r in replies]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
