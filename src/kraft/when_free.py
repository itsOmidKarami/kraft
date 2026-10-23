"""Update and restart when Kraft is free.

A marked release, an auto-taken one and any pending restart wait here until
nothing is going on and the hour is allowed (`config.Update`). This module
owns what "free" means, what is pending and what holds it back, the state
that outlives a restart (the mark, the one-shot restart request, the last
outcome), and the claim that keeps new work from starting while an update is
installed and the server restarts.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import subprocess
import sys
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


def mark(st, tag: str, by: Literal["you", "auto"], wheel_url: str = "") -> None:
    """Take release `tag` when Kraft is free. A person's mark is a person
    asking, so it also lifts the failure that would have held the version
    back. `wheel_url` is the release's wheel, for `update.perform`'s fallback
    while PyPI lags the release."""
    data = _read(st)
    data["mark"] = {"tag": tag, "by": by, "at": _now(), "wheel_url": wheel_url}
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
    # server.log and About: the notify channels read the events table, and an
    # event there belongs to a work item, which an update does not.
    logger.log(
        logging.INFO if status == "applied" else logging.WARNING,
        "update when free: %s (%s -> %s): %s",
        status,
        from_version,
        to_version,
        detail,
    )


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


_said: set[str] = set()


def settings(st) -> config_mod.Update:
    """`update.yaml`, read fresh. One that does not load reads as the defaults,
    which leave everything off."""
    try:
        return config_mod.Update.load(st.templates_dir / config_mod.Update.FILE)
    except config_mod.ConfigError as exc:
        # Read every minute: said once per message, not once per look.
        if str(exc) not in _said:
            _said.add(str(exc))
            logger.warning("%s; update when free is off until it is fixed", exc)
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


# ── the loop ─────────────────────────────────────────────────────────────────

#: Between two looks at whether something is pending and Kraft is free.
INTERVAL_S = 60

#: How long the start check may take: it imports the whole server.
START_CHECK_TIMEOUT_S = 180


def default_restarter():
    """What hands the restart to the helper: `app.state.when_free_restarter`
    replaces it in a test."""
    from kraft import apply

    return functools.partial(apply.spawn_restart, confirm=True)


async def _auto_mark(st, cfg: config_mod.Update) -> None:
    """The daily check: with auto-update on, mark the newest release within
    the installed major. A newer major is shown as available and taken only on
    a person's mark. `KRAFT_NO_UPDATE_CHECK` stops Kraft looking on its own,
    and with it this; a dev install has no release to look for."""
    if not cfg.auto_update or os.environ.get("KRAFT_NO_UPDATE_CHECK"):
        return
    if update.is_dev_install() or update.install_kind() != "uv":
        return
    parts = update._parts(update.installed())
    if not parts:
        return
    release = await asyncio.to_thread(
        update.latest_in_major, parts[0], channel=effective_channel(cfg)
    )
    if release is None or not update.is_behind(release) or failed_key(st) == release.tag:
        return
    current = marked(st)
    # A person's mark, whatever it names, is theirs to change.
    if current and (current.get("by") != "auto" or current["tag"] == release.tag):
        return
    mark(st, release.tag, "auto", release.wheel_url)


def _reinstall(requirement: dict | None) -> bool:
    """Put back the version a release install replaced. True when it is back."""
    if not requirement:
        return False
    command = [
        "uv", "tool", "install", "--force", "--python", update._python(),
        update.install_argument(requirement),
    ]  # fmt: skip
    return subprocess.run(command, check=False).returncode == 0


def _start_check() -> str | None:
    """Why the code installed now would not start, or None. The tool's own
    interpreter, `-P` so no file in the working directory is imported
    instead: `shutil.which("kraft")` can name another install that shadows
    this one."""
    try:
        done = subprocess.run(
            [sys.executable, "-P", "-m", "kraft.start_check"],
            capture_output=True,
            text=True,
            timeout=START_CHECK_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"the new install did not finish its start check in {START_CHECK_TIMEOUT_S}s"
    if done.returncode != 0:
        lines = (done.stderr or done.stdout).strip().splitlines()
        return lines[-1] if lines else f"the start check exited {done.returncode}"
    return None


def _kind_of(st, got: Pending) -> str:
    """What a failed attempt can put back: a `release` it installed itself, a
    `dev` build or an install made by hand (the package stays, the
    configuration and databases go back), or `settings` (the configuration)."""
    if got.release:
        return "release"
    installed = update.installed()
    running = getattr(st, "version", None) or installed
    return "dev" if installed != running else "settings"


async def apply_once(app, *, now: datetime | None = None) -> str | None:
    """One look. If a marked release or a pending restart is due, Kraft is
    free and the hour is allowed: claim the moment, install, check the new
    install can start, snapshot, and hand the restart to the helper. Returns a
    line for the log, or None when nothing was done."""
    from kraft.cli import admin

    st = app.state
    cfg = settings(st)
    now = now or datetime.now().astimezone()
    await _auto_mark(st, cfg)
    got = pending(st, now)
    if not got or got.blocked:
        return None
    if not claim(st.db):
        return None
    try:
        return await _apply_claimed(app, st, cfg, got, admin)
    except BaseException:
        # The restart is the last step and returns at once, so an exception
        # means it was not handed off: nothing is held for a restart that is
        # not coming. (Held past the helper, the claim ends with this process.)
        release_claim()
        raise


async def _apply_claimed(app, st, cfg, got: Pending, admin) -> str | None:
    run_dirs = st.run_dirs
    running = getattr(st, "version", None) or update.installed()
    release_obj = None
    existing = read_rollback(run_dirs)
    if got.release:
        mark_ = marked(st) or {}
        release_obj = update.Release(tag=got.release, wheel_url=mark_.get("wheel_url") or "")
        record = write_rollback(
            run_dirs,
            old_pid=os.getpid(),
            from_version=running,
            requirement=update.installed_requirement(),
            target=got.release.removeprefix("v"),
            restart_kind=admin.restart_kind(run_dirs.base),
            kind="release",
            key=got.release,
        )
        why = await asyncio.to_thread(
            admin.update_prechecks, release_obj, interactive=False, channel=effective_channel(cfg)
        )
        refusal = why
        if why is None:
            try:
                code = await asyncio.to_thread(update.perform, release_obj)
            except SystemExit as exc:
                refusal = str(exc.code or exc)
            else:
                if code != 0:
                    return _failed(
                        st,
                        got.release,
                        f"the install exited {code}",
                        record,
                        reinstall=True,
                    )
        if refusal is not None:
            clear_rollback(run_dirs)
            # Drops the mark: a refused release is not offered again.
            record_outcome(
                st,
                status="refused",
                key=got.release,
                detail=refusal,
                from_version=running,
                to_version=got.release.removeprefix("v"),
            )
            release_claim()
            return f"refused {got.release}: {refusal}"
        note_installed(st, got.release)
    elif existing and existing.get("kind") == "release" and existing.get("from_version") == running:
        # The install was made earlier and the restart waited (it went busy):
        # the record already says how to put the old version back.
        record = existing
    else:
        record = write_rollback(
            run_dirs,
            old_pid=os.getpid(),
            from_version=running,
            requirement=None,
            target=None,
            restart_kind=admin.restart_kind(run_dirs.base),
            kind=_kind_of(st, got),
            key=pending_key(st, None, restart_items(st)),
        )

    why = await asyncio.to_thread(_start_check)
    if why is not None:
        return _failed(
            st, record.get("key"), f"the new install would not start: {why}", record, reinstall=True
        )
    taken = await asyncio.to_thread(snapshot, run_dirs, config_dir_of(st))
    write_rollback(run_dirs, **taken)
    if not is_free(st.db):
        release_claim()
        return "went busy before the restart; the install stays and the restart waits"
    restarter = getattr(st, "when_free_restarter", None) or default_restarter()
    try:
        restarter(st)
    except (OSError, SystemExit) as exc:
        return _failed(st, record.get("key"), f"could not start the restart: {exc}", record)
    return f"restarting {running} -> {update.installed()} (kind {record.get('kind')})"


def config_dir_of(st) -> Path:
    return Path(st.templates_dir)


def _failed(st, key, detail: str, record: dict, *, reinstall: bool = False) -> str:
    """The attempt did not work: what it installed goes back, the claim is
    released and the failure is kept, so the same thing is not tried again."""
    put_back = ""
    if reinstall and record.get("kind") == "release":
        put_back = (
            "; the previous version is back"
            if _reinstall(record.get("requirement"))
            else ("; the previous version could not be reinstalled")
        )
        note_installed(st, None)
    clear_rollback(st.run_dirs)
    record_outcome(
        st,
        status="failed",
        key=key,
        detail=detail + put_back,
        from_version=record.get("from_version"),
        to_version=record.get("target"),
    )
    release_claim()
    return f"failed: {detail}{put_back}"


async def loop(app) -> None:
    """`apply_once` every `INTERVAL_S`. An exception is logged and the loop
    goes on: `deps.in_background` would let the task die on the first one."""
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            if line := await apply_once(app):
                logger.info("update when free: %s", line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- the next look may find it fixed
            logger.exception("update when free failed")
            release_claim()
