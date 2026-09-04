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


def classify(line: str) -> tuple[str, str | None]:
    """(source, timestamp) for one raw log line."""
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return "stdout", None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return "stdout", None
    if not isinstance(obj, dict):
        return "stdout", None
    kind = obj.get("type")
    src = "tool" if kind in ("tool_use", "tool_result") else "agent"
    t = obj.get("timestamp") or obj.get("t")
    return src, t if isinstance(t, str) else None


def read_times(path: Path) -> dict[int, str]:
    """Line number -> timestamp, from the sidecar the adapter's drain thread writes.

    An adopted session (reattached after a restart) has no sidecar, and neither
    does a log written before this existed — both read as an empty map and the
    time column stays blank rather than lying.
    """
    times: dict[int, str] = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
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
        text = path.read_text(errors="replace")
    except OSError:
        return
    times = read_times(times_path(path))
    for n, line in enumerate(text.splitlines()):
        if n < start_line:
            continue
        src, t = classify(line)
        yield {"n": n, "t": t or times.get(n), "src": src, "text": line}
