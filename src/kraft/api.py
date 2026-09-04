from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from kraft import analytics as analytics_mod
from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import events, executor, reattach, store
from kraft import logs as logs_mod
from kraft import policy as policy_mod
from kraft.adapters import beads as beads_mod
from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer
from kraft.paths import RunDirs
from kraft.templates import (
    CONFIG_FILES,
    GATE_NAMES,
    RegistryError,
    load_registry,
    load_templates,
)
from kraft.ws import Broadcaster

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = _REPO_ROOT / "templates"
DEFAULT_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"


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

    # Config the Settings screens edit. Read once here and re-read on every save,
    # so a hand edit and a UI edit are the same operation to the rest of the app.
    access = config_mod.load_access(templates_dir / "access.yaml")

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
    app.state.templates_dir = templates_dir
    app.state.access = access
    # What the server is really listening on. __main__ reads access.yaml for this,
    # so they normally agree — until someone saves a new bind and has not restarted.
    app.state.bound_host = os.environ.get("KRAFT_HOST") or access["bind"]
    app.state.invalid_policy = invalid_policy
    app.state.reattach_summary = summary

    dist = Path(os.environ.get("KRAFT_FRONTEND_DIST") or DEFAULT_FRONTEND_DIST)
    app.state.frontend_dist = dist if dist.is_dir() else None

    index_conn = index_db.open_index(run_dirs.index_db)
    indexer = Indexer(
        index_conn, database, repos_env=os.environ.get("KRAFT_INDEX_REPOS"), run_dirs=run_dirs
    )
    await indexer.startup_scan()  # 04 §2: once, before live event handling
    await indexer.start()
    app.state.index_conn = index_conn
    app.state.indexer = indexer

    broadcaster = Broadcaster(database)
    await broadcaster.start()
    database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify()))
    app.state.broadcaster = broadcaster
    try:
        yield
    finally:
        database.set_on_commit(None)
        await broadcaster.stop()
        await indexer.stop()
        index_conn.close()
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


#: Paths that must work before a session exists.
#: `/health` is deliberately open — a monitor should not need a session, and the
#: login screen reads the bind address from it.
_PUBLIC_PATHS = {"/login", "/health"}


def _is_static_asset(app: FastAPI, path: str) -> bool:
    """True for a file that ships with the SPA bundle.

    The login page cannot render if its own JS and CSS come back 401, so the
    bundle is served before a session exists. These are build artifacts, not
    data — nothing about the running system leaks through them.
    """
    dist = getattr(app.state, "frontend_dist", None)
    if dist is None or not path or path == "/":
        return False
    candidate = (dist / path.lstrip("/")).resolve()
    return dist.resolve() in candidate.parents and candidate.is_file()


def _requires_auth(app: FastAPI) -> bool:
    """Auth is off on localhost and on for anything else (design 5e).

    Two details this depends on:

    * it is keyed to the address the process actually bound at startup, not the
      one saved in `access.yaml` — a bind change "takes effect on restart", so
      saving one must not lock the operator out of a server still on loopback;
    * with no password set there is nothing to authenticate *against*, so the
      gate stays open rather than bricking the API into a state where even
      setting the first password is refused. `__main__` will not start a
      non-loopback bind in that state, and the Access screen refuses to save one.
    """
    if getattr(app.state, "bound_host", "127.0.0.1") in config_mod.LOOPBACK:
        return False
    return bool((getattr(app.state, "access", None) or {}).get("password_hash"))


@app.middleware("http")
async def _authenticate(request: Request, call_next):
    app = request.app
    if (
        not _requires_auth(app)
        or request.url.path in _PUBLIC_PATHS
        or _is_static_asset(app, request.url.path)
    ):
        return await call_next(request)
    # A browser navigating to a client-side route must get the SPA shell, not
    # JSON — but `sec-fetch-dest` is a request header any client can send, so this
    # returns the shell itself rather than letting the request reach a handler.
    # Without that, `curl -X POST -H 'sec-fetch-dest: document' .../pause` would
    # be an unauthenticated write.
    dist = getattr(app.state, "frontend_dist", None)
    if (
        request.method == "GET"
        and request.headers.get("sec-fetch-dest") == "document"
        and dist is not None
    ):
        return FileResponse(dist / "index.html")
    token = request.cookies.get(auth_mod.COOKIE)
    if not token or not await app.state.db.write(
        lambda c, token=token: auth_mod.touch_session(c, token)
    ):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"
    #: cross-repo (design 1g "Advanced · cross-repo"): submodule paths from the
    #: repo's .gitmodules, and what happens to the root pointer when they land
    submodules: list[str] = []
    root_merge_policy: str = "bump"


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
    if body.root_merge_policy not in store.ROOT_MERGE_POLICIES:
        raise HTTPException(422, f"unknown root_merge_policy {body.root_merge_policy!r}")
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
            submodules=body.submodules,
            root_merge_policy=body.root_merge_policy,
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


