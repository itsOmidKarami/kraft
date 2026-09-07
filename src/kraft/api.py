from __future__ import annotations

import asyncio
import hmac
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
from typing import Literal
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from kraft import analytics as analytics_mod
from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import events, executor, findings, reattach, review, store
from kraft import intake as intake_mod
from kraft import logs as logs_mod
from kraft import notify as notify_mod
from kraft import policy as policy_mod
from kraft import steering as steering_mod
from kraft.adapters import agent as agent_mod
from kraft.adapters import beads as beads_mod
from kraft.db import Database
from kraft.index import db as index_db
from kraft.index import ingest as ingest_mod
from kraft.index.service import Indexer
from kraft.paths import (
    BUNDLED,
    RunDirs,
    default_run_dir,
    default_skills_dir,
    default_templates_dir,
)
from kraft.templates import (
    CONFIG_FILES,
    GATE_NAMES,
    RegistryError,
    load_registry,
    load_templates,
)
from kraft.ws import Broadcaster

logger = logging.getLogger(__name__)

DEFAULT_FRONTEND_DIST = BUNDLED / "web"

#: Diff bodies larger than this are cut at a file boundary. Protects the
#: browser; not a user decision, so not policy.
DIFF_MAX_BYTES = 1_000_000


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
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).ensure()
    # The credential a non-browser client (kraft mcp, kraft <verb>) presents when
    # auth is on at all. Created once and kept, so a registered MCP client keeps
    # working across restarts.
    app.state.mcp_token = auth_mod.ensure_mcp_token(run_dirs.base)
    database = await Database.open(run_dirs.db)
    # Read the templates dir at startup, not import time, so tests (and reloads)
    # that set KRAFT_TEMPLATES_DIR after import still take effect.
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    # Set on app.state now (not after reattach below, where it lived before) so
    # `_launch` — which reads st.templates_dir — can build a launch context for
    # a reattached work item's resume.
    app.state.templates_dir = templates_dir
    # Where an operator may override a bundled method file. Absent on almost
    # every install; `kraft.skill` falls back to the packaged copy.
    app.state.skills_dir = Path(os.environ.get("KRAFT_SKILLS_DIR") or default_skills_dir())
    registry = load_registry(templates_dir / "registry.yaml", skills_dir=app.state.skills_dir)
    templates = load_templates(templates_dir, registry)

    # Config the Settings screens edit. Read once here and re-read on every save,
    # so a hand edit and a UI edit are the same operation to the rest of the app.
    access = config_mod.load_access(templates_dir / "access.yaml")
    # A hand-edit typo must not refuse the boot: degrade to the default — off —
    # so Settings → Auto-intake comes up and can be used to fix the file.
    try:
        app.state.intake = config_mod.load_intake(templates_dir / "intake.yaml")
    except config_mod.ConfigError as exc:
        logger.warning("intake.yaml is unreadable, auto-intake stays off: %s", exc)
        app.state.intake = dict(config_mod.INTAKE_DEFAULT)

    policy_obj = None
    invalid_policy: list[str] = []
    try:
        policy_obj = policy_mod.load_policy(templates_dir / "policy.yaml")
    except policy_mod.PolicyError as exc:
        invalid_policy = [str(exc)]

    # Snapshot before reattach spawns anything. A resumed executor task starts
    # running at its first `await` -- some time after this line, but well
    # before `notifier.start()` below (which comes after `startup_scan`, an
    # unbounded scan on a large index). A `MAX(seq)` read taken there instead
    # would land past whatever a just-resumed work item already emitted, and
    # the notifier would never see it: not "late", gone. This snapshot is
    # taken here, before reattach, and handed straight through to
    # `notifier.start()` so the notifier's own cursor can never be later than
    # the first event a resumed task might produce.
    notify_cursor = database.read(
        lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    )

    summary, adopted = await reattach.reattach(database, run_dirs, registry)
    app.state.tasks.update(adopted)
    for wid in summary.resumed_work_items:
        repo_row = database.read(
            lambda c, wid=wid: c.execute(
                "SELECT repo FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
        )
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
                    launch=_launch(app.state, repo_row["repo"]) if repo_row else None,
                ),
            ),
        )

    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.registry = registry
    app.state.templates = templates
    app.state.policy = policy_obj
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
        index_conn,
        database,
        repos_env=os.environ.get("KRAFT_INDEX_REPOS"),
        run_dirs=run_dirs,
        repos_path=templates_dir / "repos.yaml",
    )
    await indexer.startup_scan()  # 04 §2: once, before live event handling
    await indexer.start()
    app.state.index_conn = index_conn
    app.state.indexer = indexer

    broadcaster = Broadcaster(database)
    await broadcaster.start()
    # Third subscriber on the same fan-out. Its sends are detached tasks, so a
    # hanging webhook cannot stall the WebSocket or the indexer behind it.
    notifier = notify_mod.Notifier(
        database,
        templates_dir / "notify.yaml",
        fallback_base_url=f"http://{app.state.bound_host}:{access['port']}",
    )
    await notifier.start(cursor=notify_cursor)
    database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify(), notifier.notify()))
    app.state.broadcaster = broadcaster
    app.state.notifier = notifier
    # Off by default costs nothing at all: no task, no timer, no tick. On
    # `app.state` as well as in a local because that is the only way a test can
    # tell "no poller was created" from "a poller was created and did nothing" —
    # deleting the condition would otherwise leave every test green while a
    # disabled instance grew a live timer.
    intake_task = (
        asyncio.ensure_future(intake_mod.poller(app)) if app.state.intake["enabled"] else None
    )
    app.state.intake_task = intake_task
    # PUT /intake swaps this task, and the swap has to await the cancellation of
    # the old one. Without the lock two overlapping saves both read the same old
    # task, both start a poller, and only the last assignment is reachable --
    # the other ticks on, uncancellable, past shutdown.
    app.state.intake_lock = asyncio.Lock()
    try:
        yield
    finally:
        database.set_on_commit(None)
        await broadcaster.stop()
        await notifier.stop()
        await indexer.stop()
        index_conn.close()
        # Before the work item tasks, so a tick in flight cannot _spawn one
        # into the list that is about to be cancelled.
        # app.state, not the local: PUT /intake replaces this task when the
        # operator toggles the poller, and cancelling the one lifespan happened
        # to start would leave the live one running past shutdown.
        live_intake_task = app.state.intake_task
        if live_intake_task is not None:
            live_intake_task.cancel()
            await asyncio.gather(live_intake_task, return_exceptions=True)
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
        # The shell is served under every client-side route, including paths that
        # are also API routes (/work-items/<id>). The browser caches by URL, so
        # without no-store it answers the SPA's own fetch for that same path with
        # the cached HTML: `res.json()` throws and the detail screen renders an
        # empty husk on every deep link and every refresh. `vary` says the same
        # thing to caches that honour it.
        return FileResponse(
            dist / "index.html",
            headers={"cache-control": "no-store", "vary": "sec-fetch-dest"},
        )
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
    # After the SPA-shell branch on purpose: that branch answers any GET claiming
    # `sec-fetch-dest: document`, so a bearer check ahead of it would leave that
    # path reachable, and one inside it would hand an MCP client HTML not JSON.
    bearer = request.headers.get("authorization", "")
    expected = getattr(app.state, "mcp_token", None)
    if expected and bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], expected):
        return await call_next(request)
    token = request.cookies.get(auth_mod.COOKIE)
    if not token or not await app.state.db.write(
        lambda c, token=token: auth_mod.touch_session(c, token)
    ):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


