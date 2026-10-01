"""A template file as an author wrote it: load it with the `tasks:` shorthand
normalised to its `main` step, address a component by canonical path, and
write it back (backend req 10).

Ops edit the mapping `load` returns; `dump` writes it. Keys keep the order the
file gave them: `put` and the inherited-container copy of `at` place a key they
add right after the last present key that precedes it in the canonical order
below (the prototype's `NK`, `TK`, `SK`, `SH`), since `dump` alone cannot tell
an added key from an authored one. Comments are not written (R8).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence

import yaml

from kraft.policy import TemplatePolicyOverride
from kraft.templates.library import Namespace, _merge
from kraft.templates.models import AUTO_REVIEW_SEGMENT, JUDGE_SEGMENT, MAIN_STEP, PATH_SEPARATOR

CHAIN = "chain"
LIBRARY = "library"

CHAIN_KEYS = ("id", "description", "policy", "nodes")
LIBRARY_KEYS = ("steering", "tasks", "steps", "nodes")
NODE_KEYS = (
    "id", "kind", "extends", "icon", "message", "artifact", "artifact_required", "reject_to",
    "timeout", "chain_finalized", "tasks", "steps", "on_failure", "fix_loop", "escalation",
    "on_base_changed", "auto_review", "policy", "skippable", "read_only",
)  # fmt: skip
TASK_KEYS = (
    "id", "kind", "extends", "icon", "harness", "ref", "command", "target", "prompt", "skill",
    "produces", "profile", "model", "effort", "inputs", "fallback", "steering", "scope",
    "execution", "wait", "policy", "skippable", "on_failure",
)  # fmt: skip
STEP_KEYS = ("id", "extends", "icon", "tasks", "on_failure", "policy", "skippable", "read_only")
SHAPE_KEYS = ("tasks", "steps", JUDGE_SEGMENT, "max_attempts")
#: The order inside a nested mapping, by its key.
NESTED_KEYS = {
    "policy": tuple(TemplatePolicyOverride.model_fields),
    "on_base_changed": ("restart_from", "on_conflict"),
    "wait": ("polling",),
    "polling": ("initial_interval", "max_interval"),
}

#: Top-level collections written with a blank line between entries, as the
#: shipped files are.
_SPACED = frozenset(LIBRARY_KEYS)
_ORDER = {Namespace.NODES: NODE_KEYS, Namespace.STEPS: STEP_KEYS, Namespace.TASKS: TASK_KEYS}
_NAMESPACE = {keys: namespace for namespace, keys in _ORDER.items()}


def parse(text: str | None) -> object:
    """The file as written, `{}` for an empty one: what the checks validate.
    Raises `yaml.YAMLError`."""
    if text is None:
        return None
    data = yaml.safe_load(text)
    return {} if data is None else data


def load(text: str | None) -> object:
    """The file's mapping, every node's, handler's and fix loop's `tasks:` as
    one step `main` (`task-group-shorthand-resolves-to-one-step`). Raises
    `yaml.YAMLError`."""
    return normalise(parse(text))


def normalise(data: object) -> object:
    """`data` with the shorthand normalised in place, as `load` does."""
    if isinstance(data, dict):
        _walk(data, _normalise)
    return data


def dump(mapping: Mapping) -> str:
    """`mapping` as YAML: a lone `main` step holding only `id` and `tasks` back
    as `tasks:`, block style, two-space indents, a multi-line string as `|`."""
    data = copy.deepcopy(mapping)
    if not isinstance(data, dict):
        return _yaml(data)
    _walk(data, _collapse)
    parts = []
    for key, value in data.items():
        if key in _SPACED and isinstance(value, (list, dict)) and value:
            entries = (
                [_yaml([v]) for v in value]
                if isinstance(value, list)
                else [_yaml({k: v}) for k, v in value.items()]
            )
            parts.append(f"{key}:\n" + "\n".join(map(_indent, entries)))
        else:
            parts.append(_yaml({key: value}))
    return "\n".join(parts)


def at(
    mapping: Mapping,
    path: str,
    *,
    file: str = CHAIN,
    library: Mapping | None = None,
    write: bool = False,
) -> dict | None:
    """The authored mapping at canonical `path` (`ResolvedChain.task_paths`:
    `node.step.task`, `node.fix_loop.judge`, `node.escalation.<id>`,
    `gate.auto_review`, `node.on_base_changed.on_conflict.step`, …; `""` is the
    file). A library path starts `nodes.<name>`, `steps.<name>`, `tasks.<name>`
    or `steering.<name>`. None when the file has nothing there.

    With `write`, a container the component inherits through `extends` is first
    copied into `mapping` from `library`, the authored library mapping (the
    prototype's `own`); a library file inherits from itself."""
    found = _locate(mapping, path, file, library, write)
    return found[0] if found else None


def put(
    mapping: Mapping,
    path: str,
    field: str,
    value: object,
    *,
    file: str = CHAIN,
    library: Mapping | None = None,
) -> None:
    """Set `field` (dotted: `policy.max_attempts`) on the component at `path`,
    a key it adds placed by the canonical order. Raises `KeyError` when the
    file has no component there."""
    found = _locate(mapping, path, file, library, True)
    if found is None:
        raise KeyError(path)
    obj, order = found
    *outer, last = field.split(PATH_SEPARATOR)
    for key in outer:
        if not isinstance(obj.get(key), dict):
            _insert(obj, key, {}, order)
        obj, order = obj[key], NESTED_KEYS.get(key, ())
    _insert(obj, last, value, order)


def own(
    mapping: Mapping,
    path: str,
    key: str,
    *,
    file: str = CHAIN,
    library: Mapping | None = None,
) -> object:
    """`key` of the component at `path`, copied into `mapping` first when the
    component inherits it (the prototype's `own`). Raises `KeyError` when the
    file has no component there."""
    walk = _Walk(_parents(mapping, file, library), True)
    found = walk.file(mapping, path, file)
    if found is None:
        raise KeyError(path)
    obj, order = found
    return walk.child(obj, key, _NAMESPACE.get(tuple(order)))


def inherited(library: Mapping, namespace: Namespace, name: object) -> Mapping:
    """Library component `name` merged onto its own `extends` parents; `{}`
    when the library has none."""
    return _Walk(library, False)._resolved(namespace, name, ())


def collapses(container: Mapping) -> bool:
    """Whether `dump` writes this container's steps as the `tasks:` shorthand."""
    steps = container.get("steps")
    return (
        isinstance(steps, list)
        and len(steps) == 1
        and isinstance(steps[0], dict)
        and steps[0].get("id") == MAIN_STEP
        and set(steps[0]) == {"id", "tasks"}
        and "tasks" not in container
    )


# ── the shorthand ──


def _rekey(obj: dict, old: str, new: str, value: object) -> None:
    """Replace `old` with `new` in `old`'s place."""
    items = [(new, value) if k == old else (k, v) for k, v in obj.items()]
    obj.clear()
    obj.update(items)


def _normalise(container: dict) -> None:
    if isinstance(container.get("tasks"), list) and "steps" not in container:
        _rekey(container, "tasks", "steps", [{"id": MAIN_STEP, "tasks": container["tasks"]}])


def _collapse(container: dict) -> None:
    if collapses(container):
        _rekey(container, "steps", "tasks", container["steps"][0]["tasks"])


def _dicts(value: object) -> list[dict]:
    values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return [v for v in values if isinstance(v, dict)]


def _walk(data: dict, fn: Callable[[dict], None]) -> None:
    """Apply `fn` to every container that may take the shorthand: each node
    (a chain's list, a library's section), and each handler and fix loop."""

    def shape(c: dict) -> None:
        fn(c)
        for t in _dicts(c.get("tasks")):
            task(t)
        for s in _dicts(c.get("steps")):
            step(s)
        if isinstance(c.get(JUDGE_SEGMENT), dict):
            task(c[JUDGE_SEGMENT])

    def step(s: dict) -> None:
        for t in _dicts(s.get("tasks")):
            task(t)
        if isinstance(s.get("on_failure"), dict):
            shape(s["on_failure"])

    def task(t: dict) -> None:
        if isinstance(t.get("on_failure"), dict):
            shape(t["on_failure"])

    def node(n: dict) -> None:
        shape(n)
        for key in ("on_failure", "fix_loop"):
            if isinstance(n.get(key), dict):
                shape(n[key])
        for key in ("escalation", AUTO_REVIEW_SEGMENT):
            if isinstance(n.get(key), dict):
                task(n[key])
        base_change = n.get("on_base_changed")
        if isinstance(base_change, dict) and isinstance(base_change.get("on_conflict"), dict):
            shape(base_change["on_conflict"])

    for n in _dicts(data.get("nodes")):
        node(n)
    # A library's sections; a chain has none.
    if isinstance(data.get("steps"), dict):
        for s in _dicts(data["steps"]):
            step(s)
    if isinstance(data.get("tasks"), dict):
        for t in _dicts(data["tasks"]):
            task(t)


# ── addressing ──

_Found = tuple[dict, Sequence[str]] | None


def _insert(obj: dict, key: str, value: object, order: Sequence[str]) -> None:
    """Set `key`; a new key goes right after the last present key that precedes
    it in `order`, else first, and after everything when `order` lacks it."""
    if key in obj or key not in order:
        obj[key] = value
        return
    before = order[: order.index(key)]
    present = [k for k in obj if k in before]
    after = max(present, key=before.index) if present else None
    items = list(obj.items())
    at = next(i for i, (k, _) in enumerate(items) if k == after) + 1 if after else 0
    items.insert(at, (key, value))
    obj.clear()
    obj.update(items)


def _by_id(value: object, id: str) -> dict | None:
    return next((x for x in _dicts(value) if x.get("id") == id), None)


def _parents(mapping, file: str, library: Mapping | None) -> Mapping | None:
    return library if library is not None or file == CHAIN else mapping


def _locate(mapping, path: str, file: str, library: Mapping | None, write: bool) -> _Found:
    return _Walk(_parents(mapping, file, library), write).file(mapping, path, file)


class _Walk:
    def __init__(self, library: Mapping | None, write: bool) -> None:
        self.library = library or {}
        self.write = write

    def file(self, mapping: Mapping, path: str, file: str) -> _Found:
        if not isinstance(mapping, dict):
            return None
        segs = path.split(PATH_SEPARATOR) if path else []
        if not segs:
            return mapping, CHAIN_KEYS if file == CHAIN else LIBRARY_KEYS
        if file == CHAIN:
            return self.node(_by_id(mapping.get("nodes"), segs[0]), segs[1:])
        section, name, rest = segs[0], segs[1] if len(segs) > 1 else None, segs[2:]
        entries = mapping.get(section)
        body = entries.get(name) if isinstance(entries, dict) and name else None
        if section == Namespace.NODES:
            return self.node(body, rest)
        if section == Namespace.STEPS:
            return self.step(body, rest)
        if section == Namespace.TASKS:
            return self.task(body, rest)
        if section == Namespace.STEERING and isinstance(body, dict) and not rest:
            return body, ("instructions",)
        return None

    def child(self, obj: dict, key: str, namespace: Namespace | None) -> object:
        """`obj[key]`; when writing, an inherited one copied in first."""
        if key in obj or not (self.write and namespace and obj.get("extends")):
            return obj.get(key)
        inherited = self._resolved(namespace, obj["extends"], ()).get(key)
        if inherited is None:
            return None
        _insert(obj, key, copy.deepcopy(inherited), _ORDER[namespace])
        return obj[key]

    def _resolved(self, namespace: Namespace, name: object, seen: tuple) -> Mapping:
        """Library component `name` merged onto its own `extends` parents."""
        section = self.library.get(namespace.value)
        body = section.get(name) if isinstance(section, dict) and isinstance(name, str) else None
        if not isinstance(body, dict) or name in seen:
            return {}
        own = {k: v for k, v in body.items() if k != "extends"}
        if body.get("extends") is None:
            return own
        merged = _merge(self._resolved(namespace, body["extends"], (*seen, name)), own)
        return merged if isinstance(merged, Mapping) else {}

    def node(self, n: object, segs: list[str]) -> _Found:
        if not isinstance(n, dict):
            return None
        if not segs:
            return n, NODE_KEYS
        head, rest = segs[0], segs[1:]
        if head in ("on_failure", "fix_loop"):
            return self.shape(self.child(n, head, Namespace.NODES), rest)
        if head == "escalation":
            task = self.child(n, head, Namespace.NODES)
            if rest and isinstance(task, dict) and task.get("id") != rest[0]:
                return None
            return self.task(task, rest[1:])
        if head == AUTO_REVIEW_SEGMENT:
            return self.task(self.child(n, head, Namespace.NODES), rest)
        if head == "on_base_changed":
            base_change = self.child(n, head, Namespace.NODES)
            if not (rest and rest[0] == "on_conflict" and isinstance(base_change, dict)):
                return None
            return self.shape(base_change.get("on_conflict"), rest[1:])
        return self.shape(n, segs, Namespace.NODES)

    def shape(self, c: object, segs: list[str], namespace: Namespace | None = None) -> _Found:
        if not isinstance(c, dict):
            return None
        if not segs:
            return c, SHAPE_KEYS
        if segs[0] == JUDGE_SEGMENT:
            return self.task(c.get(JUDGE_SEGMENT), segs[1:])
        return self.step(_by_id(self.child(c, "steps", namespace), segs[0]), segs[1:])

    def step(self, s: object, segs: list[str]) -> _Found:
        if not isinstance(s, dict):
            return None
        if not segs:
            return s, STEP_KEYS
        if segs[0] == "on_failure":
            return self.shape(self.child(s, "on_failure", Namespace.STEPS), segs[1:])
        return self.task(_by_id(self.child(s, "tasks", Namespace.STEPS), segs[0]), segs[1:])

    def task(self, t: object, segs: list[str]) -> _Found:
        if not isinstance(t, dict):
            return None
        if not segs:
            return t, TASK_KEYS
        if segs[0] == "on_failure":
            return self.shape(self.child(t, "on_failure", Namespace.TASKS), segs[1:])
        return None


# ── the writer ──


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        # A sequence indented under its key, as the shipped files write it.
        return super().increase_indent(flow, False)


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    # A long line folds (`>-`), as the shipped files write a long prompt.
    style = "|" if "\n" in value else ">" if len(value) > _WIDTH else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


def _list(dumper: yaml.SafeDumper, value: list) -> yaml.Node:
    # `steering: [project-standards]`: a list of scalars stays on one line.
    flow = bool(value) and not any(isinstance(v, (dict, list)) for v in value)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=flow)


_Dumper.add_representer(str, _str)
_Dumper.add_representer(list, _list)


_WIDTH = 80


def _yaml(data: object) -> str:
    return yaml.dump(
        data,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=_WIDTH,
    )


def _indent(text: str) -> str:
    return "".join(f"  {line}" if line.strip() else line for line in text.splitlines(True))
