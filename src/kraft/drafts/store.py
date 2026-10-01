"""`config_drafts` rows: one draft per (area, key), each a map of the files it
changes, the hash each file had when it joined (the publish's stale check),
and an undo stack of one entry per request.

Every file name is relative to `templates_dir`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import yaml

from kraft.api.routes.settings import _CHAIN_ID
from kraft.store import _now as _now  # test seam for the coalescing window

#: Undo depth, as the prototype keeps it.
HISTORY_CAP = 80
#: Consecutive PUTs to one file this close together are one undo step.
COALESCE_S = 4.0


@dataclass(frozen=True)
class Area:
    #: Whether `key` names something in this area; the route answers 400 when not.
    valid: Callable[[str], bool]
    #: The files a draft of `key` may hold.
    files: Callable[[str], tuple[str, ...]]
    #: Whether a `rename` op may move the draft's file to the one `files`
    #: gives the new key, the old file becoming null.
    renames: bool = False


#: W13 adds `harnesses`, `repos`, `policy` and `intake`. Ops join more files
#: than `files` lists: `move_to_library` the library to a chain's draft, a
#: library rename each chain file it rewrites to the library's.
AREAS: dict[str, Area] = {
    "chains": Area(
        valid=_CHAIN_ID.fullmatch, files=lambda key: (f"chains/{key}.yaml",), renames=True
    ),
    "library": Area(valid=lambda key: key == "library", files=lambda key: ("library.yaml",)),
}


def digest(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode()).hexdigest()


def _parsed(text: str | None) -> object:
    try:
        return None if text is None else yaml.safe_load(text)
    except yaml.YAMLError:
        return object()  # equal to nothing, another unparseable text included


def unchanged(files: dict[str, str | None], published: dict[str, str | None]) -> bool:
    """Whether every file parses to what its published text parses to: the
    prototype's `unchanged`, which drops the draft."""
    return all(_parsed(text) == _parsed(published.get(f)) for f, text in files.items())


def get(conn: sqlite3.Connection, area: str, key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM config_drafts WHERE area = ? AND key = ?", (area, key)
    ).fetchone()
    if row is None:
        return None
    draft = dict(row)
    for column in ("files", "base", "serialized", "history"):
        draft[column] = json.loads(draft[column])
    return draft


def list_drafts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT area, key, files, changes, problems, updated_at FROM config_drafts "
        "ORDER BY updated_at DESC"
    ).fetchall()
    return [{**dict(r), "files": sorted(json.loads(r["files"]))} for r in rows]


def delete(conn: sqlite3.Connection, area: str, key: str) -> bool:
    return (
        conn.execute("DELETE FROM config_drafts WHERE area = ? AND key = ?", (area, key)).rowcount
        == 1
    )


def _store(conn, area, key, files, published, old_base, serialized, history, counts) -> bool:
    """Write the row, or delete it when `files` is back to the published
    state. False when the draft is gone."""
    if unchanged(files, published):
        delete(conn, area, key)
        return False
    # A file keeps the base it joined with; one that joins now records its own.
    base = {f: old_base[f] if f in old_base else digest(published.get(f)) for f in files}
    conn.execute(
        "INSERT OR REPLACE INTO config_drafts (area, key, files, base, serialized, history, "
        "changes, problems, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            area,
            key,
            json.dumps(files),
            json.dumps(base),
            json.dumps(sorted(set(serialized) & set(files))),
            json.dumps(history),
            *counts,
            _now(),
        ),
    )
    return True


def write(
    conn: sqlite3.Connection,
    area: str,
    key: str,
    files: dict[str, str | None],
    published: dict[str, str | None],
    *,
    serialized: list[str],
    counts: tuple[int, int],
    typed: str | None = None,
) -> bool:
    """One request's write: `files` replaces the draft's, and the files it
    replaced go on the undo stack as one entry. `typed` names the file a PUT
    wrote as typed; consecutive PUTs to it inside `COALESCE_S` share the entry
    the first of them pushed. `counts` is (changes, problems) as of this write.
    False when the write put the draft back to `published`, deleting it."""
    old = get(conn, area, key)
    history = old["history"] if old else []
    now = _now()
    top = history[-1] if history else None
    if (
        typed is not None
        and top is not None
        and top["typed"] == typed
        and (datetime.fromisoformat(now) - datetime.fromisoformat(top["at"])).total_seconds()
        < COALESCE_S
    ):
        top["at"] = now
    else:
        before = {"files": {}, "serialized": []} if old is None else old
        entry = {"files": before["files"], "serialized": before["serialized"]}
        history = [*history, {**entry, "typed": typed, "at": now}][-HISTORY_CAP:]
    base = old["base"] if old else {}
    return _store(conn, area, key, files, published, base, serialized, history, counts)


def undo(
    conn: sqlite3.Connection,
    area: str,
    key: str,
    published: dict[str, str | None],
    counts: tuple[int, int],
) -> bool | None:
    """Pop one entry: the draft as it was before the last request. `None` when
    there is nothing to undo; otherwise whether a draft is left. `counts` is
    the popped state's (changes, problems)."""
    old = get(conn, area, key)
    if old is None or not old["history"]:
        return None
    *history, entry = old["history"]
    files = entry["files"]
    return _store(
        conn, area, key, files, published, old["base"], entry["serialized"], history, counts
    )
