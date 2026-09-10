from __future__ import annotations

import asyncio
import hmac
import ipaddress
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
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from kraft import analytics as analytics_mod
from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import events, executor, findings, rate_limit_retry, reattach, review, store
from kraft import intake as intake_mod
from kraft import logs as logs_mod
from kraft import notify as notify_mod
from kraft import policy as policy_mod
from kraft import steering as steering_mod
from kraft.adapters import agent as agent_mod
from kraft.adapters import beads as beads_mod
from kraft.adapters import forge as forge_mod
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
    Registry,
    RegistryError,
    load_registry,
    load_templates,
    validate_nodes,
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


def _bead_warning(st, wid: str) -> str | None:
    """Why intake filed no bead, or None — read back from the event
    `executor.intake` wrote in the same transaction as the row (Kraft-7gy)."""
    row = st.db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE work_item_id = ? AND type = 'bead_not_filed'",
            (wid,),
        ).fetchone()
    )
    return json.loads(row["payload"])["reason"] if row else None


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
    # Always on, unlike auto-intake: waiting out a rate limit is not optional
    # behaviour an operator enables, it is what this feature promises.
    app.state.rate_limit_task = asyncio.ensure_future(rate_limit_retry.poller(app))
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
        app.state.rate_limit_task.cancel()
        await asyncio.gather(app.state.rate_limit_task, return_exceptions=True)
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

#: Every JSON endpoint lives under here, so it can never share a path with an
#: SPA client-side route (e.g. GET /work-items/<id> the page vs. the same path
#: as a JSON handler) — that collision used to make a browser render raw JSON
#: on some deep links instead of the app shell.
api_router = APIRouter(prefix="/api")


def _is_api_path(path: str) -> bool:
    """True for `/api` itself or anything under it — one predicate for the
    boundary every middleware and 404 path below has to agree on. `path ==
    "/api"` matters even though nothing is mounted at the router root today:
    `.startswith("/api/")` alone lets the bare prefix slip through as if it
    were an ordinary SPA path."""
    return path == "/api" or path.startswith("/api/")


@app.middleware("http")
async def _spa_navigation(request: Request, call_next):
    # A browser deep-link / refresh on a client-side route (e.g. /work-items/<id>)
    # would otherwise reach the same catch-all any GET falls through to anyway
    # — this is a fast path, not the only path. Any top-level navigation
    # carries Sec-Fetch-Dest: document; hand those the SPA shell directly and
    # skip routing. Static assets are dest=script/style, XHR is dest=empty, so
    # only real navigations are caught. Excluded for /api/: that prefix is
    # unambiguously JSON, so a forged header there must not stand in for a
    # real 401/404/200.
    dist = getattr(request.app.state, "frontend_dist", None)
    if (
        dist is not None
        and request.method == "GET"
        and request.headers.get("sec-fetch-dest") == "document"
        and not _is_api_path(request.url.path)
    ):
        # The browser caches by URL, so without no-store a refresh could answer
        # from a stale cached shell. `vary` says the same thing to caches that
        # honour it.
        return FileResponse(
            dist / "index.html",
            headers={"cache-control": "no-store", "vary": "sec-fetch-dest"},
        )
    return await call_next(request)


#: Paths that must work before a session exists.
#: `/api/health` is deliberately open — a monitor should not need a session,
#: and the login screen reads the bind address from it.
_PUBLIC_PATHS = {"/api/login", "/api/health"}


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


#: Hostnames that can only mean this machine. `urlsplit().hostname` strips the
#: brackets off an IPv6 literal, so "::1" covers "[::1]" as well.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _client_is_local(request: Request | WebSocket) -> bool:
    """True when the peer address is a loopback IP.

    Fails closed: an absent peer, or one that is not an IP at all (starlette's
    TestClient reports the literal "testclient"), is not local.

    uvicorn resolves the peer through `proxy_headers`, on by default with
    `forwarded_allow_ips="127.0.0.1"` — so a reverse proxy on this box surfaces
    the real client here, and a forged `X-Forwarded-For` from a remote peer is
    ignored because that peer is not trusted. Both directions only make this
    stricter than reading the socket would be.
    """
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


def _requires_auth(app: FastAPI, request: Request | WebSocket) -> bool:
    """Auth is off on localhost and on for anything else (design 5e).

    Three details this depends on:

    * it is keyed to the address the process actually bound at startup, not the
      one saved in `access.yaml` — a bind change "takes effect on restart", so
      saving one must not lock the operator out of a server still on loopback;
    * the short-circuit needs the *peer* to be loopback too. `bound_host` is a
      claim, and a launch that widened the bind without going through
      `cli._bind` (`uvicorn kraft.api:app --host 0.0.0.0`) does not update it —
      so a remote caller reaching a server that believes it is on loopback still
      has to log in;
    * with no password set there is nothing to authenticate *against*, so the
      gate stays open rather than bricking the API into a state where even
      setting the first password is refused. `__main__` will not start a
      non-loopback bind in that state, and the Access screen refuses to save one.
      `_perimeter` is what stops that open gate from being reachable remotely.
    """
    local_bind = getattr(app.state, "bound_host", "127.0.0.1") in config_mod.LOOPBACK
    if local_bind and _client_is_local(request):
        return False
    return bool((getattr(app.state, "access", None) or {}).get("password_hash"))