class Attachment(BaseModel):
    kind: Literal["spec", "plan"]
    #: repo-relative; validated and normalized server-side before it is stored
    path: str


class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"
    #: cross-repo (design 1g "Advanced · cross-repo"): submodule paths from the
    #: repo's .gitmodules, and what happens to the root pointer when they land
    submodules: list[str] = []
    root_merge_policy: str = "bump"
    #: spec/plan documents that already exist — they trim the gates they satisfy
    attachments: list[Attachment] = []
    #: False creates the item without running it (design §6 rule 1). An agent
    #: cannot spend tokens unattended; a human starts it from the board.
    autostart: bool = True


def _validated_attachments(repo: str, attachments: list[Attachment]) -> list[dict]:
    """Trust boundary: `path` comes from a browser and is used to read a file and
    to write into a worktree. Resolve under the repo and reject any escape."""
    kinds = [a.kind for a in attachments]
    if len(set(kinds)) != len(kinds):
        raise HTTPException(422, "at most one attachment per kind")
    root = Path(repo).resolve()
    out = []
    for a in attachments:
        target = (root / a.path).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(422, f"attachment path escapes the repo: {a.path}")
        # Working tree, not HEAD: a document written minutes ago is legal input,
        # and env_setup copies it into the worktree.
        if not target.is_file():
            raise HTTPException(422, f"attachment not found: {a.path}")
        out.append({"kind": a.kind, "path": str(target.relative_to(root))})
    return out


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
    attachments = _validated_attachments(body.repo, body.attachments)
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
            attachments=attachments,
            status="active" if body.autostart else "paused",
        )
    except Exception as exc:  # noqa: BLE001 -- beads.intake raises several unrelated types
        raise HTTPException(502, f"bd intake failed: {exc}") from exc

    if not body.autostart:
        # Created, not started. `/resume` begins it at node zero, because a NULL
        # current_node_id falls through that handler's `next(..., 0)` default.
        return {"id": wid, "status": "paused"}

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
                launch=_launch(st, body.repo),
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


#: The event types that bound a `work_item_needs_human` stop, newest wins —
#: the same shape of boundary `_pending_gate` models. A stop needs one because
#: `store.request_gate` also sets status 'needs_human' while appending no
#: `work_item_needs_human`: without this set a first-match scan reads an
#: answered-and-resumed stop, or a pending gate, as a live one forever.
_STOP_BOUNDARY = (
    "work_item_needs_human",
    "gate_requested",
    "gate_rejected",
    "work_item_resumed",
    "work_item_retried",
    "work_item_completed",
)


def _stop_reason(st, wid: str) -> str | None:
    """The reason of the stop the item is *currently* sitting on, or None if
    anything in `_STOP_BOUNDARY` superseded it."""
    for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid))):
        if e["type"] in _STOP_BOUNDARY:
            return e["payload"]["reason"] if e["type"] == "work_item_needs_human" else None
    return None


def _needs_context_stop(st, wid: str) -> bool:
    """True iff the item's current stop is a `needs_context` — a
    `work_item_needs_human` reason of the form `needs_context: <question>`
    (executor._needs_context_question). Answerable via /steer and /resume the
    same as a pause, unlike any other needs_human reason."""
    reason = _stop_reason(st, wid)
    return reason is not None and reason.startswith("needs_context:")


