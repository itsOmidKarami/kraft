"""Terminal formatting for the `kraft` CLI.

Pure presentation: nothing here knows what a work item is, and nothing here
makes a request. `cli.py` chooses a renderer per verb and never formats inline,
so the later CLI sub-projects (a log line, a diff stat block) add functions here
rather than growing the dispatch table.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from typing import TextIO

RESET = "\033[0m"
DIM = "\033[2m"

#: Every escape `paint` can introduce. Column widths are measured with these
#: removed: a painted cell is ~9 bytes wider than it draws, and measuring the
#: painted string shears every column to its right (spec F §2.1).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: Status -> ANSI colour. Anything unlisted renders unpainted.
STATUS_COLORS = {
    "active": "\033[32m",
    "needs_human": "\033[33m",
    "paused": DIM,
    "failed": "\033[31m",
}


def use_color(stream: TextIO | None = None) -> bool:
    """Colour only into a terminal, and never when NO_COLOR is set.

    A piped `kraft view list` is consumed by something that wants text, not escapes.
    """
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except AttributeError, ValueError:
        return False


def paint(text: str, code: str, stream: TextIO | None = None) -> str:
    return f"{code}{text}{RESET}" if code and use_color(stream) else text


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def visible_width(text: str) -> int:
    """What the cell draws, not what it stores."""
    return len(strip_ansi(text))


def relative_time(iso: str | None, now: datetime | None = None) -> str:
    """ "4m ago". A board is scanned, not read; an ISO timestamp is neither.

    Never raises: an unparseable or missing timestamp renders "-" rather than
    taking down the whole table over one bad row.
    """
    if not iso:
        return "-"
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return "-"
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = ((now or datetime.now(UTC)) - when).total_seconds()
    if seconds < 60:
        return "just now"
    for size, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{suffix} ago"
    return "just now"


def table(
    rows: list[dict],
    columns: list[tuple[str, str]],
    width: int | None = None,
    headers: bool = True,
) -> str:
    """Aligned columns, no borders. The last column truncates rather than wraps.

    A wrapped table stops being scannable, which is the only reason to render a
    table instead of `--json`.

    `headers=False` is for a stream: a followed verb renders one row per frame,
    and a header above each of them is noise (Kraft-owea). Widths are still
    measured against the header text, so a streamed row cannot be narrower than
    the table it follows.
    """
    if not rows:
        return "(nothing)"
    width = width or shutil.get_terminal_size((100, 24)).columns
    cells = [[_cell(row.get(key)) for _header, key in columns] for row in rows]
    header_cells = [header for header, _key in columns]
    widths = [
        max(len(header_cells[i]), *(visible_width(row[i]) for row in cells))
        for i in range(len(columns))
    ]
    # the last column gets whatever is left, and is cut to it
    last = max(8, width - sum(widths[:-1]) - 2 * (len(columns) - 1))
    widths[-1] = min(widths[-1], last)

    def line(values: list[str]) -> str:
        parts = [
            _fit(value, widths[i]) if i == len(values) - 1 else _pad(value, widths[i])
            for i, value in enumerate(values)
        ]
        return "  ".join(parts).rstrip()

    body = [line(row) for row in cells]
    return "\n".join([paint(line(header_cells), DIM), *body] if headers else body)


def kv(pairs: list[tuple[str, str]]) -> str:
    """A detail block: labels right-padded to a common width.

    A value spanning several lines keeps the block's shape — every line after
    the first is indented to the value column, so a multi-line description reads
    as one field rather than running back to the margin.
    """
    label_width = max((len(label) for label, _value in pairs), default=0)
    indent = " " * (label_width + 2)
    return "\n".join(
        f"{label.ljust(label_width)}  {value.replace(chr(10), chr(10) + indent)}"
        for label, value in pairs
    )


def _cell(value: object) -> str:
    """None and "" are the same absence to a reader, and both read as "-"."""
    return "-" if value in (None, "") else str(value)


def _pad(value: str, width: int) -> str:
    """`ljust` that counts what the terminal shows."""
    return value + " " * max(0, width - visible_width(value))


def _fit(value: str, width: int) -> str:
    """Truncate to a visible width, dropping the colour of anything it cuts.

    Slicing a painted string can land inside an escape sequence, and a half-
    written escape corrupts the rest of the terminal line — every row after it,
    not just this cell. A correct plain cell beats a coloured broken one.
    """
    if visible_width(value) <= width:
        return value
    return strip_ansi(value)[: width - 1] + "…"


#: Log source -> ANSI colour. stderr is the one a reader is scanning for.
LOG_COLORS = {"stderr": "\033[31m", "system": DIM}


def log_line(entry: dict) -> str:
    """One JSONL log line: time, source, text. Never raises on a partial line."""
    stamp = (entry.get("t") or "")[11:19] or "--:--:--"
    src = str(entry.get("src") or "?")
    # `summary` is what logs.jsonl derives for a stream-json line; `text` is the
    # raw line, which is what a plain (non-JSON) line has and all it needs.
    text = str(entry.get("summary") or entry.get("text") or "")
    return f"{paint(stamp, DIM)} {paint(src.ljust(6), LOG_COLORS.get(src, ''))} {text}"


_EVENT_COLUMNS = [("SEQ", "seq"), ("WHEN", "when"), ("TYPE", "type"), ("DETAIL", "detail")]


def event_line(rows: list[dict], headers: bool = True) -> str:
    """The chain's history as a table. `payload` is JSON text on the wire; only
    its first line is shown, because this view is scanned, not read — `--json`
    is there for the whole thing."""
    shaped = [
        {
            "seq": row.get("seq"),
            "when": relative_time(row.get("created_at")),
            "type": row.get("type"),
            "detail": str(row.get("payload") or "").replace("\n", " ")[:200],
        }
        for row in rows
    ]
    return table(shaped, _EVENT_COLUMNS, headers=headers)


def redraw(text: str, previous_lines: int) -> int:
    """Overwrite the previous frame in place; return this frame's line count.

    Cursor-up rather than an alternate screen, so the last frame stays in the
    scrollback when you Ctrl-C — the reason to watch in a terminal at all.
    """
    if previous_lines:
        sys.stdout.write(f"\033[{previous_lines}A\033[J")
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return len(text.splitlines())


_DIFF_LINE_COLORS = (
    ("+++", "\033[1m"),
    ("---", "\033[1m"),
    ("+", "\033[32m"),
    ("-", "\033[31m"),
    ("@@", "\033[36m"),
    ("diff --git", "\033[1m"),
)


def _diff_trailer(payload: dict) -> list[str]:
    """What must follow every diff view, stat or full: files git cannot see, and
    the warning that the diff you just read was not all of it.

    One helper for both renderers so a future third view cannot forget either.
    """
    lines: list[str] = []
    if payload.get("untracked"):
        lines.append("")
        lines.append(paint("untracked (not in the diff above):", DIM))
        lines.extend(f"  {path}" for path in payload["untracked"])
    if payload.get("truncated"):
        limit = payload.get("diff_max_bytes")
        where = payload.get("worktree_path")
        lines.append("")
        lines.append(
            paint(
                "WARNING: diff truncated by the server at "
                + (f"{limit} bytes" if limit else "its size limit")
                + " — this is not the whole change. Read the rest in "
                + (where or "the worktree")
                + ".",
                "\033[33m",
            )
        )
    return lines


def _landed_head(payload: dict) -> str:
    n = len((payload.get("landed") or {}).get("commits") or [])
    return f"landed — {n} commit{'' if n == 1 else 's'} already on this branch"


def _colour_diff(diff: str) -> list[str]:
    out = []
    for line in diff.splitlines():
        code = next((c for prefix, c in _DIFF_LINE_COLORS if line.startswith(prefix)), "")
        out.append(paint(line, code))
    return out


def diff_stat(payload: dict) -> str:
    """ "How big is this" — the question asked before deciding to read it."""
    if payload.get("base_ref") is None:
        return "no baseline recorded for this work item"

    def block(files: list[dict]) -> str:
        rows = [
            {
                "path": f["path"],
                "ins": paint(f"+{f['insertions']}", "\033[32m"),
                "del": paint(f"-{f['deletions']}", "\033[31m"),
            }
            for f in files
        ]
        body = table(rows, [("PATH", "path"), ("", "ins"), ("", "del")]) if rows else "(no changes)"
        ins = sum(f["insertions"] for f in files)
        dels = sum(f["deletions"] for f in files)
        return f"{body}\n{len(files)} files, +{ins} -{dels}"

    out: list[str] = []
    landed_files = (payload.get("landed") or {}).get("files") or []
    if landed_files:
        # Landed leads: it is what the reader scrolls past, and saying so is
        # the whole point (Kraft-nceo).
        out += [
            paint(_landed_head(payload), DIM),
            block(landed_files),
            "",
            paint("in flight — the change under review", DIM),
        ]
    out.append(block(payload.get("files", [])))
    return "\n".join([*out, *_diff_trailer(payload)])


def diff_body(payload: dict) -> str:
    """The unified diff, coloured the way `git diff` colours it."""
    if payload.get("base_ref") is None:
        return "no baseline recorded for this work item"
    out: list[str] = []
    landed = (payload.get("landed") or {}).get("diff") or ""
    if landed:
        out.append(paint(_landed_head(payload), DIM))
        out += _colour_diff(landed)
        out += ["", paint("in flight — the change under review", DIM)]
    body = _colour_diff(payload.get("diff", ""))
    out += body or ["(no changes)"]
    return "\n".join([*out, *_diff_trailer(payload)])


def artifact_body(payload: dict) -> str:
    """A gate's spec/plan document, with the same truncation notice the diff
    renderer gives — `kraft view artifact` is the one surface where a reviewer
    could otherwise approve a document whose tail was silently cut, since
    `_cmd_artifact` used to page `content` straight through with no read of
    `truncated` at all."""
    out = payload.get("content", "")
    if payload.get("truncated"):
        limit = payload.get("artifact_max_bytes")
        out += "\n\n" + paint(
            "WARNING: this document was truncated by the server at "
            + (f"{limit} bytes" if limit else "its size limit")
            + " — this is not the whole file. Read the rest at "
            + (payload.get("path") or "its path in the worktree")
            + ".",
            "\033[33m",
        )
    return out


def page(text: str, *, force_plain: bool = False) -> None:
    """Through `$PAGER` on a terminal; straight to stdout otherwise.

    `less -R` is the fallback because it passes the colour codes through; a
    pager that does not would show escape garbage.
    """
    if force_plain or not use_color():
        print(text)
        return
    pager = os.environ.get("PAGER") or ("less -R" if shutil.which("less") else "")
    if not pager:
        print(text)
        return
    try:
        subprocess.run(pager, input=text.encode(), shell=True, check=False)
    except OSError:
        print(text)


def health_block(payload: dict) -> str:
    """`/health`, readable. Every degraded reason is spelled out: a status that
    says "degraded" and nothing else sends you to the browser anyway."""
    status = payload.get("status", "?")
    colour = "\033[32m" if status == "ok" else "\033[33m"
    index = payload.get("index", {})
    embeddings = index.get("embeddings", {})
    pairs = [
        ("status", paint(status, colour)),
        ("bind", str(payload.get("bind", "-"))),
        ("documents", str(index.get("documents", 0))),
        ("repos scanned", str(index.get("repos_scanned", 0))),
        ("last scan", relative_time(index.get("last_scan_at"))),
        (
            "embeddings",
            "available"
            if embeddings.get("available")
            else f"unavailable ({embeddings.get('reason') or 'no reason given'})",
        ),
    ]
    for name, reason in (payload.get("invalid_templates") or {}).items():
        pairs.append(("invalid template", f"{name}: {reason}"))
    if payload.get("invalid_policy"):
        pairs.append(("invalid policy", str(payload["invalid_policy"])))
    for error in index.get("errors") or []:
        pairs.append(("index error", str(error)))
    reattach = payload.get("reattach_summary") or {}
    if reattach.get("scanned"):
        pairs.append(
            (
                "reattached",
                f"{len(reattach.get('adopted', []))} adopted, "
                f"{len(reattach.get('resumed_work_items', []))} resumed "
                f"of {reattach['scanned']} scanned",
            )
        )
    if reattach.get("unknown"):
        pairs.append(("orphaned sessions", ", ".join(reattach["unknown"])))
    return kv(pairs)


def doctor_block(rows: list[dict]) -> str:
    """One line per check: verdict, name, detail. Scannable by the column of
    verdicts alone — a doctor whose output has to be read word by word is the
    failure mode spec E §4 names."""
    width = max((len(row["name"]) for row in rows), default=0)
    out = []
    for row in rows:
        if row.get("skipped"):
            mark = paint("skip", DIM)
        else:
            mark = paint("ok  ", "\033[32m") if row["ok"] else paint("FAIL", "\033[31m")
        out.append(f"{mark}  {row['name'].ljust(width)}  {row['detail']}".rstrip())
    failed = sum(1 for row in rows if not row["ok"])
    out.append("")
    out.append(
        f"{failed} of {len(rows)} checks failed" if failed else f"all {len(rows)} checks passed"
    )
    return "\n".join(out)
