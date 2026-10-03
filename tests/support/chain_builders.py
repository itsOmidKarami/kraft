"""`v1_task` and `v1_node`: authored V1 task and node mappings, with keyword
overrides. A chain is the list of nodes (`support.harness.v1_chain` takes it).
Import them from `support.harness`, which re-exports them beside the rest of
the `v1_*` family; they live here only to keep that file under its line budget."""

from __future__ import annotations

#: What a task of each kind needs to validate when the caller does not say:
#: an agent goes on the `fake` harness, which `seed_v1_library` neuters.
_V1_TASK_DEFAULTS: dict[str, dict[str, str]] = {
    "subprocess": {"command": "true"},
    "agent": {"harness": "fake", "prompt": "Do it."},
    "forge": {"target": "mr.merge"},
}


def v1_task(id: str = "impl", **fields) -> dict:
    """One authored V1 task mapping: a `subprocess` running `true` unless
    `fields` say otherwise. `kind=` picks another kind and that kind's
    defaults (`_V1_TASK_DEFAULTS`; a `builtin` has none, its `handler=` is the
    caller's). Every other key in `fields` is set as given, over a default.

        v1_task()                                   # impl: subprocess `true`
        v1_task("check", command="make test")
        v1_task("implement", kind="agent", harness="claude", model="opus")
    """
    kind = fields.pop("kind", "subprocess")
    return {"id": id, "kind": kind, **_V1_TASK_DEFAULTS.get(kind, {}), **fields}


def v1_node(id: str = "build", kind: str = "exec", *, tasks=None, **fields) -> dict:
    """One authored V1 node mapping. An `exec` node runs `tasks`, or one
    `v1_task()` when the caller gives neither `tasks` nor `steps=`; a `gate`
    gets no tasks. Every other key in `fields` (`steps`, `policy`,
    `fix_loop`, `artifact`, ...) is set as given.

        v1_node()                                   # build: one task, impl
        v1_node("verify", tasks=[v1_task("check")], fix_loop={...})
        v1_node("review", "gate", artifact="work_brief")
    """
    node: dict = {"id": id, "kind": kind}
    if tasks is not None:
        node["tasks"] = list(tasks)
    elif kind == "exec" and "steps" not in fields:
        node["tasks"] = [v1_task()]
    return {**node, **fields}