def _gate_node_index(chain: dict, gate: str) -> int:
    return next(i for i, n in enumerate(chain["nodes"]) if n.get("gate_after") == gate)


def _gate_artifact(st, row, gate: str | None) -> str | None:
    """The document the pending gate is a decision *about*, or None.

    Derived from the binding, not stored: the gate's node names its hooks, a
    hook with `artifact:` names a kind, and the kind plus the work item id is
    the path (`agent.artifact_path`). Nothing here to migrate and nothing to go
    stale when a rerun revises the same file.

    None when there is no pending gate, when none of the node's hooks produce
    an artifact, or when the file is not on disk — the last case is an agent
    that reported done without honouring the contract, and the gate is still
    answerable, just without a document to read.
    """
    if not gate:
        return None
    chain = json.loads(row["chain_definition"])
    try:
        node = chain["nodes"][_gate_node_index(chain, gate)]
    except StopIteration:
        return None
    worktree = st.run_dirs.worktrees / row["id"]
    for task in node["tasks"]:
        kind = st.registry.hooks.get(task, {}).get("artifact")
        if not kind:
            continue
        rel = agent_mod.artifact_path(kind, row["id"])
        if (worktree / rel).is_file():
            return rel
    return None


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
        # The latest gate_* event per item, in one pass — the board renders a gate
        # prompt per row and must not offer Approve on a rejected gate.
        gates = c.execute(
            "SELECT work_item_id, type, payload FROM events WHERE seq IN ("
            "  SELECT MAX(seq) FROM events"
            "  WHERE type IN ('gate_requested', 'gate_approved', 'gate_rejected')"
            "  GROUP BY work_item_id)"
        ).fetchall()
        return rows, cursor, gates

    rows, cursor, gate_rows = st.db.read(_read)
    pending = {
        g["work_item_id"]: json.loads(g["payload"])["gate"]
        for g in gate_rows
        if g["type"] == "gate_requested"
    }
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
            "pending_gate": pending.get(r["id"]),
            "attachments": json.loads(r["attachments"]) if r["attachments"] else [],
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


def _deferred_findings(st, wid: str) -> list[dict]:
    """Findings that never entered the loop, for the human at the gate.

    A roll-up nobody reads is a silent discard, so these are rendered at the
    gate rather than merely recorded.
    """
    loop_severities = getattr(st.policy, "loop_severities", policy_mod.DEFAULT_LOOP_SEVERITIES)
    seen: dict[str, dict] = {}
    for e in st.db.read(lambda c: events.read_after(c, 0, wid)):
        if e["type"] != "findings_measured":
            continue
        for raw in e["payload"].get("findings", []):
            if raw.get("severity") in loop_severities:
                continue
            seen.setdefault(findings.from_payload(raw).fingerprint, raw)
    return list(seen.values())


def _concerns(st, wid: str) -> list[str]:
    """`done_with_concerns` text from every session that reported one, oldest
    first — the same shape of thing as `_deferred_findings` (something a
    machine noticed and owes a human before approval), read from the event log
    `adapters.subprocess.run_task` stamps at session exit, never from
    `result_path` on disk.

    Bounded at the last resolved gate, the way `_stop_reason` is: spec §2 puts a
    concern at the *next* gate, and the detail screen now passes concerns to
    every gate rather than only `human_review_approval`. Without a boundary one
    concern would be re-posed at every later gate the item reaches, long after
    the human who approved that gate already answered for it (Kraft-ub2).
    """
    out: list[str] = []
    for e in reversed(st.db.read(lambda c: events.read_after(c, 0, wid))):
        if e["type"] in ("gate_approved", "gate_rejected"):
            break
        if e["type"] == "worker_session_exited" and e["payload"].get("concerns"):
            out.append(e["payload"]["concerns"])
    out.reverse()  # oldest first
    return out


def _needs_context_question(st, wid: str) -> str | None:
    """The agent's question, straight from the `needs_context: <question>`
    reason `_needs_context_stop` already trusts — not a scan of
    `worker_session_exited.question` events, which has no boundary at the
    triggering stop: a later needs_context whose result file omitted the
    field would otherwise resurface an earlier, already-answered question
    instead of falling through to `executor._needs_context_question`'s own
    `"(no question given)"` guard, which the reason string always carries.
    """
    reason = _stop_reason(st, wid)
    if reason is None or not reason.startswith("needs_context:"):
        return None
    return reason.removeprefix("needs_context: ")