class Retry(BaseModel):
    steer: str | None = None


class Steer(BaseModel):
    text: str


class Resume(BaseModel):
    steer: str | None = None


class OpenDocument(BaseModel):
    editor: str | None = None


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


def _completed_nodes(st, wid: str) -> set[str]:
    return {
        e["payload"].get("node_id")
        for e in st.db.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "node_completed"
    }


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
        "usage": st.db.read(lambda c: store.usage_rollup(c, wid)),
        # empty on a single-repo item; the detail's repos panel is multi-repo only
        "repos": store.repos_for(row, _completed_nodes(st, wid)),
        # local-only: the checkout the agents are editing, for "Open worktree"
        "worktree_path": str(st.run_dirs.worktrees / wid),
    }


@app.get("/work-items/{wid}/events")
async def get_events(wid: str, request: Request, after_seq: int = 0):
    st = request.app.state
    _work_item_row(st, wid)
    return st.db.read(lambda c: events.read_after(c, after_seq, wid))


@app.get("/work-items/{wid}/documents")
async def get_work_item_documents(wid: str, request: Request):
    st = request.app.state
    _work_item_row(st, wid)  # 404s on an unknown work item
    return {"work_item_id": wid, "documents": st.indexer.documents_for_work_item(wid)}


@app.post("/work-items/{wid}/gates/{gate}/approve")
async def approve_gate(wid: str, gate: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    if gate not in GATE_NAMES:
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
    if gate not in GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    await st.db.write(lambda c: store.reject_gate(c, wid, gate, body.note))
    return {k: v for k, v in dict(_work_item_row(st, wid)).items()}


def _terminate(pid: int | None) -> None:
    """SIGTERM the session's whole process group.

    Sessions are launched with `start_new_session=True`, so the child is its own
    group leader — signalling the group reaches an agent CLI's own children too,
    which a bare kill(pid) would orphan.
    """
    if pid is None:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError, PermissionError:
        pass  # already gone, or not ours — the row still moves to paused


@app.post("/work-items/{wid}/pause")
async def pause_work_item(wid: str, request: Request):
    """Stop the current node's running sessions (02 §10.2).

    There is no stdin channel into a one-shot agent CLI, so pause and steer are
    one mechanism: kill this attempt, carry new context into the next one.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] not in ("active",):
        raise HTTPException(409, f"work item is {row['status']}, not running")
    sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))
    ids = [s["id"] for s in sessions]
    # mark first, then signal: the adapter checks the row when its child dies, and
    # a SIGTERM that lands before the row is paused would resolve as 'failed'
    await st.db.write(lambda c: store.pause_work_item(c, wid, ids))
    for s in sessions:
        _terminate(s["pid"])
    return {"id": wid, "paused_sessions": ids}


@app.post("/work-items/{wid}/steer")
async def steer_work_item(wid: str, body: Steer, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] != "paused":
        raise HTTPException(409, "work item is not paused")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "steer text is required")
    await st.db.write(lambda c: store.set_steer(c, wid, text))
    return {"id": wid, "steer": text}


@app.post("/work-items/{wid}/resume")
async def resume_work_item(wid: str, body: Resume, request: Request):
    """Relaunch the paused node, carrying the steer into the next agent launch."""
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] != "paused":
        raise HTTPException(409, "work item is not paused")
    if body.steer and body.steer.strip():
        await st.db.write(lambda c: store.set_steer(c, wid, body.steer.strip()))
    steer = await st.db.write(lambda c: store.take_steer(c, wid))
    await st.db.write(lambda c: store.resume_work_item(c, wid, steer))

    chain = json.loads(row["chain_definition"])
    start = next((i for i, n in enumerate(chain["nodes"]) if n["id"] == row["current_node_id"]), 0)
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
                steer=steer,
            ),
        ),
    )
    return {"id": wid, "node_id": row["current_node_id"], "steer": steer}


@app.post("/work-items/{wid}/open-worktree")
async def open_worktree(wid: str, body: OpenDocument, request: Request):
    """Open the item's worktree in an editor (design 4b, handoff spec §8).

    Local-only by nature: the path means nothing to a browser on another
    machine, which is why the UI only offers this when the server can act on it.
    """
    st = request.app.state
    _work_item_row(st, wid)
    path = st.run_dirs.worktrees / wid
    if not path.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")
    return _launch_editor(body.editor, path)


@app.post("/work-items/{wid}/retry")
async def retry_work_item(wid: str, body: Retry, request: Request):
    """Clear a breached loop cap and re-run the node, steer text in hand (4b).

    Only a node that actually has a fix loop can be capped, so a retry on any
    other node is a client bug rather than a no-op worth pretending to honour.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    chain = json.loads(row["chain_definition"])
    node_id = row["current_node_id"]
    node = next((n for n in chain["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise HTTPException(409, "work item has no current node to retry")
    key = node.get("fix_loop")
    if not key:
        raise HTTPException(409, f"node {node_id!r} has no fix loop to retry")
    if row["status"] != "needs_human":
        raise HTTPException(409, "work item is not stopped")

    steer = (body.steer or "").strip() or None
    await st.db.write(lambda c: store.retry_after_cap(c, wid, node_id, key, steer))
    start = next(i for i, n in enumerate(chain["nodes"]) if n["id"] == node_id)
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
                steer=steer,
            ),
        ),
    )
    return {"id": wid, "node_id": node_id, "loop": key, "steer": steer}


def _session_row(st, sid: str):
    row = st.db.read(
        lambda c: c.execute(
            "SELECT id, log_path, status FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown session")
    return row


async def _tail(st, sid: str, path: Path, *, poll_s: float = 0.4):
    """SSE tail: every structured line, then new ones until the session ends.

    Stops one poll *after* the session leaves 'running', so the lines written
    between the last poll and the exit are not dropped on the floor.
    """
    sent = 0
    running = True
    while True:
        for line in logs_mod.jsonl(path, start_line=sent):
            sent = line["n"] + 1
            yield f"data: {json.dumps(line)}\n\n"
        if not running:
            break
        running = _session_row(st, sid)["status"] == "running"
        await asyncio.sleep(poll_s)
    yield "event: end\ndata: {}\n\n"


@app.get("/worker-sessions/{sid}/log")
async def get_log(sid: str, request: Request, format: str | None = None, follow: bool = False):
    """Plain text by default (the modal's copy button); `format=jsonl` for the
    filterable, followable view (design 6c)."""
    st = request.app.state
    row = _session_row(st, sid)
    path = Path(row["log_path"])
    if format == "jsonl":
        if follow:
            return StreamingResponse(
                _tail(st, sid, path),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )
        return {"session_id": sid, "status": row["status"], "lines": list(logs_mod.jsonl(path))}
    if not path.exists():
        raise HTTPException(404, "log not found")
    return FileResponse(path, media_type="text/plain")


@app.get("/search")
async def search(
    request: Request,
    q: str = "",
    source_kind: str | None = None,
    kind: str | None = None,
    repo: str | None = None,
    mode: str = "hybrid",
    limit: int = 20,
):
    if not q.strip():
        raise HTTPException(422, "q is required")
    limit = max(1, min(limit, 100))
    try:
        results, served = request.app.state.indexer.search_with_mode(
            q, source_kind=source_kind, kind=kind, repo=repo, limit=limit, mode=mode
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        # An explicit mode=vector on an instance with no embedder. `mode` echoes
        # what was actually served, so hybrid quietly degrades instead (04 §9).
        raise HTTPException(422, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise HTTPException(422, f"bad search query: {exc}") from exc
    return {"query": q, "mode": served, "results": results}


@app.get("/beads/search")
async def beads_search(request: Request, q: str = "", limit: int = 5):
    """The live strip under the search results (design 1h)."""
    if not q.strip():
        return {"query": q, "beads": []}
    return {"query": q, "beads": await beads_mod.search(q, cwd=_bd_cwd(), limit=limit)}


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request):
    doc = request.app.state.indexer.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "unknown document")
    return doc


#: Editor id -> the argv that opens a file with it. The UI offers exactly these.
_EDITORS = {
    "code": ["code"],
    "cursor": ["cursor"],
    "zed": ["zed"],
    "obsidian": ["obsidian"],
}


def _os_open() -> list[str] | None:
    if sys.platform == "darwin":
        return ["open"]
    if sys.platform.startswith("linux"):
        return ["xdg-open"]
    return None


def _launch_editor(editor: str | None, path: Path) -> dict:
    """Open `path` in an editor, or say plainly that this server cannot.

    501 rather than 500 when nothing here can open a window: that is the signal
    the SPA falls back on, handing the path to the viewer's own machine.
    """
    name = editor or os.environ.get("KRAFT_EDITOR") or ""
    argv = _EDITORS.get(name) if name else None
    if name and argv is None:
        raise HTTPException(400, f"unknown editor {name!r}")
    if argv is None:
        argv = _os_open()
    exe = shutil.which(argv[0]) if argv else None
    if exe is None:
        raise HTTPException(501, f"no editor available on the server for {name or 'default'}")
    try:
        subprocess.Popen([exe, str(path)], start_new_session=True)
    except OSError as exc:
        raise HTTPException(501, f"could not launch {name or 'the default editor'}: {exc}") from exc
    return {"path": str(path), "editor": name or "system"}


@app.post("/documents/{doc_id}/open")
async def open_document(doc_id: str, body: OpenDocument, request: Request):
    """Launch an editor on the document's absolute path.

    501 when there is nothing here that can open a window — the SPA falls back
    to a `vscode://file/...` URL, which the *viewer's* machine can honour even
    though the server cannot.
    """
    st = request.app.state
    doc = st.indexer.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "unknown document")
    path = Path(doc["repo"]) / doc["path"]

    return {"document_id": doc_id, **_launch_editor(body.editor, path)}


@app.get("/analytics")
async def get_analytics(
    request: Request,
    range: str = "7d",
    repo: str | None = None,
    template: str | None = None,
):
    """Totals, throughput and cost for the board's Analytics view (design 6b)."""
    if range not in analytics_mod.RANGES:
        raise HTTPException(400, f"unknown range {range!r}")
    st = request.app.state
    return st.db.read(
        lambda c: analytics_mod.compute(c, range_=range, repo=repo, template=template)
    )


@app.post("/index/rescan")
async def index_rescan(request: Request, repo: str | None = None):
    ix = request.app.state.indexer
    if repo is not None:
        if repo not in ix.repos():
            raise HTTPException(404, f"unknown repo: {repo}")
        stats = await ix.rescan_repo(repo)
        return {"repo": repo, "stats": vars(stats)}
    allstats = await ix.rescan_all()
    return {
        "repo": None,
        "stats": {
            k: sum(getattr(s, k) for s in allstats.values())
            for k in ("inserted", "updated", "renamed", "deleted")
        },
    }


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
    # HTTP middleware does not run for websockets, so the session check has to be
    # here too — otherwise a LAN bind would leave the live event stream open.
    if _requires_auth(websocket.app):
        token = websocket.cookies.get(auth_mod.COOKIE)
        if not token or not await websocket.app.state.db.write(
            lambda c, token=token: auth_mod.touch_session(c, token)
        ):
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


# ══ settings (design 5a–5e) ═════════════════════════════════════════════════
#
# Every write lands in the same versioned YAML an operator edits by hand
# (`02` §4.7 revised), and every write re-reads and re-validates the whole set
# so a bad save is refused rather than discovered at the next restart.


def _reload_templates(st) -> None:
    st.registry = load_registry(st.templates_dir / "registry.yaml")
    st.templates = load_templates(st.templates_dir, st.registry)


class RepoBody(BaseModel):
    path: str
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    gitlab_project: str | None = None
    enabled: bool = True


class ProbeBody(BaseModel):
    path: str


def _repos_path(st) -> Path:
    return st.templates_dir / "repos.yaml"


@app.get("/repos")
async def list_repos(request: Request):
    st = request.app.state
    return {"repos": config_mod.load_repos(_repos_path(st))}


@app.post("/repos/probe")
async def probe_repo(body: ProbeBody, request: Request):
    """Read-only inspection of a candidate repo — Kraft never edits repo files."""
    try:
        return config_mod.probe_repo(body.path)
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/repos", status_code=201)
async def add_repo(body: RepoBody, request: Request):
    st = request.app.state
    try:
        probed = config_mod.probe_repo(body.path)
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    repos = config_mod.load_repos(_repos_path(st))
    if any(r["path"] == probed["path"] for r in repos):
        raise HTTPException(409, f"{probed['path']} is already connected")
    entry = {
        "path": probed["path"],
        "name": body.name or probed["name"],
        "default_chain_template": body.default_chain_template or "default",
        "test_command": body.test_command or probed["test_command"],
        "gitlab_project": body.gitlab_project or probed["gitlab_project"],
        "enabled": body.enabled,
    }
    repos.append(entry)
    config_mod.save_repos(_repos_path(st), repos)
    return entry


class RepoPatch(BaseModel):
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    gitlab_project: str | None = None
    enabled: bool | None = None


@app.patch("/repos")
async def update_repo(body: RepoPatch, request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(_repos_path(st))
    entry = next((r for r in repos if r["path"] == path), None)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    entry.update({k: v for k, v in body.model_dump().items() if v is not None})
    config_mod.save_repos(_repos_path(st), repos)
    return entry


@app.delete("/repos", status_code=204)
async def remove_repo(request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(_repos_path(st))
    kept = [r for r in repos if r["path"] != path]
    if len(kept) == len(repos):
        raise HTTPException(404, f"{path} is not connected")
    config_mod.save_repos(_repos_path(st), kept)


class TemplateBody(BaseModel):
    nodes: list[dict]


def _template_path(st, tid: str) -> Path:
    if not tid.isidentifier() and not tid.replace("-", "_").isidentifier():
        raise HTTPException(400, f"invalid template id {tid!r}")
    return st.templates_dir / f"{tid}.yaml"


@app.get("/templates")
async def list_templates(request: Request):
    st = request.app.state
    return [
        {"id": tid, "nodes": t.nodes, "gates": sum(1 for n in t.nodes if n.get("gate_after"))}
        for tid, t in sorted(st.templates.valid.items())
    ]


@app.get("/templates/{tid}")
async def get_template(tid: str, request: Request):
    st = request.app.state
    template = st.templates.valid.get(tid)
    if template is None:
        raise HTTPException(404, f"unknown template {tid!r}")
    return {"id": tid, "nodes": template.nodes}


def _validate_template(st, tid: str, nodes: list[dict]) -> dict:
    """Write the candidate to a scratch dir and run the real loader over it.

    Re-using `load_templates` rather than re-deriving the rules is the point:
    there is one definition of a valid chain, and this is it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        (scratch / "registry.yaml").write_text((st.templates_dir / "registry.yaml").read_text())
        (scratch / f"{tid}.yaml").write_text(yaml.safe_dump({"id": tid, "nodes": nodes}))
        result = load_templates(scratch, st.registry)
    hooks = {t for n in nodes for t in (n.get("tasks") or [])}
    repos = config_mod.load_repos(_repos_path(st))
    return {
        "id": tid,
        "valid": tid in result.valid,
        "error": result.invalid.get(tid),
        # a hook disabled for a repo makes any template using it unresolvable there
        "by_repo": [
            {
                "repo": r["path"],
                "resolvable": all(h in st.registry.hooks for h in hooks),
            }
            for r in repos
        ],
    }


@app.post("/templates/{tid}/validate")
async def validate_template(tid: str, body: TemplateBody, request: Request):
    return _validate_template(request.app.state, tid, body.nodes)


@app.put("/templates/{tid}")
async def put_template(tid: str, body: TemplateBody, request: Request):
    st = request.app.state
    path = _template_path(st, tid)
    report = _validate_template(st, tid, body.nodes)
    if not report["valid"]:
        raise HTTPException(422, report["error"] or f"template {tid!r} is not valid")
    config_mod.write_yaml(path, {"id": tid, "nodes": body.nodes})
    _reload_templates(st)
    return {"id": tid, "nodes": body.nodes}


class RegistryBody(BaseModel):
    hooks: dict


@app.get("/registry")
async def get_registry(request: Request):
    return {"hooks": request.app.state.registry.hooks}


@app.put("/registry")
async def put_registry(body: RegistryBody, request: Request):
    """Save bindings, then re-run the chain validator over every template.

    A binding change affects intake only — a live work item keeps the
    `chain_definition` it materialized — but a template that stops resolving
    has to surface immediately, not at the next intake.
    """
    st = request.app.state
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "registry.yaml"
        candidate.write_text(yaml.safe_dump({"hooks": body.hooks}))
        try:
            registry = load_registry(candidate)
        except RegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        for src in st.templates_dir.glob("*.yaml"):
            if src.name not in CONFIG_FILES:
                (Path(tmp) / src.name).write_text(src.read_text())
        checked = load_templates(Path(tmp), registry)
    config_mod.write_yaml(st.templates_dir / "registry.yaml", {"hooks": body.hooks})
    _reload_templates(st)
    return {"hooks": body.hooks, "invalid_templates": checked.invalid}


class PolicyBody(BaseModel):
    loops: dict
    default: dict


@app.get("/policy")
async def get_policy(request: Request):
    st = request.app.state
    return config_mod.read_yaml(st.templates_dir / "policy.yaml", {"loops": {}, "default": {}})


@app.put("/policy")
async def put_policy(body: PolicyBody, request: Request):
    """Caps apply to loops that start after the save — a counter already running
    keeps the cap it snapshotted at first fire (`02` §2.C)."""
    st = request.app.state
    data = {"loops": body.loops, "default": body.default}
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "policy.yaml"
        candidate.write_text(yaml.safe_dump(data))
        try:
            policy_obj = policy_mod.load_policy(candidate)
        except policy_mod.PolicyError as exc:
            raise HTTPException(422, str(exc)) from exc
    config_mod.write_yaml(st.templates_dir / "policy.yaml", data)
    st.policy = policy_obj
    st.invalid_policy = []
    return data


class AccessBody(BaseModel):
    bind: str | None = None
    port: int | None = None
    password: str | None = None
    session_expiry_days: int | None = None


@app.get("/access")
async def get_access(request: Request):
    access = request.app.state.access
    return {
        "bind": access["bind"],
        "port": access["port"],
        "session_expiry_days": access["session_expiry_days"],
        "password_set": bool(access["password_hash"]),
        "auth_required": _requires_auth(request.app),
    }


@app.put("/access")
async def put_access(body: AccessBody, request: Request):
    st = request.app.state
    access = dict(st.access)
    if body.bind is not None:
        access["bind"] = body.bind
    if body.port is not None:
        access["port"] = body.port
    if body.session_expiry_days is not None:
        access["session_expiry_days"] = body.session_expiry_days
    if body.password:
        access["password_hash"] = auth_mod.hash_password(body.password)
    # Binding off-localhost without a password is the configuration that puts an
    # agent runner on the office wifi. Refuse it rather than allow it quietly.
    if access["bind"] not in config_mod.LOOPBACK and not access["password_hash"]:
        raise HTTPException(422, "set a password before binding off localhost")
    config_mod.save_access(st.templates_dir / "access.yaml", access)
    st.access = access
    # Only once the new hash is durable: revoking first and then failing to write
    # would sign everyone out while leaving the *old* password live.
    if body.password:
        await st.db.write(auth_mod.revoke_all)
    return await get_access(request)


class Login(BaseModel):
    password: str


@app.post("/login")
async def login(body: Login, request: Request):
    st = request.app.state
    if not auth_mod.verify_password(body.password, st.access["password_hash"]):
        raise HTTPException(401, "wrong password")
    token = auth_mod.new_token()
    await st.db.write(
        lambda c: auth_mod.create_session(
            c,
            token,
            label=request.headers.get("user-agent", "unknown"),
            ip=request.client.host if request.client else "",
            expiry_days=int(st.access["session_expiry_days"]),
        )
    )
    response = JSONResponse({"ok": True})
    response.set_cookie(
        auth_mod.COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=int(st.access["session_expiry_days"]) * 86400,
        path="/",
    )
    return response


@app.post("/logout", status_code=204)
async def logout(request: Request):
    token = request.cookies.get(auth_mod.COOKIE)
    if token:
        await request.app.state.db.write(
            lambda c: auth_mod.revoke_session(c, auth_mod.token_id(token))
        )
    response = Response(status_code=204)
    response.delete_cookie(auth_mod.COOKIE, path="/")
    return response


@app.get("/sessions")
async def list_sessions(request: Request):
    token = request.cookies.get(auth_mod.COOKIE)
    return {"sessions": request.app.state.db.read(lambda c: auth_mod.list_sessions(c, token))}


@app.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: str, request: Request):
    revoked = await request.app.state.db.write(lambda c: auth_mod.revoke_session(c, session_id))
    if not revoked:
        raise HTTPException(404, "unknown session")


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
        "index": st.indexer.health(),
        # public: the login screen says which address it is asking a password for
        "bind": st.access["bind"],
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
