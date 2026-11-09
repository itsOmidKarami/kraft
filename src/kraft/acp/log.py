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
    `error`, `stderr`, `line_too_long`, `cancel_timeout`, `interrupted`, `ask_withdrawn`,
    `heartbeat`, `unasked_call`, `policy_violation`, `asks_everything`, `continuing`,
    `rejection_limit`."""
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


def _first_event(path: Path, name: str) -> str | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    for rec in records(text.splitlines()):
        if rec.get("dir") == "client" and rec.get("event") == name:
            return str(rec.get("message") or "")
    return None


def config_error(path: Path) -> str | None:
    """The `message` of the first `config_error` client event in the log at `path`."""
    return _first_event(path, "config_error")


def policy_violation(path: Path) -> str | None:
    """The `message` of the first `policy_violation` client event: the agent ran a tool the
    task's policy does not allow (`client.Client.backstop`)."""
    return _first_event(path, "policy_violation")


def rejection_limit(path: Path) -> str | None:
    """The `message` of the first `rejection_limit` client event: the agent kept ending its
    turn after Kraft rejected a tool, past the launch's cap on continuations."""
    return _first_event(path, "rejection_limit")


VIOLATION_PREFIX = "tool policy violated: "
#: Begins every `rejection_limit` message (`client.Client.stop_after_rejections`).
REJECTION_PREFIX = "the agent ended its turn after Kraft rejected "
#: What a cause begins with when the session ran and was stopped, rather than failing to start.
RAN_PREFIXES = (VIOLATION_PREFIX, REJECTION_PREFIX)


def cause(text: str) -> str | None:
    """Why an ACP session stopped, from its log's text: the `message` of its `config_error`
    event, else `VIOLATION_PREFIX` + that of its `policy_violation` event, else that of its
    `rejection_limit` event (which carries its own `REJECTION_PREFIX`), else "". None when
    the text is not an ACP session log (no line of this format), so the caller keeps its own
    rule for other harnesses."""
    seen, found = False, {}
    for rec in records(text.splitlines()):
        seen = True
        if rec.get("dir") == "client" and rec.get("event") in (
            "config_error",
            "policy_violation",
            "rejection_limit",
        ):
            found.setdefault(rec["event"], str(rec.get("message") or ""))
    if not seen:
        return None
    if "config_error" in found:
        return found["config_error"]
    if "policy_violation" in found:
        return VIOLATION_PREFIX + found["policy_violation"]
    return found.get("rejection_limit", "")
