"""Draft-op test helpers: a server state over a templates dir, and one batch
of ops applied to a published chain with the resolve result after it.

    st = draft_state(templates_dir, database)
    r = apply_ops(st, ("add_node", {"at": 1, "id": "lint", "kind": "gate"}))
    assert r.order[1] == "lint"
    assert "already taken" in refused(st, ("add_node", {"at": 0, "id": "spec", "kind": "gate"}))
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kraft.drafts import ops, resolve
from kraft.templates.library import TemplateLibrary


def draft_state(templates_dir, database) -> SimpleNamespace:
    """What the ops and the resolve read off the server's state."""
    return SimpleNamespace(
        templates_dir=templates_dir,
        library=TemplateLibrary.from_yaml_dir(templates_dir),
        instance_policy=None,
        skills_dir=None,
        db=database,
    )


def apply_ops(st, *batch, key="default"):
    """The batch applied to the published chain, and the resolve result after it."""
    name = f"chains/{key}.yaml"
    published = resolve.published(st.templates_dir, [name])
    draft = ops.Draft(
        st, key, {n: t for n, t in published.items() if t is not None}, exists=bool(published[name])
    )
    answers = ops.apply(draft, [{"op": op, **fields} for op, fields in batch])
    files = draft.finish()
    result = resolve.resolve(st, "chains", key, files, published)
    model = result["model"][resolve.chain_file(key, files)]
    # An extending chain's file holds no `nodes`.
    nodes = (model or {}).get("nodes")
    return SimpleNamespace(
        answers=[a.get("result") for a in answers],
        files=files,
        model=model,
        text=files[name],
        nodes={n["id"]: n for n in nodes} if nodes is not None else None,
        order=[n["id"] for n in nodes] if nodes is not None else None,
        paths=(result["resolved"] or {}).get("task_paths"),
        resolved_order=[n["id"] for n in (result["resolved"] or {}).get("nodes", [])],
        problems=[(p["path"], p["field"], p["message"]) for p in result["problems"]],
        result=result,
    )


def refused(st, *batch, key="default") -> str:
    """The message of the `OpError` the batch raises."""
    with pytest.raises(ops.OpError) as exc:
        apply_ops(st, *batch, key=key)
    return str(exc.value)
