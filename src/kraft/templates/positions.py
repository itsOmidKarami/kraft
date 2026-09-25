"""Where in a YAML document a key path points, as an editor shows it.

A pydantic error's `loc` and a template issue's `loc` are key paths into the
authored file -- `("nodes", 0, "tasks", 1, "extends")`. `locate` walks the
composed document (PyYAML node marks, no extra dependency) to the deepest node
on that path that exists, so an error about a field the author never wrote
(one inherited through `extends`) lands on the component that inherited it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from kraft.templates.library import TemplateIssue

#: 1-based line and column.
Position = tuple[int, int]

_TOP: Position = (1, 1)


def locate(text: str, loc: tuple[object, ...]) -> Position:
    if not loc:
        return _TOP
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return _TOP
    if node is None:
        return _TOP
    found: yaml.Node | None = None
    for part in loc:
        child: tuple[yaml.Node, yaml.Node] | None = None
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value == str(part):
                    child = (key, value)
                    break
        elif (
            isinstance(node, yaml.SequenceNode)
            and isinstance(part, int)
            and 0 <= part < len(node.value)
        ):
            child = (node.value[part], node.value[part])
        if child is None:
            break
        found, node = child
    if found is None:
        return _TOP
    return (found.start_mark.line + 1, found.start_mark.column + 1)


def yaml_mark(exc: BaseException) -> Position | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        mark = getattr(current, "problem_mark", None)
        if mark is not None:
            return (mark.line + 1, mark.column + 1)
        current = current.__cause__ or current.__context__
    return None


def issue_view(issue: TemplateIssue, buffers: Mapping[Path, str] | None = None) -> dict:
    """An issue as the API and CLI show it: where it is, 1-based."""

    def text(path: Path) -> str:
        if buffers is not None and path in buffers:
            return buffers[path]
        try:
            return path.read_text()
        except OSError:
            return ""

    line, column = issue.mark or locate(text(issue.file), issue.loc or ())
    related = None
    if issue.related is not None:
        rfile, rloc = issue.related
        rline, rcolumn = locate(text(rfile), rloc)
        related = {"file": str(rfile), "line": rline, "column": rcolumn}
    return {
        "file": str(issue.file),
        "chain": issue.chain,
        "message": issue.message,
        "line": line,
        "column": column,
        "related": related,
    }
