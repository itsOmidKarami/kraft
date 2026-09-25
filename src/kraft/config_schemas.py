"""JSON Schemas of Kraft's config files, from the pydantic models that load
them, for editors (`vscode/schemas/`, VS Code extension spec "Config files").

Chains and `library.yaml` are exported in **authored** form. A chain is
validated after `extends:` expansion (`TemplateLibrary.resolve_chain`), so the
file an author writes may leave out anything a parent supplies: `required` is
dropped, a discriminated union becomes `anyOf` (its tag may be inherited), and
every extendable component may say `extends:`. `extra="forbid"` survives as
`additionalProperties: false`, which is what catches a typo. What only
resolution can know -- an unknown parent, a cycle, a skill, a policy ceiling --
is `POST /templates/check`'s, not a schema's.

A schema is never stricter than its loader: a file Kraft loads must validate.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from kraft import config as config_mod
from kraft import policy as policy_mod
from kraft.templates import models as models_mod
from kraft.templates.environment import AgentProfileInput, HarnessProfileInput

DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_DIR = Path("vscode/schemas")

#: Components a chain or the library may declare with `extends:`
#: (`Namespace.NODES`/`STEPS`/`TASKS`).
EXTENDABLE = frozenset(
    {"ExecNode", "GateNode", "Step", "AgentTask", "BuiltinTask", "SubprocessTask", "ForgeTask"}
)
_TASKS = ("AgentTask", "BuiltinTask", "SubprocessTask", "ForgeTask")
_NODES = ("ExecNode", "GateNode")

EXTENDS = {
    "type": "string",
    "description": "A library component of the same kind to inherit from; this one's own keys win.",
}


def authored(schema: dict) -> dict:
    out = copy.deepcopy(schema)

    def walk(node: object) -> None:
        if isinstance(node, dict):
            # Only schema keywords: a *property* may be named `required`.
            if isinstance(node.get("required"), list):
                del node["required"]
            if isinstance(node.get("discriminator"), dict):
                del node["discriminator"]
            if "oneOf" in node:
                node["anyOf"] = node.pop("oneOf")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(out)
    for name, definition in out.get("$defs", {}).items():
        if name in EXTENDABLE:
            definition.setdefault("properties", {})["extends"] = EXTENDS
    return out


def _ref(name: str) -> dict:
    return {"$ref": f"#/$defs/{name}"}


def _model(model: type[BaseModel], title: str) -> dict:
    return {"$schema": DRAFT, **model.model_json_schema(), "title": title}


def _file(title: str, properties: dict, models: list[type[BaseModel]], *, closed: bool) -> dict:
    defs: dict = {}
    for model in models:
        schema = model.model_json_schema()
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
    return {
        "$schema": DRAFT,
        "title": title,
        "type": "object",
        "properties": properties,
        "additionalProperties": not closed,
        "$defs": defs,
    }


def chain_schema() -> dict:
    return authored(_model(models_mod.Chain, "Kraft chain template"))


def library_schema() -> dict:
    chain = chain_schema()
    defs = chain["$defs"]
    steering = models_mod.SteeringProfile.model_json_schema()
    defs.update(steering.pop("$defs", {}))
    defs["SteeringProfile"] = steering
    section = lambda item: {"type": "object", "additionalProperties": item}  # noqa: E731
    return {
        "$schema": DRAFT,
        "title": "Kraft template library",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "steering": section(_ref("SteeringProfile")),
            "tasks": section({"anyOf": [_ref(n) for n in _TASKS]}),
            "steps": section(_ref("Step")),
            "nodes": section({"anyOf": [_ref(n) for n in _NODES]}),
        },
        "$defs": defs,
    }


def harnesses_schema() -> dict:
    section = lambda name: {"type": "object", "additionalProperties": _ref(name)}  # noqa: E731
    return _file(
        "Kraft harness and agent profiles",
        {"harnesses": section("HarnessProfileInput"), "profiles": section("AgentProfileInput")},
        [HarnessProfileInput, AgentProfileInput],
        closed=False,
    )


def repos_schema() -> dict:
    return _file(
        "Kraft connected repositories",
        {"repos": {"type": "array", "items": _ref("RepoEntry")}},
        [config_mod.RepoEntry],
        closed=False,
    )


SCHEMAS: dict[str, Callable[[], dict]] = {
    "chain.schema.json": chain_schema,
    "library.schema.json": library_schema,
    "policy.schema.json": lambda: _model(policy_mod.PolicyInput, "Kraft policy"),
    "harnesses.schema.json": harnesses_schema,
    "repos.schema.json": repos_schema,
    "intake.schema.json": lambda: _model(config_mod.Intake, "Kraft intake"),
    "access.schema.json": lambda: _model(config_mod.Access, "Kraft access"),
    "theme.schema.json": lambda: _model(config_mod.Theme, "Kraft theme"),
    "notify.schema.json": lambda: _model(config_mod.Notify, "Kraft notifications"),
}


def render(name: str) -> str:
    return json.dumps(SCHEMAS[name](), indent=2, sort_keys=True) + "\n"


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in sorted(SCHEMAS):
        path = out_dir / name
        path.write_text(render(name))
        written.append(path)
    return written
