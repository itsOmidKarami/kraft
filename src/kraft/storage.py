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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from kraft import item_paths, render
from kraft.item_paths import ITEMS
from kraft.store import _now
from kraft.vocab import Verb, admitting
from kraft.vocab.sql import in_list
from kraft.worker import tree_walk

logger = logging.getLogger(__name__)

#: Top-level run folders by the category the Storage page shows them under.
#: Everything a worker can write (`worktrees`, `sandboxes`, and the
#: `documents` and session output it writes outside its worktree) is the
#: figure the quota and limit govern: what an agent can grow at will.
_CATEGORY = {
    "worktrees": "worktrees",
    "sandbox-git": "sandboxes",
    "sandbox-home": "sandboxes",
    "logs": "logs",
    "results": "results",
    "attachments": "attachments",
}
CATEGORIES = (
    "worktrees",
    "sandboxes",
    "documents",
    "logs",
    "results",
    "databases",
    "attachments",
    "other",
)
#: The categories the quota and limit govern.
GOVERNED = ("worktrees", "sandboxes", "documents")
#: Folders whose children are named by work item id.
_PER_ITEM = ("worktrees", "sandbox-home")
#: What of an item's own folder, `items/<id>/<name>`, counts where
#: (`kraft.item_paths`). `docs/` is the worker's to write at any size, so it
#: is governed, as is each session's `out/` (below); `history/` and `filed/`
#: are Kraft's copies, capped in size, and `sessions/` Kraft's own files.
#: Only the sandbox home is also the item's in `items`: archiving removes
#: it, and keeps the rest.
_IN_ITEM = {
    "sandbox-home": "sandboxes",
    "docs": "documents",
    "history": "attachments",
    "filed": "attachments",
    "sessions": "logs",
}


@dataclass(frozen=True)
class Usage:
    measured_at: str
    #: Bytes the quota and limit are compared with.
    governed: int
    #: Bytes per work item id: its worktree and sandbox home, what archiving
    #: frees.
    # shortcut: a sandboxed item's ref store (`sandbox-git/`) is in `governed`
    # but not here, so archiving frees a little more than this says; attribute
    # it through `worker.refstore` if the Storage page's rows must be exact.
    items: dict[str, int]
    categories: dict[str, int]
    #: Bytes per work item id of its `documents` (its `docs/` and sessions'
    #: `out/`): governed, and kept by archiving, so apart from `items`.
    documents: dict[str, int] = field(default_factory=dict)


def archivable(used: Usage) -> int:
    """The part of the governed figure archiving can free: all of it but the
    items' `documents`, which archiving keeps and only abandoning removes."""
    return used.governed - used.categories.get("documents", 0)


def _children(path: Path) -> list[os.DirEntry]:
    """The entries of a folder Kraft owns, a link to one followed: `run/`,
    `worktrees/`, `items/` and an item's folder. Never a worker's folder."""
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except OSError:
        return []  # missing, unreadable, or removed while we walked


def _blocks(info: os.stat_result, seen: set[tuple[int, int]]) -> int:
    """`info`'s disk blocks, a hard-linked file counted once (uv and pnpm
    link from their caches)."""
    if not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1:
        key = (info.st_dev, info.st_ino)
        if key in seen:
            return 0
        seen.add(key)
    return info.st_blocks * 512


def _bytes(path: str | os.PathLike[str], seen: set[tuple[int, int]], stop) -> int:
    """Disk blocks under `path`, a hard-linked file once, no link followed.

    `path` itself is not followed either: a link there counts as the link.
    Below it, `tree_walk.walk` opens each folder by its name in its parent's
    descriptor, with no link followed, and lists it by its own, so a worker
    swapping a folder in its worktree or home for a link to `/` while the
    walk runs makes it count the link, never what it points at; and the walk
    holds a bounded number of descriptors, in time linear in the folders,
    however deep they go. A folder that cannot be opened or listed is
    skipped, never an error."""
    if stop is not None and stop.is_set():
        return 0
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    total = _blocks(info, seen)
    if not stat.S_ISDIR(info.st_mode):
        return total
    try:
        root = os.open(path, tree_walk.DIR_FLAGS)
    except OSError:
        return total

    def count(_dir_fd: int, _name: str, st: os.stat_result) -> bool:
        nonlocal total
        total += _blocks(st, seen)
        return stat.S_ISDIR(st.st_mode)

    try:
        tree_walk.walk(root, count, stop=stop)
    finally:
        os.close(root)
    return total


