"""Reading a worker session's log back as structured lines (design 6c).

The log file is the child process's raw stdout+stderr — Kraft never sees the
lines as they are written, so there are no per-line timestamps to report. An
agent line that is itself JSON may carry its own; everything else reports
``t: None`` and the modal leaves the time column blank.

ponytail: no timestamps of our own until the adapter pipes the child's output
through the parent instead of handing it an fd (Kraft-7z6.19).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

#: Source classes the log modal filters on.
SOURCES = ("sys", "stdout", "agent", "tool")


def split_lines(text: str) -> list[str]:
    """The one line-numbering rule, shared by everything that numbers log lines.

    `str.splitlines()` splits on `\\r`, `\\v`, `\\x1c` and U+2028 as well as
    `\\n`; a reader watching a byte stream for `b"\\n"` cannot. Rather than let
    the writer (`adapters.subprocess._watch_log`) and the reader (`jsonl`)
    disagree about which line is line 4 -- the sidecar between them is keyed by
    that number -- both split on `"\\n"` alone. The trailing empty element a
    newline-terminated file leaves behind is dropped; a file that ends mid-line
    still counts that line.

    Callers must read the file with `newline=""`: the default universal-
    newlines mode folds a bare `\\r` into `\\n` on the way in, which would move
    a line boundary this function never put there.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


#: Rendered lines are capped here. The modal renders every line and a
#: `tool_result` for a large file read is tens of KB; the plain-text endpoint
#: (`FileResponse`, kraft.api.routes.sessions) serves the file itself and stays whole.
MAX_TEXT = 2000


