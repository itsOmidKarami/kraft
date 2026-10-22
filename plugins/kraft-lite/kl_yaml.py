"""A reader for the YAML subset Kraft's chain templates are written in.

Stdlib only, on purpose, like `kl.py`: the plugin may land in a repo where
PyYAML was never installed. `read` uses `yaml.safe_load` when PyYAML is there
and this reader when it is not, so a template reads the same either way. That
only holds if this reader agrees with `safe_load` on everything it accepts, and
refuses, with the line it stopped at, everything it cannot be sure to agree on.

Supported: block mappings and sequences; plain, single- and double-quoted
scalars; literal (`|`) and folded (`>`) block scalars with their chomping and
indent indicators; flow sequences and mappings, nested and over several lines;
comments; one optional leading `---`. Plain scalars resolve as PyYAML's
YAML 1.1 core does (`30s` stays a string, `yes` and `on` are booleans).

Refused: anchors, aliases, tags, more than one document, complex keys
(`? a`), multi-line quoted scalars, plain scalars that `safe_load` would turn
into a timestamp or a base-60 number (quote them), and, where `safe_load`
raises too: a tab anywhere but inside a quoted scalar, a block scalar's text
or a comment; and a plain scalar starting with an indicator (`%`, `,`, `]`,
`}`, `?` before a space; in a flow collection also `:`, `#` and `- `, and a
`?` anywhere in it).
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml as _yaml

    _safe_load = _yaml.safe_load
    _YAML_ERRORS: tuple[type[BaseException], ...] = (_yaml.YAMLError,)
except ImportError:
    _safe_load = None
    _YAML_ERRORS = ()


class YamlError(SystemExit):
    """A refusal or a syntax error. A `SystemExit`, so the CLI prints the one
    line and exits non-zero, the way `kl.py` reports every other error."""


def load(text: str, source: str = "<string>") -> object:
    """Read `text` with the subset reader, never PyYAML."""
    return _Reader(text, source).document()


def read(path: Path) -> object:
    """The document in `path`: `yaml.safe_load` when PyYAML is importable, the
    subset reader when it is not. An empty file is `None`."""
    text = Path(path).read_text(encoding="utf-8")
    if _safe_load is None:
        return load(text, str(path))
    try:
        data = _safe_load(text)
    except _YAML_ERRORS as exc:
        raise YamlError(f"kraft-lite: {path}: {' '.join(str(exc).split())}") from exc
    if (flaw := _not_json(data, "")) is not None:
        raise YamlError(
            f"kraft-lite: {path}: {flaw}, not a string, number, boolean or null; quote it"
        )
    return data


#: What a value in a chain or bindings file may be: what the subset reader
#: gives, what JSON (the frozen chain) holds, and what Kraft's models accept.
_JSON_SCALARS = (str, int, float, bool, type(None))


def _not_json(value: object, where: str) -> str | None:
    """Where `safe_load` gave a value outside `_JSON_SCALARS` (a timestamp, a
    `!!binary` or `!!set`), as a key or a value, and what it is; None if none."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, _JSON_SCALARS):
                return f"key {key} is a {type(key).__name__}"
            here = f"{where}.{key}" if where else str(key)
            if (flaw := _not_json(item, here)) is not None:
                return flaw
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            if (flaw := _not_json(item, f"{where}[{index}]")) is not None:
                return flaw
        return None
    if isinstance(value, _JSON_SCALARS):
        return None
    shown = value.isoformat() if hasattr(value, "isoformat") else repr(value)
    return f"{where}: {shown} is a {type(value).__name__}"


# -- scalars ----------------------------------------------------------------

_BOOLS = {
    **dict.fromkeys(("yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON"), True),
    **dict.fromkeys(("no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF"), False),
}
_NULLS = frozenset(("~", "null", "Null", "NULL", ""))

# The patterns PyYAML's resolver tries, written without its verbose-mode spaces.
_INT = re.compile(
    r"[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+"
    r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+"
)
_FLOAT = re.compile(
    r"[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?"
    r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
    r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?"
)

