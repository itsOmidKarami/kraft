"""Config drafts (spec §6.3, W9 A): the Templates editor's unpublished edits,
one per (area, key), kept here rather than in the browser. Every read and
write answers with the resolve result (`kraft.drafts.resolve`); a publish
writes the files only if none changed since it joined the draft (R18).

Item drafts (W9 G): a person's unapplied edits to one work item's chain
(`kraft.drafts.item`), applied whole or refused."""

from __future__ import annotations

import difflib

import yaml
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from kraft import apply as apply_mod
from kraft import config as config_mod
from kraft import store as items
from kraft.api import api_router, config_check, deps
from kraft.api.routes import board
from kraft.drafts import authored, item, ops, policy_caps, resolve, store
from kraft.policy import PolicyError
from kraft.templates import catalogue, revision
from kraft.templates.library import LIBRARY_FILE
from kraft.templates.models import AgentTask
from kraft.vocab import ENDED


def _area(area: str, key: str, st=None, *, read: bool = False) -> store.Area:
    from kraft.drafts import areas  # here, not at import: `resolve` imports this package

    areas.register()
    found = store.AREAS.get(area)
    if found is None:
        raise HTTPException(404, f"no draft area {area!r}")
    library = getattr(st, "library", None)
    # Before the key-shape check, which would answer 400 for a qualified id:
    # a plugin's chain has no draft.
    if area == "chains" and library is not None:
        if why := catalogue.read_only_message(library, key):
            if read:
                return found  # shown as published, never as a draft
            raise HTTPException(409, why)
    if not found.valid(key):
        raise HTTPException(400, f"invalid {area} key {key!r}")
    return found


def _state(st, area: str, key: str, draft: dict | None, history: list[dict]):
    """The files as the editor sees them (the published ones, the draft laid
    over them), the published ones, and their resolve result."""
    names = {*store.AREAS[area].files(key), *(draft["files"] if draft else ())}
    published = resolve.published(st.templates_dir, sorted(names))
    library = getattr(st, "library", None)
    if area == "chains" and library is not None and library.plugin_of(key):
        # A plugin's chain is in its store, not under config/: shown as the
        # library holds it, its own references already qualified.
        if key in library.chain_ids:
            published[f"chains/{key}.yaml"] = authored.dump(ops.plain(library.chain_data(key)))
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
        "published": published,
        "updated_at": draft["updated_at"] if draft else None,
        "result": result,
        # The plugin a chain comes from: the screen shows it and offers no edit.
        "plugin": catalogue.plugin_view(st.library, key)
        if area == "chains" and getattr(st, "library", None) is not None
        else None,
        # On the library: the loaded plugins' components, in `library.yaml`'s
        # own shape (`{section: {"release:base": definition}}`), to show beside
        # the local ones. Never part of `files`: no op or publish touches them.
        "plugin_library": ops.plugin_components(st) if area == "library" else None,
    }


@api_router.get("/drafts")
async def list_config_drafts(request: Request):
    """Every open draft, with its counts as of its last write."""
    return request.app.state.db.read(store.list_drafts)


@api_router.get("/drafts/{area}/{key}")
async def get_config_draft(area: str, key: str, request: Request):
    """The draft, or the published files and their result when there is none."""
    st = request.app.state
    found = _area(area, key, request.app.state, read=True)
    draft = st.db.read(lambda c: store.get(c, area, key))
    files, published, result = _state(st, area, key, draft, draft["history"] if draft else [])
    # A config file that is not there yet is still a page to open: ops create it.
    if not files and found.working is None:
        raise HTTPException(404, f"no {area} {key!r}")
    return _view(st, area, key, files, published, result)


class FileText(BaseModel):
    text: str


@api_router.put("/drafts/{area}/{key}/files/{file:path}")
async def put_draft_file(area: str, key: str, file: str, body: FileText, request: Request):
    """One file's text as typed, comments kept. One undo step, shared with the
    PUTs to the same file just before it (`store.COALESCE_S`)."""
    st = request.app.state
    found = _area(area, key, request.app.state)
    old = st.db.read(lambda c: store.get(c, area, key))
    # A file an op joined to the draft (a rename's new file, a chain a library
    # rename rewrote, `move_to_library`'s library) is the draft's to write too.
    joined = old is not None and file in old["files"]
    if file not in found.files(key) and not joined:
        raise HTTPException(422, f"{file!r} is not a file of {area} {key!r}")
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


class Ops(BaseModel):
    ops: list[dict]