def _shorten(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _decode(line: str) -> dict | None:
    """The line as a JSON object, or None if it is not one."""
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _blocks(obj: dict) -> list[dict]:
    """`message.content[]`, where stream-json nests tool use and text.

    Every block, not just the first: an assistant message routinely leads with
    a `thinking` or `text` block and carries its `tool_use` after it.
    """
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _classify_obj(obj: dict | None) -> tuple[str, str | None]:
    if obj is None:
        return "stdout", None
    kind = obj.get("type")
    if kind in ("system", "result"):
        # the stream's own bookkeeping: the init line, the rate-limit lines and
        # the final envelope are the session speaking, not the agent
        src = "sys"
    elif kind in ("tool_use", "tool_result") or any(
        b.get("type") in ("tool_use", "tool_result") for b in _blocks(obj)
    ):
        src = "tool"
    else:
        src = "agent"
    t = obj.get("timestamp") or obj.get("t")
    return src, t if isinstance(t, str) else None


def classify(line: str) -> tuple[str, str | None]:
    """(source, timestamp) for one raw log line."""
    return _classify_obj(_decode(line))


def _tool_target(inp: object) -> str:
    """The one argument worth naming beside a tool: its path, pattern or command."""
    if not isinstance(inp, dict):
        return ""
    for key in ("file_path", "path", "pattern", "command", "url", "query"):
        value = inp.get(key)
        if isinstance(value, str) and value:
            return _shorten(value, 120)
    return ""


def summary(obj: dict | None, line: str) -> str:
    """One readable line for the modal and for `kraft view logs`.

    A wall of raw stream-json is not the log Kraft-77z asks for. `text` still
    carries the raw line and the plain-text endpoint still serves the file, so
    nothing is lost -- this is the column a reader scans.
    """
    if obj is None:
        return _shorten(line)
    kind = obj.get("type")
    if kind == "system":
        model = obj.get("model")
        if isinstance(model, str) and model:
            return f"init {model}"
        return f"system {obj.get('subtype') or ''}".strip()
    if kind == "result":
        return "result: error" if obj.get("is_error") else "result: success"
    for block in _blocks(obj):
        btype = block.get("type")
        if btype == "tool_use":
            return _shorten(
                f"{block.get('name') or 'tool'}({_tool_target(block.get('input'))})", 200
            )
        if btype == "tool_result":
            return "tool result"
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return _shorten(text.strip().splitlines()[0], 200)
        if btype in ("thinking", "redacted_thinking"):
            # `thinking` carries the model's private reasoning -- often empty
            # under extended thinking + prompt caching -- and its `signature`
            # is a base64 blob no reader wants dumped raw.
            thinking = block.get("thinking")
            if isinstance(thinking, str) and thinking.strip():
                return _shorten("thinking: " + thinking.strip().splitlines()[0], 200)
            return "thinking"
    # A shape this function doesn't know yet (a new stream-json event kind).
    # Its bare `type` is still a real word, unlike the raw JSON -- Kraft-5x45w.
    if isinstance(kind, str) and kind:
        return _shorten(kind)
    return _shorten(line)


def read_times(path: Path) -> dict[int, str]:
    """Line number -> timestamp, from the sidecar the adapter's drain thread writes.

    An adopted session (reattached after a restart) has no sidecar, and neither
    does a log written before this existed — both read as an empty map and the
    time column stays blank rather than lying.
    """
    times: dict[int, str] = {}
    try:
        for line in split_lines(path.read_text(errors="replace", newline="")):
            _stamp(line, times, 0)
    except OSError:
        pass
    return times


def _stamp(line: str | bytes, times: dict[int, str], first: int) -> None:
    """One sidecar row into `times`, if it names a line numbered `first` or later."""
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return
    if isinstance(row, dict) and isinstance(row.get("n"), int) and row["n"] >= first:
        times[row["n"]] = row.get("t")


def times_path(log_path: Path) -> Path:
    return log_path.with_suffix(".times.jsonl")


#: What any reader hands back of one log, at most: the last this-many bytes. A
#: runaway session wrote 187MB into one log (Kraft-2vvus); the whole-file view
#: is the plain-text endpoint, which streams the file from disk.
MAX_LOG_BYTES = 2 * 1024 * 1024
#: One line is held in memory up to this size; the rest of it is read past.
#: Rendering keeps only `MAX_TEXT` characters of it anyway.
MAX_LINE_BYTES = 1024 * 1024
_CHUNK = 64 * 1024


def _row(n: int, line: str, times: dict[int, str]) -> dict:
    obj = _decode(line)
    src, t = _classify_obj(obj)
    return {
        "n": n,
        "t": t or times.get(n),
        "src": src,
        "text": _shorten(line),
        "summary": summary(obj, line),
    }


def _marker(lines: int, nbytes: int) -> dict:
    """The row that says a reader was handed a tail, not the log. `n` is -1:
    it is no line of the file, and it sorts before every line that is."""
    text = (
        f"… {lines} earlier lines ({nbytes} bytes) not shown: this log is over "
        f"{MAX_LOG_BYTES} bytes, so only its end is read. The plain-text log has all of it."
    )
    return {
        "n": -1,
        "t": None,
        "src": "sys",
        "text": text,
        "summary": text,
        "truncated": {"lines": lines, "bytes": nbytes},
    }


class Tail:
    """One reader's place in one session log: each byte is read once.

    The first read opens on the last `MAX_LOG_BYTES` of the file, at a line
    boundary, and says so with a `_marker` row; every read after it seeks to
    where the last one stopped. A line still being written is held back until
    its newline arrives, or until the `final` read after the session ended --
    `split_lines`' rule that a file ending mid-line still counts that line. The
    sidecar of timestamps is read forward the same way.
    """

    def __init__(self, path: Path):
        self.path = path
        self._offset: int | None = None  # None until the file has been opened
        self._n = 0
        self._pending = bytearray()
        self._pending_open = False  # a line has begun that has no newline yet
        self._times: dict[int, str] = {}

    def read(self, *, final: bool = False) -> list[dict]:
        """The rows written since the last read. A missing log is no rows."""
        try:
            with self.path.open("rb") as fh:
                rows = [] if self._offset is not None else self._open_window(fh)
                fh.seek(self._offset or 0)
                lines = self._complete_lines(fh, final)
                self._offset = fh.tell()
        except OSError:
            return []
        first = self._n - len(lines)
        self._read_times(first)
        rows += [_row(first + i, line, self._times) for i, line in enumerate(lines)]
        self._times = {n: t for n, t in self._times.items() if n >= self._n}
        return rows

    def _open_window(self, fh) -> list[dict]:
        """Skip to the last `MAX_LOG_BYTES`, counting the lines skipped."""
        start = os.fstat(fh.fileno()).st_size - MAX_LOG_BYTES
        if start <= 0:
            self._offset = 0
            return []
        while chunk := fh.read(min(_CHUNK, start - fh.tell())):
            self._n += chunk.count(b"\n")
        fh.seek(start - 1)
        if fh.read(1) != b"\n":  # the window opens mid-line: skip that line whole
            while (part := fh.readline(_CHUNK)) and not part.endswith(b"\n"):
                pass
            self._n += 1 if part else 0
        self._offset = fh.tell()
        return [_marker(self._n, self._offset)]

    def _complete_lines(self, fh, final: bool) -> list[str]:
        lines = []
        while chunk := fh.readline(_CHUNK):
            self._pending_open = True
            room = MAX_LINE_BYTES - len(self._pending)
            if room > 0:
                self._pending += chunk[:room]
            if chunk.endswith(b"\n"):
                lines.append(self._take())
        if final and self._pending_open:
            lines.append(self._take())
        return lines

    def _take(self) -> str:
        line = bytes(self._pending).removesuffix(b"\n")
        self._pending.clear()
        self._pending_open = False
        self._n += 1
        return line.decode("utf-8", errors="replace")

    def _read_times(self, first: int) -> None:
        """New sidecar rows, keeping only lines numbered `first` or later."""
        try:
            with times_path(self.path).open("rb") as fh:
                fh.seek(self._times_offset)
                while (row := fh.readline(_CHUNK)).endswith(b"\n"):
                    _stamp(row, self._times, first)
                    self._times_offset = fh.tell()
        except OSError:
            pass


def jsonl(path: Path, *, start_line: int = 0) -> Iterator[dict]:
    """Structured lines from `start_line` onward, bounded as `Tail` bounds them,
    behind a truncation marker when it had to. Missing file yields nothing."""
    for row in Tail(path).read(final=True):
        if row["n"] >= start_line or "truncated" in row:
            yield row
