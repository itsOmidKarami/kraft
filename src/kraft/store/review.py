"""Review flow storage (docs/superpowers/specs/2026-09-27-review-flow-backend-design.md).

`node_runs` pins the commit each node run started and ended on, so any two
attempts stay diffable. Threads, comments and reviews are added in later
sections of this module.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from kraft import events, render
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.vocab import ChainEvent, GateEvent

__all__ = [
    "CLAIMS",
    "LABELS",
    "OUTCOMES",
    "YOU",
    "add_draft_reply",
    "agent_reply",
    "cancel_rewind",
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
    "mark_viewed",
    "node_run_rows",
    "open_must_fix",
    "pending_rewind",
    "pin_gate",
    "render_note",
    "render_threads",
    "request_rewind",
    "set_thread_state",
    "start_run",
    "publish_review",
    "record_review",
    "submit_review",
    "unmark_viewed",
    "unrecord_review",
    "thread_row",
    "threads_for",
    "unanswered",
    "update_draft_comment",
    "update_draft_thread",
    "viewed_marks",
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
    start_side=None,
    quote=None,
) -> str:
    """`start_side` puts `start_line` on the other side from `side` (a range
    across sides); it is stored only then, so a range on one side reads the
    same as one written before the column."""
    tid = _id()
    now = _now()
    conn.execute(
        "INSERT INTO review_threads (id, work_item_id, gate, node_id, file_path, side, "
        "start_side, start_line, end_line, quote, anchor_sha, label, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            tid,
            wid,
            gate,
            node_id,
            file_path,
            side,
            start_side if start_side != side else None,
            start_line,
            end_line,
            quote,
            anchor_sha,
            label,
            now,
        ),
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
    t = thread_row(conn, tid)
    events.append(
        conn,
        t["work_item_id"],
        GateEvent.THREAD_UPDATED,
        {"thread_id": tid, "state": state},
        node_id=t["node_id"],
    )


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
            conn,
            t["work_item_id"],
            GateEvent.THREAD_UPDATED,
            {"thread_id": tid, "state": t["state"]},
            node_id=t["node_id"],
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
        # NULL is `side`'s: every range written before ranges could cross sides.
        d["start_side"] = t["start_side"] or t["side"]
        d["comments"] = comments
        d["draft"] = bool(comments) and comments[0]["draft"]
        out.append(d)
    return out


def open_must_fix(conn, wid) -> list[str]:
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM review_threads WHERE work_item_id = ? "
            "AND label = 'must_fix' AND state != 'resolved' ORDER BY created_at, rowid",
            (wid,),
        ).fetchall()
    ]


def unanswered(conn, wid) -> list[dict]:
    return [
        t
        for t in threads_for(conn, wid)
        if not t["draft"] and t["state"] != "resolved" and t["comments"][-1]["author"] == YOU
    ]


def record_review(conn, *, wid, gate: str | None, outcome, summary, head_sha, base_sha) -> str:
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
        "AND thread_id IN (SELECT id FROM review_threads WHERE work_item_id = ?)",
        (rid, YOU, wid),
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
        GateEvent.REVIEW_SUBMITTED,
        {"review_id": rid, "gate": r["gate"], "outcome": r["outcome"]},
        node_id=r["gate"],
    )


def unrecord_review(conn, rid: str) -> None:
    """Undo `record_review`: its comments are drafts again and the row is gone."""
    conn.execute("UPDATE review_comments SET review_id = NULL WHERE review_id = ?", (rid,))
    conn.execute("DELETE FROM reviews WHERE id = ?", (rid,))


def submit_review(conn, *, wid, gate: str | None, outcome, summary, head_sha, base_sha) -> str:
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


def last_review(conn, wid):
    return conn.execute(
        "SELECT * FROM reviews WHERE work_item_id = ? ORDER BY submitted_at DESC, rowid DESC "
        "LIMIT 1",
        (wid,),
    ).fetchone()


def mark_viewed(conn, wid, path, to_sha, blob_id):
    conn.execute(
        "INSERT INTO review_viewed (work_item_id, file_path, to_sha, blob_id, viewed_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (work_item_id, file_path, to_sha) "
        "DO UPDATE SET blob_id = excluded.blob_id, viewed_at = excluded.viewed_at",
        (wid, path, to_sha, blob_id, _now()),
    )
    conn.commit()


def unmark_viewed(conn, wid, path, blob_id):
    """Clear every mark on this blob, whichever `to` it was made against."""
    conn.execute(
        "DELETE FROM review_viewed WHERE work_item_id = ? AND file_path = ? AND blob_id IS ?",
        (wid, path, blob_id),
    )
    conn.commit()


def viewed_marks(conn, wid, paths):
    """`{path: {blob_id, ...}}` for the marked paths among `paths`."""
    marks: dict[str, set] = {}
    for path, blob in conn.execute(
        "SELECT file_path, blob_id FROM review_viewed WHERE work_item_id = ?", (wid,)
    ):
        if path in paths:
            marks.setdefault(path, set()).add(blob)
    return marks


_NOTE_HEAD = (
    "Review threads to address (reply to each with "
    '`kraft item reply <thread-id> --claim fixed|answered --body "..."`):'
)


def render_threads(threads: list[dict]) -> str:
    blocks = []
    for t in threads:
        if t["state"] == "resolved":
            continue
        head = f"[{t['id']}] {render.thread_where(t)}" + (f" ({t['label']})" if t["label"] else "")
        first, *replies = t["comments"]
        lines = [head]
        # The lines as the reviewer saw them, before what they said about them.
        lines += ["    | " + ln for ln in (t.get("quote") or "").splitlines()]
        lines.append(first["body"])
        s = first.get("suggestion")
        if s:
            lines.append(f"Suggested replacement for lines {s['start_line']}-{s['end_line']}:")
            lines += ["    " + ln for ln in s["replacement"].splitlines()]
        lines += [f"  > {r['author']}: {r['body']}" for r in replies]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def request_rewind(conn, wid, *, review_id, target, note) -> None:
    events.append(
        conn,
        wid,
        GateEvent.REWIND_REQUESTED,
        {"review_id": review_id, "target": target, "note": note},
        node_id=target,
    )


def cancel_rewind(conn, wid, review_id) -> None:
    events.append(conn, wid, GateEvent.REWIND_CANCELLED, {"review_id": review_id})


def pending_rewind(conn, wid) -> dict | None:
    """The newest unspent `rewind_requested`: newest wins, spent by its target's
    next `node_started`, void once cancelled (review threads anywhere §1)."""
    evs = events.read_after(conn, 0, wid)
    last = next(
        (i for i in range(len(evs) - 1, -1, -1) if evs[i]["type"] == GateEvent.REWIND_REQUESTED),
        None,
    )
    if last is None:
        return None
    p = evs[last]["payload"]
    for e in evs[last + 1 :]:
        if e["type"] == ChainEvent.NODE_STARTED and e["payload"].get("node_id") == p["target"]:
            return None
        if (
            e["type"] == GateEvent.REWIND_CANCELLED
            and e["payload"].get("review_id") == p["review_id"]
        ):
            return None
    return {"seq": evs[last]["seq"], **p}


def render_note(threads: list[dict], summary: str | None) -> str:
    parts = [summary.strip()] if summary and summary.strip() else []
    body = render_threads(threads)
    if body:
        parts += [_NOTE_HEAD, body]
    return "\n\n".join(parts)
