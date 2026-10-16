"""The four moments the console marks (opening, deciding, work finishing,
leaving), worded once here. Truthful only: every count is read from the board
rows, and a moment with nothing true to say says nothing."""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime
from pathlib import Path

from textual.markup import escape

from kraft.console import words
from kraft.paths import default_run_dir

#: Where the console notes when it was last closed: this instance's run
#: directory, one file, one person (each instance is single-user).
SEEN_FILE = "console-seen.json"
#: How long the since line stays, unless a key is pressed first.
SINCE_S = 60.0
#: The mark a decision's or a finish's toast leads with, in the success colour.
DONE_MARK = "[$success]✓[/]"


def seen_path() -> Path:
    """`console-seen.json` in the run directory the console's server uses, as
    `kraft admin start` resolves it."""
    return Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir()) / SEEN_FILE


def read_seen() -> float | None:
    """When the console was last closed, in seconds since the epoch; None the
    first time, or for a file that does not say."""
    try:
        seen = json.loads(seen_path().read_text())["seen_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return float(seen) if isinstance(seen, int | float) else None


def write_seen(now: float) -> None:
    """Note `now` as when the console was last closed. A run directory that
    cannot take it loses only the next since line, never the quit."""
    path = seen_path()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_text(json.dumps({"seen_at": now}))
        part.replace(path)


def _clock(seen: float, now: float) -> str:
    """`14:02` today, `Oct 14 14:02` on another day: local time."""
    at, today = datetime.fromtimestamp(seen), datetime.fromtimestamp(now)
    return f"{at:%H:%M}" if at.date() == today.date() else f"{at:%b} {at.day} {at:%H:%M}"


def _changed_since(row: dict, seen: float) -> bool:
    try:
        return datetime.fromisoformat(row.get("updated_at") or "").timestamp() > seen
    except ValueError:
        return False


def since_line(rows: list[dict], seen: float, now: float) -> str:
    """Opening (D1): what moved while the console was closed, from the rows
    whose last change came after `seen`: those done, and those that need you."""
    after = [r for r in rows if r.get("display_status") != "archived" and _changed_since(r, seen)]
    at = _clock(seen, now)
    if not after:
        return f"nothing changed since {at}"
    finished = sum(r.get("display_status") == "done" for r in after)
    needs = sum(words.group_of(r) == "needs" for r in after)
    parts = [
        f"{finished} finished" if finished else "nothing finished",
        f"{needs} need{'s' if needs == 1 else ''} you" if needs else "nothing needs you",
    ]
    return f"since {at} · {' · '.join(parts)}"


def finished(before: list[dict], rows: list[dict]) -> list[dict]:
    """The rows a live update moved into done: there before and outside the
    Done group then, so one restored from the archive has not just finished,
    and nothing has on the first read (no rows before it)."""
    was = {row["id"]: words.group_of(row) for row in before}
    return [
        row
        for row in rows
        if row.get("display_status") == "done" and was.get(row["id"], "done") != "done"
    ]


def finished_toast(done: list[dict], now: float) -> str:
    """Work finishing (D3), as toast markup: one item by its outcome in the
    Board's own words (`merged !42`, `completed`) and its title, or how many."""
    if len(done) > 1:
        return f"{DONE_MARK} {len(done)} finished"
    row = done[0]
    title = " ".join((row.get("title") or "").split())
    return f"{DONE_MARK} {escape(words.reason_tail(row, now))} · {escape(title)}"


def marked(text: str) -> str:
    """Deciding (D2): the CLI's own sentence for an approve or a reject, as
    toast markup led by the mark."""
    return f"{DONE_MARK} {escape(text)}"


def at_rest(running: int, between: str) -> str:
    """Nothing needs you, and what runs without you; `between` joins the two
    halves. The empty Needs you and the quit line both say it."""
    if not running:
        return "nothing needs you, and nothing is running"
    return f"nothing needs you{between}{running} running on their own"


def empty_needs_you(running: int) -> str:
    """The empty Needs you (D5): one sentence under its heading."""
    return words.sentence(at_rest(running, ". "))


def leaving(rows: list[dict]) -> str:
    """Leaving (D4): the one line the terminal keeps after the console quits."""
    needs = sum(words.group_of(r) == "needs" for r in rows)
    running = sum(words.group_of(r) == "running" for r in rows)
    if not needs:
        return f"kraft console · {at_rest(running, ' · ')}"
    said = f"{needs} need{'s' if needs == 1 else ''} you"
    return f"kraft console · {said}" + (f" · {running} running" if running else "")