@api_router.post("/drafts/{area}/{key}/ops")
async def apply_draft_ops(area: str, key: str, body: Ops, request: Request, preview: bool = False):
    """Apply `ops` in order, all or nothing, as one undo step. A chain or the
    library is written back whole (`authored.dump`), comments dropped, and
    the result warns of it; a config file (`drafts.config.ConfigDraft`) is
    written over its own text, comments kept. With `?preview=1`, the result
    without saving."""
    st = request.app.state
    found = _area(area, key, request.app.state)
    old = st.db.read(lambda c: store.get(c, area, key))
    history = old["history"] if old else []
    current, published, result = _state(st, area, key, old, history)
    if "yaml_error" in result:
        raise HTTPException(409, "fix the YAML first")
    exists = old is not None or any(t is not None for t in published.values())
    if found.working is None:
        working, table = ops.Draft(st, key, current, exists=exists, area=area), ops.OPS
    else:
        working, table = found.working(st, key, current, exists=exists), found.ops
    try:
        answers = ops.apply(working, body.ops, table)
        written = working.finish()
    except ops.OpError as exc:
        return JSONResponse(
            status_code=409 if isinstance(exc, ops.PluginReadOnly) else 422,
            content={"detail": str(exc), "op": exc.index, **exc.extra},
        )
    names = {*(old["files"] if old else ()), *working.dirty}
    # Only a file whose comments the write drops is `serialized`: the
    # results warn of it (`drafts.resolve`). A config area keeps them.
    drops = getattr(working, "drops_comments", True)
    draft = {
        "files": {n: written.get(n) for n in names},
        "serialized": sorted({*(old["serialized"] if old else ()), *working.dirty})
        if drops
        else [],
    }
    history = [*history, {"files": old["files"] if old else {}}]
    files, published, result = _state(st, area, key, draft, history)
    if not preview:
        await st.db.write(
            lambda c: store.write(
                c,
                area,
                key,
                draft["files"],
                published,
                serialized=draft["serialized"],
                counts=_counts(result),
            )
        )
    return {**_view(st, area, key, files, published, result), "ops": answers}


@api_router.post("/drafts/{area}/{key}/undo")
async def undo_draft(area: str, key: str, request: Request):
    """Back one request. Back to the published state drops the draft."""
    st = request.app.state
    _area(area, key, request.app.state)
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
        # A file already holding the draft's text has nothing to overwrite: a
        # publish whose apply hook failed, tried again.
        if store.digest(published[f]) != base and published[f] != draft["files"].get(f)
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
    found = _area(area, key, request.app.state)
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
        try:
            if found.after_publish is None:
                deps._reload_templates(st)
            else:
                await found.after_publish(request.app, draft["files"])
        except Exception as exc:  # noqa: BLE001 -- the files are written: say so, keep the draft
            raise HTTPException(500, f"published, but applying it failed: {exc}") from exc
        await st.db.write(lambda c: store.delete(c, area, key))
    apply_mod.notify(request.app)
    # What went out, as the review listed it: `result` is the state after the
    # publish, which has no draft left to differ (R10b-02).
    published_changes = result["changes"]
    _, _, result = _state(st, area, key, None, [])
    return {"published": sorted(draft["files"]), "changes": published_changes, "result": result}


@api_router.post("/drafts/{area}/{key}/rebase")
async def rebase_draft(area: str, key: str, request: Request):
    """ "Keep my version" after a publish answered 409: the draft's base becomes
    what its files are on disk now, so the next publish goes through and
    overwrites what changed underneath. No undo entry, no change to the draft's
    text."""
    st = request.app.state
    _area(area, key, request.app.state)
    draft = st.db.read(lambda c: store.get(c, area, key))
    if draft is None:
        raise HTTPException(404, f"no draft of {area} {key!r}")
    published = resolve.published(st.templates_dir, sorted(draft["base"]))
    base = {f: store.digest(t) for f, t in published.items()}
    await st.db.write(lambda c: store.rebase(c, area, key, base))
    draft = st.db.read(lambda c: store.get(c, area, key))
    files, published, result = _state(st, area, key, draft, draft["history"])
    return _view(st, area, key, files, published, result)


