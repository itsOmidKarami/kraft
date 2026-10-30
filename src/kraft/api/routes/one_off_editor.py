"""The editor behind a one-off chain: the Templates screen's ops applied to a
chain file's text, with nothing stored."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraft import store
from kraft.api import api_router, deps
from kraft.api.routes import drafts
from kraft.drafts import ops
from kraft.drafts import store as draft_store
from kraft.templates import one_off
from kraft.templates.library import LIBRARY_FILE, TemplateLibraryError
from kraft.templates.models import PATH_SEPARATOR

#: Ops that write beyond the one chain being edited.
_LIBRARY_OPS = frozenset(
    {"new_chain", "delete_chain", "move_to_library", "add_component", "copy_component"}
)
_REFUSED = "a one-off chain can't change the library or other chains"


class OneOffOps(BaseModel):
    text: str
    ops: list[dict] = []
    preview: bool = False


def _custom_nodes(parts, nodes: list[dict]) -> list[str]:
    """The nodes an added node or an override with custom content names; every
    node when the whole chain is custom (it extends no library chain)."""
    if any(part.path == "" for part in parts):
        return [n["id"] for n in nodes]
    named = []
    for part in parts:
        if part.path.startswith("add["):
            named.append(part.value.get("id"))
        elif part.path.startswith("overrides."):
            named.append(part.path.removeprefix("overrides.").split(PATH_SEPARATOR)[0])
    return [n for n in dict.fromkeys(named) if isinstance(n, str)]


def _resolve(st, authored: dict) -> tuple[list[dict], list[dict], list[str]]:
    """The one-off chain's nodes in the board's `ChainNode` shape, its custom
    parts and the nodes they name; all empty while it does not resolve."""
    library = deps.library_or_503(st)
    try:
        candidate, id = library.with_chain(
            Path("<unsaved>") / "chains" / f"{authored['id']}.yaml", authored
        )
        resolved = candidate.resolve_chain(id)
        extends = authored.get("extends")
        parent = candidate.resolve_chain(extends) if isinstance(extends, str) else None
        parts = one_off.custom_parts(authored, parent, resolved)
    except (TemplateLibraryError, ValueError, TypeError):
        return [], [], []
    nodes = [store.node_view(n) for n in resolved.nodes]
    return nodes, [p.view() for p in parts], _custom_nodes(parts, nodes)


def _parse(text: str) -> dict | None:
    """The text as a plain mapping; None for a YAML syntax error, which the
    editor shows instead of refusing. Anything else wrong is a 422."""
    try:
        data = deps.authored_mapping(text, "chain")
    except HTTPException as exc:
        if isinstance(exc.__cause__, yaml.YAMLError):
            return None
        raise
    deps.plain_one_off_or_422(data)
    return data


@api_router.post("/one-off-chain/ops")
async def one_off_chain_ops(body: OneOffOps, request: Request):
    """Apply editor `ops` to the text of a chain used by one work item, and
    answer the draft view of the result with the chain's digest, its resolved
    nodes, its custom parts and the nodes they name. Nothing is stored. A text
    that is not YAML is answered with its error and no ops applied."""
    st = request.app.state
    if len(body.text) > one_off.MAX_TEXT:
        raise HTTPException(422, "a one-off chain is at most 256 KiB")
    parsed = _parse(body.text)
    authored, digest = (None, None) if parsed is None else one_off.prepare(parsed)
    key = authored["id"] if authored is not None else one_off.DEFAULT_ID
    if not isinstance(key, str) or not draft_store.AREAS["chains"].valid(key):
        raise HTTPException(
            422,
            "a one-off chain's id is a lowercase letter, then lowercase letters, digits, - or _",
        )
    if parsed is not None:
        deps.own_id_or_422(deps.library_or_503(st), key)
    files = {f"chains/{key}.yaml": body.text}
    answers: list[dict] = []
    if parsed is not None:
        working = ops.Draft(st, key, files, exists=True)
        if any(
            isinstance(op, dict)
            and (
                op.get("op") in _LIBRARY_OPS or (op.get("op") == "rename" and op.get("path") == "")
            )
            for op in body.ops
        ):
            raise HTTPException(422, _REFUSED)
        try:
            answers = ops.apply(working, body.ops, ops.OPS)
            files = working.finish()
        except ops.OpError as exc:
            return JSONResponse(
                status_code=422, content={"detail": str(exc), "op": exc.index, **exc.extra}
            )
        # Defence in depth: the refusals above name every op that writes the library.
        if LIBRARY_FILE in working.dirty:
            raise HTTPException(422, _REFUSED)
    files_now, _, result = drafts._state(st, "chains", key, {"files": files, "serialized": []}, [])
    text = files_now.get(f"chains/{key}.yaml", body.text)
    nodes, custom, custom_nodes = [], [], []
    if parsed is not None:
        try:
            after = _parse(text)
        except HTTPException:
            after = None
        if after is not None:
            authored, digest = one_off.prepare(after)
            nodes, custom, custom_nodes = _resolve(st, authored)
    return {
        "area": "chains",
        "key": key,
        "draft": True,
        "files": files_now,
        "base": {},
        "published": {},
        "updated_at": None,
        "result": result,
        "ops": answers,
        "text": text,
        "digest": digest,
        "custom": custom,
        "custom_nodes": custom_nodes,
        "nodes": nodes,
    }
