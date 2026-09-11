from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import analytics as analytics_mod
from kraft import config as config_mod
from kraft.adapters import beads as beads_mod
from kraft.api import api_router, deps, perimeter


class OpenDocument(BaseModel):
    editor: str | None = None


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
    override = deps.bd_cwd()
    if override:
        return await beads_mod.search(q, cwd=override, limit=limit)
    try:
        repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
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
    if not perimeter._client_is_local(request):
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
    # Ahead of everything else, `_launch_editor` would otherwise check this
    # first internally: a remote caller learns nothing about a document --
    # not even whether it has a file to open -- before being told it may not
    # ask this server to start a process at all.
    if not perimeter._client_is_local(request):
        raise HTTPException(403, "this server only opens editors for a client on its own machine")
    st = request.app.state
    doc = st.indexer.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "unknown document")
    if doc_id.startswith("attachment:"):
        # A synthetic attachment doc's file lives in the item's own worktree,
        # not necessarily the connected repo's checkout (Kraft-2jy6) — the
        # same worktree-first lookup `get_document` already reads its content
        # from. `resolve_attachment_path` does its own containment check
        # (`Indexer._summary_path`), so there is no separate escape check here.
        path = st.indexer.resolve_attachment_path(doc_id)
        if path is None:
            raise HTTPException(404, "attachment file not found")
        return {"document_id": doc_id, **_launch_editor(request, body.editor, path)}
    if doc.get("origin") == "event_ingest":
        # A session summary or gate artifact: `path` is a synthetic identifier
        # Kraft made up for the index, never a file the connected repo
        # checkout has (`forge._work_product_pathspec` keeps it out of git
        # entirely now). `doc["repo"] / doc["path"]` would resolve to nothing
        # -- launching an editor on it, or handing the SPA's `vscode://`
        # fallback that same path, just points at a file that does not exist.
        raise HTTPException(
            409, "this document exists only in Kraft's index; it was never written to the repo"
        )
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
