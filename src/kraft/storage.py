"""How much disk the run folder uses, and what `storage.worktrees` in
`policy.yaml` decides from it.

One walk measures everything; it takes seconds on a real instance, so it runs
in a thread and its result is cached on `app.state.storage_usage`. Every
decision (the state, whether one start is held) reads that cache. Nothing on a
request path or in the start queue's tick may walk.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from kraft import render
from kraft.store import _now
from kraft.vocab import Verb, admitting
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

#: Top-level run folders by the category the Storage page shows them under.
#: Everything archiving an item removes is `worktrees` or `sandboxes`, and
#: those two are the figure the quota and limit govern.
_CATEGORY = {
    "worktrees": "worktrees",
    "sandbox-git": "sandboxes",
    "sandbox-home": "sandboxes",
    "logs": "logs",
    "results": "results",
    "attachments": "attachments",
}
CATEGORIES = ("worktrees", "sandboxes", "logs", "results", "databases", "attachments", "other")
#: Folders whose children are named by work item id.
_PER_ITEM = ("worktrees", "sandbox-home")


@dataclass(frozen=True)
class Usage:
    measured_at: str
    #: Bytes the quota and limit are compared with.
    governed: int
    #: Bytes per work item id.
    # shortcut: a sandboxed item's ref store (`sandbox-git/`) is in `governed`
    # but not here, so archiving frees a little more than this says; attribute
    # it through `worker.refstore` if the Storage page's rows must be exact.
    items: dict[str, int]
    categories: dict[str, int]


def _children(path: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except OSError:
        return []  # missing, unreadable, or removed while we walked


def _bytes(entry: os.DirEntry, seen: set[tuple[int, int]], stop) -> int:
    """Disk blocks under `entry`, symlinks not followed, a hard-linked file once."""
    total = 0
    stack = [entry]
    while stack and not (stop is not None and stop.is_set()):
        current = stack.pop()
        try:
            info = current.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode):
            stack.extend(_children(Path(current.path)))
        elif info.st_nlink > 1:
            key = (info.st_dev, info.st_ino)
            if key in seen:
                continue
            seen.add(key)
        total += info.st_blocks * 512
    return total


def measure(base: Path, stop: threading.Event | None = None) -> Usage:
    """Walk the run folder. Blocking: call it through `refresh`. `stop` ends it
    early at shutdown; what it returns then is partial and is not cached."""
    # One entry per hard-linked file (uv and pnpm link from their caches):
    # large on a big instance, and freed when the walk returns.
    seen: set[tuple[int, int]] = set()
    items: dict[str, int] = {}
    categories = dict.fromkeys(CATEGORIES, 0)
    for top in _children(base):
        category = _CATEGORY.get(top.name) or ("databases" if ".db" in top.name else "other")
        if top.name in _PER_ITEM:
            for child in _children(Path(top.path)):
                size = _bytes(child, seen, stop)
                items[child.name] = items.get(child.name, 0) + size
                categories[category] += size
        else:
            categories[category] += _bytes(top, seen, stop)
    return Usage(_now(), categories["worktrees"] + categories["sandboxes"], items, categories)


def usage(st) -> Usage | None:
    """The last measurement, or None before the first."""
    return getattr(st, "storage_usage", None)


def state_of(policy, used: Usage | None) -> str | None:
    """`ok`, `over_quota` or `held`; None without a limit or a measurement.
    Read off the measurement alone, so a restart cannot change the answer."""
    if policy is None or policy.storage_limit_bytes is None or used is None:
        return None
    if used.governed > policy.storage_limit_bytes:
        return "held"
    if used.governed > policy.storage_quota_bytes:
        return "over_quota"
    return "ok"


def health(st) -> dict | None:
    """`/health`'s `storage`; None without a limit or before the first walk."""
    used = usage(st)
    state = state_of(st.policy, used)
    if state is None:
        return None
    return {
        "state": state,
        "used_bytes": used.governed,
        "quota_bytes": st.policy.storage_quota_bytes,
        "limit_bytes": st.policy.storage_limit_bytes,
        "measured_at": used.measured_at,
        "too_recent": getattr(st, "storage_too_recent", 0),
    }


