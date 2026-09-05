from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from kraft import steering as _steering

_VALID_KINDS = {"builtin", "agent", "subprocess"}
# Config files that share the templates directory but are not chain templates.
# One definition: `load_templates` skips them, and the registry save copies the
# templates around them. Without this, every settings file the UI writes would be
# read as a malformed template and show up as degraded health.
CONFIG_FILES = frozenset(
    {"registry.yaml", "policy.yaml", "repos.yaml", "access.yaml", "intake.yaml"}
)
# The complete gate set. Public because the API validates approve/reject against it
# and the chain-review skill documents it — a second copy is how those drift apart.
GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}
#: What an intake attachment stands in for (Kraft-dgh). Keyed on the gate rather
#: than the node id: gate names are a validated closed vocabulary, node ids are
#: free text a custom template chooses.
ATTACHMENT_GATES = {"spec": "spec_approval", "plan": "plan_approval"}


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    hooks: dict


@dataclass(frozen=True)
class Template:
    id: str
    nodes: list


@dataclass(frozen=True)
class TemplateSet:
    valid: dict
    invalid: dict


def _agent_profiles() -> dict:
    # Function-local: a config loader that imports the adapter layer at module
    # scope invites a cycle later, even though there is none today.
    from kraft.adapters.agent import PROFILES

    return PROFILES


def load_registry(path: str | Path, *, steering_dir: Path | None = None) -> Registry:
    path = Path(path)
    steering_dir = steering_dir if steering_dir is not None else path.parent / "steering"
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise RegistryError(f"{path.name}: expected a top-level 'hooks' mapping")
    for hook, binding in data["hooks"].items():
        if not isinstance(binding, dict) or "kind" not in binding:
            raise RegistryError(f"{path.name}: hook {hook!r} is missing 'kind'")
        kind = binding["kind"]
        if kind not in _VALID_KINDS:
            raise RegistryError(f"{path.name}: hook {hook!r} has unknown kind {kind!r}")
        if kind == "builtin" and not isinstance(binding.get("handler"), str):
            raise RegistryError(f"{path.name}: builtin hook {hook!r} needs a string 'handler'")
        if kind == "agent" and not isinstance(binding.get("command"), str):
            raise RegistryError(f"{path.name}: agent hook {hook!r} needs a string 'command'")
        if kind == "subprocess" and not (
            isinstance(binding.get("command"), list)
            and all(isinstance(x, str) for x in binding["command"])
        ):
            raise RegistryError(
                f"{path.name}: subprocess hook {hook!r} needs a list-of-strings 'command'"
            )

        agent_only = ("profile", "model", "deny_tools", "steering")
        if kind == "agent":
            profiles = _agent_profiles()
            profile = binding.get("profile", "claude")
            if profile not in profiles:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} has unknown profile {profile!r}; "
                    f"known: {sorted(profiles)}"
                )
            if binding.get("model") is not None and not isinstance(binding["model"], str):
                raise RegistryError(f"{path.name}: hook {hook!r} 'model' must be a string")
            for key in ("deny_tools", "steering"):
                v = binding.get(key, [])
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} {key!r} must be a list of strings"
                    )
            try:
                _steering.validate(steering_dir, binding.get("steering", []), where=path.name)
            except _steering.SteeringError as exc:
                raise RegistryError(str(exc)) from exc
        else:
            for key in agent_only:
                if key in binding:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} is kind {kind!r}; {key!r} applies "
                        "only to an agent hook"
                    )

        # Last, so the agent-only keys keep their own sharper message above: a
        # key nobody reads is a setting that silently does nothing — a
        # `deny_tool:` typo denies no tool and fails nowhere, the same failure
        # an unknown *profile* is already rejected for.
        known = {"kind", "handler"} if kind == "builtin" else {"kind", "command"}
        # `interactive` is UI-facing rather than dispatch-facing (02 §13): the
        # registry round-trips it through Settings today, ahead of the screen
        # that will read it.
        known |= {"interactive"}
        if kind == "agent":
            known |= set(agent_only)
        if "interactive" in binding and not isinstance(binding["interactive"], bool):
            raise RegistryError(f"{path.name}: hook {hook!r} 'interactive' must be a boolean")
        unknown = sorted(set(binding) - known)
        if unknown:
            raise RegistryError(
                f"{path.name}: hook {hook!r} has unknown key(s) {unknown}; "
                f"a {kind} hook takes {sorted(known)}"
            )
    return Registry(hooks=data["hooks"])


def load_templates(dir: str | Path, registry: Registry) -> TemplateSet:
    valid: dict[str, Template] = {}
    invalid: dict[str, str] = {}

    for path in sorted(Path(dir).glob("*.yaml")):
        if path.name in CONFIG_FILES:
            continue
        stem = path.stem
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            invalid[stem] = f"{path.name}: YAML parse error: {exc}"
            continue

        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            invalid[stem] = f"{path.name}: missing a string 'id'"
            continue
        tid = data["id"]
        if tid in valid or tid in invalid:
            invalid[stem] = f"{path.name}: duplicate template id {tid!r}"
            continue

        nodes = data.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            invalid[tid] = f"template {tid!r}: 'nodes' must be a non-empty list"
            continue
        if not all(
            isinstance(n, dict)
            and isinstance(n.get("id"), str)
            and isinstance(n.get("tasks"), list)
            and all(isinstance(t, str) for t in n["tasks"])
            for n in nodes
        ):
            invalid[tid] = (
                f"template {tid!r}: each node needs a string 'id' and a list-of-strings 'tasks'"
            )
            continue

        bad_gates = sorted(
            {
                n["gate_after"]
                for n in nodes
                if n.get("gate_after") is not None and n["gate_after"] not in GATE_NAMES
            }
        )
        if bad_gates:
            invalid[tid] = f"template {tid!r}: unknown gate_after value(s) {bad_gates}"
            continue

        bad_loop = next(
            (
                n["id"]
                for n in nodes
                if "fix_loop" in n
                and n["fix_loop"] is not None
                and not (isinstance(n["fix_loop"], str) and n["fix_loop"])
            ),
            None,
        )
        if bad_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_loop!r} 'fix_loop' must be a non-empty string or null"
            )
            continue

        empty_loop = next((n["id"] for n in nodes if n.get("fix_loop") and not n["tasks"]), None)
        if empty_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {empty_loop!r} has 'fix_loop' but no tasks to measure"
            )
            continue

        unknown = sorted({t for n in nodes for t in n["tasks"] if t not in registry.hooks})
        if unknown:
            invalid[tid] = f"template {tid!r}: hook(s) {unknown} are not in the registry"
            continue

        valid[tid] = Template(id=tid, nodes=nodes)

    return TemplateSet(valid=valid, invalid=invalid)


def materialize(template: Template, *, satisfied_gates: frozenset[str] = frozenset()) -> dict:
    """The template's nodes, minus any whose gate an intake attachment already
    satisfies — the item genuinely has no spec node, rather than one skipped at
    runtime (Kraft-dgh)."""
    return {
        "template_id": template.id,
        "nodes": [
            {
                "id": n["id"],
                "tasks": list(n["tasks"]),
                "gate_after": n.get("gate_after"),
                "fix_loop": n.get("fix_loop"),
            }
            for n in template.nodes
            if n.get("gate_after") not in satisfied_gates
        ],
    }