@app.middleware("http")
async def _authenticate(request: Request, call_next):
    app = request.app
    if (
        not _requires_auth(app, request)
        or request.url.path in _PUBLIC_PATHS
        or _is_static_asset(app, request.url.path)
    ):
        return await call_next(request)
    # A browser navigating to a client-side route must get the SPA shell, not a
    # 401 — but `sec-fetch-dest` is a request header any client can send, and
    # every mutating route lives under /api/, so excluding that prefix here
    # costs nothing: a forged header on /api/ still has to clear the bearer or
    # cookie check below, same as an honest request would.
    dist = getattr(app.state, "frontend_dist", None)
    if (
        request.method == "GET"
        and request.headers.get("sec-fetch-dest") == "document"
        and dist is not None
        and not _is_api_path(request.url.path)
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


@app.middleware("http")
async def _perimeter(request: Request, call_next):
    """Who may talk to this server at all, before any question of a session.

    Declared *after* `_authenticate` on purpose. Starlette inserts each added
    middleware at the front of the stack, so the last one declared is the
    outermost and runs first — this has to be entered before the auth gate and
    before the SPA-shell branch, or a refused request gets answered by them.
    """
    st = request.app.state
    peer = request.client.host if request.client else "an unknown peer"
    has_password = bool((getattr(st, "access", None) or {}).get("password_hash"))

    # 1. The bind in access.yaml is a claim; the peer address is the fact. A
    #    launch that widened the bind without going through `cli._bind` would
    #    otherwise serve the whole API to the LAN with the auth gate wide open,
    #    because `_requires_auth` has no password to demand.
    if not _client_is_local(request) and not has_password:
        return JSONResponse(
            {"detail": f"this server is configured for a loopback bind; refusing {peer}"},
            status_code=403,
        )

    # 2. DNS rebinding: a page on evil.com whose name flips to 127.0.0.1 is
    #    same-origin with a local Kraft and can read every response, ids
    #    included, then drive any route. A server that bound loopback may only
    #    be addressed by a loopback name.
    #
    #    Keyed on `sec-fetch-site` because only a browser can be rebound, and
    #    every other client — the CLI, MCP, httpx, curl — would otherwise need a
    #    Host allowlist for no gain. Browsers older than the Fetch Metadata
    #    rollout (pre-2020) do not send it and are not covered.
    #
    #    Restricted to a loopback bind because a `0.0.0.0` server is reached
    #    under whatever address the client used, which is never `0.0.0.0`;
    #    comparing the Host to `bound_host` there would 403 every LAN browser.
    #    A non-loopback bind gets no rebinding protection from this rule.
    if (
        request.headers.get("sec-fetch-site")
        and getattr(st, "bound_host", "127.0.0.1") in config_mod.LOOPBACK
        and urlsplit(f"//{request.headers.get('host', '')}").hostname not in _LOCAL_HOSTS
    ):
        return JSONResponse(
            {"detail": "unexpected Host for a server bound to loopback"}, status_code=403
        )

    # 3. Cross-site write. `_origin_ok` plus one clause, so that a LAN instance
    #    serving its own SPA (Origin and Host both 192.168.1.5:8765) is not
    #    locked out of its own board. Reads are left alone: this is about the
    #    mutating routes that take no JSON body and so need no preflight.
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD")
        and origin
        and not _origin_ok(origin)
        and urlsplit(origin).netloc != request.headers.get("host")
    ):
        return JSONResponse({"detail": "cross-site request refused"}, status_code=403)

    return await call_next(request)


class Attachment(BaseModel):
    kind: Literal["spec", "plan"]
    #: repo-relative; validated and normalized server-side before it is stored
    path: str


class NewWorkItem(BaseModel):
    title: str
    #: the brief — prose, and what the spec node writes a design from. A title is
    #: only a label.
    description: str = ""
    repo: str
    #: None means no explicit template was chosen (Kraft-cd47) -- resolved to
    #: the `default` template below, at lookup time, and stored as None so it
    #: stays distinguishable from an item that named `chain_template:
    #: "default"` outright.
    chain_template: str | None = None
    #: cross-repo (design 1g "Advanced · cross-repo"): submodule paths from the
    #: repo's .gitmodules, and what happens to the root pointer when they land
    submodules: list[str] = []
    root_merge_policy: str = "bump"
    #: spec/plan documents that already exist — they trim the gates they satisfy
    attachments: list[Attachment] = []
    #: the caller's working directory, sent only when there are attachment paths
    #: to resolve. A local CLI or MCP caller is often standing in a worktree of
    #: `repo` — for a Kraft worker, always — and that is where the document it
    #: wants to hand over actually is (Kraft-85wk). The browser sends nothing.
    cwd: str | None = None
    #: False creates the item without running it (design §6 rule 1). An agent
    #: cannot spend tokens unattended; a human starts it from the board.
    autostart: bool = True


def _git_common_dir(path: Path) -> Path | None:
    """The `.git` that every working tree of one repository shares, or None.

    Identical for a main checkout and every worktree linked to it; different for
    an unrelated repo, and different for a submodule of this one (whose common
    dir is `<super>/.git/modules/<path>`). `config_mod.git_read` never raises,
    so a directory that is not a repo, or is not readable, is None rather than a
    500.
    """
    common = config_mod.git_read(
        path, "rev-parse", "--path-format=absolute", "--git-common-dir", expected_failure=True
    )
    return Path(common).resolve() if common else None


def _attachment_roots(repo: str, cwd: str | None) -> list[Path]:
    """The roots an attachment path may be resolved against, in order.

    The registered repo always, and first. The caller's own working tree second,
    and only when it proves it is a working tree of that same repository —
    same `.git`, or no second root (Kraft-85wk). With no `cwd` the reachable set
    is exactly what it has always been.

    This check is also what enforces Kraft-vwv's decision: an intake attachment
    belongs to the item's root worktree only. A `cwd` inside a submodule
    contributes no root, because a submodule's common dir is not the
    superproject's — so a submodule-local spec cannot be attached by accident,
    and `ensure_worktree` copies into the root worktree only. One change has one
    spec; N copies in the MR would be N places for it to drift.
    """
    root = Path(repo).resolve()
    if not cwd:
        return [root]
    common = _git_common_dir(Path(cwd))
    if common is None or common != _git_common_dir(root):
        return [root]
    toplevel = config_mod.git_read(Path(cwd), "rev-parse", "--show-toplevel", expected_failure=True)
    if toplevel is None:
        return [root]
    top = Path(toplevel).resolve()
    return [root] if top == root else [root, top]


