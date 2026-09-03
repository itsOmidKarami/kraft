from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from kraft import events, executor, reattach, store
from kraft import policy as policy_mod
from kraft.db import Database
from kraft.paths import RunDirs
from kraft.templates import load_registry, load_templates
from kraft.ws import Broadcaster

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = _REPO_ROOT / "templates"
DEFAULT_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"

_GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}


async def _guard(db, wid: str, coro) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("executor task crashed for %s", wid)
        reason = f"executor crashed: {exc!r}"
        try:
            await db.write(lambda c: store.mark_needs_human(c, wid, None, reason))
        except Exception:  # noqa: BLE001
            logger.exception("could not mark %s needs_human after crash", wid)


def _bd_cwd() -> str | None:
    return os.environ.get("KRAFT_BD_CWD") or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.tasks = {}
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR", ".kraft-run"))).ensure()
    database = await Database.open(run_dirs.db)
    # Read the templates dir at startup, not import time, so tests (and reloads)
    # that set KRAFT_TEMPLATES_DIR after import still take effect.
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or TEMPLATES_DIR)
    registry = load_registry(templates_dir / "registry.yaml")
    templates = load_templates(templates_dir, registry)

    policy_obj = None
    invalid_policy: list[str] = []
    try:
        policy_obj = policy_mod.load_policy(templates_dir / "policy.yaml")
    except policy_mod.PolicyError as exc:
        invalid_policy = [str(exc)]

    summary, adopted = await reattach.reattach(database, run_dirs, registry)
    app.state.tasks.update(adopted)
    for wid in summary.resumed_work_items:
        _spawn(
            app,
            wid,
            _guard(
                database,
                wid,
                executor.resume(
                    database,
                    run_dirs,
                    work_item_id=wid,
                    registry=registry,
                    adopted=adopted,
                    bd_cwd=_bd_cwd(),
                    policy=policy_obj,
                ),
            ),
        )

    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.registry = registry
    app.state.templates = templates
    app.state.policy = policy_obj
    app.state.invalid_policy = invalid_policy
    app.state.reattach_summary = summary

    dist = Path(os.environ.get("KRAFT_FRONTEND_DIST") or DEFAULT_FRONTEND_DIST)
    app.state.frontend_dist = dist if dist.is_dir() else None

    broadcaster = Broadcaster(database)
    await broadcaster.start()
    database.set_on_commit(broadcaster.notify)
    app.state.broadcaster = broadcaster
    try:
        yield
    finally:
        database.set_on_commit(None)
        await broadcaster.stop()
        tasks = list(app.state.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await database.close()


def _spawn(app: FastAPI, wid: str, coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    app.state.tasks[wid] = task
    task.add_done_callback(lambda _t, wid=wid: app.state.tasks.pop(wid, None))
    return task


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def _spa_navigation(request: Request, call_next):
    # A browser deep-link / refresh on a client-side route (e.g. /work-items/<id>)
    # would otherwise hit the matching API route and render raw JSON. Any top-level
    # navigation carries Sec-Fetch-Dest: document; hand those the SPA shell and let
    # the client router resolve the path. Static assets are dest=script/style, XHR
    # is dest=empty, so only real navigations are caught.
    dist = getattr(request.app.state, "frontend_dist", None)
    if (
        dist is not None
        and request.method == "GET"
        and request.headers.get("sec-fetch-dest") == "document"
    ):
        return FileResponse(dist / "index.html")
    return await call_next(request)


class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"


@app.post("/work-items", status_code=201)
async def create_work_item(body: NewWorkItem, request: Request):
    st = request.app.state
    if st.invalid_policy:
        # Spec §9: a malformed policy.yaml makes the process refuse work, same
        # posture as an invalid registry — do not accept a run we cannot bound.
        detail = "; ".join(st.invalid_policy)
        raise HTTPException(503, f"policy config invalid, refusing work: {detail}")
    template = st.templates.valid.get(body.chain_template)
    if template is None:
        raise HTTPException(422, "unknown or invalid template")
    if not Path(body.repo).is_dir():
        raise HTTPException(422, f"repo path does not exist: {body.repo}")
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            repo=body.repo,
            template=template,
            bd_cwd=_bd_cwd(),
        )
    except Exception as exc:  # noqa: BLE001 -- beads.intake raises several unrelated types
        raise HTTPException(502, f"bd intake failed: {exc}") from exc

    _spawn(
        request.app,
        wid,
        _guard(
            st.db,
            wid,
            executor.run(
                st.db,
                st.run_dirs,
                work_item_id=wid,
                registry=st.registry,
                bd_cwd=_bd_cwd(),
                policy=st.policy,
            ),
        ),
    )
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    chain = json.loads(row["chain_definition"])
    return JSONResponse(
        status_code=201,
        content={
            "id": wid,
            "bead_id": row["bead_id"],
            "status": row["status"],
            "chain_definition": chain,
            # the run task advances this asynchronously; before its first write the
            # chain still starts at node 0 by definition.
            "current_node_id": row["current_node_id"] or chain["nodes"][0]["id"],
        },
    )


def _work_item_row(st, wid):
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown work item")
    return row


def _pending_gate(st, wid: str) -> str | None:
    evts = st.db.read(lambda c: events.read_after(c, 0, wid))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["payload"]["gate"] if e["type"] == "gate_requested" else None
    return None


def _gate_node_index(chain: dict, gate: str) -> int:
    return next(i for i, n in enumerate(chain["nodes"]) if n.get("gate_after") == gate)


class GateReject(BaseModel):
    note: str


@app.get("/work-items")
async def list_work_items(request: Request):
    st = request.app.state

    def _read(c):
        rows = c.execute("SELECT * FROM work_items ORDER BY created_at").fetchall()
        cursor = c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        return rows, cursor

    rows, cursor = st.db.read(_read)
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "repo": r["repo"],
            "status": r["status"],
            "chain_template": r["chain_template"],
            "chain_definition": json.loads(r["chain_definition"]),
            "current_node_id": r["current_node_id"],
            "bead_id": r["bead_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return {"items": items, "cursor": cursor}


@app.get("/work-items/{wid}")
async def get_work_item(wid: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    sessions = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at", (wid,)
        ).fetchall()
    )
    return {
        **{k: row[k] for k in row.keys()},
        "chain_definition": json.loads(row["chain_definition"]),
        "worker_sessions": [{k: s[k] for k in s.keys()} for s in sessions],
    }


@app.get("/work-items/{wid}/events")
async def get_events(wid: str, request: Request, after_seq: int = 0):
    st = request.app.state
    _work_item_row(st, wid)
    return st.db.read(lambda c: events.read_after(c, after_seq, wid))


@app.post("/work-items/{wid}/gates/{gate}/approve")
async def approve_gate(wid: str, gate: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    if gate not in _GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    await st.db.write(lambda c: store.approve_gate(c, wid, gate))
    chain = json.loads(row["chain_definition"])
    start = _gate_node_index(chain, gate) + 1
    _spawn(
        request.app,
        wid,
        _guard(
            st.db,
            wid,
            executor.run(
                st.db,
                st.run_dirs,
                work_item_id=wid,
                registry=st.registry,
                bd_cwd=_bd_cwd(),
                start_index=start,
                policy=st.policy,
            ),
        ),
    )
    return {k: v for k, v in dict(_work_item_row(st, wid)).items()}


@app.post("/work-items/{wid}/gates/{gate}/reject")
async def reject_gate(wid: str, gate: str, body: GateReject, request: Request):
    st = request.app.state
    _work_item_row(st, wid)
    if gate not in _GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    await st.db.write(lambda c: store.reject_gate(c, wid, gate, body.note))
    return {k: v for k, v in dict(_work_item_row(st, wid)).items()}


@app.get("/worker-sessions/{sid}/log")
async def get_log(sid: str, request: Request):
    st = request.app.state
    row = st.db.read(
        lambda c: c.execute("SELECT log_path FROM worker_sessions WHERE id = ?", (sid,)).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown session")
    path = Path(row["log_path"])
    if not path.exists():
        raise HTTPException(404, "log not found")
    return FileResponse(path, media_type="text/plain")


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _origin_ok(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser client
    return urlsplit(origin).hostname in _LOCAL_HOSTS


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket, after_seq: int = 0):
    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    st = websocket.app.state
    bc = st.broadcaster
    client = bc.register()
    live_start = bc.cursor
    await websocket.accept()
    try:
        for ev in st.db.read(lambda c: events.read_after(c, after_seq)):
            if ev["seq"] <= live_start:
                await websocket.send_json(ev)
        while True:
            if client.dropped:
                await websocket.close(code=1011)
                return
            try:
                ev = await asyncio.wait_for(client.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if ev["seq"] <= live_start:
                continue
            await websocket.send_json(ev)
    except WebSocketDisconnect:
        pass
    finally:
        bc.unregister(client)


@app.get("/templates")
async def list_templates(request: Request):
    return [{"id": tid} for tid in sorted(request.app.state.templates.valid)]


@app.get("/health")
async def health(request: Request):
    st = request.app.state
    invalid = st.templates.invalid
    invalid_policy = st.invalid_policy
    return {
        "status": "degraded" if (invalid or invalid_policy) else "ok",
        "invalid_templates": invalid,
        "invalid_policy": invalid_policy,
        "reattach_summary": asdict(st.reattach_summary),
    }


@app.get("/{path:path}")
async def spa(path: str, request: Request):
    dist = request.app.state.frontend_dist
    if dist is None:
        raise HTTPException(404, "not found")
    candidate = (dist / path).resolve()
    if dist.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(dist / "index.html")


@app.exception_handler(404)
async def _spa_deep_link(request: Request, exc: HTTPException):
    # A client-side route that shadows a real API path (e.g. GET /work-items/<id>)
    # 404s before the catch-all sees it; hand those GETs the SPA shell too.
    dist = getattr(request.app.state, "frontend_dist", None)
    if (
        dist is not None
        and request.method == "GET"
        and "text/html" in request.headers.get("accept", "")
    ):
        return FileResponse(dist / "index.html")
    return JSONResponse({"detail": exc.detail}, status_code=404)
