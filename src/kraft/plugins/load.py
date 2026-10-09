"""An installed Kraft plugin as the template library and the agent-profile
table read it.

A leaf module on purpose: it imports nothing from `kraft.templates` or
`kraft.config`. `kraft.config` imports `kraft.templates.environment`, which
reads plugins, so an import the other way would be a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class InstalledPlugin:
    """One plugin the instance loads: where its extracted files are and the
    namespace everything it declares is addressed under."""

    #: `<plugin>@<collection>`.
    id: str
    #: The name its `plugin.json` declares.
    name: str
    #: `name`, or the alias it was installed under.
    namespace: str
    version: str
    #: Its extracted files, in the fixed layout: `library.yaml`, `chains/`,
    #: `skills/`, `profiles.yaml`.
    root: Path
    #: Who sees it. Repo collections will add a second value.
    scope: Literal["instance"] = "instance"


def _reference(value: str, plugin: InstalledPlugin, *, bare_is_own: bool) -> str:
    qualifier, colon, name = value.partition(":")
    if not colon:
        return f"{plugin.namespace}:{value}" if bare_is_own else value
    return f"{plugin.namespace}:{name}" if qualifier == plugin.name else value


def qualify(data: object, plugin: InstalledPlugin) -> object:
    """`data`, a plugin's parsed YAML, with its references made absolute.

    A bare `extends:`, `steering:` or `skill:` names the plugin's own
    declaration, and so does one qualified by the plugin's own name; both
    become `<namespace>:<name>`, so an aliased plugin works unchanged. A bare
    `profile:` is the instance's agent profile and stays bare; only the
    plugin's own name in it is rewritten. Any other qualifier (`kraft:`,
    another tool's plugin) and every `harness:` is left as written.
    """
    if isinstance(data, list):
        return [qualify(item, plugin) for item in data]
    if not isinstance(data, Mapping):
        return data
    out: dict[object, object] = {}
    for key, value in data.items():
        if key in ("extends", "skill") and isinstance(value, str):
            out[key] = _reference(value, plugin, bare_is_own=True)
        elif key == "steering" and isinstance(value, list):
            out[key] = [
                _reference(v, plugin, bare_is_own=True) if isinstance(v, str) else v
                for v in value
            ]
        elif key == "profile" and isinstance(value, str):
            out[key] = _reference(value, plugin, bare_is_own=False)
        else:
            out[key] = qualify(value, plugin)
    return out
