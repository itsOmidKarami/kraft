"""Review threads and reviews on a pending gate
(docs/superpowers/specs/2026-09-27-review-flow-backend-design.md §3).

Human routes refuse a worker session outright: agents speak only through
`POST /threads/{tid}/replies`, where the author comes from their session.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, model_validator

from kraft import config as config_mod
from kraft import executor, review_reply, store
from kraft.api import api_router, deps
from kraft.api.routes import board, lifecycle
from kraft.api.routes import gates as gate_routes


class Suggestion(BaseModel):
    start_line: int
    end_line: int
    replacement: str


class ThreadIn(BaseModel):
    body: str
    node_id: str | None = None
    file_path: str | None = None
    side: Literal["old", "new"] | None = None
    start_line: int | None = None
    end_line: int | None = None
    label: Literal["must_fix", "question", "nit"] | None = None
    suggestion: Suggestion | None = None
    anchor_sha: str | None = None

    @model_validator(mode="after")
    def _ranges(self):
        lines = (self.side, self.start_line, self.end_line)
        if any(v is not None for v in lines) and not all(v is not None for v in lines):
            raise ValueError("side, start_line and end_line are all set or all omitted")
        if self.start_line is not None:
            if self.file_path is None:
                raise ValueError("a line range needs a file_path")
            if not 1 <= self.start_line <= self.end_line:
                raise ValueError("start_line must be >= 1 and <= end_line")
        _check_suggestion(self.suggestion, self.start_line, self.end_line)
        if not self.body.strip():
            raise ValueError("body is empty")
        return self


def _check_suggestion(s: Suggestion | None, start: int | None, end: int | None) -> None:
    if s is None:
        return
    if start is None:
        raise ValueError("a suggestion needs a thread with a line range")
    if not (start <= s.start_line <= s.end_line <= end):
        raise ValueError(f"the suggestion's lines must sit inside {start}-{end}")


class ThreadPatch(BaseModel):
    body: str | None = None
    label: Literal["must_fix", "question", "nit"] | None = None
    suggestion: Suggestion | None = None


class CommentIn(BaseModel):
    body: str
    suggestion: Suggestion | None = None


def _refuse_agents(request: Request) -> None:
    if request.headers.get("x-kraft-session-id"):
        raise HTTPException(403, "worker agents reply through POST /threads/{id}/replies")


def _thread_or_404(st, tid: str):
    row = st.db.read(lambda c: store.thread_row(c, tid))
    if row is None:
        raise HTTPException(404, f"unknown thread {tid!r}")
    return row


def _one(st, wid: str, tid: str) -> dict:
    return next(t for t in st.db.read(lambda c: store.threads_for(c, wid)) if t["id"] == tid)


@api_router.get("/work-items/{wid}/threads")
async def list_threads(wid: str, request: Request):
    st = request.app.state
    deps._work_item_row(st, wid)
    return st.db.read(lambda c: store.threads_for(c, wid))


@api_router.post("/work-items/{wid}/threads", status_code=201)
async def create_thread(wid: str, body: ThreadIn, request: Request):
    _refuse_agents(request)
    st = request.app.state
    deps._live_work_item_row(st, wid)
    gate = board._pending_gate(st, wid)
    anchor = body.anchor_sha or config_mod.git_read(
        st.run_dirs.worktrees / wid, "rev-parse", "HEAD"
    )
    if not anchor:
        raise HTTPException(409, "this work item has no commit to anchor a thread to")
    tid = await st.db.write(
        lambda c: store.create_thread(
            c,
            wid=wid,
            gate=gate,
            anchor_sha=anchor,
            body=body.body,
            node_id=body.node_id,
            file_path=body.file_path,
            side=body.side,
            start_line=body.start_line,
            end_line=body.end_line,
            label=body.label,
            suggestion=body.suggestion.model_dump() if body.suggestion else None,
        )
    )
    return _one(st, wid, tid)


def _draft_thread_or_409(st, tid):
    row = _thread_or_404(st, tid)
    if not st.db.read(lambda c: store.is_draft_thread(c, tid)):
        raise HTTPException(409, "this thread has been submitted; reply to it instead")
    return row


@api_router.patch("/threads/{tid}")
async def patch_thread(tid: str, body: ThreadPatch, request: Request):
    _refuse_agents(request)
    st = request.app.state
    row = _draft_thread_or_409(st, tid)
    fields = body.model_fields_set
    if "suggestion" in fields:
        try:
            _check_suggestion(body.suggestion, row["start_line"], row["end_line"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    kw = {}
    if "label" in fields:
        kw["label"] = body.label
    if "suggestion" in fields:
        kw["suggestion"] = body.suggestion.model_dump() if body.suggestion else None
    await st.db.write(lambda c: store.update_draft_thread(c, tid, body=body.body, **kw))
    return _one(st, row["work_item_id"], tid)


@api_router.delete("/threads/{tid}", status_code=204)
async def delete_thread(tid: str, request: Request):
    _refuse_agents(request)
    st = request.app.state
    _draft_thread_or_409(st, tid)
    await st.db.write(lambda c: store.delete_draft_thread(c, tid))


@api_router.post("/threads/{tid}/comments", status_code=201)
async def add_comment(tid: str, body: CommentIn, request: Request):
    _refuse_agents(request)
    st = request.app.state
    row = _thread_or_404(st, tid)
    deps._live_work_item_row(st, row["work_item_id"])
    try:
        _check_suggestion(body.suggestion, row["start_line"], row["end_line"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cid = await st.db.write(
        lambda c: store.add_draft_reply(
            c,
            tid,
            body=body.body,
            suggestion=body.suggestion.model_dump() if body.suggestion else None,
        )
    )
    return st.db.read(lambda c: store.comment_dict(store.comment_row(c, cid)))


def _draft_comment_or_409(st, cid):
    row = st.db.read(lambda c: store.comment_row(c, cid))
    if row is None:
        raise HTTPException(404, f"unknown comment {cid!r}")
    if row["author"] != store.YOU or row["review_id"] is not None:
        raise HTTPException(409, "only an unsubmitted comment of yours can change")
    return row


@api_router.patch("/comments/{cid}")
async def patch_comment(cid: str, body: CommentIn, request: Request):
    _refuse_agents(request)
    st = request.app.state
    row = _draft_comment_or_409(st, cid)
    t = _thread_or_404(st, row["thread_id"])
    try:
        _check_suggestion(body.suggestion, t["start_line"], t["end_line"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await st.db.write(
        lambda c: store.update_draft_comment(
            c,
            cid,
            body=body.body,
            suggestion=body.suggestion.model_dump() if body.suggestion else None,
        )
    )
    return st.db.read(lambda c: store.comment_dict(store.comment_row(c, cid)))


@api_router.delete("/comments/{cid}", status_code=204)
async def delete_comment(cid: str, request: Request):
    _refuse_agents(request)
    st = request.app.state
    _draft_comment_or_409(st, cid)
    await st.db.write(lambda c: store.delete_draft_comment(c, cid))


async def _set_state(tid: str, request: Request, state: str):
    _refuse_agents(request)
    st = request.app.state
    row = _thread_or_404(st, tid)
    if st.db.read(lambda c: store.is_draft_thread(c, tid)):
        raise HTTPException(409, "a draft thread has nothing to resolve yet")
    await st.db.write(lambda c: store.set_thread_state(c, tid, state))
    return _one(st, row["work_item_id"], tid)


@api_router.post("/threads/{tid}/resolve")
async def resolve_thread(tid: str, request: Request):
    return await _set_state(tid, request, "resolved")


@api_router.post("/threads/{tid}/reopen")
async def reopen_thread(tid: str, request: Request):
    return await _set_state(tid, request, "open")


class ReviewIn(BaseModel):
    outcome: Literal["approve", "request_changes", "comment"]
    summary: str | None = None
    node: str | None = None


async def _submit(st, request: Request, row, gate: str | None, body: ReviewIn):
    wid = row["id"]
    nodes = gate_routes.gate_nodes(st, row) if gate is not None else None
    # Every refusal before the review is written: a refused review records nothing.
    if gate is None and body.outcome == "approve":
        raise HTTPException(409, "nothing to approve: no gate is pending")
    if body.outcome == "approve":
        blocking = st.db.read(lambda c: store.open_must_fix(c, wid))
        if blocking:
            raise HTTPException(
                409, f"must-fix review threads are not resolved: {', '.join(blocking)}"
            )
    if gate is not None and body.outcome == "request_changes":
        try:
            executor.reject_target(nodes, executor.gate_node_index(nodes, gate), body.node)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if gate is None and body.outcome == "comment" and not lifecycle.review_reachable(row):
        raise HTTPException(
            409,
            "nothing ahead in this chain will read these threads; use `kraft item retry --steer`",
        )
    head = config_mod.git_read(st.run_dirs.worktrees / wid, "rev-parse", "HEAD")
    if not head:
        # A review's `head_sha` is what `last_review` compares from; never ''.
        raise HTTPException(409, "git could not read this work item's HEAD; nothing was recorded")
    if gate is not None:
        attempts = st.db.read(lambda c: store.gate_attempts(c, wid, gate))
        base = attempts[-1]["base_sha"] if attempts else (row["base_ref"] or head)
    else:
        base = row["base_ref"] or head
    if gate is None and body.outcome == "request_changes":
        return await _request_changes_now(st, request, row, body, head, base)
    note = None
    if body.outcome == "request_changes":
        threads = st.db.read(lambda c: store.threads_for(c, wid))
        note = store.render_note(threads, body.summary) or "Changes requested."
    # Recorded *before* the gate call (Kraft-dl5fl): a reject starts the walk,
    # and the re-run agent may reply to these threads before this route
    # returns -- they must not still be drafts. `approve_gate`/`reject_gate`
    # can still refuse after every check above (missing final-review
    # artifact, stale chain-revision digest, invalid policy, a running walk),
    # so a refusal undoes the record: a refused review records nothing.
    rid = await st.db.write(
        lambda c: store.record_review(
            c,
            wid=wid,
            gate=gate,
            outcome=body.outcome,
            summary=body.summary,
            head_sha=head,
            base_sha=base or head,
        )
    )
    if body.outcome != "comment":
        try:
            if body.outcome == "approve":
                result = await gate_routes.approve_gate(wid, gate, request, None)
            else:
                result = await gate_routes.reject_gate(
                    wid, gate, gate_routes.GateReject(note=note, node=body.node), request
                )
        except Exception:
            await st.db.write(lambda c: store.unrecord_review(c, rid))
            raise
        await st.db.write(lambda c: store.publish_review(c, rid))
        return result
    await st.db.write(lambda c: store.publish_review(c, rid))
    if gate is None:
        # Nothing pending to reply for: no gate, no reply agent to launch.
        return {"review_id": rid, "outcome": "comment", "gate": None, "reply_agent": False}
    try:
        deps.spawn(
            request.app,
            wid,
            review_reply.run(
                st.db,
                st.run_dirs,
                work_item_id=wid,
                gate=gate,
                nodes=nodes,
                launch=deps.launch(st, row["repo"]),
            ),
        )
        spawned = True
    except deps.AlreadyRunning:
        spawned = False  # an auto-review is still running; the threads wait for the next comment
    return {"review_id": rid, "outcome": "comment", "gate": gate, "reply_agent": spawned}


async def _request_changes_now(st, request, row, body, head, base):
    """A `request_changes` with no gate pending (review threads anywhere §1):
    record the review and a rewind at the right node, then act on the item's
    current status without ever making the review wait on a gate that isn't
    there. `action` in the response is one of:

    - `"retried"`: the item was stopped (`needs_human`) with nothing else to
      claim it, so this retries it at the target right now.
    - `"rerun"`: the target is the node that is currently running -- pause and
      resume it immediately, carrying the note.
    - `"queued"`: the item is paused, or running an earlier target than the
      one this review names -- the rewind sits recorded and is honoured by
      the next `/resume`, `/retry`, or the running node's own completion
      (`walk.run_once`), never by stopping work in flight.

    Any exception from the retry/pause/resume calls below means the action
    itself was refused (Review Focus 4: e.g. a 409 because a walk is already
    running) -- the rewind is cancelled and the review unrecorded so nothing
    later honours a request that never actually landed, and the exception
    propagates so the caller sees the same refusal.
    """
    from kraft import review as review_mod
    from kraft.api.routes import lifecycle

    wid = row["id"]
    nodes = gate_routes.gate_nodes(st, row)
    current = next(i for i, n in enumerate(nodes) if n.id == row["current_node_id"])
    threads = st.db.read(lambda c: store.threads_for(c, wid))
    if body.node:
        idx = next((i for i, n in enumerate(nodes) if n.id == body.node), None)
        if idx is None or idx > current:
            raise HTTPException(
                400, f"cannot request changes at {body.node!r}: not at or before the current node"
            )
        why = "requested"
    else:
        runs = st.db.read(lambda c: store.node_run_rows(c, wid))
        try:
            idx, why = review_mod.changes_target(
                st.run_dirs.worktrees / wid, nodes, current, threads, runs
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    target = nodes[idx].id
    note = store.render_note(threads, body.summary) or "Changes requested."
    rid = await st.db.write(
        lambda c: store.record_review(
            c,
            wid=wid,
            gate=None,
            outcome="request_changes",
            summary=body.summary,
            head_sha=head,
            base_sha=base or head,
        )
    )
    await st.db.write(
        lambda c: store.request_rewind(c, wid, review_id=rid, target=target, note=note)
    )
    try:
        if row["status"] == "needs_human":
            await lifecycle.retry_work_item(wid, lifecycle.Retry(), request)
            action = "retried"
        elif row["status"] in ("active", "waiting") and idx == current:
            await lifecycle.pause_work_item(wid, request)
            try:
                await lifecycle.resume_work_item(wid, lifecycle.Resume(), request)
            except Exception:
                # The pause already landed; a failed resume must not leave the
                # item silently stuck paused, so retry once before concluding
                # the action failed. Only if the retry itself also fails does
                # this propagate to the outer handler that cancels the rewind
                # -- a retry that succeeds means the rewind is genuinely
                # running, so it must not be reported as refused.
                await lifecycle.resume_work_item(wid, lifecycle.Resume(), request)
            action = "rerun"
        else:  # paused, or running with an earlier target
            action = "queued"
    except Exception:
        await st.db.write(lambda c: store.cancel_rewind(c, wid, rid))
        await st.db.write(lambda c: store.unrecord_review(c, rid))
        raise
    await st.db.write(lambda c: store.publish_review(c, rid))
    return {
        "review_id": rid,
        "outcome": "request_changes",
        "gate": None,
        "target": target,
        "target_reason": why,
        "action": action,
    }


@api_router.post("/work-items/{wid}/review")
async def submit_item_review(wid: str, body: ReviewIn, request: Request):
    """The review submission route that acts on whatever gate is pending, if
    any -- the item-level door Task 4 adds beside the gate-keyed one below, now
    that a review or a thread belongs to the item rather than to a gate."""
    _refuse_agents(request)
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    return await _submit(st, request, row, board._pending_gate(st, wid), body)


@api_router.post("/work-items/{wid}/gates/{gate:path}/review")
async def submit_review(wid: str, gate: str, body: ReviewIn, request: Request):
    _refuse_agents(request)
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    gate_routes._gate_or_404(gate_routes.gate_nodes(st, row), gate)
    if board._pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    return await _submit(st, request, row, gate, body)


class ReplyIn(BaseModel):
    body: str
    claim: Literal["fixed", "answered", "should_fix"] | None = None


@api_router.post("/threads/{tid}/replies", status_code=201)
async def agent_reply(tid: str, body: ReplyIn, request: Request):
    """The one door an agent speaks through. Author and item come from its
    session, never from the request body."""
    st = request.app.state
    sid = request.headers.get("x-kraft-session-id")
    session = (
        st.db.read(
            lambda c: c.execute(
                "SELECT work_item_id, node_id FROM worker_sessions WHERE id = ?", (sid,)
            ).fetchone()
        )
        if sid
        else None
    )
    if session is None:
        raise HTTPException(403, "only a Kraft worker session can reply to a thread")
    row = _thread_or_404(st, tid)
    if row["work_item_id"] != session["work_item_id"]:
        raise HTTPException(403, "that thread belongs to another work item")
    if st.db.read(lambda c: store.is_draft_thread(c, tid)):
        raise HTTPException(404, f"unknown thread {tid!r}")
    if not body.body.strip():
        raise HTTPException(422, "body is empty")
    wid, gate = row["work_item_id"], row["gate"]
    if gate is None:
        attempt = None
    else:
        n = len(st.db.read(lambda c: store.gate_attempts(c, wid, gate)))
        attempt = n if board._pending_gate(st, wid) == gate else n + 1
    cid = await st.db.write(
        lambda c: store.agent_reply(
            c, tid, author=session["node_id"], body=body.body, claim=body.claim, attempt=attempt
        )
    )
    return st.db.read(lambda c: store.comment_dict(store.comment_row(c, cid)))