@app.get("/work-items/{wid}")
async def get_work_item(wid: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    sessions = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at", (wid,)
        ).fetchall()
    )
    pending = _pending_gate(st, wid)
    return {
        **{k: row[k] for k in row.keys()},
        "chain_definition": json.loads(row["chain_definition"]),
        "attachments": json.loads(row["attachments"]) if row["attachments"] else [],
        "worker_sessions": [{k: s[k] for k in s.keys()} for s in sessions],
        "usage": st.db.read(lambda c: store.usage_rollup(c, wid)),
        # empty on a single-repo item; the detail's repos panel is multi-repo only
        "repos": store.repos_for(row, _completed_nodes(st, wid)),
        # local-only: the checkout the agents are editing, for "Open worktree"
        "worktree_path": str(st.run_dirs.worktrees / wid),
        # The gate actually waiting on a person. Inferring it client-side from
        # "the node has a gate_after and its sessions are done" cannot see a
        # rejection, and offers Approve on a gate the API will 409 (Kraft).
        "pending_gate": pending,
        # The document the gate is a decision about — the spec at
        # spec_approval, the plan at plan_approval. The detail screen offers
        # "Review spec" only when this is set.
        "gate_artifact": _gate_artifact(st, row, pending),
        # Why the item is stopped, when it is: the detail screen has to tell a
        # loop escalation from an unrelated crash on the same node (Kraft-esc).
        "stop_reason": _stop_reason(st, wid),
        "deferred_findings": _deferred_findings(st, wid),
        "concerns": _concerns(st, wid),
        "needs_context_question": _needs_context_question(st, wid),
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


def _truncate_at_file_boundary(diff: str, limit: int) -> tuple[str, bool]:
    """Cut a unified diff to `limit` bytes on a `diff --git` boundary.

    A single file bigger than `limit` has no boundary to cut at — the whole
    first chunk is kept unconditionally so at least one file always survives
    truncation. That file is then cut at the last newline that fits, so a
    committed lockfile or vendored blob doesn't come back whole.
    """
    if len(diff.encode()) <= limit:
        return diff, False
    kept: list[str] = []
    size = 0
    for chunk in diff.split("\ndiff --git ")[:1] + [
        "\ndiff --git " + c for c in diff.split("\ndiff --git ")[1:]
    ]:
        if size + len(chunk.encode()) > limit and kept:
            break
        kept.append(chunk)
        size += len(chunk.encode())
    result = "".join(kept)
    encoded = result.encode()
    if len(kept) == 1 and len(encoded) > limit:
        cut = encoded[:limit].rfind(b"\n")
        encoded = encoded[: cut if cut != -1 else limit]
        result = encoded.decode(errors="ignore")
    return result, True


@app.get("/work-items/{wid}/diff")
async def get_work_item_diff(wid: str, request: Request):
    """The changes an agent made, for a reviewer with no filesystem access.

    Diffs the working tree against `base_ref`, not `base_ref...HEAD`: an agent
    that wrote files without committing them is the normal mid-chain state, and
    a committed-only diff would show an empty change set while the work sat on
    disk.
    """
    st = request.app.state
    row = _work_item_row(st, wid)  # 404s on an unknown work item
    base = row["base_ref"]
    if not base:
        # Pre-migration items (and any future template with no env_setup node)
        # never got a base_ref stamped, and are also the likeliest to have had
        # their worktree cleaned up since — check this before the worktree, so
        # that combination degrades to "no diff" rather than a 404.
        return {
            "work_item_id": wid,
            "base_ref": None,
            "files": [],
            "diff": "",
            "untracked": [],
            "truncated": False,
            "diff_max_bytes": DIFF_MAX_BYTES,
            "worktree_path": str(st.run_dirs.worktrees / wid),
        }
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")

    change = review.read_change(worktree, base)
    if change is None:
        # None means git itself failed (and git_read has already logged the
        # command and stderr). Returning an empty diff here would be
        # indistinguishable from "no changes" to the human approving the gate,
        # and the worktree path is a server filesystem detail a remote
        # reviewer's browser has no business seeing.
        raise HTTPException(500, "git could not read this work item's worktree")

    diff, truncated = _truncate_at_file_boundary(change.diff, DIFF_MAX_BYTES)
    return {
        "work_item_id": wid,
        "base_ref": base,
        "files": change.files,
        "diff": diff,
        "untracked": change.untracked,
        "truncated": truncated,
        # A reviewer told the diff is partial and not told how much is missing
        # or where the rest is has been given half a warning (spec F §2.2).
        # `worktree_path` is no new disclosure: GET /work-items/{wid} has always
        # returned it, to the same authenticated caller.
        "diff_max_bytes": DIFF_MAX_BYTES,
        "worktree_path": str(worktree),
    }


def _open_no_symlinks(root: Path, rel: str) -> int:
    """Open `rel` beneath `root`, refusing a symlink at every component.

    A resolved path is a fact about the filesystem at the instant it was
    resolved. The agent owns this worktree and can swap any component --
    including a parent directory -- between the resolve and the open, so
    containment has to be enforced by the open itself: walk down from the
    root, one `openat` per component, never following a link.
    """
    parts = Path(rel).parts
    # The root's own ancestors are server-owned, so following links above the
    # worktree is fine (and necessary on macOS, where /tmp is a symlink).
    dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


async def _refuse_artifact(st, wid: str, rel: str, reason: str) -> None:
    """Record why an artifact read was refused, on the item's own timeline.

    Spec §4's premise is a reviewer with no access to the server's log, so a
    `logger.warning` is not a substitute for this -- it is what this replaces.
    Not called for `rel is None` (`_gate_artifact` found no file): that is the
    ordinary state of a gate whose agent session has not finished yet, and a
    UI that polls the detail endpoint while it waits would turn "no document
    yet" into an event on every poll. Everything past that point means
    `_gate_artifact` *did* find a file and something went wrong reading the
    one it found, which is the actual refusal this exists to explain.
    """
    payload = {"path": rel, "reason": reason}
    await st.db.write(lambda c: events.append(c, wid, "artifact_refused", payload))


@app.get("/work-items/{wid}/artifact")
async def get_work_item_artifact(wid: str, request: Request):
    """The pending gate's document, for a reviewer with no filesystem access.

    Read off disk rather than out of the index: the index ingests committed
    files on its own schedule, and a reviewer who is looking at the gate right
    now must see what the agent just wrote.
    """
    st = request.app.state
    row = _work_item_row(st, wid)  # 404s on an unknown work item
    rel = _gate_artifact(st, row, _pending_gate(st, wid))
    if rel is None:
        raise HTTPException(404, "this work item's gate has no artifact")
    worktree = st.run_dirs.worktrees / wid
    try:
        root = worktree.resolve(strict=True)
        target = (worktree / rel).resolve(strict=True)
    except OSError:
        await _refuse_artifact(st, wid, rel, "absent")
        raise HTTPException(404, "this work item's gate has no artifact") from None
    if not target.is_relative_to(root):
        # A symlink out of the worktree is the one way a derived, unstored path
        # can still point somewhere it should not. Same answer as a missing
        # file: a reviewer's browser learns nothing about the server's disk.
        logger.warning("artifact for %s resolves outside its worktree: %s", wid, target)
        await _refuse_artifact(st, wid, rel, "escaped_containment")
        raise HTTPException(404, "this work item's gate has no artifact")
    try:
        # The resolve+is_relative_to check above is what produces the warning
        # and names the work item; it is a fact about the instant it ran, not
        # a guarantee. This walk is the actual containment: the agent that
        # owns this worktree can swap any component -- not just the leaf --
        # for a symlink between that check and this open.
        fd = _open_no_symlinks(root, rel)
    except OSError:
        await _refuse_artifact(st, wid, rel, "unreadable")
        raise HTTPException(404, "this work item's gate has no artifact") from None
    try:
        fh = os.fdopen(fd, "rb")
    except OSError:
        # fdopen failed before taking ownership of fd (e.g. the walk landed on
        # a directory) -- close it ourselves, or it leaks.
        os.close(fd)
        await _refuse_artifact(st, wid, rel, "unreadable")
        raise HTTPException(404, "this work item's gate has no artifact") from None
    try:
        with fh:
            # Capped at the read, not just at the response: an agent that writes
            # a multi-gigabyte file by mistake must not be able to make the
            # server read it into memory to decide it is too big (spec §4). One
            # byte over the cap is how `truncated` is known without a stat race.
            data = fh.read(DIFF_MAX_BYTES + 1)
    except OSError:
        await _refuse_artifact(st, wid, rel, "unreadable")
        raise HTTPException(404, "this work item's gate has no artifact") from None
    truncated = len(data) > DIFF_MAX_BYTES
    # Slicing bytes can land mid-codepoint; `errors="replace"` is what makes
    # that a single replacement character instead of a 500.
    text = data[:DIFF_MAX_BYTES].decode(errors="replace")
    fm, body = ingest_mod.split_front_matter(text)
    return {
        "work_item_id": wid,
        "path": rel,
        "title": ingest_mod.derive_title(rel, fm, body),
        # Front matter stripped: it is the contract's plumbing, not the
        # document, and a reviewer reading a spec should not have to skip it.
        "content": body,
        "truncated": truncated,
        "artifact_max_bytes": DIFF_MAX_BYTES,
    }


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
                launch=_launch(st, row["repo"]),
            ),
        ),
    )
    return {k: v for k, v in dict(_work_item_row(st, wid)).items()}


