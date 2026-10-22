"""Update and restart when Kraft is free.

A marked release, an auto-taken one and any pending restart wait here until
nothing is going on and the hour is allowed (`config.Update`). This module
owns what "free" means, what is pending and what holds it back, the state
that outlives a restart (the mark, the one-shot restart request, the last
outcome), and the claim that keeps new work from starting while an update is
installed and the server restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from kraft import config as config_mod
from kraft import update
from kraft.vocab import WorkItemStatus
from kraft.vocab.session import SessionStatus
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

STATE_FILE = "update-when-free.json"

#: Items that are going on. `needs_human` (a pending gate, or a stop), `paused`,
#: `blocked`, `completed` and `abandoned` are waiting on a person or over, and
#: do not hold an update back.
BUSY = (
    WorkItemStatus.ACTIVE,
    WorkItemStatus.WAITING,
    WorkItemStatus.QUEUED,
    WorkItemStatus.RATE_LIMITED,
)


# ── free ─────────────────────────────────────────────────────────────────────


def busy_counts(db) -> tuple[int, int]:
    """(items going on, sessions running), in one read."""
    row = db.read(
        lambda c: c.execute(
            f"SELECT (SELECT COUNT(*) FROM work_items WHERE status IN ({in_list(BUSY)})), "
            f"(SELECT COUNT(*) FROM worker_sessions WHERE status = ?)",
            (SessionStatus.RUNNING,),
        ).fetchone()
    )
    return row[0], row[1]


def is_free(db) -> bool:
    """Nothing is going on: no worker session running (an implementer, an
    escalation turn, a gate reviewer, a reply agent), and no item active,
    waiting, queued or rate-limited."""
    items, sessions = busy_counts(db)
    return items == 0 and sessions == 0


def _waiting_for(db) -> str:
    items, sessions = busy_counts(db)
    if items:
        return f"waiting for {items} running item{'s' if items != 1 else ''}"
    return f"waiting for {sessions} running session{'s' if sessions != 1 else ''}"


# ── the claim ────────────────────────────────────────────────────────────────


class Claimed(Exception):
    """A walk or session tried to start while an update holds the moment."""


MESSAGE = "Kraft is restarting for an update; try again in a minute"

#: Module-level, not on `app.state`: `run_agent_task` has no `app`, and the
#: claim has to reach it. One server per process.
_claimed = False


def claim(db) -> bool:
    """Stop every new walk and agent session from starting, if Kraft is free.
    The flag is held until `release_claim`, or until the restart replaces the
    process."""
    global _claimed
    if _claimed or not is_free(db):
        return False
    _claimed = True
    return True


def release_claim() -> None:
    global _claimed
    _claimed = False


def claimed() -> bool:
    return _claimed


def refuse_if_claimed() -> None:
    """For a route, before its first store write: a start refused afterwards
    would leave the write behind with nothing running (`deps.task_is_live`'s
    reason). Changes nothing."""
    if _claimed:
        from fastapi import HTTPException

        raise HTTPException(503, MESSAGE)


# ── what is stored ───────────────────────────────────────────────────────────


def _path(st) -> Path:
    return st.run_dirs.base / STATE_FILE


def _read(st) -> dict:
    try:
        data = json.loads(_path(st).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(st, data: dict) -> None:
    path = _path(st)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def mark(st, tag: str, by: Literal["you", "auto"]) -> None:
    """Take release `tag` when Kraft is free. A person's mark is a person
    asking, so it also lifts the failure that would have held the version
    back."""
    data = _read(st)
    data["mark"] = {"tag": tag, "by": by, "at": _now()}
    if by == "you":
        data.pop("failed", None)
    _write(st, data)


def unmark(st) -> None:
    data = _read(st)
    if data.pop("mark", None) is not None:
        _write(st, data)


def marked(st) -> dict | None:
    mark_ = _read(st).get("mark")
    return mark_ if isinstance(mark_, dict) and mark_.get("tag") else None


def ask_restart(st) -> None:
    """One restart at the next free moment, whatever `restart_when_free` says."""
    data = _read(st)
    data["restart_asked"] = _now()
    data.pop("failed", None)
    _write(st, data)


def clear_restart_request(st) -> None:
    data = _read(st)
    if data.pop("restart_asked", None) is not None:
        _write(st, data)


def note_installed(st, tag: str | None) -> None:
    """Remember that the installed version came from a mark, so the restart it
    needs counts even with `restart_when_free` off. None forgets it."""
    data = _read(st)
    if tag is None:
        data.pop("installed_by_mark", None)
    else:
        data["installed_by_mark"] = tag.removeprefix("v")
    _write(st, data)


def outcome(st) -> dict | None:
    """The last attempt: `status` (applied, failed or refused), `detail`,
    the versions it moved between, and when."""
    last = _read(st).get("outcome")
    return last if isinstance(last, dict) else None


def record_outcome(
    st,
    *,
    status: Literal["applied", "failed", "refused"],
    key: str | None,
    detail: str,
    from_version: str | None = None,
    to_version: str | None = None,
) -> None:
    """Keep the last outcome with the mark. A failed or refused attempt is not
    made again until something changes (`failure_key`); an applied one ends
    what was waiting."""
    data = _read(st)
    data["outcome"] = {
        "status": status,
        "detail": detail,
        "from": from_version,
        "to": to_version,
        "at": _now(),
    }
    if status == "applied":
        data.pop("failed", None)
        data.pop("restart_asked", None)
        data.pop("installed_by_mark", None)
        # The mark is done once its release is what is installed.
        if (mark_ := data.get("mark")) and not update.is_behind(
            update.Release(tag=str(mark_["tag"]), wheel_url="")
        ):
            data.pop("mark", None)
    else:
        if status == "refused":
            data.pop("mark", None)
        if key:
            data["failed"] = {"key": key, "detail": detail}
    _write(st, data)
    logger.info("update when free: %s (%s -> %s): %s", status, from_version, to_version, detail)


def clear_failure(st) -> None:
    """A person asked again (a mark, `restart --when-free`, the apply chip's
    Restart), or what failed changed."""
    data = _read(st)
    if data.pop("failed", None) is not None:
        _write(st, data)


def failed_key(st) -> str | None:
    failed = _read(st).get("failed")
    return failed.get("key") if isinstance(failed, dict) else None


# ── what is pending ──────────────────────────────────────────────────────────


@dataclass
class Pending:
    #: The marked release not yet installed.
    release: str | None = None
    marked_by: Literal["you", "auto"] | None = None
    #: Why a restart is due, when one is.
    restart: list[str] = field(default_factory=list)
    #: Why none of it can happen yet.
    blocked: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.release or self.restart)


def settings(st) -> config_mod.Update:
    """`update.yaml`, read fresh. One that does not load reads as the defaults,
    which leave everything off."""
    try:
        return config_mod.Update.load(st.templates_dir / config_mod.Update.FILE)
    except config_mod.ConfigError:
        logger.warning("update.yaml does not load; update when free is off until it is fixed")
        return config_mod.Update()


def effective_channel(cfg: config_mod.Update) -> str:
    return cfg.channel or update.channel_of(update.installed())


def _settings_key(items: list[dict]) -> str:
    digest = hashlib.sha256(
        "\n".join(sorted(f"{i.get('id')}:{i.get('text')}" for i in items)).encode()
    ).hexdigest()
    return f"settings:{digest[:16]}"


def restart_items(st) -> list[dict]:
    """The apply chip's restart items: saved settings the running server has
    not picked up."""
    from kraft import apply

    return list(apply.pending(st)["restart"])


def pending_key(st, release: str | None, items: list[dict]) -> str | None:
    """What a failed attempt is keyed on: the tag; else the settings it would
    have applied; else the dev build it would have restarted into."""
    if release:
        return release
    if items:
        return _settings_key(items)
    installed, running = update.installed(), getattr(st, "version", None) or update.installed()
    if installed != running:
        return f"dev:{installed}"
    return None


def pending(st, now: datetime | None = None) -> Pending:
    """What Kraft would do at its next free moment, and what holds it back."""
    from kraft.cli import admin

    now = now or datetime.now().astimezone()
    cfg = settings(st)
    data = _read(st)
    out = Pending()

    mark_ = marked(st)
    if mark_ and update.is_behind(update.Release(tag=mark_["tag"], wheel_url="")):
        out.release, out.marked_by = mark_["tag"], mark_.get("by")

    installed = update.installed()
    running = getattr(st, "version", None) or installed
    items = restart_items(st)
    asked = bool(data.get("restart_asked"))
    reasons = []
    if installed != running and not update.is_older(installed, running):
        reasons.append(f"installed {installed}, running {running}")
    reasons += [str(i.get("text") or i.get("id")) for i in items]
    if asked:
        reasons.append("a restart was asked for")
    from_mark = data.get("installed_by_mark") == installed
    if reasons and (cfg.restart_when_free or asked or from_mark):
        out.restart = reasons
    if not out:
        return out

    key = pending_key(st, out.release, items)
    failed = data.get("failed")
    if key and isinstance(failed, dict) and failed.get("key") == key:
        out.blocked.append(f"the last attempt failed: {failed.get('detail')}")
    if out.release:
        if update.is_dev_install():
            out.blocked.append("a dev install has no release to install")
        elif (kind := update.install_kind()) != "uv":
            out.blocked.append(f"this Kraft was installed with {kind}, which Kraft cannot update")
    if admin.restart_kind(st.run_dirs.base) == "attached":
        out.blocked.append("attached to a terminal")
    if not cfg.allows(now):
        out.blocked.append("outside " + ", ".join(cfg.allowed_hours))
    if not is_free(st.db):
        out.blocked.append(_waiting_for(st.db))
    return out


# ── the attempt in flight: rollback record, snapshot ─────────────────────────

#: How long the restart helper waits for the new server to answer as a new
#: process (and, for a release, as the version installed) before it puts the
#: old one back.
CONFIRM_S = 120

#: How long a new server on probation waits for a helper's confirmation before
#: it ends probation itself. Being up and healthy is enough to go on: with the
#: helper gone, no other process is left to roll back.
PROBATION_S = 300

#: How long a snapshot is kept once the new server has left probation.
KEEP_SNAPSHOT_S = 3600

ROLLBACK_FILE = "update-rollback.json"
SNAPSHOT_PREFIX = "update-snapshot-"


def write_rollback(run_dirs, **fields) -> dict:
    """Merge `fields` into the rollback record and return it.

    Written in two phases, so a crash anywhere after the first leaves a record
    the next start can act on. Phase 1, before an install: `old_pid`,
    `from_version`, `requirement` (uv's receipt entry for the running version,
    None for a dev install or a settings restart), `target` (the release's
    version, or None), `restart_kind` and `key`. Phase 2, at the snapshot:
    `config_snapshot` and `db_snapshots`."""
    record = read_rollback(run_dirs) or {}
    record.update(fields)
    path = run_dirs.base / ROLLBACK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(record, indent=2))
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return record


def read_rollback(run_dirs) -> dict | None:
    try:
        record = json.loads((run_dirs.base / ROLLBACK_FILE).read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def clear_rollback(run_dirs) -> None:
    (run_dirs.base / ROLLBACK_FILE).unlink(missing_ok=True)


def snapshot(run_dirs, config_dir: Path) -> dict:
    """Copy the configuration directory, and back up every run database with
    SQLite's online backup (the server is still writing them), into a folder
    of its own under the run dir. Returns phase 2's fields."""
    import shutil
    import sqlite3
    import time

    root = run_dirs.base / f"{SNAPSHOT_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}"
    config_copy = root / "config"
    shutil.copytree(config_dir, config_copy, symlinks=True)
    snapshots = {}
    (root / "db").mkdir(parents=True, exist_ok=True)
    for source in sorted(run_dirs.base.glob("*.db")):
        target = root / "db" / source.name
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        snapshots[source.name] = str(target)
    return {
        "config_dir": str(config_dir),
        "config_snapshot": str(config_copy),
        "db_snapshots": snapshots,
    }


