"""Config drafts (spec §6.3, W9 A): the Templates editor's unpublished edits,
one per (area, key), kept here rather than in the browser. Every read and
write answers with the resolve result (`kraft.drafts.resolve`); a publish
writes the files only if none changed since it joined the draft (R18)."""

from __future__ import annotations

import difflib

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraft import config as config_mod
from kraft.api import api_router, deps
from kraft.drafts import resolve, store


def _area(area: str, key: str) -> store.Area:
    found = store.AREAS.get(area)
    if found is None:
        raise HTTPException(404, f"no draft area {area!r}")
    if not found.valid(key):
        raise HTTPException(400, f"invalid {area} key {key!r}")
    return found


def _state(st, area: str, key: str, draft: dict | None, history: list[dict]):
    """The files as the editor sees them (the published ones, the draft laid
    over them), the published ones, and their resolve result."""
    names = {*store.AREAS[area].files(key), *(draft["files"] if draft else ())}
    published = resolve.published(st.templates_dir, sorted(names))
    files = {n: t for n, t in published.items() if t is not None}
    files |= draft["files"] if draft else {}
    result = resolve.resolve(
        st,
        area,
        key,
        files,
        published,
        history=history,
        serialized=draft["serialized"] if draft else [],
    )
    return files, published, result


def _counts(result: dict) -> tuple[int, int]:
    return len(result["changes"]), len(result["problems"])


def _view(st, area: str, key: str, files: dict, published: dict, result: dict) -> dict:
    draft = st.db.read(lambda c: store.get(c, area, key))
    return {
        "area": area,
        "key": key,
        "draft": draft is not None,
        "files": files,
        "base": draft["base"] if draft else {f: store.digest(t) for f, t in published.items()},
        "updated_at": draft["updated_at"] if draft else None,
        "result": result,
    }


@api_router.get("/drafts")
async def list_config_drafts(request: Request):
    """Every open draft, with its counts as of its last write."""
    return request.app.state.db.read(store.list_drafts)


@api_router.get("/drafts/{area}/{key}")
async def get_config_draft(area: str, key: str, request: Request):
    """The draft, or the published files and their result when there is none."""
    st = request.app.state
    _area(area, key)
    draft = st.db.read(lambda c: store.get(c, area, key))
    files, published, result = _state(st, area, key, draft, draft["history"] if draft else [])
    if not files:
        raise HTTPException(404, f"no {area} {key!r}")
    return _view(st, area, key, files, published, result)


class FileText(BaseModel):
    text: str


@api_router.put("/drafts/{area}/{key}/files/{file:path}")
async def put_draft_file(area: str, key: str, file: str, body: FileText, request: Request):
    """One file's text as typed, comments kept. One undo step, shared with the
    PUTs to the same file just before it (`store.COALESCE_S`)."""
    st = request.app.state
    if file not in _area(area, key).files(key):
        raise HTTPException(422, f"{file!r} is not a file of {area} {key!r}")
    old = st.db.read(lambda c: store.get(c, area, key))
    draft = {
        "files": {**(old["files"] if old else {}), file: body.text},
        "serialized": [f for f in (old["serialized"] if old else []) if f != file],
    }
    # The state this write replaces is the newest one a YAML error falls back to.
    history = [*(old["history"] if old else []), {"files": old["files"] if old else {}}]
    files, published, result = _state(st, area, key, draft, history)
    await st.db.write(
        lambda c: store.write(
            c,
            area,
            key,
            draft["files"],
            published,
            serialized=draft["serialized"],
            counts=_counts(result),
            typed=file,
        )
    )
    return _view(st, area, key, files, published, result)


@api_router.post("/drafts/{area}/{key}/undo")
async def undo_draft(area: str, key: str, request: Request):
    """Back one request. Back to the published state drops the draft."""
    st = request.app.state
    _area(area, key)
    old = st.db.read(lambda c: store.get(c, area, key))
    if old is None or not old["history"]:
        raise HTTPException(409, "nothing to undo")
    *history, entry = old["history"]
    files, published, result = _state(st, area, key, entry, history)
    await st.db.write(lambda c: store.undo(c, area, key, published, _counts(result)))
    return _view(st, area, key, files, published, result)


def _stale(st, draft: dict) -> JSONResponse | None:
    """The 409 for a draft any of whose files changed on disk since it joined."""
    published = resolve.published(st.templates_dir, sorted(draft["base"]))
    changed = {
        f: {
            "published": published[f],
            "draft": draft["files"].get(f),
            "diff": "".join(
                difflib.unified_diff(
                    (published[f] or "").splitlines(keepends=True),
                    (draft["files"].get(f) or "").splitlines(keepends=True),
                    f"published/{f}",
                    f"draft/{f}",
                )
            ),
        }
        for f, base in draft["base"].items()
        if store.digest(published[f]) != base
    }
    if not changed:
        return None
    return JSONResponse(
        status_code=409,
        content={
            "detail": f"published since this draft began: {', '.join(sorted(changed))}",
            "files": changed,
        },
    )


@api_router.post("/drafts/{area}/{key}/publish")
async def publish_draft(area: str, key: str, request: Request):
    """Write the draft over the published files and reload, only if none of
    them changed since it joined the draft and the draft has no problem."""
    st = request.app.state
    _area(area, key)
    draft = st.db.read(lambda c: store.get(c, area, key))
    if draft is None:
        raise HTTPException(404, f"no draft of {area} {key!r}")
    if stale := _stale(st, draft):
        return stale
    _, _, result = _state(st, area, key, draft, draft["history"])
    if "yaml_error" in result or result["problems"]:
        error = result.get("yaml_error")
        detail = (
            f"{error['file']}:{error['line']}:{error['col']}: {error['message']}"
            if error
            else f"{len(result['problems'])} problem(s) to fix before publishing"
        )
        return JSONResponse(
            status_code=422, content={"detail": detail, "problems": result["problems"]}
        )
    async with st.draft_publish_lock:
        if stale := _stale(st, draft):
            return stale
        # ponytail: one file after another, each atomically; a crash between
        # two leaves a partial publish. The `/templates/*` saves don't take
        # this lock; a save racing a publish is caught by the next base check.
        for name, text in draft["files"].items():
            path = st.templates_dir / name
            if text is None:
                path.unlink(missing_ok=True)
            else:
                config_mod.write_text(path, text)
        deps._reload_templates(st)
        await st.db.write(lambda c: store.delete(c, area, key))
    _, _, result = _state(st, area, key, None, [])
    return {"published": sorted(draft["files"]), "result": result}


@api_router.delete("/drafts/{area}/{key}", status_code=204)
async def discard_draft(area: str, key: str, request: Request):
    """Final: a discard has no undo."""
    st = request.app.state
    _area(area, key)
    if not await st.db.write(lambda c: store.delete(c, area, key)):
        raise HTTPException(404, f"no draft of {area} {key!r}")
    return Response(status_code=204)