#: `human_review_approval` sits at the end of the chain with nothing to loop back
#: to, so rejecting it stops the item (design 01 §7.2). Every other gate re-runs
#: its producer node with the note injected — "reject and re-plan".
TERMINAL_REJECT_GATES = {"human_review_approval"}


@app.post("/work-items/{wid}/gates/{gate}/reject")
async def reject_gate(wid: str, gate: str, body: GateReject, request: Request):
    """Reject a gate and put the chain back to work (02 §7.2, backward motion).

    Without the re-run below a rejection is a dead end: the gate stops being
    pending, the node has no fix loop to retry and the item is not running, so
    approve/retry/pause/resume all 409 and the work item is stranded.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    if gate not in GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")

    chain = json.loads(row["chain_definition"])
    node_id = chain["nodes"][_gate_node_index(chain, gate)]["id"]
    key = f"{gate}_reject_loop"
    replan = gate not in TERMINAL_REJECT_GATES
    if replan:
        if st.invalid_policy:
            # Same posture as intake (§9): a re-run we cannot bound is not started.
            raise HTTPException(
                503, f"policy config invalid, refusing work: {'; '.join(st.invalid_policy)}"
            )
        # Same counter machinery as a fix loop: rejections are bounded, and the
        # cap is snapshotted on first fire rather than re-resolved per attempt.
        cap = policy_mod.resolve_cap(st.policy, key)
        count, started_at, cap = await st.db.write(
            lambda c, cap=cap: store.bump_counter(c, wid, key, cap)
        )
        replan = (
            policy_mod.check(count=count, started_at=started_at, cap=cap, now=executor._now())
            == "ok"
        )

    await st.db.write(lambda c: store.reject_gate(c, wid, gate, body.note, reopen=replan))
    if not replan:
        if gate not in TERMINAL_REJECT_GATES:
            await st.db.write(
                lambda c: store.mark_needs_human(
                    c,
                    wid,
                    node_id,
                    f"{key} exhausted after {count - 1} rejection(s)",
                    {"cycles": count - 1, "attempts": cap.attempts},
                )
            )
        return {k: v for k, v in dict(_work_item_row(st, wid)).items()}

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
                start_index=_gate_node_index(chain, gate),
                policy=st.policy,
                steer=body.note,
                launch=_launch(st, row["repo"]),
            ),
        ),
    )
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
    if row["status"] != "paused" and not (
        row["status"] == "needs_human" and _needs_context_stop(st, wid)
    ):
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
    if row["status"] != "paused" and not (
        row["status"] == "needs_human" and _needs_context_stop(st, wid)
    ):
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
                launch=_launch(st, row["repo"]),
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
                launch=_launch(st, row["repo"]),
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
    #
    # Both credentials, for the same reason the HTTP middleware takes both: a
    # browser has a session cookie, and a non-browser client (`kraft watch`, an
    # agent) has the bearer token from run/. Accepting only the cookie made the
    # live stream the one endpoint a CLI could not reach.
    if _requires_auth(websocket.app):
        bearer = websocket.headers.get("authorization", "")
        expected = getattr(websocket.app.state, "mcp_token", None)
        authorised = bool(
            expected and bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], expected)
        )
        if not authorised:
            token = websocket.cookies.get(auth_mod.COOKIE)
            authorised = bool(
                token
                and await websocket.app.state.db.write(
                    lambda c, token=token: auth_mod.touch_session(c, token)
                )
            )
        if not authorised:
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
    st.registry = load_registry(st.templates_dir / "registry.yaml", skills_dir=st.skills_dir)
    st.templates = load_templates(st.templates_dir, st.registry)


class RepoBody(BaseModel):
    path: str
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    forge: str | None = None
    project: str | None = None
    enabled: bool = True
    default_model: str | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None


class ProbeBody(BaseModel):
    path: str


def _repos_path(st) -> Path:
    return st.templates_dir / "repos.yaml"


def _validate_repos(st, repos: list[dict]) -> None:
    """Write `repos` to a scratch file and run the real loader over it.

    Mirrors `put_registry`: the write side must reject exactly what the read
    side would later choke on, or a bad `POST`/`PATCH` persists and every
    subsequent `GET /repos` 500s until an operator hand-edits the file.
    """
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "repos.yaml"
        candidate.write_text(yaml.safe_dump({"repos": repos}))
        try:
            config_mod.load_repos(candidate, steering_dir=st.templates_dir / "steering")
        except config_mod.ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc


@app.get("/repos")
async def list_repos(request: Request):
    st = request.app.state
    # No steering validation on the read path (config.load_repos): a steering
    # file deleted out from under an entry must not 422 the screen that would
    # let an operator clear it. See `config.load_repos`'s docstring.
    return {"repos": config_mod.load_repos(_repos_path(st), validate_steering=False)}


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
    repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
    if any(r["path"] == probed["path"] for r in repos):
        raise HTTPException(409, f"{probed['path']} is already connected")
    entry = {
        "path": probed["path"],
        "name": body.name or probed["name"],
        "default_chain_template": body.default_chain_template or "default",
        "test_command": body.test_command or probed["test_command"],
        "forge": body.forge or probed["forge"],
        "project": body.project or probed["project"],
        "enabled": body.enabled,
        "default_model": body.default_model,
        "deny_tools": body.deny_tools or [],
        "steering": body.steering or [],
    }
    repos.append(entry)
    _validate_repos(st, repos)
    config_mod.save_repos(_repos_path(st), repos)
    # Index it now: a repo connected mid-session would otherwise stay invisible
    # to search (and to the intake picker) until the next restart. The scan is
    # `git ls-files .engineering/` — cheap enough to await. A scan failure must
    # not fail a connect that is already saved.
    try:
        await st.indexer.rescan_repo(entry["path"])
    except Exception:  # noqa: BLE001 -- scan_repo touches git and the filesystem
        logger.exception("index scan failed for newly connected repo %s", entry["path"])
    return entry


class RepoPatch(BaseModel):
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    forge: str | None = None
    project: str | None = None
    enabled: bool | None = None
    default_model: str | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None


def _connected(repos: list[dict], path: str) -> dict | None:
    """Find a connected repo by path.

    `POST /repos` stores git's `--show-toplevel`, which resolves symlinks (on
    macOS /var -> /private/var), so the path a client added with is not always
    the path stored. Match either, or a caller cannot patch or delete the repo
    it just connected.
    """
    entry = next((r for r in repos if r["path"] == path), None)
    if entry is not None:
        return entry
    resolved = str(Path(path).expanduser().resolve())
    return next((r for r in repos if r["path"] == resolved), None)


def _launch(st, repo: str) -> executor.LaunchContext:
    """The repo config for one agent dispatch, degrading like `invalid_policy`
    rather than raising: a malformed `repos.yaml` must not crash `lifespan` on
    reattach (locking an operator out of the Settings UI that would let them
    fix it) or 500 the approve/reject/resume/retry routes — the agent launches
    without repo-level model/steering, which is today's behaviour anyway.

    Steering is deliberately *not* validated here (`validate_steering=False`):
    a name whose file has since been deleted must still let the repo entry
    load normally, model/deny_tools intact, rather than losing them along with
    everything else. The dispatch that actually reads that steering file is
    what surfaces the problem — `steering.read` raises `SteeringError` naming
    the file, and it reaches `_guard` from there — needs_human for that one
    launch, not a crash."""
    steering_dir = st.templates_dir / "steering"
    try:
        repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
    except config_mod.ConfigError as exc:
        logger.warning("repo config invalid, launching without it: %s", exc)
        return executor.LaunchContext(
            repo_entry=None, steering_dir=steering_dir, skills_dir=st.skills_dir
        )
    return executor.LaunchContext(
        repo_entry=_connected(repos, repo),
        steering_dir=steering_dir,
        skills_dir=st.skills_dir,
    )


@app.patch("/repos")
async def update_repo(body: RepoPatch, request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
    entry = _connected(repos, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    entry.update({k: v for k, v in body.model_dump().items() if v is not None})
    _validate_repos(st, repos)
    config_mod.save_repos(_repos_path(st), repos)
    return entry


@app.delete("/repos", status_code=204)
async def remove_repo(request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
    entry = _connected(repos, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    kept = [r for r in repos if r["path"] != entry["path"]]
    config_mod.save_repos(_repos_path(st), kept)
    # Mirror of the connect-time scan. A repo with work items stays in
    # `Indexer.repos()` and is simply re-ingested by the next rescan.
    st.indexer.purge_repo(entry["path"])


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
    repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
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
            registry = load_registry(
                candidate, steering_dir=st.templates_dir / "steering", skills_dir=st.skills_dir
            )
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
    findings: dict | None = None
    budget: dict | None = None


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
    if body.findings is not None:
        data["findings"] = body.findings
    if body.budget is not None:
        data["budget"] = body.budget
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


class SteeringBody(BaseModel):
    body: str


def _steering_dir(st) -> Path:
    return st.templates_dir / "steering"


def _check_steering_change(st, name: str, body: str | None) -> None:
    """Would the configs still load with this change applied? Raise if not.

    Names resolve at config-load time, not at write time, so the edited file is
    only half the question: `registry.yaml` and `repos.yaml` name it, and a body
    that pushes an assembled block past the injection budget -- or a delete that
    orphans a name -- breaks a launch nowhere near this screen.

    Checked against a scratch *copy* of the steering directory with the change
    applied (`body=None` means the delete), never by writing the real file and
    undoing it after. Agent dispatch reads these files straight off disk, so the
    real directory must never be briefly wrong -- and an undo only undoes the
    failures it anticipated. Note the inversion versus `_validate_repos`: there
    the config is the candidate and the steering dir is real; here it is the
    other way round, which is why both loaders take a `steering_dir`.
    """
    real = _steering_dir(st)
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        if real.is_dir():
            for src in real.glob("*.md"):
                try:
                    shutil.copy2(src, scratch / src.name)
                except OSError:
                    # This directory is hand-editable, so it can hold a broken
                    # symlink, an unreadable file, or a directory named *.md.
                    # Skip it rather than failing the save: if a config
                    # references it, the loaders below reject the save with a
                    # message naming it; if nothing does, it is none of this
                    # save's business, and the write path it replaced never
                    # touched unreferenced entries either.
                    continue
        target = scratch / f"{name}.md"
        if body is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(body)
        try:
            load_registry(
                st.templates_dir / "registry.yaml", steering_dir=scratch, skills_dir=st.skills_dir
            )
            config_mod.load_repos(_repos_path(st), steering_dir=scratch)
        except (RegistryError, config_mod.ConfigError) as exc:
            raise HTTPException(422, str(exc)) from exc


@app.get("/steering")
async def list_steering(request: Request):
    """Names and sizes, not bodies: the list is a picker, and the assembled
    budget is the number an operator is actually rationing."""
    steering_dir = _steering_dir(request.app.state)
    if not steering_dir.is_dir():
        return {"files": [], "max_bytes": steering_mod.MAX_BYTES}
    files = []
    for path in sorted(steering_dir.glob("*.md")):
        try:
            files.append({"name": path.stem, "bytes": len(path.read_text().encode())})
        except OSError, ValueError:
            # Unreadable or not UTF-8: it exists and it is broken, which is
            # more useful on the screen than a file that silently is not there.
            files.append({"name": path.stem, "bytes": None})
    return {"files": files, "max_bytes": steering_mod.MAX_BYTES}


@app.get("/steering/{name}")
async def get_steering(name: str, request: Request):
    st = request.app.state
    try:
        path = steering_mod.path_for(_steering_dir(st), name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return {"name": name, "body": path.read_text()}
    except FileNotFoundError:
        raise HTTPException(404, f"unknown steering file {name!r}") from None
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"{name}: cannot read: {exc}") from exc


@app.put("/steering/{name}")
async def put_steering(name: str, body: SteeringBody, request: Request):
    st = request.app.state
    steering_dir = _steering_dir(st)
    try:
        path = steering_mod.path_for(steering_dir, name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    _check_steering_change(st, name, body.body)
    config_mod.write_text(path, body.body)
    # Nothing to reload: no `app.state` holds steering bodies. They are read
    # from disk at dispatch, and `resolve_invocation` re-checks the assembled
    # budget at every launch, so the next agent picks this up on its own.
    return {"name": name, "body": body.body}


@app.delete("/steering/{name}")
async def delete_steering(name: str, request: Request):
    st = request.app.state
    try:
        path = steering_mod.path_for(_steering_dir(st), name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, f"unknown steering file {name!r}")
    _check_steering_change(st, name, None)
    path.unlink()
    return {"deleted": name}


class IntakeBody(BaseModel):
    """`intake.yaml`, typed. Unlike `policy.yaml` this file is a flat fixed
    shape, so the bounds live here rather than in a loader that has to accept a
    hand-edited file it did not write."""

    enabled: bool
    # The poller floors this at 30s anyway; rejecting it is better than
    # accepting a number the running instance will not honour.
    interval_s: int = Field(ge=30)
    max_concurrent: int = Field(ge=1)
    # P0 is the *highest* priority, so the ceiling is "P<n> and below".
    priority_ceiling: int = Field(ge=0, le=4)
    repos: list[str] = []


@app.get("/intake")
async def get_intake(request: Request):
    """From disk, like `GET /policy`. `intake.yaml` was hand-edited until this
    screen existed, so returning the cached state would hide an edit made since
    boot and let the next save overwrite it silently."""
    st = request.app.state
    try:
        return config_mod.load_intake(st.templates_dir / "intake.yaml")
    except config_mod.ConfigError:
        # Unreadable: show what the instance is actually running on, which
        # lifespan already degraded to the defaults. Saving replaces the file.
        return st.intake


@app.put("/intake")
async def put_intake(body: IntakeBody, request: Request):
    """Applies without a restart: the poller task is replaced, not just the
    config it reads. `interval_s` is read once at task start, so a live poller
    would otherwise keep the old interval until the next reboot."""
    app_ = request.app
    st = app_.state
    data = body.model_dump()
    config_mod.write_yaml(st.templates_dir / "intake.yaml", data)
    st.intake = data
    async with st.intake_lock:
        task = st.intake_task
        # Clear it before the await: a second saver that gets in here while we
        # are waiting must not find, and cancel, a task we are already retiring.
        st.intake_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if data["enabled"]:
            st.intake_task = asyncio.ensure_future(intake_mod.poller(app_))
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


class NotifyBody(BaseModel):
    enabled: bool | None = None
    #: Omitted leaves the stored secret alone — editing the event list must not
    #: require re-entering a token the UI is never allowed to show back. `""`
    #: is the explicit clear.
    url: str | None = None
    base_url: str | None = None
    events: list[str] | None = None


def _notify_view(notify_cfg: dict) -> dict:
    """What `/notify` is allowed to say. The URL is not in it: a settings screen
    that renders the value back into the DOM puts the token in the browser, in
    screenshots, and in any future session recording."""
    return {
        "enabled": bool(notify_cfg["enabled"]),
        "url_set": bool(notify_cfg["url"]),
        "base_url": notify_cfg["base_url"],
        "events": notify_cfg["events"],
    }


def _checked_url(value: str, field: str) -> str:
    scheme = urlsplit(value).scheme
    if scheme not in ("http", "https"):
        raise HTTPException(422, f"{field} must be an http or https URL")
    return value


@app.get("/notify")
async def get_notify(request: Request):
    # Reads straight off disk. `st.notifier` holds its own copy of this file in
    # memory and only refreshes it on `reload()` (called by `put_notify` below,
    # and at startup) -- so a hand edit to notify.yaml shows up here right away
    # but does not change what the running notifier actually does until the
    # next PUT or a restart. That is a knowing divergence from this module's
    # docstring claim that a hand edit and a UI edit are the same operation;
    # closing it means a file watcher, which this task does not build.
    st = request.app.state
    return _notify_view(config_mod.load_notify(st.templates_dir / "notify.yaml"))


@app.put("/notify")
async def put_notify(body: NotifyBody, request: Request):
    st = request.app.state
    path = st.templates_dir / "notify.yaml"
    cfg = config_mod.load_notify(path)
    if body.enabled is not None:
        cfg["enabled"] = body.enabled
    if body.url is not None:
        cfg["url"] = _checked_url(body.url, "url") if body.url else None
        # Clearing the URL is how an operator revokes a leaked token. It must
        # not just fail the invariant below -- it must disable, same as
        # "Clear URL"'s own hint claims. This is the only path back to a valid
        # state once the URL is gone, so force it rather than reject it.
        if not cfg["url"]:
            cfg["enabled"] = False
    if body.base_url is not None:
        cfg["base_url"] = _checked_url(body.base_url, "base_url") if body.base_url else None
    if body.events is not None:
        cfg["events"] = body.events
    # Enabled with nowhere to send is a setting that looks armed and is not.
    # Kept as a belt-and-braces check: the branch above already makes it
    # unreachable for the "clear the URL" path, but not for "enable with no
    # URL ever set" (body.url is None and cfg["url"] was already empty).
    if cfg["enabled"] and not cfg["url"]:
        raise HTTPException(422, "set a webhook URL before enabling notifications")
    config_mod.save_notify(path, cfg)
    st.notifier.reload()
    return _notify_view(cfg)


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


@app.exception_handler(config_mod.ConfigError)
async def _bad_config_file(request: Request, exc: config_mod.ConfigError) -> JSONResponse:
    """A legible 422 for any `load_repos`/`load_registry`/etc. caller that does
    not catch `ConfigError` itself. `templates/` is a plain directory an
    operator can hand-edit, and the message already names the file and what is
    wrong with it — a global backstop is the fix, not a guard at each call
    site, because the next caller of `load_repos` would just inherit the same
    trap a per-route `try/except` does nothing to close.

    A route that already catches `ConfigError` and raises its own 4xx
    (`_validate_repos`, `probe_repo`, `_launch`) never reaches this handler —
    only an *uncaught* `ConfigError` does."""
    return JSONResponse({"detail": str(exc)}, status_code=422)


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