def _item_bytes(item: Path, seen: set[tuple[int, int]], stop) -> dict[str, int]:
    """One item folder's bytes by category: each part of it as `_IN_ITEM`
    says, and each session's `out/`, the worker's, as `documents`."""
    sizes: dict[str, int] = {}
    for part in _children(item):
        kind = _IN_ITEM.get(part.name, "other")
        if part.name != "sessions" or not part.is_dir(follow_symlinks=False):
            sizes[kind] = sizes.get(kind, 0) + _bytes(part.path, seen, stop)
            continue
        # Kraft makes `sessions/` and each session's folder, and no worker
        # can write either; only `out/` in one is the worker's.
        logs = _bytes_of_folder_itself(part.path, seen)
        for session in _children(Path(part.path)):
            if not session.is_dir(follow_symlinks=False):
                logs += _bytes(session.path, seen, stop)
                continue
            logs += _bytes_of_folder_itself(session.path, seen)
            for entry in _children(Path(session.path)):
                size = _bytes(entry.path, seen, stop)
                if entry.name == "out":
                    sizes["documents"] = sizes.get("documents", 0) + size
                else:
                    logs += size
        sizes["logs"] = sizes.get("logs", 0) + logs
    return sizes


def _bytes_of_folder_itself(path: str, seen: set[tuple[int, int]]) -> int:
    """A folder's own blocks, not what is in it."""
    try:
        return _blocks(os.lstat(path), seen)
    except OSError:
        return 0


def measure(base: Path, stop: threading.Event | None = None) -> Usage:
    """Walk the run folder. Blocking: call it through `refresh`. `stop` ends it
    early at shutdown; what it returns then is partial and is not cached."""
    # One entry per hard-linked file (uv and pnpm link from their caches):
    # large on a big instance, and freed when the walk returns.
    seen: set[tuple[int, int]] = set()
    items: dict[str, int] = {}
    documents: dict[str, int] = {}
    categories = dict.fromkeys(CATEGORIES, 0)
    for top in _children(base):
        category = _CATEGORY.get(top.name) or ("databases" if ".db" in top.name else "other")
        # `items/` and the per-item folders are Kraft's, and a link to one
        # (moved to another disk) is followed, as `worktrees/` always was.
        if top.name == ITEMS and top.is_dir():
            for item in _children(Path(top.path)):
                if not item.is_dir():
                    continue
                for kind, size in _item_bytes(Path(item.path), seen, stop).items():
                    categories[kind] += size
                    if kind == "sandboxes":
                        items[item.name] = items.get(item.name, 0) + size
                    elif kind == "documents" and size:
                        documents[item.name] = documents.get(item.name, 0) + size
        elif top.name in _PER_ITEM:
            for child in _children(Path(top.path)):
                size = _bytes(child.path, seen, stop)
                items[child.name] = items.get(child.name, 0) + size
                categories[category] += size
        else:
            categories[category] += _bytes(top.path, seen, stop)
    governed = sum(categories[c] for c in GOVERNED)
    return Usage(_now(), governed, items, categories, documents)


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
    """Take an archived or abandoned item's worktree and sandbox home
    (`items`) off the cache, so the change is seen without a walk: off
    `worktrees` first, then `sandboxes`, and off `governed`. Its `documents`
    stay: archiving keeps them (`forget_documents` is the abandon's). The
    next walk reconciles."""
    used = usage(st)
    if used is None or wid not in used.items:
        return
    categories = dict(used.categories)
    left = used.items[wid]
    for key in ("worktrees", "sandboxes"):
        take = min(left, categories.get(key, 0))
        if take:
            categories[key] -= take
            left -= take
    st.storage_usage = dataclasses.replace(
        used,
        governed=max(0, used.governed - used.items[wid]),
        items={k: v for k, v in used.items.items() if k != wid},
        categories=categories,
    )


