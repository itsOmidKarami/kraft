"""Where in a YAML document a key path points, as an editor shows it.

A pydantic error's `loc` and a template issue's `loc` are key paths into the
authored file -- `("nodes", 0, "tasks", 1, "extends")`. `locate` walks the
composed document (PyYAML node marks, no extra dependency) to the deepest node
on that path that exists, so an error about a field the author never wrote
(one inherited through `extends`) lands on the component that inherited it.
"""

from __future__ import annotations

import yaml

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
