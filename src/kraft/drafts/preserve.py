"""Write a config mapping back over the file it came from, keeping the
file's comments, order and layout for every key it still has.

The seeded `policy.yaml` is mostly comments: what each key does, and the
levels and maxima an operator may set. A Settings screen's op edits the
mapping (`drafts.config.ConfigDraft`), and dumping that mapping whole threw
every comment away on the first click. Here the original text is loaded
round-trip (`ruamel.yaml`), the mapping's changes are applied onto it key by
key, and the result keeps whatever a changed value did not touch.

A file that does not parse, is not a mapping, or is empty is written the
plain way (`authored.dump`): there is nothing to keep.
"""

from __future__ import annotations

import io
from collections.abc import Mapping

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark, YAMLError
from ruamel.yaml.tokens import CommentToken

from kraft.drafts import authored


def rewrite(text: str | None, data: Mapping) -> str:
    """`data` written over `text`. A key `text` has keeps its comments, its
    place and its layout (a flow-style block stays flow-style); a key `data`
    adds goes at the end of its mapping; one `data` drops or replaces keeps
    the comment block that followed it (the next key's documentation); a
    list is replaced whole."""
    if not text or not text.strip():
        return authored.dump(data)
    yaml = _yaml()
    try:
        doc = yaml.load(text)
    except YAMLError:
        return authored.dump(data)
    if not isinstance(doc, CommentedMap):
        return authored.dump(data)
    _merge(doc, data)
    out = io.StringIO()
    yaml.dump(doc, out)
    return out.getvalue()


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap a long line an author laid out
    return yaml


def _merge(doc: CommentedMap, data: Mapping, parent: tuple | None = None) -> None:
    """`parent` is `(mapping, key)` holding `doc`, None at the root: where the
    comment block before `doc`'s first key lives."""
    for key in [k for k in doc if k not in data]:
        block = _take_block(doc, key)
        before = list(doc)[: list(doc).index(key)]
        del doc[key]
        if block:
            _keep_block(doc, before[-1] if before else None, block, parent)
    for key, value in data.items():
        if key in doc:
            current = doc[key]
            if isinstance(current, CommentedMap) and isinstance(value, Mapping):
                _merge(current, value, (doc, key))
                continue
            if _same(current, value):
                continue
            block = _take_block(doc, key)
            doc[key] = _node(value)
            if block:
                _put_block(doc, key, block)
            continue
        doc[key] = _node(value)


# A comment block between two keys is stored by ruamel as the tail of
# whatever node comes last before it: the previous key's value, or the last
# item deep inside it. Dropping or replacing that subtree took the block with
# it, which on the shipped policy.yaml was the next section's documentation
# (dropping `archive` deleted 106 lines). The block is taken off first and
# put back on what now precedes it.


def _is_block(node: object) -> bool:
    return (
        isinstance(node, CommentedMap | CommentedSeq) and len(node) > 0 and not node.fa.flow_style()
    )


def _tail(container: CommentedMap | CommentedSeq, key: object) -> tuple:
    """`(holder, index, slot, blocks)` of the comment that follows
    `container[key]`'s whole subtree: on the last item of the deepest last
    block under it; `blocks` are the block nodes on the way down, whose own
    `end` comments come after it."""
    node = container[key]
    blocks = []
    while _is_block(node):
        blocks.append(node)
        container = node
        key = next(reversed(node)) if isinstance(node, CommentedMap) else len(node) - 1
        node = container[key]
    return container, key, 2 if isinstance(container, CommentedMap) else 0, blocks


def _take_block(container: CommentedMap, key: object) -> str:
    """The comment lines after `container[key]`'s subtree, without the
    subtree's own end-of-line comment, taken off it."""
    holder, index, slot, blocks = _tail(container, key)
    parts = []
    entry = holder.ca.items.get(index)
    token = entry[slot] if entry and len(entry) > slot else None
    if token is not None and "\n" in token.value:
        own, _, after = token.value.partition("\n")
        token.value = own + "\n"
        parts.append(after)
    for node in reversed(blocks):
        if isinstance(node.ca.end, list):
            parts.extend(t.value for t in node.ca.end if t is not None)
            node.ca.end = []
    block = "".join(parts)
    return block if block.strip() else ""


def _put_block(container: CommentedMap, key: object, block: str) -> None:
    """`block` after `container[key]`'s subtree, after any comment already there."""
    holder, index, slot, _ = _tail(container, key)
    entry = holder.ca.items.setdefault(index, [None, None, None, None])
    if entry[slot] is None:
        entry[slot] = CommentToken("\n" + block, CommentMark(0), None)
    else:
        value = entry[slot].value
        entry[slot].value = (value if value.endswith("\n") else value + "\n") + block


def _keep_block(doc: CommentedMap, before: object, block: str, parent: tuple | None) -> None:
    """Put a dropped key's trailing `block` where the key was: after the key
    now before it, else before the mapping's first key."""
    if before is not None:
        _put_block(doc, before, block)
    elif parent is not None:
        holder, key = parent
        entry = holder.ca.items.setdefault(key, [None, None, None, None])
        if entry[2] is None:
            entry[2] = CommentToken("\n" + block, CommentMark(0), None)
        else:
            value = entry[2].value
            entry[2].value = (value if value.endswith("\n") else value + "\n") + block
        doc.ca.comment = [entry[2], None]
    else:
        if doc.ca.comment is None:
            doc.ca.comment = [None, []]
        if doc.ca.comment[1] is None:
            doc.ca.comment[1] = []
        doc.ca.comment[1].append(CommentToken(block, CommentMark(0), None))


def _node(value: object) -> object:
    """`value` as ruamel nodes, so a comment can be attached to it."""
    if isinstance(value, Mapping):
        node = CommentedMap()
        for k, v in value.items():
            node[k] = _node(v)
        return node
    if isinstance(value, list | tuple):
        return CommentedSeq(_node(v) for v in value)
    return value


def _same(current: object, value: object) -> bool:
    """Whether the authored node already holds `value`: compared as plain
    data, so a quoted string or a flow-style mapping is left as written."""
    return _plain(current) == _plain(value)


def _plain(value: object) -> object:
    """`value` as plain `dict`/`list`/scalars, with no ruamel node types."""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return value
