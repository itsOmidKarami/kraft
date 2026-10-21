"""Which local skill, prompt or command runs a task, by Kraft's own task names.

Stdlib only, like `kl.py`. A repo's choices live in `.kraft-lite/bindings.yaml`,
keyed by canonical task path, library component name, builtin ref or forge
target; `dispatch` turns a frozen chain task into the one instruction the walk
follows, and says which rule chose it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import kl_chains
import kl_yaml

BINDINGS = ".kraft-lite/bindings.yaml"

#: The sections a layer holds, in lookup order, and the keys the file may carry.
SECTIONS = ("tasks", "builtins", "forge")
SLICE = "slice"
KINDS = ("skill", "prompt", "subprocess", "skip")
ENTRY_KEYS = ("kind", "skill", "prompt", "command", "why")

BUILTIN_DEFAULTS: dict[str, dict] = {
    "kraft.verify_changed_test_scopes": {
        "kind": "prompt",
        "prompt": (
            "Run the repo's tests for the paths this change touches, plus its fast checks, and "
            "report the result."
        ),
    },
    "kraft.mr_rebase": {
        "kind": "prompt",
        "prompt": (
            "Rebase the working branch onto its base branch, resolve any conflicts, and report "
            "whether the base moved."
        ),
    },
}

FORGE_DEFAULTS: dict[str, dict] = {
    "mr.open_draft": {
        "kind": "prompt",
        "prompt": (
            "Open a draft merge request for the working branch on this repo's forge, or report "
            "the one already open."
        ),
    },
    "mr.sync": {
        "kind": "prompt",
        "prompt": (
            "Push the working branch so the merge request matches it, and report anything the "
            "forge rejected."
        ),
    },
    "mr.ci": {
        "kind": "prompt",
        "prompt": (
            "Report the merge request's CI status once; never block waiting for it to finish."
        ),
    },
    "mr.automated_review": {
        "kind": "prompt",
        "prompt": (
            "Report the findings of the merge request's automated review once; never block "
            "waiting for it."
        ),
    },
    "mr.mark_ready": {
        "kind": "prompt",
        "prompt": "Mark the draft merge request ready for review.",
    },
    "mr.external_approval": {
        "kind": "prompt",
        "prompt": (
            "Report whether the merge request has the approval it needs, once; never block "
            "waiting for it."
        ),
    },
    "mr.merge": {
        "kind": "prompt",
        "prompt": (
            "Merge the merge request once its checks and approvals allow it, and report the result."
        ),
    },
    "mr.post_merge_ci": {
        "kind": "prompt",
        "prompt": (
            "Report the CI status of the merged branch once; never block waiting for it to finish."
        ),
    },
}

#: What a child walking a slice runs unless `slice:` in the bindings says otherwise.
SLICE_DEFAULTS: dict[str, dict] = {
    "tasks": {
        "implementer": {
            "kind": "skill",
            "skill": "superpowers:subagent-driven-development",
            "prompt": (
                "Implement the slice's tasks with one subagent per task, each working test "
                "first. After each task run a spec-compliance review and then a code-quality "
                "review, with a capped fix round. Run independent tasks in parallel, each in "
                "its own isolated worktree. Set a model explicitly on every subagent. Stop "
                "after the last task's review: no final whole-branch review and no finishing "
                "or merge step, since on.review.local.run and open_mr are those."
            ),
        },
    },
    "builtins": {
        "kraft.verify_changed_test_scopes": {
            "kind": "prompt",
            "prompt": (
                "Run only the test paths this slice's tasks touch, and the repo's fast checks, "
                "as your slice instructions list them. Never the full suite; CI runs it. List "
                "the paths in the PR body."
            ),
        },
    },
    "forge": {
        "mr.automated_review": {
            "kind": "skip",
            "why": "the controller's PR review is this review",
        },
    },
}

SKILL_MISSING = " (skill missing)"


class BindingError(SystemExit):
    """A bindings file Lite cannot use, or a task it has no instruction for."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"kraft-lite: {detail}")
        self.detail = detail


# -- reading ----------------------------------------------------------------


def read_bindings(root: Path) -> dict:
    """The repo's `.kraft-lite/bindings.yaml`, checked; `{}` when it has none."""
    path = Path(root) / BINDINGS
    if not path.is_file():
        return {}
    data = kl_yaml.read(path)
    if data is None:
        return {}

    def refuse(where: str, why: str) -> BindingError:
        return BindingError(f"{BINDINGS}: {where + ': ' if where else ''}{why}")

    if not isinstance(data, Mapping):
        raise refuse("", "expected a mapping of sections")
    for key in data:
        if key not in (*SECTIONS, SLICE):
            raise refuse(str(key), f"unknown section (expected {', '.join((*SECTIONS, SLICE))})")
    checked: dict = {}
    for key in SECTIONS:
        if key in data:
            checked[key] = _section(data[key], key, refuse)
    if SLICE in data:
        layer = data[SLICE] or {}
        if not isinstance(layer, Mapping):
            raise refuse(SLICE, "expected a mapping of sections")
        inner: dict = {}
        for key in layer:
            if key not in SECTIONS:
                raise refuse(f"{SLICE}.{key}", f"unknown section (expected {', '.join(SECTIONS)})")
            inner[key] = _section(layer[key], f"{SLICE}.{key}", refuse)
        checked[SLICE] = inner
    return checked