@api_router.get("/drafts/policy/{key}/preview")
async def get_policy_preview(key: str, request: Request, chain: str):
    """A published chain's effective caps under the policy draft: per scope the
    value it runs under, the layer that set it and the maximum bounding it. A
    read; the draft is not changed."""
    st = request.app.state
    _area("policy", key)
    draft = st.db.read(lambda c: store.get(c, "policy", key))
    files, _, _ = _state(st, "policy", key, draft, draft["history"] if draft else [])
    try:
        parsed, _policy = config_check.validate_policy(
            authored.parse(files.get("policy.yaml")) or {}
        )
    except (PolicyError, yaml.YAMLError) as exc:
        raise HTTPException(422, f"policy.yaml does not load: {exc}") from exc
    library = deps.library_or_503(st)
    if chain not in library.chain_ids:
        raise HTTPException(404, f"unknown chain {chain!r}")
    scopes = policy_caps.chain_scopes(st, chain, parsed.instance_policy())
    if scopes is None:
        raise HTTPException(422, f"chain {chain!r} does not resolve")
    return {"chain": chain, "scopes": scopes}


@api_router.get("/drafts/{area}/{key}/fragment")
async def get_draft_fragment(area: str, key: str, request: Request, path: str | None = None):
    """The authored component at a canonical `path` as YAML (the serializer a
    publish uses), from the draft when there is one, else the published file.
    `set_fragment` is the write side."""
    st = request.app.state
    _area(area, key, request.app.state, read=True)
    if path is None:
        raise HTTPException(400, "path is required")
    draft = st.db.read(lambda c: store.get(c, area, key))
    files, published, result = _state(st, area, key, draft, draft["history"] if draft else [])
    if not files:
        raise HTTPException(404, f"no {area} {key!r}")
    model = result["model"]
    if area == "repos":
        # Read-only: the entry whose `path` is `path`, as `repos.yaml` writes it.
        listed = (model.get("repos.yaml") or {}).get("repos")
        entry = next(
            (e for e in listed or () if isinstance(e, dict) and e.get("path") == path), None
        )
        if entry is None:
            raise HTTPException(404, f"nothing at {path!r} in {area} {key!r}")
        return {"path": path, "text": authored.dump(entry)}
    if area not in ("chains", "library"):
        raise HTTPException(404, f"{area} drafts have no fragments")
    library = model.get(LIBRARY_FILE)
    if library is None:
        library = authored.load(resolve.published(st.templates_dir, [LIBRARY_FILE])[LIBRARY_FILE])
    if area == "library":
        name, kind = LIBRARY_FILE, authored.LIBRARY
    else:
        name, kind = resolve.chain_file(key, files), authored.CHAIN
    mapping = model.get(name)
    # A throwaway copy of the request's model: owning what the component
    # inherits (as `set_fragment` does) writes nothing.
    found = (
        authored.at(mapping, path, file=kind, library=ops.with_plugins(st, library), write=True)
        if isinstance(mapping, dict)
        else None
    )
    if found is None:
        raise HTTPException(404, f"nothing at {path!r} in {area} {key!r}")
    return {"path": path, "text": authored.dump(found)}


@api_router.delete("/drafts/{area}/{key}", status_code=204)
async def discard_draft(area: str, key: str, request: Request):
    """Final: a discard has no undo."""
    st = request.app.state
    _area(area, key, request.app.state)
    if not await st.db.write(lambda c: store.delete(c, area, key)):
        raise HTTPException(404, f"no draft of {area} {key!r}")
    return Response(status_code=204)


# ── the item chain draft (W9 G, B15) ──


def _item_row(st, request: Request, wid: str):
    """A door onto the item's chain: never its own worker's (an escalation may,
    as it may retry with an override), never an ended item's."""
    deps.forbid_self_action(st, request, wid)
    return deps._live_work_item_row(st, wid)


def _chain(row):
    chain = items.materialized_chain_of(row)
    if chain is None:
        raise HTTPException(409, "this work item has no materialized chain to draft against")
    return chain


def _added_checks(st, evaluated: item.Evaluated) -> list[dict]:
    """What Review & apply says of each node the draft adds that resolved: the
    harnesses its agent tasks run on (materializing the chain already refused one
    outside `allowed_harnesses`, as a problem on that op, so these are allowed)
    and what the instance's runs of a node of that id cost, per item
    (`estimate_usd`, the mean over `estimate_runs` items). Both are null for an
    id nothing has run yet. The estimate is information: it blocks nothing."""
    failed = {p["op"] for p in evaluated.problems}
    nodes = {n.id: n for n in evaluated.chain.chain.nodes}
    out = []
    for i, op in enumerate(evaluated.ops):
        if op["op"] != "add_node" or op["passed"] or i in failed or op["node"]["id"] not in nodes:
            continue
        node_id = op["node"]["id"]
        harnesses = sorted(
            {t.task.harness for t in nodes[node_id].tasks() if isinstance(t.task, AgentTask)}
        )
        estimate, runs = st.db.read(
            lambda c, node_id=node_id: tuple(
                c.execute(
                    "SELECT AVG(spent), COUNT(spent) FROM (SELECT SUM(cost_usd) AS spent "
                    "FROM worker_sessions WHERE node_id = ? AND cost_usd IS NOT NULL "
                    "GROUP BY work_item_id)",
                    (node_id,),
                ).fetchone()
            )
        )
        out.append(
            {
                "op": i,
                "node": node_id,
                "harnesses": harnesses,
                "estimate_usd": None if estimate is None else round(float(estimate), 2),
                "estimate_runs": runs or None,
            }
        )
    return out