def _validated_attachments(
    repo: str, attachments: list[Attachment], cwd: str | None = None
) -> list[dict]:
    """Trust boundary: `path` comes from a browser or a local agent and is used
    to read a file and to write into a worktree. Resolve under a candidate root
    and reject any escape.

    The widening `cwd` buys is small and proven: other working trees of the same
    repository, for a caller the perimeter middleware has already established is
    local and same-site. A path is taken at the first root it both stays inside
    and exists under.
    """
    kinds = [a.kind for a in attachments]
    if len(set(kinds)) != len(kinds):
        raise HTTPException(422, "at most one attachment per kind")
    roots = _attachment_roots(repo, cwd)
    out = []
    for a in attachments:
        inside = False
        for root in roots:
            target = (root / a.path).resolve()
            if not target.is_relative_to(root):
                continue
            inside = True
            # Working tree, not HEAD: a document written minutes ago is legal
            # input, and ensure_worktree copies it into the worktree.
            if not target.is_file():
                continue
            entry = {"kind": a.kind, "path": str(target.relative_to(root))}
            if root != roots[0]:
                # The copy reads `repo / path` by default and this file is not
                # in `repo` at all, so it would silently copy nothing and leave
                # a trimmed gate with no document. Absolute, so the copy needs
                # to know nothing about roots.
                entry["source"] = str(target)
            out.append(entry)
            break
        else:
            raise HTTPException(
                422,
                f"attachment not found: {a.path}"
                if inside
                else f"attachment path escapes the repo: {a.path}",
            )
    return out


@api_router.post("/work-items", status_code=201)
async def create_work_item(body: NewWorkItem, request: Request):
    st = request.app.state
    if st.invalid_policy:
        # Spec §9: a malformed policy.yaml makes the process refuse work, same
        # posture as an invalid registry — do not accept a run we cannot bound.
        detail = "; ".join(st.invalid_policy)
        raise HTTPException(503, f"policy config invalid, refusing work: {detail}")
    template = st.templates.valid.get(
        body.chain_template if body.chain_template is not None else "default"
    )
    if template is None:
        raise HTTPException(422, "unknown or invalid template")
    if body.root_merge_policy not in store.ROOT_MERGE_POLICIES:
        raise HTTPException(422, f"unknown root_merge_policy {body.root_merge_policy!r}")
    # Before `executor.intake`, which no longer 502s on a bd failure (Kraft-7gy)
    # and would file the item with no bead and a warning nobody reads. An
    # explicit check rather than `Field(max_length=...)`: pydantic's 422 body is
    # a list of error dicts, and `kraft item create` prints `detail` straight
    # through -- one sentence is the contract every other CLI error keeps.
    if len(body.title) > beads_mod.MAX_TITLE:
        raise HTTPException(
            422,
            f"title is {len(body.title)} characters; the tracker's limit is {beads_mod.MAX_TITLE}",
        )
    if not Path(body.repo).is_dir():
        raise HTTPException(422, f"repo path does not exist: {body.repo}")
    attachments = _validated_attachments(body.repo, body.attachments, body.cwd)
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
            template=template,
            # The raw request value, not the resolved template's id (Kraft-cd47):
            # None here means no explicit template was chosen, and must stay
            # None in the row -- `intake`'s own default would otherwise store
            # `template.id`, indistinguishable from an item that named
            # `chain_template: "default"` outright.
            chain_template=body.chain_template,
            bd_cwd=_bd_cwd(),
            submodules=body.submodules,
            root_merge_policy=body.root_merge_policy,
            attachments=attachments,
            status="active" if body.autostart else "paused",
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        # No longer reachable for a bd failure — `executor.intake` degrades
        # instead (Kraft-7gy). A 502 here is now a template or a DB problem.
        raise HTTPException(502, f"intake failed: {exc}") from exc

    # Present only when there is one: a null field on every successful create is
    # noise in `kraft item create`'s kv block and in the API.
    warning = _bead_warning(st, wid)
    extra = {"bead_warning": warning} if warning else {}

    if not body.autostart:
        # Created, not started. `/resume` begins it at node zero, because a NULL
        # current_node_id falls through that handler's `next(..., 0)` default.
        return {"id": wid, "status": "paused", **extra}

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
            **extra,
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
    "work_item_rate_limited",
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