def _section(section: object, where: str, refuse: Callable[[str, str], BindingError]) -> dict:
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise refuse(where, "expected a mapping of bindings")
    name = where.rsplit(".", 1)[-1]
    known = {"builtins": kl_chains.BUILTIN_REFS, "forge": kl_chains.FORGE_TARGETS}.get(name)
    for key, entry in section.items():
        here = f"{where}.{key}"
        if known is not None and key not in known:
            raise refuse(here, f"unknown {name} name (expected one of {', '.join(known)})")
        _entry(entry, here, refuse)
    return dict(section)


def _entry(entry: object, where: str, refuse: Callable[[str, str], BindingError]) -> None:
    if not isinstance(entry, Mapping):
        raise refuse(where, "expected a mapping with a kind")
    for key in entry:
        if key not in ENTRY_KEYS:
            raise refuse(f"{where}.{key}", f"unknown key (expected {', '.join(ENTRY_KEYS)})")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise refuse(f"{where}.kind", f"unknown kind {kind!r} (expected {', '.join(KINDS)})")

    def need(key: str) -> None:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise refuse(f"{where}.{key}", f"a {kind} binding needs a {key}")

    if kind == "skill":
        need("skill")
        need("prompt")
    elif kind == "prompt":
        need("prompt")
    elif kind == "subprocess":
        command = entry.get("command")
        if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
            raise refuse(
                f"{where}.command", "a subprocess binding needs a command: a list of arguments"
            )
    else:
        need("why")


# -- dispatch ---------------------------------------------------------------


def dispatch(
    task: dict,
    bindings: dict,
    installed: Callable[[str], bool],
    *,
    slice_layer: bool = False,
) -> dict:
    """What to do for `task`: the first matching rule, as a dispatch record.

    `installed` says whether a skill is available in this harness; a skill that
    is not falls back to its prompt. `task_kind`, `ref` and `target` are the
    task's own, whatever ran it: the walk keys Kraft's rules for builtin and
    forge tasks on them, never on `source`, which a path binding hides."""
    chosen = _choose(task, bindings, slice_layer)
    if chosen is None:
        chosen = _default(task, installed)
    entry, source = chosen
    kind = entry["kind"]
    skill, prompt = entry.get("skill"), entry.get("prompt")
    if kind == "skill" and not installed(skill):
        kind, skill, source = "prompt", None, source + SKILL_MISSING
    if kind in ("skill", "prompt"):
        prompt = "\n\n".join(filter(None, [prompt, *(task.get("steering") or ())])) or None
    return {
        "path": task["path"],
        "kind": kind,
        "skill": skill if kind == "skill" else None,
        "prompt": prompt if kind in ("skill", "prompt") else None,
        "command": list(entry["command"]) if kind == "subprocess" else None,
        "why": entry["why"] if kind == "skip" else None,
        "produces": task.get("produces"),
        "hints": {key: task.get(key) for key in ("profile", "model", "effort")},
        "source": source,
        "task_kind": task.get("kind"),
        "ref": task.get("ref"),
        "target": task.get("target"),
    }


def _choose(task: dict, bindings: Mapping, slice_layer: bool) -> tuple[dict, str] | None:
    layers = []
    if slice_layer:
        layers.append(("bindings:slice.", bindings.get(SLICE) or {}))
        layers.append(("slice-default:", SLICE_DEFAULTS))
    layers.append(("bindings:", bindings))
    for prefix, layer in layers:
        for section, key in _keys(task):
            entry = (layer.get(section) or {}).get(key)
            if entry is not None:
                return entry, f"{prefix}{section}.{key}"
    return None


def _keys(task: dict) -> list[tuple[str, str]]:
    keys = [("tasks", task["path"]), *(("tasks", name) for name in task.get("components") or ())]
    if task.get("kind") == "builtin" and task.get("ref"):
        keys.append(("builtins", task["ref"]))
    if task.get("kind") == "forge" and task.get("target"):
        keys.append(("forge", task["target"]))
    return keys


def _default(task: dict, installed: Callable[[str], bool]) -> tuple[dict, str]:
    kind = task.get("kind")
    if kind == "agent":
        skill = task.get("skill")
        if skill and not installed(skill):
            # Said in the source, as for a bound skill, so the walk can say it fell back.
            return {"kind": "prompt", **_fields(task)}, "default:agent" + SKILL_MISSING
        return {"kind": "skill" if skill else "prompt", **_fields(task)}, "default:agent"
    if kind == "subprocess":
        return {"kind": "subprocess", "command": task.get("command") or []}, "default:subprocess"
    table, section, name = (
        (BUILTIN_DEFAULTS, "builtins", task.get("ref"))
        if kind == "builtin"
        else (FORGE_DEFAULTS, "forge", task.get("target"))
    )
    if name not in table:
        raise BindingError(f"{task['path']}: no default for {kind} {name!r}")
    return table[name], f"default:{section}.{name}"


def _fields(task: dict) -> dict:
    return {key: task.get(key) for key in ("skill", "prompt")}
