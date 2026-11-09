"""Which of Kraft's tools an ACP tool call is (spec §6.2). Pure: no I/O, no Kraft imports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: ACP's closed `ToolKind` enum: a `tool_names` key in this set is a kind, any other is a
#: tool `name`.
KINDS = (
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
)


@dataclass(frozen=True)
class Tool:
    key: str  # what the call was keyed on: its name, else its kind ("" if neither)
    names: tuple[str, ...]  # Kraft's bare tool names it is; () when Kraft cannot tell


def resolve(call: Mapping, tool_names: Mapping[str, Sequence[str]]) -> Tool:
    name, kind = call.get("name"), call.get("kind")
    if isinstance(name, str) and name:
        return Tool(name, tuple(tool_names.get(name) or (name,)))
    if isinstance(kind, str) and kind != "other" and kind in tool_names:
        return Tool(kind, tuple(tool_names[kind]))
    return Tool(kind if isinstance(kind, str) else "", ())