def holds(st, wid: str) -> bool:
    """Whether starting `wid` must wait for space. Only a start that would
    create a worktree: an item that has one adds little by continuing, and
    finishing it is what makes it archivable."""
    return state_of(st.policy, usage(st)) == "held" and not (st.run_dirs.worktrees / wid).exists()


def figures(st) -> dict:
    """What a held start is told. Call only while `state_of` is not None."""
    return {"used_bytes": usage(st).governed, "limit_bytes": st.policy.storage_limit_bytes}


def forget(st, wid: str) -> None:
    """Take a removed item's bytes off the cache, so an archive or abandon is
    seen without a walk, off `categories` too (worktrees first, then sandboxes,
    so the two still add up to `governed`). The next walk reconciles."""
    used = usage(st)
    if used is None or wid not in used.items:
        return
    items = {k: v for k, v in used.items.items() if k != wid}
    categories = dict(used.categories)
    left = used.items[wid]
    for key in ("worktrees", "sandboxes"):
        take = min(left, categories.get(key, 0))
        if take:
            categories[key] -= take
            left -= take
    st.storage_usage = dataclasses.replace(
        used, governed=max(0, used.governed - used.items[wid]), items=items, categories=categories
    )


async def refresh(app) -> Usage:
    """Measure and cache. One walk at a time: a caller during a walk waits for
    that walk instead of starting a second."""
    st = app.state
    walk = getattr(st, "_storage_walk", None)
    if walk is None or walk.done():
        st._storage_stop = threading.Event()
        walk = st._storage_walk = asyncio.ensure_future(
            asyncio.to_thread(measure, st.run_dirs.base, st._storage_stop)
        )
    used = await asyncio.shield(walk)
    if st._storage_stop.is_set():
        return used  # stopped at shutdown: partial, not cached
    st.storage_usage = used
    # An item archived or abandoned while the walk ran was measured before it
    # went: take it off again, or the walk would put it back.
    for wid in list(st.storage_usage.items):
        if not any((st.run_dirs.base / top / wid).exists() for top in _PER_ITEM):
            forget(st, wid)
    return st.storage_usage


_INTERVAL_S = 600


async def tick(app) -> list[str]:
    """Measure, when a limit is set. Over the limit with `auto_cleanup` set,
    archive finished items that ended at least `min_age` ago, oldest first,
    until usage is back at or under quota. Returns the ids archived."""
    st = app.state
    st.storage_too_recent = 0
    if st.policy is None or st.policy.storage_limit_bytes is None:
        return []
    await refresh(app)
    min_age = st.policy.storage_auto_cleanup_min_age_s
    if min_age is None or state_of(st.policy, usage(st)) != "held":
        return []
    from kraft.api.routes import lifecycle  # deferred: kraft.api starts this poller

    cutoff = (datetime.fromisoformat(_now()) - timedelta(seconds=min_age)).isoformat()
    finished = st.db.read(
        lambda c: c.execute(
            f"SELECT * FROM work_items WHERE status IN ({in_list(admitting(Verb.ARCHIVE))}) "
            "AND archived_at IS NULL ORDER BY updated_at, id"
        ).fetchall()
    )
    finished = [r for r in finished if (st.run_dirs.worktrees / r["id"]).is_dir()]
    # Read once: a reload that drops the limit mid-loop must not change the target.
    quota = st.policy.storage_quota_bytes
    archived: list[str] = []
    stuck = 0
    for n, row in enumerate(finished):
        if usage(st).governed <= quota:
            break
        if row["updated_at"] > cutoff:
            # Ordered by `updated_at`: every row after this one is newer still.
            st.storage_too_recent = len(finished) - n
            break
        try:
            await lifecycle._archive_one(app, row, "auto", reason="storage")
        except Exception:  # noqa: BLE001 -- one row's failure must not keep the rest
            logger.exception("storage: archiving %s failed", row["id"])
            continue
        # The directory, not `worktree_removed` (see `_archive_one`).
        if not (st.run_dirs.worktrees / row["id"]).exists():
            archived.append(row["id"])
            stuck = 0
            continue
        # Archived, but its worktree stayed (a failed rescue, or git failing).
        # Three in a row is a broken repo or disk, not three unlucky items:
        # stop marking finished items archived without freeing anything.
        stuck += 1
        if stuck == 3:
            logger.error("storage: three archives in a row freed nothing; stopping this tick")
            break
    if archived:
        logger.info(
            "storage: over limit, archived %s; worktrees now use %s",
            ", ".join(archived),
            render.human_size(usage(st).governed),
        )
    return archived