_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": " ",
    "P": " ",
}
_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}

_BLOCK_HEADER = re.compile(r"([|>])(?:([1-9])([-+]?)|([-+]?)([1-9]?))(?: *| +#.*)$")
_COMMENT = re.compile(r"\s+#")
_FLOW_STOP = ",[]{}"
#: What a plain scalar cannot start with, in any context: `safe_load` raises.
_NOT_PLAIN_START = "%,]}"
_TAB = "a tab is only allowed inside a quoted or block scalar's text or a comment"


class _Reader:
    def __init__(self, text: str, source: str):
        self.source = source
        text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
        self.text = text
        self.eof_newline = text.endswith("\n") or not text
        self.lines = text.split("\n")
        if self.lines[-1] == "":
            self.lines.pop()
        self.starts: list[int] = []
        offset = 0
        for line in self.lines:
            self.starts.append(offset)
            offset += len(line) + 1
        self.i = 0  # the next raw line nobody has read
        self.pending: tuple[int, int] | None = None  # (line, column) of a half-read line

    # -- errors ------------------------------------------------------------

    def fail(self, message: str, line: int) -> YamlError:
        return YamlError(f"kraft-lite: {self.source}:{line + 1}: {message}")

    def fail_at(self, message: str, offset: int) -> YamlError:
        return self.fail(message, self.text.count("\n", 0, offset))

    # -- lines -------------------------------------------------------------

    def document(self) -> object:
        self.check_markers()
        node = self.peek()
        if node is None:
            return None
        value = self.node(node[1], -1)
        leftover = self.peek()
        if leftover is not None:
            line, col = leftover
            if self.lines[line][col:].startswith("- ") or self.lines[line][col:] == "-":
                raise self.fail("a sequence item cannot follow a mapping at this indent", line)
            raise self.fail("bad indentation: this line does not belong to the document", line)
        return value

    def check_markers(self) -> None:
        seen_content = False
        for n, line in enumerate(self.lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            head = line[:3]
            if head in ("---", "...") and (len(line) == 3 or line[3] in " \t"):
                if "\t" in line.split("#", 1)[0]:
                    raise self.fail(_TAB, n)
                if head == "---" and not seen_content and not line[3:].strip():
                    seen_content = True  # the one leading marker
                    self.lines[n] = ""
                    continue
                raise self.fail("only one YAML document per file is supported", n)
            seen_content = True

    def peek(self) -> tuple[int, int] | None:
        """The next line with something on it, as (line, column), unread."""
        if self.pending is not None:
            return self.pending
        while self.i < len(self.lines):
            line = self.lines[self.i]
            stripped = line.lstrip(" ")
            if stripped.startswith("#"):
                self.i += 1
                continue
            if stripped.startswith("\t") and stripped.strip():
                raise self.fail("tabs are not allowed in indentation", self.i)
            if not stripped.strip():
                if "\t" in stripped:
                    raise self.fail(_TAB, self.i)
                self.i += 1
                continue
            return self.i, len(line) - len(stripped)
        return None

    def take(self, line: int) -> None:
        """Mark `line` (the one `peek` returned) read."""
        self.pending = None
        self.i = line + 1

    def rest(self, line: int, col: int) -> str:
        return self.lines[line][col:]

    # -- block structure ---------------------------------------------------

    def node(self, indent: int, parent: int) -> object:
        """The node whose first line `peek` has just returned, at column `indent`."""
        line, col = self.peek()  # type: ignore[misc]
        text = self.rest(line, col)
        if text == "-" or text.startswith("- "):
            return self.sequence(indent)
        if text == "?" or text.startswith("? "):
            raise self.fail("complex keys (`? key`) are not supported", line)
        if self.split_key(line, col) is not None:
            return self.mapping(indent)
        return self.inline(line, col, parent)

    def sequence(self, indent: int) -> list:
        items: list = []
        while (here := self.peek()) is not None:
            line, col = here
            if col < indent:
                break
            text = self.rest(line, col)
            if col > indent:
                raise self.fail("bad indentation", line)
            if not (text == "-" or text.startswith("- ")):
                break
            self.take(line)
            inner = col + 1
            while inner < len(self.lines[line]) and self.lines[line][inner] == " ":
                inner += 1
            if self.lines[line][inner : inner + 1] == "\t":
                raise self.fail(_TAB, line)
            if self.blank_from(line, inner):
                items.append(self.nested(indent))
            else:
                self.pending = (line, inner)
                items.append(self.node(inner, indent))
        return items

    def mapping(self, indent: int) -> dict:
        result: dict = {}
        while (here := self.peek()) is not None:
            line, col = here
            if col < indent:
                break
            if col > indent:
                raise self.fail("bad indentation", line)
            text = self.rest(line, col)
            if text == "-" or text.startswith("- "):
                if self.pending is None:
                    raise self.fail("a sequence item cannot follow a mapping at this indent", line)
                break
            if text == "?" or text.startswith("? "):
                raise self.fail("complex keys (`? key`) are not supported", line)
            split = self.split_key(line, col)
            if split is None:
                raise self.fail(f"expected `key: value` in a mapping, found {text!r}", line)
            key, value_col = split
            self.take(line)
            if self.blank_from(line, value_col):
                value = self.nested(indent, in_mapping=True)
            else:
                value = self.inline(line, value_col, indent)
            result[key] = value
        return result

    def nested(self, indent: int, in_mapping: bool = False) -> object:
        """The value on the lines after a `key:` or `-` that has nothing after it."""
        here = self.peek()
        if here is None:
            return None
        line, col = here
        if col > indent:
            return self.node(col, indent)
        if in_mapping and col == indent:
            text = self.rest(line, col)
            if text == "-" or text.startswith("- "):
                return self.sequence(indent)
        return None

    def blank_from(self, line: int, col: int) -> bool:
        text = self.lines[line][col:].strip()
        return not text or (text.startswith("#") and self.lines[line][col - 1 : col] in ("", " "))

    # -- keys --------------------------------------------------------------

    def split_key(self, line: int, col: int) -> tuple[object, int] | None:
        """`key:` at (line, col) as (the key, the column after the colon and the
        spaces that follow), or None when this line is not a mapping entry."""
        text = self.lines[line]
        first = text[col]
        if first in "\"'":
            offset = self.starts[line] + col
            key, end = self.quoted(offset)
            after = end - self.starts[line]
            while after < len(text) and text[after] == " ":
                after += 1
            if (
                after < len(text)
                and text[after] == ":"
                and (after + 1 == len(text) or text[after + 1] in " \t")
            ):
                after += 1
                while after < len(text) and text[after] == " ":
                    after += 1
                if text[after : after + 1] == "\t":
                    raise self.fail(_TAB, line)
                return key, after
            return None
        if first in "[{|>&*!":
            return None
        if first in _NOT_PLAIN_START:
            raise self.fail(f"a plain scalar cannot start with {first!r}; quote it", line)
        cut = _COMMENT.search(text, col)
        limit = cut.start() if cut else len(text)
        pos = col
        while True:
            pos = text.find(":", pos, limit)
            if pos < 0:
                return None
            if pos + 1 == limit or text[pos + 1] in " \t":
                break
            pos += 1
        raw = text[col:pos].rstrip(" ")
        if not raw:
            return None
        if "\t" in raw:
            raise self.fail(_TAB, line)
        after = pos + 1
        while after < len(text) and text[after] == " ":
            after += 1
        if text[after : after + 1] == "\t":
            raise self.fail(_TAB, line)
        return self.resolve(raw, line), after

    # -- values on a line --------------------------------------------------

    def inline(self, line: int, col: int, parent: int) -> object:
        """The value starting at (line, col), consuming what it spans."""
        text = self.lines[line]
        first = text[col]
        if first == "&":
            raise self.fail("anchors (`&name`) are not supported", line)
        if first == "*":
            raise self.fail("aliases (`*name`) are not supported", line)
        if first == "!":
            raise self.fail("tags (`!tag`) are not supported", line)
        if first in "|>":
            return self.block_scalar(line, col, parent)
        if first in "[{":
            return self.flow_value(line, col)
        if first in "\"'":
            value, end = self.quoted(self.starts[line] + col)
            self.expect_end(line, end - self.starts[line])
            self.take(line)
            return value
        if first in "@`" + _NOT_PLAIN_START or text[col : col + 2] in ("?", "? "):
            raise self.fail(f"a plain scalar cannot start with {first!r}; quote it", line)
        cut = _COMMENT.search(text, col)
        raw = text[col : cut.start() if cut else len(text)]
        if "\t" in raw or (cut and "\t" in cut.group()):
            raise self.fail(_TAB, line)
        plain = raw.strip()
        if plain.endswith(":") or ": " in plain or ":\t" in plain:
            raise self.fail("a mapping value cannot itself start a mapping on the same line", line)
        if plain.startswith("- ") or plain == "-":
            raise self.fail("a sequence cannot start on the same line as a key", line)
        self.take(line)
        return self.resolve(plain, line)

    def expect_end(self, line: int, col: int) -> None:
        tail = self.lines[line][col:]
        if "\t" in tail.split("#", 1)[0]:
            raise self.fail(_TAB, line)
        if tail.strip() and not (tail[0] == " " and tail.strip().startswith("#")):
            raise self.fail(f"unexpected text after the value: {tail.strip()!r}", line)

    def resolve(self, text: str, line: int) -> object:
        if text == "<<":
            raise self.fail("merge keys (`<<`) are not supported; quote it to keep it text", line)
        if text == "=":
            raise self.fail("the plain scalar `=` is not supported; quote it", line)
        if text in _NULLS:
            return None
        if text in _BOOLS:
            return _BOOLS[text]
        first = text[0]
        if first in "-+.0123456789":
            if _TIMESTAMP.fullmatch(text):
                raise self.fail(f"{text!r} reads as a timestamp in YAML; quote it", line)
            numeric = _INT.fullmatch(text) or _FLOAT.fullmatch(text)
            if numeric and ":" in text:
                raise self.fail(f"{text!r} reads as a sexagesimal number in YAML; quote it", line)
            if _INT.fullmatch(text):
                return self.integer(text, line)
            if _FLOAT.fullmatch(text):
                return self.real(text)
        return text

    def integer(self, text: str, line: int) -> int:
        digits = text.replace("_", "")
        sign = -1 if digits.startswith("-") else 1
        digits = digits.lstrip("+-")
        try:
            if digits == "0":
                return 0
            if digits.startswith("0b"):
                return sign * int(digits[2:], 2)
            if digits.startswith("0x"):
                return sign * int(digits[2:], 16)
            if digits.startswith("0"):
                return sign * int(digits, 8)
            return sign * int(digits)
        except ValueError:
            raise self.fail(f"{text!r} is not a valid number; quote it", line) from None

    @staticmethod
    def real(text: str) -> float:
        value = text.replace("_", "").lower()
        sign = -1.0 if value.startswith("-") else 1.0
        value = value.lstrip("+-")
        if value == ".inf":
            return sign * float("inf")
        if value == ".nan":
            return float("nan")
        return sign * float(value)

    # -- quoted scalars ----------------------------------------------------

    def quoted(self, offset: int) -> tuple[str, int]:
        """The quoted scalar at `offset` and the offset after its closing quote."""
        text = self.text
        quote = text[offset]
        out: list[str] = []
        p = offset + 1
        while True:
            if p >= len(text) or text[p] == "\n":
                raise self.fail_at(
                    "unterminated quoted scalar (a quoted scalar must close on its own line)",
                    offset,
                )
            ch = text[p]
            if quote == "'":
                if ch == "'":
                    if text[p + 1 : p + 2] == "'":
                        out.append("'")
                        p += 2
                        continue
                    return "".join(out), p + 1
                out.append(ch)
                p += 1
            elif ch == '"':
                return "".join(out), p + 1
            elif ch == "\\":
                esc = text[p + 1 : p + 2]
                if esc in _ESCAPES and esc != "":
                    out.append(_ESCAPES[esc])
                    p += 2
                elif esc in _HEX_ESCAPES:
                    width = _HEX_ESCAPES[esc]
                    digits = text[p + 2 : p + 2 + width]
                    try:
                        if len(digits) != width:
                            raise ValueError
                        out.append(chr(int(digits, 16)))
                    except (ValueError, OverflowError):
                        raise self.fail_at(f"bad \\{esc} escape in a quoted scalar", p) from None
                    p += 2 + width
                else:
                    raise self.fail_at(f"unknown escape `\\{esc}` in a quoted scalar", p)
            else:
                out.append(ch)
                p += 1

    # -- block scalars -----------------------------------------------------

    def block_scalar(self, line: int, col: int, parent: int) -> str:
        header = _BLOCK_HEADER.match(self.lines[line], col)
        if header is None and "\t" in self.lines[line][col:].split("#", 1)[0]:
            raise self.fail(_TAB, line)
        if header is None:
            raise self.fail(
                "bad block scalar header (expected `|`, `>`, with `-`/`+` or a digit)", line
            )
        style = header.group(1)
        digit = header.group(2) or header.group(5) or ""
        chomp = header.group(3) or header.group(4) or ""
        floor = max(parent + 1, 1)
        indent = floor + int(digit) - 1 if digit else None

        body: list[str] = []
        leading: list[int] = []  # the lengths of blank lines seen before the indent is known
        n = line + 1
        while n < len(self.lines):
            raw = self.lines[n]
            if not raw.strip(" "):
                if indent is not None and len(raw) > indent:
                    body.append(raw[indent:])  # spaces past the indent are content
                else:
                    body.append("")
                    if indent is None:
                        leading.append(len(raw))
                n += 1
                continue
            lead = len(raw) - len(raw.lstrip(" "))
            if indent is None:
                if lead < floor:
                    break
                indent = lead
                for k, length in enumerate(leading):
                    if length > indent:
                        raise self.fail(
                            "bad indentation: a blank line inside a block scalar is "
                            "indented past its first line",
                            line + 1 + k,
                        )
            if lead < indent:
                break
            body.append(raw[indent:])
            n += 1
        self.i = n
        self.pending = None

        trailing = 0
        while body and body[-1] == "":
            body.pop()
            trailing += 1
        # The file's last line has no break after it, so it adds none.
        unterminated = n >= len(self.lines) and not self.eof_newline
        at_eof_without_break = unterminated and trailing == 0
        if unterminated and trailing:
            trailing -= 1
        if not body:
            return "\n" * trailing if chomp == "+" else ""
        text = self.literal(body) if style == "|" else self.folded(body)
        if chomp == "+":
            return text + ("" if at_eof_without_break else "\n") + "\n" * trailing
        if chomp == "-" or at_eof_without_break:
            return text
        return text + "\n"

    @staticmethod
    def literal(body: list[str]) -> str:
        return "\n".join(body)

    @staticmethod
    def folded(body: list[str]) -> str:
        out: list[str] = []
        previous: str | None = None
        blanks = 0
        for entry in body:
            if entry == "":
                blanks += 1
                continue
            more = entry[0] in " \t"
            if previous is None:
                out.append("\n" * blanks)
            elif more or previous[0] in " \t":
                out.append("\n" * (blanks + 1))
            else:
                out.append(" " if blanks == 0 else "\n" * blanks)
            out.append(entry)
            previous = entry
            blanks = 0
        return "".join(out)

    # -- flow collections --------------------------------------------------

    def flow_value(self, line: int, col: int) -> object:
        value, end = self.flow(self.starts[line] + col)
        last = self.text.count("\n", 0, end)
        self.expect_end(last, end - self.starts[last])
        self.take(last)
        return value

    def skip(self, p: int) -> int:
        """Past whitespace, line breaks and comments."""
        text = self.text
        while p < len(text):
            ch = text[p]
            if ch == "\t":
                raise self.fail_at(_TAB, p)
            if ch in " \n":
                p += 1
            elif ch == "#" and (p == 0 or text[p - 1] in " \n"):
                while p < len(text) and text[p] != "\n":
                    p += 1
            else:
                break
        return p

    def flow(self, p: int) -> tuple[object, int]:
        text = self.text
        opener = text[p]
        closer = "]" if opener == "[" else "}"
        what = "flow sequence" if opener == "[" else "flow mapping"
        start = p
        p += 1
        items: list = []
        pairs: dict = {}
        while True:
            p = self.skip(p)
            if p >= len(text):
                raise self.fail_at(f"unterminated {what} (no closing `{closer}`)", start)
            if text[p] == closer:
                return (items if opener == "[" else pairs), p + 1
            if text[p] == ",":
                raise self.fail_at(f"an empty entry in a {what}", p)
            node, p = self.flow_node(p)
            if opener == "{" and isinstance(node, (dict, list)):
                raise self.fail_at("a flow mapping key must be a scalar", start)
            if opener == "{":
                key_end, p = p, self.skip(p)
                value: object = None
                if p < len(text) and text[p] == ":" and "\n" in text[key_end:p]:
                    # `safe_load` keeps a key and its `:` on one line.
                    raise self.fail_at("a flow mapping's `:` must be on its key's line", key_end)
                if p < len(text) and text[p] == ":":
                    p = self.skip(p + 1)
                    if p < len(text) and text[p] not in ",}":
                        value, p = self.flow_node(p)
                pairs[node] = value
            else:
                items.append(node)
            p = self.skip(p)
            if p < len(text) and text[p] == ",":
                p += 1
            elif p >= len(text) or text[p] != closer:
                where = self.text.count("\n", 0, min(p, len(text) - 1)) + 1
                raise self.fail_at(
                    f"malformed {what}: expected `,` or `{closer}` at line {where}", start
                )

    def flow_node(self, p: int) -> tuple[object, int]:
        text = self.text
        ch = text[p]
        if ch in "[{":
            return self.flow(p)
        if ch in "\"'":
            return self.quoted(p)
        if ch == "&":
            raise self.fail_at("anchors (`&name`) are not supported", p)
        if ch == "*":
            raise self.fail_at("aliases (`*name`) are not supported", p)
        if ch == "!":
            raise self.fail_at("tags (`!tag`) are not supported", p)
        # In a flow collection `?` and `:` are indicators wherever they start a
        # scalar, and `#` starts a comment there for `safe_load`.
        if ch in "@`|>%?:#":
            raise self.fail_at(f"a plain scalar cannot start with {ch!r}; quote it", p)
        if ch == "-" and text[p + 1 : p + 2] in (" ", "\n"):
            raise self.fail_at("a plain scalar cannot start with '- ' in a flow collection", p)
        end = p
        while end < len(text) and text[end] != "\n":
            ch = text[end]
            if ch in _FLOW_STOP:
                break
            if ch == ":" and (end + 1 == len(text) or text[end + 1] in " \t\n" + _FLOW_STOP):
                break
            if ch == "#" and text[end - 1] == " ":
                break
            end += 1
        if "\t" in text[p:end]:
            raise self.fail_at(_TAB, p)
        if "?" in text[p:end]:
            # `safe_load` ends a plain scalar at `?` in a flow collection.
            raise self.fail_at("a `?` inside a flow collection's plain scalar; quote it", p)
        plain = text[p:end].strip()
        if not plain:
            raise self.fail_at("an empty entry in a flow collection", p)
        return self.resolve(plain, self.text.count("\n", 0, p)), end
