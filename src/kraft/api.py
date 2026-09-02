from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from kraft import events, executor, reattach, store
from kraft.db import Database
from kraft.paths import RunDirs
from kraft.templates import load_registry, load_templates

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


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
                ),
            ),
        )

    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.registry = registry
    app.state.templates = templates
    app.state.reattach_summary = summary
    try:
        yield
    finally:
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


class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"


@app.post("/work-items", status_code=201)
async def create_work_item(body: NewWorkItem, request: Request):
    st = request.app.state
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
                st.db, st.run_dirs, work_item_id=wid, registry=st.registry, bd_cwd=_bd_cwd()
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


@app.get("/health")
async def health(request: Request):
    st = request.app.state
    invalid = st.templates.invalid
    return {
        "status": "degraded" if invalid else "ok",
        "invalid_templates": invalid,
        "reattach_summary": asdict(st.reattach_summary),
    }