def restore_snapshot(run_dirs, record: dict) -> None:
    """Put back what `snapshot` copied: the configuration directory's contents
    (the directory itself may be a link), and each database after the
    `-wal` and `-shm` beside it are deleted -- a stale write-ahead log applied
    to a restored file corrupts it."""
    import shutil

    config_dir, copy = record.get("config_dir"), record.get("config_snapshot")
    if config_dir and copy and Path(copy).is_dir():
        target = Path(config_dir)
        target.mkdir(parents=True, exist_ok=True)
        for child in target.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in Path(copy).iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.copytree(child, target / child.name, symlinks=True)
            else:
                shutil.copy2(child, target / child.name, follow_symlinks=False)
    for name, saved in (record.get("db_snapshots") or {}).items():
        live = run_dirs.base / name
        for suffix in ("-wal", "-shm"):
            Path(f"{live}{suffix}").unlink(missing_ok=True)
        if Path(saved).is_file():
            shutil.copy2(saved, live)


def delete_snapshots(run_dirs, *, keep: str | None = None, older_than_s: float = 0.0) -> None:
    """Remove snapshot folders, except `keep` and any younger than
    `older_than_s`."""
    import shutil
    import time

    for folder in run_dirs.base.glob(f"{SNAPSHOT_PREFIX}*"):
        if str(folder) == keep:
            continue
        try:
            if time.time() - folder.stat().st_mtime < older_than_s:
                continue
        except OSError:
            continue
        shutil.rmtree(folder, ignore_errors=True)