def _item_view(st, row, draft: dict | None) -> dict:
    evaluated = item.evaluate(
        _chain(row), row["current_node_id"], draft["ops"] if draft else [], st.library
    )
    cap = board.budget_cap(st, row)
    return {
        "ops": evaluated.ops,
        "problems": evaluated.problems,
        "checks": {
            "budget": {"spent_usd": cap["spent_usd"], "cap_usd": cap["cap_usd"]},
            "added": _added_checks(st, evaluated),
        },
        "nodes": [items.node_view(n) for n in evaluated.chain.chain.nodes],
        "base_seq": draft["base_seq"] if draft else None,
        "updated_at": draft["updated_at"] if draft else None,
    }


@api_router.get("/work-items/{wid}/draft")
async def get_item_draft(wid: str, request: Request):
    """The item's draft, each op marked `passed` or not, with the chain the
    ops not passed make; with no draft, `ops: []` and the item's own chain."""
    st = request.app.state
    row = _item_row(st, request, wid)
    return _item_view(st, row, st.db.read(lambda c: store.get_item(c, wid)))


@api_router.put("/work-items/{wid}/draft")
async def put_item_draft(wid: str, body: Ops, request: Request):
    """Replace the whole op list (an empty one deletes the draft)."""
    st = request.app.state
    row = _item_row(st, request, wid)
    try:
        ops_ = item.OPS.dump_python(
            item.OPS.validate_python(body.ops), mode="json", exclude_none=True
        )
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    await st.db.write(lambda c: store.put_item(c, wid, ops_) if ops_ else store.delete_item(c, wid))
    return _item_view(st, row, st.db.read(lambda c: store.get_item(c, wid)))


@api_router.delete("/work-items/{wid}/draft", status_code=204)
async def discard_item_draft(wid: str, request: Request):
    st = request.app.state
    _item_row(st, request, wid)
    if not await st.db.write(lambda c: store.delete_item(c, wid)):
        raise HTTPException(404, "this work item has no draft")
    return Response(status_code=204)


@api_router.post("/work-items/{wid}/draft/apply")
async def apply_item_draft(wid: str, request: Request):
    """Apply the draft whole, in one write transaction: refused (409, with the
    op indexes) if the item has moved past any op since, 422 on any problem.
    Otherwise the revised chain replaces the item's (`chain_revised`, `source:
    "draft"`), each `skip` is recorded as `scope_skipped`, and the draft goes."""
    st = request.app.state
    _item_row(st, request, wid)
    library = st.library

    def apply(c) -> tuple[int, dict | None]:
        row = c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        if row["status"] in ENDED:
            return 409, {"detail": f"work item is {row['status']}; its chain does not run again"}
        draft = store.get_item(c, wid)
        if draft is None:
            return 404, {"detail": "this work item has no draft"}
        chain = _chain(row)
        evaluated = item.evaluate(chain, row["current_node_id"], draft["ops"], library)
        if evaluated.passed:
            return 409, {
                "detail": "the item has moved past some of this draft's ops; remove them or "
                "move them after the current node",
                "passed": evaluated.passed,
            }
        if evaluated.problems:
            return 422, {
                "detail": f"{len(evaluated.problems)} problem(s) to fix before applying",
                "problems": evaluated.problems,
            }
        if any(op["op"] != "skip" for op in draft["ops"]):
            payload = {
                "gate": None,
                "changes": draft["ops"],
                "diff": revision.diff(chain.chain, evaluated.chain.chain),
                "source": "draft",
            }
            items.revise_chain(
                c,
                wid,
                evaluated.chain.to_json(),
                payload,
                seen=(row["materialized_chain"], row["run_chain"]),
            )
        for path in evaluated.skips:
            items.skip_scope(c, wid, path, item.EVIDENCE)
        store.delete_item(c, wid)
        return 200, None

    status, refusal = await st.db.write(apply)
    if refusal is not None:
        return JSONResponse(status_code=status, content=refusal)
    return _item_view(st, deps._work_item_row(st, wid), None)
