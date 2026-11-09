"""The session log an ACP launch writes: one JSON object per line (spec §4, §7).

The client's stdout *is* the session log, so a log is only ever read back by this format
(`usage.READERS["acp"]`, `kraft admin doctor`). Replayed history from `session/load` is
marked, never dropped, so the log stays the whole conversation and the reader skips it."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

SCHEMA = 1


def _line(direction: str, **fields) -> str:
    return json.dumps(
        {"kraft_acp": SCHEMA, "dir": direction, "t": round(time.time(), 3), **fields},
        separators=(",", ":"),
    )


def message(direction: str, msg: dict, *, replay: bool = False) -> str:
    """One ACP message, `direction` being `from_agent` or `to_agent`."""
    return _line(direction, msg=msg, **({"replay": True} if replay else {}))


def event(name: str, **fields) -> str:
    """A client event: `starting`, `session`, `prompting`, `ask`, `stop`, `config_error`,
    `error`, `stderr`, `line_too_long`, `cancel_timeout`, `interrupted`."""
    return _line("client", event=name, **fields)


def stray(text: str) -> str:
    """A line the agent printed that is not JSON-RPC (`opencode acp` prints help text
    to stdout on a bad flag)."""
    return _line("stray", text=text)


def records(lines: Iterable[str]) -> Iterator[dict]:
    """Each line that is a JSON object of this format; anything else is skipped."""
    for line in lines:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(obj, dict) and obj.get("kraft_acp") == SCHEMA:
            yield obj


def config_error(path: Path) -> str | None:
    """The `message` of the first `config_error` client event in the log at `path`."""
    try:
        text = path.read_text()
    except OSError:
        return None
    for rec in records(text.splitlines()):
        if rec.get("dir") == "client" and rec.get("event") == "config_error":
            return str(rec.get("message") or "")
    return None
