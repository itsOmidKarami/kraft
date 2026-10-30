"""What a worker may read of its item's merge request and CI, through Kraft
rather than `gh`/`glab`: the item's MR, pipelines, jobs and a job's log, recent
merges and the repo's labels. The item decides the project and the MR; a
caller names only ids the item owns, and a workspace member by its mount path.
Host workers call these unscoped, like every loopback route."""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from pathlib import Path

from fastapi import HTTPException, Query, Request

from kraft import builtins as builtins_mod
from kraft import inplace, store
from kraft.adapters import forge as forge_mod
from kraft.adapters.forge import Forge, ForgeError
from kraft.api import api_router, deps

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _mapped(exc: ForgeError) -> HTTPException:
    """A forge failure as a status: a rate limit the caller can wait out, a
    missing thing, or the forge being unusable (which includes no CLI)."""
    if exc.reason == "rate_limited":
        headers = {"Retry-After": str(math.ceil(exc.retry_after))} if exc.retry_after else None
        return HTTPException(429, str(exc), headers=headers)
    return HTTPException(404 if exc.reason == "not_found" else 502, str(exc))


async def _item_forge(st, wid: str, repo: str | None) -> tuple[Forge, Path, str, str]:
    """`(forge, checkout, branch, base)` for the item's root, or for the
    workspace member mounted at `repo`, exactly as the forge nodes run them."""
    row = deps._work_item_row(st, wid)
    entry = deps.launch(st, row["repo"]).repo_entry
    repo_forge = entry.forge if entry else None
    branch = store.branch_for(row)
    try:
        if repo is None:
            forge = forge_mod.resolve(forge_mod.backend_for("auto", repo_forge))
            root = Path(row["repo"])
            return (
                forge,
                inplace.item_folder(st.run_dirs, row),
                branch,
                (await builtins_mod.base_branch(st.db, wid, root)),
            )
        members = st.db.read(
            lambda c: c.execute(
                "SELECT repo_path, submodule_path FROM work_item_repos "
                "WHERE work_item_id = ? AND role = 'submodule'",
                (wid,),
            ).fetchall()
        )
        # Whole-string match against recorded mounts, never a path lookup.
        found = next((m for m in members if m["submodule_path"] == repo), None)
        if found is None:
            mounts = ", ".join(m["submodule_path"] for m in members)
            raise HTTPException(
                404, f"{repo!r} is not a member of {wid}; its members: {mounts or 'none'}"
            )
        path = Path(found["repo_path"])
        return (
            forge_mod.member_forge(row, repo_forge, repo),
            path,
            branch,
            await builtins_mod.base_branch(st.db, wid, path, member=True),
        )
    except ForgeError as exc:
        raise _mapped(exc) from exc


async def _read(call):
    try:
        return await call
    except ForgeError as exc:
        raise _mapped(exc) from exc


@api_router.get("/work-items/{wid}/forge/mr")
async def forge_mr(wid: str, request: Request, repo: str | None = None):
    forge, checkout, branch, _ = await _item_forge(request.app.state, wid, repo)
    found = await _read(forge.mr_details(repo=checkout, branch=branch))
    if found is None:
        raise HTTPException(404, f"no merge request for {branch} yet")
    return asdict(found)


@api_router.get("/work-items/{wid}/forge/pipelines")
async def forge_pipelines(wid: str, request: Request, repo: str | None = None):
    forge, checkout, branch, base = await _item_forge(request.app.state, wid, repo)
    return [
        asdict(p) for p in await _read(forge.pipelines(repo=checkout, branch=branch, base=base))
    ]


async def _own_jobs(forge: Forge, checkout: Path, branch: str, base: str, pid: str):
    """The pipeline's jobs, once `pid` is one of the item's pipelines."""
    own = await _read(forge.pipelines(repo=checkout, branch=branch, base=base))
    if pid not in {p.id for p in own}:
        raise HTTPException(404, f"pipeline {pid} is not among this item's newest")
    return await _read(forge.jobs(repo=checkout, pipeline_id=pid))


@api_router.get("/work-items/{wid}/forge/pipelines/{pid}/jobs")
async def forge_jobs(wid: str, pid: str, request: Request, repo: str | None = None):
    forge, checkout, branch, base = await _item_forge(request.app.state, wid, repo)
    return [asdict(j) for j in await _own_jobs(forge, checkout, branch, base, pid)]


@api_router.get("/work-items/{wid}/forge/pipelines/{pid}/jobs/{jid}/log")
async def forge_job_log(
    wid: str,
    pid: str,
    jid: str,
    request: Request,
    repo: str | None = None,
    lines: int = Query(200, ge=1, le=5000),
):
    forge, checkout, branch, base = await _item_forge(request.app.state, wid, repo)
    if jid not in {j.id for j in await _own_jobs(forge, checkout, branch, base, pid)}:
        raise HTTPException(404, f"job {jid} is not in pipeline {pid}")
    text = await _read(forge.job_log(repo=checkout, job_id=jid))
    # shortcut: the whole log is fetched, then cut; fine until a forge offers a ranged read.
    # A `\r` rewrites its line on a terminal (a progress bar, GitLab's section
    # markers): keep what was left showing, not every overwritten fragment. Before
    # the colour codes go, or `\r\x1b[0K\n` would read as a plain line ending.
    lines_in = text.replace("\r\n", "\n").removesuffix("\n").split("\n")
    all_lines = [_ANSI.sub("", ln.rstrip("\r").rsplit("\r", 1)[-1]) for ln in lines_in]
    return {"log": "\n".join(all_lines[-lines:]), "truncated": len(all_lines) > lines}


@api_router.get("/work-items/{wid}/forge/merged")
async def forge_merged(
    wid: str,
    request: Request,
    repo: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    forge, checkout, _, base = await _item_forge(request.app.state, wid, repo)
    return [asdict(m) for m in await _read(forge.merged_mrs(repo=checkout, base=base, limit=limit))]


@api_router.get("/work-items/{wid}/forge/labels")
async def forge_labels(wid: str, request: Request, repo: str | None = None):
    forge, checkout, _, _ = await _item_forge(request.app.state, wid, repo)
    return [asdict(x) for x in await _read(forge.labels(repo=checkout))]
