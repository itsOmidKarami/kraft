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
#: (`FileResponse`, api.py) serves the file itself and stays whole.
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
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and isinstance(row.get("n"), int):
                times[row["n"]] = row.get("t")
    except OSError:
        pass
    return times


def times_path(log_path: Path) -> Path:
    return log_path.with_suffix(".times.jsonl")


def jsonl(path: Path, *, start_line: int = 0) -> Iterator[dict]:
    """Structured lines from `start_line` onward. Missing file yields nothing."""
    try:
        text = path.read_text(errors="replace", newline="")
    except OSError:
        return
    times = read_times(times_path(path))
    for n, line in enumerate(split_lines(text)):
        if n < start_line:
            continue
        obj = _decode(line)
        src, t = _classify_obj(obj)
        yield {
            "n": n,
            "t": t or times.get(n),
            "src": src,
            "text": _shorten(line),
            "summary": summary(obj, line),
        }
