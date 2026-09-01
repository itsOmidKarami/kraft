from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_VALID_KINDS = {"builtin", "agent", "subprocess"}


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


def load_registry(path: str | Path) -> Registry:
    path = Path(path)
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
    return Registry(hooks=data["hooks"])


def load_templates(dir: str | Path, registry: Registry) -> TemplateSet:
    valid: dict[str, Template] = {}
    invalid: dict[str, str] = {}

    for path in sorted(Path(dir).glob("*.yaml")):
        if path.name == "registry.yaml":
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

        unknown = sorted({t for n in nodes for t in n["tasks"] if t not in registry.hooks})
        if unknown:
            invalid[tid] = f"template {tid!r}: hook(s) {unknown} are not in the registry"
            continue

        valid[tid] = Template(id=tid, nodes=nodes)

    return TemplateSet(valid=valid, invalid=invalid)


def materialize(template: Template) -> dict:
    return {
        "template_id": template.id,
        "nodes": [
            {
                "id": n["id"],
                "tasks": list(n["tasks"]),
                "gate_after": n.get("gate_after"),
            }
            for n in template.nodes
        ],
    }
