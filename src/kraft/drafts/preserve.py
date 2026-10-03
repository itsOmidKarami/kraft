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
import re
from collections.abc import Mapping

import yaml as pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark, YAMLError
from ruamel.yaml.scalarstring import ScalarString, SingleQuotedScalarString
from ruamel.yaml.tokens import CommentToken

from kraft.drafts import authored


def rewrite(text: str | None, data: Mapping, *, list_offset: int = 0) -> str:
    """`data` written over `text`. A key `text` has keeps its comments, its
    place and its layout (a flow-style block stays flow-style); a key `data`
    adds goes at the end of its mapping; one `data` drops or replaces keeps
    the comment block that followed it (the next key's documentation). A
    block list of mappings keeps each entry `data` still has (`_merge_seq`),
    with its comments; any other list is replaced whole. A value in `data`
    that is already a ruamel node (`carried`) is written as it is, with its
    own comments. `list_offset` is the `- ` indent to use when `text` has no
    block list to take it from (`list_offset`: another file's)."""
    if not text or not text.strip():
        return authored.dump(data)
    yaml = _yaml()
    try:
        doc = yaml.load(text)
    except YAMLError:
        return authored.dump(data)
    if not isinstance(doc, CommentedMap):
        return authored.dump(data)
    if offset := _list_offset(text) or list_offset:
        # `  - path: …` under its key, as the file has it, not `- path: …`.
        yaml.indent(mapping=2, sequence=offset + 2, offset=offset)
    _merge(doc, data)
    out = io.StringIO()
    yaml.dump(doc, out)
    return _keep_flow_lines(text, out.getvalue())


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap a long line an author laid out
    # ruamel writes None as nothing (`project:`); every other writer here,
    # and the operator's own file, says `null`.
    yaml.representer.add_representer(
        type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null")
    )
    return yaml


_KEY_LINE = re.compile(r"(?P<indent> *)[^\s#-][^:#]*:\s*(#.*)?")
_ITEM_LINE = re.compile(r"(?P<indent> *)- ")


def list_offset(text: str | None) -> int:
    """`_list_offset` of `text`, for `rewrite(list_offset=)`."""
    return _list_offset(text or "")


def _list_offset(text: str) -> int:
    """How far the file indents a block list's `- ` past its key: the first
    one it has decides, 0 when it has none (ruamel's own default)."""
    lines = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    for key, item in zip(lines, lines[1:], strict=False):
        head, dash = _KEY_LINE.fullmatch(key), _ITEM_LINE.match(item)
        if head and dash:
            return max(0, len(dash["indent"]) - len(head["indent"]))
    return 0


def _line_value(line: str) -> object:
    """One line read on its own, or a marker no other line equals."""
    try:
        return pyyaml.safe_load(line.strip())
    except pyyaml.YAMLError:
        return _line_value


def _keep_flow_lines(text: str, out: str) -> str:
    """Each line of `out` holding a flow collection the rewrite left as it
    was, written as the author spaced it (`default:   { attempts: 3 }`):
    ruamel keeps the flow style but not the spaces. A line is put back only
    when it reads as the same YAML at the same indent."""
    authored_lines: dict[tuple[int, str], list[str]] = {}
    for line in text.splitlines():
        if "{" in line or "[" in line:
            squeezed = re.sub(r"\s+", "", line)
            authored_lines.setdefault((len(line) - len(line.lstrip()), squeezed), []).append(line)
    if not authored_lines:
        return out
    lines = out.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "{" not in line and "[" not in line:
            continue
        body = line.rstrip("\n")
        key = (len(body) - len(body.lstrip()), re.sub(r"\s+", "", body))
        found = authored_lines.get(key)
        if found and len(set(found)) == 1 and _line_value(found[0]) == _line_value(body):
            lines[i] = found[0] + line[len(body) :]
    return "".join(lines)


def carried(text: str | None, key: str) -> list:
    """The entries of `text`'s top-level block list `key`, as ruamel nodes
    with their own comments, for `rewrite` to put in another file (the 2.0
    move of `policy.yaml`'s `triggers:`). The comment block after the list,
    the next key's documentation, stays behind. Empty when `text` has no
    such list."""
    try:
        doc = _yaml().load(text or "")
    except YAMLError:
        return []
    seq = doc.get(key) if isinstance(doc, CommentedMap) else None
    if not isinstance(seq, CommentedSeq) or not seq:
        return []
    _take_block(doc, key)
    items = list(seq)
    # The comment lines above an entry are stored on the entry before it (or,
    # for the first, on the list): each is taken off there and goes with its
    # own entry (`_place_leads`), not with the one before it.
    first = seq.ca.comment[1] if seq.ca.comment and seq.ca.comment[1] else []
    leads = ["".join(" " * t.column + t.value.lstrip(" ") for t in first)]
    leads += [_take_block(seq, index - 1) for index in range(1, len(items))]
    for index, item in enumerate(items):
        # A flow entry's own comment (`- {cron: …}  # weekly`) is the list's,
        # by position: it goes with the entry.
        if isinstance(item, CommentedMap | CommentedSeq) and seq.ca.items.get(index):
            item._kraft_entry_comment = seq.ca.items[index]
        if isinstance(item, CommentedMap | CommentedSeq) and leads[index].strip():
            item._kraft_lead = leads[index]
    return items


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
            if _same(current, value):
                continue
            if isinstance(current, CommentedMap) and isinstance(value, Mapping) and value:
                _merge(current, value, (doc, key))
                continue
            if _mappings(current) and isinstance(value, list | tuple) and value:
                # The comment block after the list (the next key's
                # documentation) hangs off its last entry: take it off first,
                # or removing that entry loses it and appending one lands it
                # between entries.
                block = _take_block(doc, key)
                _merge_seq(current, value)
                if block:
                    _put_block(doc, key, block)
                continue
            block = _take_block(doc, key)
            doc[key] = _node(value)
            if block:
                _put_block(doc, key, block)
            continue
        doc[_quoted(key) if isinstance(key, str) else key] = _node(value)


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