def _strip_front_matter(text: str) -> str:
    """The body of an artifact file, after its mandatory YAML front matter
    (`agent._ARTIFACT`'s contract, shared by every artifact-carrying hook).
    The whole text back if there is no front-matter block, so a hand-edited
    or malformed file still gets a chance to parse as-is."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def _splice_chain_review(st, row) -> tuple[dict | None, str | None]:
    """The chain_finalized gate's approval decision (Kraft-hm0).

    Reads the `chain_review` artifact, parses its `{status,
    revised_chain_nodes, rationale}` envelope (JSON in the artifact body,
    after the shared front-matter block), and validates a revision through
    Kraft-unk's splice-tier `validate_nodes` before it is trusted anywhere
    near `chain_definition`.

    Returns `(spliced_chain, None)` when the gate may advance, or `(None,
    reason)` when it must not — approving a gate must never be the thing
    that lets a corrupt chain through, so every failure here stops the item
    at needs_human instead of silently keeping the old tail.
    """
    rel = agent_mod.artifact_path("chain_review", row["id"])
    path = st.run_dirs.worktrees / row["id"] / rel
    if not path.is_file():
        return None, "chain_review: no artifact found; the worker did not write one"
    try:
        envelope = json.loads(_strip_front_matter(path.read_text()))
    except (OSError, ValueError) as exc:
        return None, f"chain_review: could not parse artifact: {exc}"
    if not isinstance(envelope, dict) or envelope.get("status") not in (
        "ready_for_approval",
        "error",
    ):
        return None, "chain_review: artifact is missing a valid 'status'"
    if envelope["status"] == "error":
        return None, envelope.get("rationale") or "chain_review: reported status 'error'"

    nodes = envelope.get("revised_chain_nodes")
    errs = validate_nodes(nodes, st.registry) if isinstance(nodes, list) else ["not a list"]
    if errs:
        return None, f"chain_review: revised_chain_nodes invalid: {errs[0]}"

    chain = json.loads(row["chain_definition"])
    tail_start = _gate_node_index(chain, "chain_finalized") + 1
    # No special-case for an unchanged tail (spec: splicing the same list back
    # in is a no-op in effect) -- one code path for both, not two that drift.
    chain["nodes"][tail_start:] = nodes
    return chain, None


class GateReject(BaseModel):
    note: str
    #: Where the chain re-enters. Defaults to the gate node's `reject_to`, and
    #: failing that to the gate node itself (Kraft-ko7j).
    node: str | None = None


class Retry(BaseModel):
    steer: str | None = None


class MrLabels(BaseModel):
    labels: list[str]


class Steer(BaseModel):
    text: str


class Resume(BaseModel):
    steer: str | None = None


class OpenDocument(BaseModel):
    editor: str | None = None


@api_router.get("/work-items")
async def list_work_items(request: Request):
    st = request.app.state
    # Read outside `_read`: that closure runs on the database thread and has no
    # request to ask.
    include_abandoned = request.query_params.get("include_abandoned") == "true"

    def _read(c):
        rows = c.execute(
            "SELECT * FROM work_items WHERE (? OR status != 'abandoned') ORDER BY created_at",
            (include_abandoned,),
        ).fetchall()
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
            "description": r["description"],
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
            "retry_at": r["retry_at"],
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


@api_router.get("/work-items/{wid}")
async def get_work_item(wid: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    sessions = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at", (wid,)
        ).fetchall()
    )
    pending = _pending_gate(st, wid)
    chain = json.loads(row["chain_definition"])
    return {
        **{k: row[k] for k in row.keys()},
        "chain_definition": chain,
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
        # Whether a stranded-at-this-node retry could ever carry a steer note
        # anywhere downstream (Kraft-bz9b): the detail screen uses this to
        # drop the steer box entirely rather than offer text `retry` would
        # 409 on. Fails open (True) when the node isn't in its own chain --
        # an unmapped edge case is not a reason to hide a control that may
        # still work.
        "steerable": (
            _steer_reachable(chain["nodes"], row["current_node_id"], st.registry)
            if any(n["id"] == row["current_node_id"] for n in chain["nodes"])
            else True
        ),
    }


class WorkItemPatch(BaseModel):
    #: Absent means untouched, in both fields. This route is still not a general
    #: table editor -- a body carrying anything else is ignored, not applied --
    #: but a screen that edits one field must not blank the other, so neither
    #: field has a default that means "clear it". `description: ""` clears the
    #: brief; `description` omitted leaves it alone.
    title: str | None = None
    description: str | None = None


@api_router.patch("/work-items/{wid}")
async def update_work_item(wid: str, body: WorkItemPatch, request: Request):
    st = request.app.state
    _work_item_row(st, wid)  # 404s on an unknown work item, before any 422
    if body.title is None and body.description is None:
        raise HTTPException(422, "nothing to patch: send a title, a description, or both")
    if body.title is not None and not body.title.strip():
        raise HTTPException(422, "title cannot be empty")

    def apply(c):
        # One write, so a two-field patch is one transaction and cannot land half.
        if body.title is not None:
            store.set_title(c, wid, body.title)
        if body.description is not None:
            store.set_description(c, wid, body.description)

    await st.db.write(apply)
    return {"id": wid, **body.model_dump(exclude_none=True)}


@api_router.get("/work-items/{wid}/events")
async def get_events(wid: str, request: Request, after_seq: int = 0):
    st = request.app.state
    _work_item_row(st, wid)
    return st.db.read(lambda c: events.read_after(c, after_seq, wid))


@api_router.get("/work-items/{wid}/documents")
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


@api_router.get("/work-items/{wid}/diff")
async def get_work_item_diff(wid: str, request: Request):
    """The changes an agent made, for a reviewer with no filesystem access.

    Two ranges, kept apart (Kraft-nceo). `landed` is `base_ref..HEAD` -- what
    earlier nodes committed, the chain's own spec and plan documents among it.
    The top level is `HEAD`..working tree, the change actually under review:
    one combined range spent the viewer's open-line budget on paperwork before
    the code was reached.
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
            "landed": {"commits": [], "files": [], "diff": "", "truncated": False},
            "diff_max_bytes": DIFF_MAX_BYTES,
            "worktree_path": str(st.run_dirs.worktrees / wid),
        }
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")

    change = review.read_change(worktree, "HEAD")
    landed = review.read_change(worktree, base, head="HEAD")
    if change is None or landed is None:
        # None means git itself failed (and git_read has already logged the
        # command and stderr). Returning an empty diff here would be
        # indistinguishable from "no changes" to the human approving the gate,
        # and the worktree path is a server filesystem detail a remote
        # reviewer's browser has no business seeing.
        raise HTTPException(500, "git could not read this work item's worktree")

    diff, truncated = _truncate_at_file_boundary(change.diff, DIFF_MAX_BYTES)
    # Each side against the whole cap, independently: the in-flight change is
    # what the reviewer is deciding about and must not be squeezed by the size
    # of the documents ahead of it.
    landed_diff, landed_truncated = _truncate_at_file_boundary(landed.diff, DIFF_MAX_BYTES)
    return {
        "work_item_id": wid,
        "base_ref": base,
        "files": change.files,
        "diff": diff,
        "untracked": change.untracked,
        "truncated": truncated,
        "landed": {
            "commits": landed.commits,
            "files": landed.files,
            "diff": landed_diff,
            "truncated": landed_truncated,
        },
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


@api_router.get("/work-items/{wid}/artifact")
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


@api_router.post("/work-items/{wid}/gates/{gate}/approve")
async def approve_gate(wid: str, gate: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    if gate not in GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")

    if gate == "chain_finalized":
        chain, reason = _splice_chain_review(st, row)
        if chain is None:
            await st.db.write(
                lambda c: store.mark_needs_human(c, wid, row["current_node_id"], reason)
            )
            return {k: v for k, v in dict(_work_item_row(st, wid)).items()}
        await st.db.write(lambda c: store.splice_chain(c, wid, json.dumps(chain)))
    else:
        chain = json.loads(row["chain_definition"])

    await st.db.write(lambda c: store.approve_gate(c, wid, gate))
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


def _reject_target(chain: dict, gate_index: int, requested: str | None) -> int:
    """The index a rejection re-enters the chain at (Kraft-ko7j).

    `requested`, else the gate node's `reject_to`, else the gate node itself.
    A `reject_to` that `materialize` dropped — an intake attachment satisfied
    that node's gate — falls back to the gate node rather than 500-ing; a bad
    `node` in the request body is the caller's error and is a 400.
    """
    nodes = chain["nodes"]
    name = requested or nodes[gate_index].get("reject_to")
    if not name:
        return gate_index
    index = next((i for i, n in enumerate(nodes) if n["id"] == name), None)
    if index is None or index > gate_index:
        if requested:
            raise HTTPException(
                400,
                f"cannot reject to {name!r}: not a node of this chain at or before "
                f"{nodes[gate_index]['id']!r}",
            )
        return gate_index
    return index


@api_router.post("/work-items/{wid}/gates/{gate}/reject")
async def reject_gate(wid: str, gate: str, body: GateReject, request: Request):
    """Reject a gate and put the chain back to work (02 §7.2, backward motion).

    Every gate takes this one path now. `human_review_approval` used to be
    terminal: the note landed in an event nothing read, no node was re-run, and
    the only exits left were approving the thing just rejected or abandoning
    the item (Kraft-ko7j). The single thing that may park an item at a rejected
    gate is the reject loop's own cap.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    if gate not in GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if _pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")

    chain = json.loads(row["chain_definition"])
    gate_index = _gate_node_index(chain, gate)
    node_id = chain["nodes"][gate_index]["id"]
    # Resolved before anything is written: a bad target must leave the gate
    # pending and the events table untouched.
    target = _reject_target(chain, gate_index, body.node)
    key = f"{gate}_reject_loop"

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
        policy_mod.check(count=count, started_at=started_at, cap=cap, now=executor._now()) == "ok"
    )

    target_id = chain["nodes"][target]["id"]
    await st.db.write(
        lambda c: store.reject_gate(c, wid, gate, body.note, reopen=replan, node=target_id)
    )
    if not replan:
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
                start_index=target,
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


async def _remove_worktree(repo: Path, worktree: Path, branch: str) -> bool:
    """Reclaim the worktree and its branch.

    Takes the branch rather than the work item id: the name is stored on the
    row now, and rebuilding it here would be a second derivation that can
    disagree with the one git actually created (Kraft-nhps).

    Best-effort: the row is already abandoned by the time this runs, and a git
    failure here must not leave the item in a state the board cannot show. The
    prune is between the two because a directory removed out from under git
    leaves an administrative entry that makes the branch delete fail.
    """
    ok = True
    for args in (
        ["git", "worktree", "remove", "--force", str(worktree)],
        ["git", "worktree", "prune"],
        ["git", "branch", "-D", branch],
    ):
        done = await asyncio.to_thread(
            subprocess.run, args, cwd=repo, capture_output=True, text=True
        )
        if done.returncode != 0:
            logger.warning("abandon %s: %s failed: %s", branch, args[1], done.stderr.strip())
            ok = False
    return ok


@api_router.post("/work-items/{wid}/abandon")
async def abandon_work_item(wid: str, request: Request):
    """Terminal state plus worktree and branch reclaim (Kraft-x85).

    Refuses while the item is active rather than killing its sessions itself:
    `pause` already owns stopping an attempt, and doing both here would leave
    two places that know how to terminate an agent.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] == "active":
        raise HTTPException(409, "work item is active; pause it before abandoning")
    if row["status"] == "abandoned":
        return {"id": wid, "status": "abandoned", "worktree_removed": False}
    await st.db.write(lambda c: store.abandon_work_item(c, wid))
    removed = await _remove_worktree(
        Path(row["repo"]), st.run_dirs.worktrees / wid, store.branch_for(row)
    )
    return {"id": wid, "status": "abandoned", "worktree_removed": removed}


@api_router.post("/work-items/{wid}/pause")
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


def _node_has_agent_task(node: dict, registry: Registry) -> bool:
    """Whether this node's own dispatch could ever consume a Steer note.

    `Steer.take()` (executor.py) is read only from the agent-kind branch of
    `_dispatch`. A node whose own tasks are all subprocess/forge/builtin, and
    which has no `fix_loop` (a fix cycle always falls back to the agent-kind
    `on.implementation.start`), has nothing on it that will ever read one.
    """
    if node.get("fix_loop"):
        return True
    hooks = [*node.get("tasks", []), *(node.get("on_failure") or [])]
    return any(registry.hooks.get(h, {}).get("kind") == "agent" for h in hooks)


def _steer_reachable(nodes: list[dict], start_id: str, registry: Registry) -> bool:
    """Whether a Steer note given at `start_id` could reach *any* agent task
    from there to the end of the chain.

    `run()` threads one `Steer` object through every node from `start_index`
    on (`carried`, executor.py `run`) -- it is consumed by whichever agent-kind
    dispatch runs first, not necessarily the one it was given on. `open_mr`
    (forge-kind, no fix_loop) has nothing of its own, but `human_review` right
    after it does; a note given while stopped at `open_mr` still reaches that
    agent if `open_mr` and `mr_checks` succeed on retry. Checking only the
    current node (Kraft-bz9b's first pass) refused that as dead on arrival.

    Not a guarantee of delivery -- if `start_id` fails again, the walk never
    reaches the later node and the note is dropped same as before -- only
    that it is not *structurally* impossible, which is what the API can 409
    on and the UI can hide a control for.
    """
    reached = False
    for n in nodes:
        if n["id"] == start_id:
            reached = True
        if reached and _node_has_agent_task(n, registry):
            return True
    return False


@api_router.post("/work-items/{wid}/steer")
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


@api_router.post("/work-items/{wid}/resume")
async def resume_work_item(wid: str, body: Resume, request: Request):
    """Relaunch the paused node, carrying the steer into the next agent launch."""
    st = request.app.state
    row = _work_item_row(st, wid)
    if row["status"] != "paused" and not (
        row["status"] == "needs_human" and _needs_context_stop(st, wid)
    ):
        raise HTTPException(409, "work item is not paused")
    # The one door that starts new work. Deliberately not on `approve` or
    # `retry`: those continue an item that is already underway, and refusing
    # them would strand a human mid-chain with no way to finish (Kraft-n2d).
    limit = int(st.intake.get("max_concurrent", 1))
    if st.db.read(store.active_count) >= limit:
        raise HTTPException(
            409, f"all {limit} slots are busy; pause something or raise max_concurrent"
        )
    chain = json.loads(row["chain_definition"])
    if body.steer and body.steer.strip():
        found = any(n["id"] == row["current_node_id"] for n in chain["nodes"])
        if found and not _steer_reachable(chain["nodes"], row["current_node_id"], st.registry):
            raise HTTPException(
                409,
                f"node {row['current_node_id']!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )
        await st.db.write(lambda c: store.set_steer(c, wid, body.steer.strip()))
    steer = await st.db.write(lambda c: store.take_steer(c, wid))
    await st.db.write(lambda c: store.resume_work_item(c, wid, steer))

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


@api_router.post("/work-items/{wid}/open-worktree")
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
    return _launch_editor(request, body.editor, path)


@api_router.post("/work-items/{wid}/retry")
async def retry_work_item(wid: str, body: Retry, request: Request):
    """Re-run the stopped node, steer text in hand (4b), clearing a breached
    loop cap if there was one.

    This is the only door back onto an item stopped by a task failure. It used
    to refuse a node with no fix loop, on the grounds that only a capped node
    can be capped — true, and beside the point: resume wants `paused`, pause
    wants `running`, and approve/reject want a pending gate, so refusing here
    stranded the item with no route at all (Kraft-bzwi). A missing fix loop now
    just means there is no counter to clear.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    chain = json.loads(row["chain_definition"])
    node_id = row["current_node_id"]
    node = next((n for n in chain["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise HTTPException(409, "work item has no current node to retry")
    key = node.get("fix_loop") or None
    gate = node.get("gate_after")
    gate_key = f"{gate}_reject_loop" if gate else None
    if row["status"] != "needs_human":
        raise HTTPException(409, "work item is not stopped")

    steer = (body.steer or "").strip() or None
    if steer is not None and not _steer_reachable(chain["nodes"], node_id, st.registry):
        # Explicit only: the last-rejection fallback below is Kraft's own
        # carry-forward, not something the caller just typed and needs told.
        raise HTTPException(
            409,
            f"node {node_id!r} has no agent task downstream to steer; this text would be dropped",
        )
    if steer is None:
        # The reason the human already typed at the gate. Without this a
        # rejection that exhausted its cap makes them type it twice for it to
        # reach an agent at all (Kraft-ko7j).
        last = st.db.read(lambda c: store.last_rejection(c, wid))
        steer = (last or {}).get("note") or None
    await st.db.write(
        lambda c: store.retry_after_cap(c, wid, node_id, key, steer, gate_key=gate_key)
    )
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


@api_router.post("/work-items/{wid}/mr-labels")
async def set_mr_labels(wid: str, body: MrLabels, request: Request):
    """Label this item's merge request and re-create its pipeline (Kraft-xh0q
    layer 3).

    The mechanism, not the policy: this is the thing an `on_failure` repair
    agent calls once it has read a red `on.ci.poll` and decided which labels
    the trace is asking for. No `_forbid_self_action` here on purpose — that
    guard exists for gates, where a worker deciding for itself would collapse
    the human-gate model (design §6 rule 2). This is the opposite shape: the
    chain fixing metadata on its own merge request is exactly what `open_mr`
    and `ci_poll` already do from inside the same worktree, just triggered by
    an agent's judgement call instead of the executor's own dispatch.
    """
    st = request.app.state
    row = _work_item_row(st, wid)
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")
    labels = tuple(label.strip() for label in body.labels if label.strip())
    if not labels:
        raise HTTPException(422, "no labels given")
    repo_entry = _launch(st, row["repo"]).repo_entry
    try:
        backend = forge_mod.backend_for("auto", (repo_entry or {}).get("forge"))
        forge = forge_mod.resolve(backend)
        await forge.set_labels(repo=worktree, mr=forge_mod.MR(number=0, url=""), labels=labels)
    except forge_mod.ForgeError as exc:
        raise HTTPException(502, str(exc)) from exc
    await st.db.write(lambda c: events.append(c, wid, "mr_labels_set", {"labels": list(labels)}))
    return {"work_item_id": wid, "labels": list(labels)}


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

    Stops one poll *after* the session stops being pending or running, so the
    lines written between the last poll and the exit are not dropped on the
    floor.
    """
    sent = 0
    running = True
    while True:
        for line in logs_mod.jsonl(path, start_line=sent):
            sent = line["n"] + 1
            yield f"data: {json.dumps(line)}\n\n"
        if not running:
            break
        # not *finished* -- a session still in 'pending' has an agent about to
        # write to this log, and a stream opened on it used to get one poll and
        # an end event
        running = _session_row(st, sid)["status"] in ("pending", "running")
        await asyncio.sleep(poll_s)
    yield "event: end\ndata: {}\n\n"


@api_router.get("/worker-sessions/{sid}/log")
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


class PermissionAsk(BaseModel):
    """What `--permission-prompt-tool` hands over: the tool the agent wants to
    use, the arguments it wants to use it with, and the CLI's own id for the
    call."""

    tool_name: str
    input: dict = {}
    tool_use_id: str | None = None


@api_router.post("/worker-sessions/{sid}/permission")
async def permission_request(sid: str, body: PermissionAsk, request: Request):
    """Answer a worker's permission prompt from its node's grant (Kraft-oor).

    The policy is the hook binding's `allowed_tools` (Kraft-3tw), resolved
    session -> hook_point -> binding. A binding that declares none allows
    everything: `--permission-mode auto` already resolves these asks silently
    today, and a default that denied would turn an observability change into a
    behaviour change on every node at once. A binding that *does* declare an
    allowlist is taken at its word.

    Every decision appends an event. That is the whole point -- it is the only
    way the orchestrator ever learns what a worker decided it was allowed to do.
    """
    st = request.app.state
    row = st.db.read(
        lambda c: c.execute(
            "SELECT work_item_id, node_id, hook_point FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown session")
    allowed = st.registry.hooks.get(row["hook_point"], {}).get("allowed_tools") or []
    if not allowed:
        decision, reason = "allow", f"{row['node_id']} declares no allowed_tools"
    elif body.tool_name in allowed:
        decision, reason = "allow", f"{body.tool_name} is in {row['node_id']}'s allowed_tools"
    else:
        decision, reason = (
            "deny",
            f"{body.tool_name} is not in {row['node_id']}'s allowed_tools ({', '.join(allowed)})",
        )
    await st.db.write(
        lambda c: events.append(
            c,
            row["work_item_id"],
            "permission_decision",
            {
                "session_id": sid,
                "node_id": row["node_id"],
                "tool": body.tool_name,
                "decision": decision,
                "reason": reason,
            },
        )
    )
    if decision == "allow":
        return {"behavior": "allow", "updatedInput": body.input}
    return {"behavior": "deny", "message": reason}


@api_router.get("/search")
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


@api_router.get("/beads/search")
async def beads_search(request: Request, q: str = "", limit: int = 5):
    """The live strip under the search results (design 1h)."""
    if not q.strip():
        return {"query": q, "beads": []}
    return {"query": q, "beads": await _beads_search(request.app.state, q, limit)}


async def _beads_search(st, q: str, limit: int) -> list[dict]:
    """KRAFT_BD_CWD when it is set; otherwise every connected repo that has a
    `.beads` (Kraft-ibwj).

    The strip is the one repo-less bd caller and there is no repo to scope it
    to — `SearchOverlay` is global and sends only `q`. The `.beads` test is what
    keeps this from spawning a bd per keystroke per repo for nothing; the
    overlay debounces, and an instance has a handful of repos, not hundreds.
    """
    override = _bd_cwd()
    if override:
        return await beads_mod.search(q, cwd=override, limit=limit)
    try:
        repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
    except config_mod.ConfigError:
        # Best-effort by contract, same as `beads.search` itself: a malformed
        # repos.yaml is a missing footer strip, not a 500 on the search route.
        return []
    cwds = [r["path"] for r in repos if (Path(r["path"]) / ".beads").is_dir()]
    if not cwds:
        return []
    merged: dict[str, dict] = {}
    for hits in await asyncio.gather(*(beads_mod.search(q, cwd=c, limit=limit) for c in cwds)):
        for hit in hits:
            # Dedupe by bead id: two connected repos can share one workspace.
            merged.setdefault(hit["id"], hit)
    return list(merged.values())[:limit]


@api_router.get("/documents/{doc_id}")
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


def _launch_editor(request: Request, editor: str | None, path: Path) -> dict:
    """Open `path` in an editor, or say plainly that this server cannot.

    Loopback callers only. This starts a process and opens a window on the
    machine hosting the API, and Kraft has one shared password and no notion of
    who is holding it — so "authenticated" is not "sitting at this keyboard".
    The guard is here rather than in each route because both routes reach the
    same `Popen`.

    501 rather than 500 when nothing here can open a window: that is the signal
    the SPA falls back on, handing the path to the viewer's own machine. A 403
    reaches the same fallback (`DocumentModal.tsx:112` catches any rejection).
    """
    if not _client_is_local(request):
        raise HTTPException(403, "this server only opens editors for a client on its own machine")
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


@api_router.post("/documents/{doc_id}/open")
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
    # The row comes from the indexer, not the caller — but it is still the only
    # thing between a stored `../..` and an editor opened outside the repo.
    # Validate the resolved path, launch the unresolved one: a repo under a
    # symlinked temp dir (/var on macOS) resolves to a different string, and the
    # editor should get the path the rest of the UI shows.
    root = Path(doc["repo"]).resolve()
    path = Path(doc["repo"]) / doc["path"]
    if not path.resolve().is_relative_to(root):
        raise HTTPException(400, f"document path escapes its repo: {doc['path']}")

    return {"document_id": doc_id, **_launch_editor(request, body.editor, path)}


@api_router.get("/analytics")
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


@api_router.post("/index/rescan")
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


def _origin_ok(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser client
    return urlsplit(origin).hostname in _LOCAL_HOSTS


@api_router.websocket("/ws/events")
async def ws_events(websocket: WebSocket, after_seq: int = 0):
    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    # Rule 1 of `_perimeter`, restated: HTTP middleware does not run for
    # websockets, so without this the live event stream is the one route that
    # still answers a remote peer on a server that has no password to demand.
    if not _client_is_local(websocket) and not (
        (getattr(websocket.app.state, "access", None) or {}).get("password_hash")
    ):
        await websocket.close(code=1008)
        return
    # HTTP middleware does not run for websockets, so the session check has to be
    # here too — otherwise a LAN bind would leave the live event stream open.
    #
    # Both credentials, for the same reason the HTTP middleware takes both: a
    # browser has a session cookie, and a non-browser client (`kraft view watch`, an
    # agent) has the bearer token from run/. Accepting only the cookie made the
    # live stream the one endpoint a CLI could not reach.
    if _requires_auth(websocket.app, websocket):
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
    # Uvicorn's graceful shutdown asks every open connection to close and then
    # waits for its handler to return; Starlette delivers that ask as a
    # `websocket.disconnect` message to `receive()`. A handler that only ever
    # sends never sees it, so one browser with the board open kept the server
    # alive indefinitely and `kraft admin stop` reported a false failure
    # (Kraft-9oab). Any completion of this future means the connection is going
    # away -- which is also how a tab closed without a close frame stops
    # leaking a Client into the broadcaster's set until a send happens to fail.
    closing = asyncio.ensure_future(websocket.receive())
    try:
        for ev in st.db.read(lambda c: events.read_after(c, after_seq)):
            if ev["seq"] <= live_start:
                await websocket.send_json(ev)
        while True:
            if closing.done() or client.dropped:
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
        closing.cancel()
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


@api_router.get("/repos")
async def list_repos(request: Request):
    st = request.app.state
    # No steering validation on the read path (config.load_repos): a steering
    # file deleted out from under an entry must not 422 the screen that would
    # let an operator clear it. See `config.load_repos`'s docstring.
    return {"repos": config_mod.load_repos(_repos_path(st), validate_steering=False)}


@api_router.post("/repos/probe")
async def probe_repo(body: ProbeBody, request: Request):
    """Read-only inspection of a candidate repo — Kraft never edits repo files."""
    try:
        return config_mod.probe_repo(body.path)
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


@api_router.post("/repos", status_code=201)
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


@api_router.patch("/repos")
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


@api_router.delete("/repos", status_code=204)
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
    await st.indexer.purge_repo(entry["path"])


class TemplateBody(BaseModel):
    nodes: list[dict]


def _template_path(st, tid: str) -> Path:
    if not tid.isidentifier() and not tid.replace("-", "_").isidentifier():
        raise HTTPException(400, f"invalid template id {tid!r}")
    return st.templates_dir / f"{tid}.yaml"


@api_router.get("/templates")
async def list_templates(request: Request):
    st = request.app.state
    return [
        {"id": tid, "nodes": t.nodes, "gates": sum(1 for n in t.nodes if n.get("gate_after"))}
        for tid, t in sorted(st.templates.valid.items())
    ]


@api_router.get("/templates/{tid}")
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
    return {
        "id": tid,
        "valid": tid in result.valid,
        "error": result.invalid.get(tid),
        # Per node and per task, not per repo. `by_repo.resolvable` was
        # `all(h in st.registry.hooks for h in hooks)` -- one bit of information
        # repeated once per connected repo, computed without consulting the repo
        # at all, despite the comment that used to sit above it. "unresolvable"
        # named a repo; the human editing a chain needs the node. Replaced, not
        # repaired: two answers to one question, one of them wrong, is worse
        # than one.
        "unresolved": [
            {"node": n.get("id"), "task": task}
            for n in nodes
            for task in (n.get("tasks") or [])
            if task not in st.registry.hooks
        ],
    }


@api_router.post("/templates/{tid}/validate")
async def validate_template(tid: str, body: TemplateBody, request: Request):
    return _validate_template(request.app.state, tid, body.nodes)


@api_router.put("/templates/{tid}")
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


@api_router.get("/registry")
async def get_registry(request: Request):
    return {"hooks": request.app.state.registry.hooks}


@api_router.put("/registry")
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


@api_router.get("/policy")
async def get_policy(request: Request):
    st = request.app.state
    return config_mod.read_yaml(st.templates_dir / "policy.yaml", {"loops": {}, "default": {}})


@api_router.put("/policy")
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


PALETTE_IDS = frozenset({"nocturne", "rose", "forest", "amber", "slate"})
THEME_DEFAULT: dict = {"palette": "nocturne", "mode": "dark"}


class ThemeBody(BaseModel):
    palette: str
    mode: Literal["light", "dark", "system"]


@api_router.get("/theme")
async def get_theme(request: Request):
    st = request.app.state
    return config_mod.read_yaml(st.templates_dir / "theme.yaml", THEME_DEFAULT)


@api_router.put("/theme")
async def put_theme(body: ThemeBody, request: Request):
    if body.palette not in PALETTE_IDS:
        raise HTTPException(422, f"unknown palette: {body.palette!r}")
    st = request.app.state
    data = {"palette": body.palette, "mode": body.mode}
    config_mod.write_yaml(st.templates_dir / "theme.yaml", data)
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

    Synchronous and directory-sized, so both callers run it through
    `asyncio.to_thread` (Kraft-e9a) -- an `HTTPException` raised here propagates
    out of the thread unchanged. The real write (`config_mod.write_text`) and
    `path.unlink()` stay on the loop: one file, one syscall, and moving those
    would be cargo.
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


@api_router.get("/steering")
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


@api_router.get("/steering/{name}")
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


@api_router.put("/steering/{name}")
async def put_steering(name: str, body: SteeringBody, request: Request):
    st = request.app.state
    steering_dir = _steering_dir(st)
    try:
        path = steering_mod.path_for(steering_dir, name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    await asyncio.to_thread(_check_steering_change, st, name, body.body)
    config_mod.write_text(path, body.body)
    # Nothing to reload: no `app.state` holds steering bodies. They are read
    # from disk at dispatch, and `resolve_invocation` re-checks the assembled
    # budget at every launch, so the next agent picks this up on its own.
    return {"name": name, "body": body.body}


@api_router.delete("/steering/{name}")
async def delete_steering(name: str, request: Request):
    st = request.app.state
    try:
        path = steering_mod.path_for(_steering_dir(st), name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, f"unknown steering file {name!r}")
    await asyncio.to_thread(_check_steering_change, st, name, None)
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


@api_router.get("/intake")
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


@api_router.put("/intake")
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


@api_router.get("/access")
async def get_access(request: Request):
    access = request.app.state.access
    return {
        "bind": access["bind"],
        "port": access["port"],
        "session_expiry_days": access["session_expiry_days"],
        "password_set": bool(access["password_hash"]),
        "auth_required": _requires_auth(request.app, request),
    }


@api_router.put("/access")
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


@api_router.get("/notify")
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


@api_router.put("/notify")
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


@api_router.post("/login")
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


@api_router.post("/logout", status_code=204)
async def logout(request: Request):
    token = request.cookies.get(auth_mod.COOKIE)
    if token:
        await request.app.state.db.write(
            lambda c: auth_mod.revoke_session(c, auth_mod.token_id(token))
        )
    response = Response(status_code=204)
    response.delete_cookie(auth_mod.COOKIE, path="/")
    return response


@api_router.get("/sessions")
async def list_sessions(request: Request):
    token = request.cookies.get(auth_mod.COOKIE)
    return {"sessions": request.app.state.db.read(lambda c: auth_mod.list_sessions(c, token))}


@api_router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: str, request: Request):
    revoked = await request.app.state.db.write(lambda c: auth_mod.revoke_session(c, session_id))
    if not revoked:
        raise HTTPException(404, "unknown session")


@api_router.get("/health")
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


app.include_router(api_router)


@app.get("/{path:path}")
async def spa(path: str, request: Request):
    if _is_api_path(f"/{path}"):
        # A real API prefix with no matching route is a bad request, not a
        # missing page — this must not fall through to the SPA shell.
        raise HTTPException(404, "not found")
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
    # Every non-/api GET is answered directly by the spa() catch-all above —
    # it returns a file or index.html unconditionally and never raises — so
    # the only 404s that reach here are genuinely /api/ ones (a bad path, or a
    # handler's own `raise HTTPException(404, ...)`). Always JSON: nothing
    # under /api/ is ever the shell, whatever Accept header asks for it.
    return JSONResponse({"detail": exc.detail}, status_code=404)
