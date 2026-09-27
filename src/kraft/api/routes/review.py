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
from kraft import store
from kraft.api import api_router, deps
from kraft.api.routes import board


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


def _pending_or_409(st, wid: str) -> str:
    gate = board._pending_gate(st, wid)
    if gate is None:
        raise HTTPException(409, "review threads need a pending gate")
    return gate


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
    gate = _pending_or_409(st, wid)
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
    _pending_or_409(st, row["work_item_id"])
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