def _mappings(node: object) -> bool:
    """A block list whose every entry is a mapping: `repos:`, `schedules:`."""
    return (
        isinstance(node, CommentedSeq)
        and not node.fa.flow_style()
        and all(isinstance(item, CommentedMap) for item in node)
    )


def _identity(item: object) -> object:
    """What names one entry of a list of mappings across a rewrite: a repo's
    `path`. None for an entry with none."""
    return item.get("path") if isinstance(item, Mapping) else None


def _merge_seq(seq: CommentedSeq, values: list | tuple) -> None:
    """`seq` holding `values`, in their order: an entry already there (the
    same `path`, or the same value) is kept with its comments and merged
    into, a new one is added. A Settings save of one repo once rewrote the
    whole `repos:` list and every comment inside it (R12b-04)."""
    unused = list(seq)
    items = []
    for value in values:
        match = next(
            (
                node
                for node in unused
                if (_identity(value) is not None and _identity(node) == _identity(value))
                or _same(node, value)
            ),
            None,
        )
        if match is None or not isinstance(value, Mapping):
            items.append(_node(value))
            continue
        unused.remove(match)
        if not _same(match, value):
            _merge(match, value)
        items.append(match)
    _drop_removed_entries_comments(seq, {id(node) for node in unused})
    # The list's own comments are by position: each follows its entry.
    by_entry = {id(node): seq.ca.items[i] for i, node in enumerate(seq) if seq.ca.items.get(i)}
    seq.clear()
    seq.extend(items)
    seq.ca.items.clear()
    _place_entry_comments(seq, by_entry)


def _drop_removed_entries_comments(seq: CommentedSeq, removed: set[int]) -> None:
    """A comment line above an entry is stored on the entry before it. So a
    removed entry's own leading comment is dropped with it, and the one it
    held for the entry after it moves to the kept entry now before that one,
    or above the list when none is. The first entry's own is the list's
    (`seq.ca.comment`, shared with the parent key). When the key has an
    end-of-line comment (`repos:   # my repos`), it is the lines after that
    comment on the key's own token."""
    lead = seq.ca.comment[1] if seq.ca.comment and seq.ca.comment[1] else []
    on_key = seq.ca.comment[0] if seq.ca.comment and not lead else None
    kept_before = None
    for index, node in enumerate(seq):
        if id(node) not in removed:
            kept_before = index
            continue
        if index > 0:
            _take_block(seq, index - 1)  # this entry's own leading comment
        block = _take_block(seq, index) if index + 1 < len(seq) else ""
        if block and id(seq[index + 1]) in removed:
            block = ""  # the next entry's own: it goes with that one
        if kept_before is not None:
            if block:
                _put_block(seq, kept_before, block)
        elif lead:
            # Still at the top: the next entry's comment is the list's first.
            lead[0].value = block.lstrip(" ") if block else ""
            for token in lead[1:]:
                token.value = ""
        elif on_key is not None:
            on_key.value = on_key.value.partition("\n")[0] + "\n" + block
        elif block:
            lead = [CommentToken(block, CommentMark(0), None)]
            seq.ca.comment = [None, lead]


def _place_entry_comments(seq: CommentedSeq, by_entry: dict | None = None) -> None:
    """Each entry's own comment on `seq` at its new position: one it had
    there (`by_entry`, by node id) or one it was `carried` with, and the
    comment lines a `carried` entry had above it."""
    for index, node in enumerate(seq):
        entry = (by_entry or {}).get(id(node)) or getattr(node, "_kraft_entry_comment", None)
        if entry:
            seq.ca.items[index] = entry
    for index, node in enumerate(seq):
        if not (lead := getattr(node, "_kraft_lead", None)):
            continue
        if index:
            _put_block(seq, index - 1, lead)
        elif not (seq.ca.comment and seq.ca.comment[1]):
            seq.ca.comment = [None, [CommentToken(lead, CommentMark(0), None)]]


def _quoted(value: str) -> str:
    """A string Kraft's readers (PyYAML, YAML 1.1) would not read back as
    the same string is single-quoted: ruamel writes `yes`, `off` or `on`
    bare by YAML 1.2's rules, and Kraft then read a boolean (R12E-02)."""
    if isinstance(value, ScalarString) or "\n" in value:
        return value
    try:
        same = pyyaml.safe_load(value) == value
    except pyyaml.YAMLError:
        same = False
    return value if same else SingleQuotedScalarString(value)


def _node(value: object) -> object:
    """`value` as ruamel nodes, so a comment can be attached to it. A node
    already (`carried`) is kept, comments and all."""
    if isinstance(value, CommentedMap | CommentedSeq):
        return value
    if isinstance(value, str):
        return _quoted(value)
    if isinstance(value, Mapping):
        node = CommentedMap()
        for k, v in value.items():
            # A key too: an env key `on` read back as `True` (R12 review P3).
            node[_quoted(k) if isinstance(k, str) else k] = _node(v)
        if not node:
            # A block-style empty mapping dumps as a bare `{}` on the line
            # after its key, which is not YAML: `key: {}` is.
            node.fa.set_flow_style()
        return node
    if isinstance(value, list | tuple):
        seq = CommentedSeq(_node(v) for v in value)
        _place_entry_comments(seq)
        return seq
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