def kick(app) -> None:
    """Start a `tick` without waiting for it, and keep the task on `app.state`:
    asyncio holds a task only weakly, so one nobody references can be collected
    before its walk ends. A kick during a kick starts nothing; `refresh` is
    single-flight, so the one running already measures what the caller changed."""
    st = app.state
    task = getattr(st, "storage_kick", None)
    if task is None or task.done():
        st.storage_kick = asyncio.ensure_future(tick(app))


async def poller(app) -> None:
    """`tick` at startup and every ten minutes until cancelled."""
    while True:
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("storage tick failed")
        await asyncio.sleep(_INTERVAL_S)


def stop(st) -> None:
    """End a walk in flight. A walk takes many seconds and runs in a thread the
    event loop joins at close, so shutdown would wait for it."""
    event = getattr(st, "_storage_stop", None)
    if event is not None:
        event.set()


def refusal(row) -> str | None:
    """Why `row` cannot be archived, or None: the archive route's own two checks
    (`lifecycle.archive_work_item`), asked without archiving. `row` is None for
    an id that names no work item."""
    if row is None:
        return "unknown work item"
    if row["archived_at"]:
        return "already archived"
    if row["status"] not in admitting(Verb.ARCHIVE):
        return "only a completed or abandoned item can be archived"
    return None


def reclaimable(row) -> bool:
    """Whether archiving `row` would free its worktree: completed or abandoned,
    and not archived yet."""
    return refusal(row) is None


def report(policy, used: Usage, rows: dict) -> dict:
    """`GET /storage`'s answer: the cached measurement joined to the work item
    `rows` (by id). An id with a row is an item, largest first; an id without
    one is an orphan, which the page lists read-only."""
    items = [
        {
            "id": wid,
            "title": row["title"],
            "status": row["status"],
            "archived": bool(row["archived_at"]),
            "bytes": size,
            "updated_at": row["updated_at"],
            "reclaimable": reclaimable(row),
        }
        for wid, size in used.items.items()
        if (row := rows.get(wid)) is not None
    ]
    items.sort(key=lambda item: -item["bytes"])
    return {
        "measured_at": used.measured_at,
        "state": state_of(policy, used),
        "used_bytes": used.governed,
        "quota_bytes": policy.storage_quota_bytes if policy else None,
        "limit_bytes": policy.storage_limit_bytes if policy else None,
        "reclaimable_bytes": sum(item["bytes"] for item in items if item["reclaimable"]),
        "categories": used.categories,
        "items": items,
        "orphans": [
            {"name": name, "bytes": size}
            for name, size in sorted(used.items.items(), key=lambda kv: -kv[1])
            if name not in rows
        ],
    }


def after(policy, used: Usage, freed: int) -> tuple[int, str | None]:
    """The governed figure with `freed` bytes taken off, and the state it would
    be in; None without a limit. What a clean-up's confirmation says."""
    left = max(0, used.governed - freed)
    return left, state_of(policy, dataclasses.replace(used, governed=left))