def forget_documents(st, wid: str) -> None:
    """Take an abandoned item's `documents` off the cache, off `governed`
    too: abandoning removes its folder. The next walk reconciles."""
    used = usage(st)
    if used is None or wid not in used.documents:
        return
    size = used.documents[wid]
    categories = dict(used.categories)
    categories["documents"] = max(0, categories.get("documents", 0) - size)
    st.storage_usage = dataclasses.replace(
        used,
        governed=max(0, used.governed - size),
        categories=categories,
        documents={k: v for k, v in used.documents.items() if k != wid},
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
        left = (st.run_dirs.base / "worktrees" / wid, *item_paths.sandbox_homes(st.run_dirs, wid))
        if not any(os.path.lexists(p) for p in left):
            forget(st, wid)
    # Its documents too, once it was abandoned: archiving keeps its folder.
    for wid in list(st.storage_usage.documents):
        if not os.path.lexists(item_paths.item_dir(st.run_dirs, wid)):
            forget_documents(st, wid)
    return st.storage_usage


_INTERVAL_S = 600


async def tick(app) -> list[str]:
    """Measure, when a limit is set. Over the limit with `auto_cleanup` set,
    archive finished items that ended at least `min_age` ago, oldest first,
    until the part of usage archiving can free (`archivable`) is back at or
    under quota. Returns the ids archived.

    Items' `documents` are governed but archiving keeps them, so they are no
    reason to archive: a worker that filled its `docs/` would otherwise have
    every finished item archived without the figure ever coming down. When
    they are what keeps usage over the limit, the items holding the most are
    named in the log, for a person to abandon one."""
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
    quota, limit = st.policy.storage_quota_bytes, st.policy.storage_limit_bytes
    archived: list[str] = []
    stuck = 0
    for n, row in enumerate(finished):
        if archivable(usage(st)) <= quota:
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
            "storage: over limit, archived %s; usage is now %s",
            ", ".join(archived),
            render.human_size(usage(st).governed),
        )
    # Documents are named only when they are what keeps usage over the
    # limit: with more than the limit left to archive, abandoning an item
    # would not bring it under.
    if usage(st).documents and archivable(usage(st)) <= limit < usage(st).governed:
        heaviest = sorted(usage(st).documents.items(), key=lambda kv: -kv[1])[:3]
        logger.warning(
            "storage: still over the limit; items' documents, which archiving keeps, "
            "use %s (largest: %s): abandon an item to remove its documents",
            render.human_size(usage(st).categories.get("documents", 0)),
            ", ".join(f"{wid} {render.human_size(size)}" for wid, size in heaviest),
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
    measured = dict.fromkeys([*used.items, *used.documents])
    items = [
        {
            "id": wid,
            "title": row["title"],
            "status": row["status"],
            "archived": bool(row["archived_at"]),
            "bytes": used.items.get(wid, 0),
            "documents_bytes": used.documents.get(wid, 0),
            "updated_at": row["updated_at"],
            "reclaimable": reclaimable(row) and wid in used.items,
        }
        for wid in measured
        if (row := rows.get(wid)) is not None
    ]
    items.sort(key=lambda item: -(item["bytes"] + item["documents_bytes"]))
    return {
        "measured_at": used.measured_at,
        "state": state_of(policy, used),
        "used_bytes": used.governed,
        "quota_bytes": policy.storage_quota_bytes if policy else None,
        "limit_bytes": policy.storage_limit_bytes if policy else None,
        "reclaimable_bytes": sum(item["bytes"] for item in items if item["reclaimable"]),
        "categories": used.categories,
        "items": items,
        "orphans": sorted(
            (
                {"name": name, "bytes": used.items.get(name, 0) + used.documents.get(name, 0)}
                for name in measured
                if name not in rows
            ),
            key=lambda orphan: -orphan["bytes"],
        ),
    }


def after(policy, used: Usage, freed: int) -> tuple[int, str | None]:
    """The governed figure with `freed` bytes taken off, and the state it would
    be in; None without a limit. What a clean-up's confirmation says."""
    left = max(0, used.governed - freed)
    return left, state_of(policy, dataclasses.replace(used, governed=left))
