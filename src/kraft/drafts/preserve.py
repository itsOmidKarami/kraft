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
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from kraft.drafts import authored


def rewrite(text: str | None, data: Mapping) -> str:
    """`data` written over `text`. A key `text` has keeps its comments, its
    place and its layout (a flow-style block stays flow-style); a key `data`
    adds goes at the end of its mapping, one `data` drops goes with its
    comments; a list is replaced whole."""
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
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _merge(doc: CommentedMap, data: Mapping) -> None:
    for key in [k for k in doc if k not in data]:
        del doc[key]
    for key, value in data.items():
        if key in doc:
            current = doc[key]
            if isinstance(current, CommentedMap) and isinstance(value, Mapping):
                _merge(current, value)
                continue
            if _same(current, value):
                continue
        doc[key] = _plain(value)


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
