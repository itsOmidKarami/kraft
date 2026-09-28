"""A sandboxed worker's private ref store.

A linked worktree commits through its repository's common gitdir, so a
container that can commit could also write every ref there: `git update-ref
refs/heads/main <its commit>` moved the operator's `main` (reproduced
2026-09-28). This module gives each sandboxed worktree a directory of its own,
`S`, which `backends.docker.docker_argv` mounts *over* the common gitdir inside the
container: the worker sees every branch and tag as of its session's start and
may create, move, pack or delete refs, but all of it lands in `S`. Objects are
still shared (they are data, and a commit must write them).

Only the item's own branch travels back, through `sync`: Kraft reads it out
of `S` by parsing a file -- host git never runs with `S` as its gitdir, since
the worker writes everything there -- and moves the real branch with a
compare-and-swap `update-ref`. Nothing else the worker did to refs survives.

Everything Kraft trusts about a store -- which branch it publishes, the value
that branch had when Kraft last published it (the compare-and-swap's old
value), which sessions have it mounted -- lives in a sidecar beside `S`, never
in it: a worker that could rewrite the old value could publish over a branch
that moved in the repository, and one that could name the branch could
publish onto `main`.

`S` is per worktree, not per session: co-tasks of one step run concurrently
in one worktree and commit onto one branch, and sharing one ref store keeps
their commits serialised exactly as a shared gitdir did. It is rebuilt from
the repository whenever none of the sessions that mounted it is still live.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from kraft.worker import sandbox as _sandbox

logger = logging.getLogger(__name__)

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: The most of one file in `S` Kraft reads: `packed-refs` of a very large
#: repository fits many times over, and a worker cannot make Kraft read more.
_MAX_READ = 64 * 1024 * 1024
#: Where a commit the worker left on its branch, but Kraft could not publish,
#: is kept when its store is rebuilt, so rebuilding never loses it.
UNSYNCED_PREFIX = "refs/kraft/unsynced/"


@dataclass(frozen=True)
class RefStore:
    shadow: Path
    common: Path
    worktree_gitdir: Path
    #: The item branch this store publishes, or None for a store a sandboxed
    #: setup command mounts, which publishes nothing.
    branch: str | None
    #: Why a commit a previous session left could not be published when this
    #: store was rebuilt, for the caller to record; None when nothing was lost.
    carried: str | None = None


def shadow_dir(base: Path, worktree_gitdir: Path) -> Path:
    digest = hashlib.sha256(str(worktree_gitdir).encode()).hexdigest()[:16]
    return base / "sandbox-git" / f"{worktree_gitdir.name}-{digest}"


def _git(common: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    _sandbox.harden_host_git_env(env)
    return subprocess.run(
        ["git", f"--git-dir={common}", *args], capture_output=True, text=True, env=env
    )


@contextmanager
def _locked(shadow: Path) -> Iterator[None]:
    shadow.parent.mkdir(parents=True, exist_ok=True)
    with open(shadow.parent / f"{shadow.name}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _meta_path(shadow: Path) -> Path:
    """Beside `S`, never inside it: `sandbox-git/` itself is never mounted."""
    return shadow.parent / f"{shadow.name}.json"


def _read_meta(shadow: Path) -> dict:
    try:
        data = json.loads(_meta_path(shadow).read_text())
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(shadow: Path, data: dict) -> None:
    path = _meta_path(shadow)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _read_regular(path: Path, shadow: Path) -> str | None:
    """`path`'s text, only if it is a regular file genuinely inside `shadow`:
    the worker writes `S`, so a symlink there could point Kraft at any host
    file, and a FIFO would block the read (and the lock around it) forever."""
    try:
        if not path.resolve().is_relative_to(shadow.resolve()):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, _MAX_READ).decode(errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def read_branch(shadow: Path, branch: str) -> str | None:
    """The object id `branch` holds in `shadow`, or None. A loose ref wins
    over `packed-refs`, as in git; anything but a bare object id is None."""
    loose_path = shadow / "refs" / "heads" / branch
    if os.path.lexists(loose_path):
        loose = _read_regular(loose_path, shadow)
        value = loose.strip() if loose is not None else ""
        return value if _OID.fullmatch(value) else None
    packed = _read_regular(shadow / "packed-refs", shadow)
    if packed is None:
        return None
    want = f"refs/heads/{branch}"
    for line in packed.splitlines():
        oid, _, name = line.partition(" ")
        if name == want and _OID.fullmatch(oid):
            return oid
    return None


def _real_branch(common: Path, branch: str) -> str:
    done = _git(common, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return done.stdout.strip() if done.returncode == 0 else ""


def _is_commit(common: Path, oid: str) -> bool:
    return _git(common, "cat-file", "-e", f"{oid}^{{commit}}").returncode == 0


def _publish(store: RefStore) -> str | None:
    """Move the real branch to what `S` holds, if the worker moved it. The
    caller holds the lock. Returns why it could not, or None."""
    meta = _read_meta(store.shadow)
    if store.branch is None or meta.get("branch") != store.branch:
        return None
    synced = meta.get("synced", "")
    new = read_branch(store.shadow, store.branch)
    if new is None or new == synced:
        return None
    if not _is_commit(store.common, new):
        return f"the sandbox left {store.branch} at {new}, which is not a commit in the repository"
    moved = _git(
        store.common,
        "update-ref",
        "-m",
        "kraft: sandboxed session",
        f"refs/heads/{store.branch}",
        new,
        synced,
    )
    if moved.returncode != 0:
        return (
            f"{store.branch} moved in the repository since its sandboxed session started, "
            f"so Kraft did not overwrite it with {new}: {moved.stderr.strip()}"
        )
    _write_meta(store.shadow, {**meta, "synced": new})
    return None


def _keep_unpublished(store: RefStore) -> str | None:
    """Park the commit a store's branch holds under `UNSYNCED_PREFIX`, before
    the store is thrown away; the ref's name, or None when there is nothing
    to keep."""
    new = read_branch(store.shadow, store.branch) if store.branch else None
    if new is None or not _is_commit(store.common, new):
        return None
    ref = f"{UNSYNCED_PREFIX}{store.branch}"
    if _git(store.common, "update-ref", ref, new).returncode != 0:
        return None
    return ref


def _build(store: RefStore) -> None:
    s, c = store.shadow, store.common
    s.mkdir(parents=True)
    listed = _git(c, "for-each-ref", "--format=%(objectname) %(refname)")
    if listed.returncode != 0:
        raise RuntimeError(f"could not list the refs of {c}: {listed.stderr.strip()}")
    (s / "packed-refs").write_text("# pack-refs with: sorted \n" + listed.stdout)
    for sub in ("refs/heads", "refs/tags", "logs"):
        (s / sub).mkdir(parents=True)
    # LFS objects are data a commit's clean filter writes; without the real
    # directory they would land in `S` and be lost with it.
    (c / "lfs").mkdir(exist_ok=True)
    # Every mount point `docker_argv` puts inside `S`, made here by Kraft so
    # docker never creates one as root in a directory Kraft must later remove.
    for name in _sandbox.SHADOW_FILES:
        if (c / name).is_file():
            (s / name).touch()
    for name in _sandbox.SHADOW_DIRS:
        if (c / name).is_dir():
            (s / name).mkdir()
    (s / "worktrees" / store.worktree_gitdir.name).mkdir(parents=True)


def prepare(
    base: Path,
    cwd: Path,
    branch: str | None,
    *,
    session_id: str | None = None,
    live: Iterable[str] = (),
    work_item_id: str | None = None,
) -> RefStore | None:
    """The ref store a sandboxed launch in `cwd` mounts, or None when `cwd` is
    not a linked worktree (its `.git` is then inside the mounted worktree).

    A session (`session_id`, publishing `branch`) reuses the store exactly as
    it is when one of the sessions that mounted it is still in `live`: that
    container has it mounted now. Otherwise any commit an earlier session left
    unpublished is published -- or, if it cannot be, kept under
    `UNSYNCED_PREFIX` -- and the store is rebuilt from the repository, so the
    worker sees today's refs. A launch with no session (a sandboxed setup
    command, `branch` None) mounts the store as it is, or a fresh one, and
    publishes nothing."""
    dirs = _sandbox.linked_gitdirs(Path(cwd))
    if dirs is None:
        return None
    common, worktree_gitdir = dirs
    storage = _git(common, "config", "--get", "extensions.refStorage").stdout.strip()
    if storage not in ("", "files"):
        raise RuntimeError(
            f"{common} keeps its refs in {storage!r} storage, and a sandbox's ref store "
            "supports only git's default files storage"
        )
    store = RefStore(shadow_dir(base, worktree_gitdir), common, worktree_gitdir, branch)
    carried = None
    with _locked(store.shadow):
        meta = _read_meta(store.shadow)
        mounted = set(meta.get("sessions", ())) & set(live)
        if session_id is None and store.shadow.exists():
            return store
        if store.shadow.exists() and mounted and meta.get("branch") == branch:
            _write_meta(store.shadow, {**meta, "sessions": sorted({*mounted, session_id})})
            return store
        if store.shadow.exists():
            previous = RefStore(store.shadow, common, worktree_gitdir, meta.get("branch") or branch)
            if problem := _publish(previous):
                kept = _keep_unpublished(previous)
                carried = problem + (f"; the commit is kept at {kept}" if kept else "")
                logger.warning("%s", carried)
            shutil.rmtree(store.shadow)
        _meta_path(store.shadow).unlink(missing_ok=True)
        _build(store)
        if branch is not None and session_id is not None:
            _write_meta(
                store.shadow,
                {
                    "work_item_id": work_item_id,
                    "common": str(common),
                    "worktree_gitdir": str(worktree_gitdir),
                    "branch": branch,
                    "synced": _real_branch(common, branch),
                    "sessions": [session_id],
                },
            )
    return RefStore(store.shadow, common, worktree_gitdir, branch, carried)


def sync(store: RefStore, session_id: str | None = None) -> str | None:
    """Publish the item branch out of `S` after a session, and forget that
    `session_id` has it mounted. Returns why it could not publish, for the
    caller to record; never raises for a git refusal."""
    with _locked(store.shadow):
        try:
            return _publish(store)
        except OSError as exc:
            return f"could not read {store.branch} back from its sandbox: {exc}"
        finally:
            if session_id is not None:
                meta = _read_meta(store.shadow)
                if session_id in meta.get("sessions", ()):
                    sessions = [s for s in meta["sessions"] if s != session_id]
                    _write_meta(store.shadow, {**meta, "sessions": sessions})


def sync_item(base: Path, work_item_id: str) -> list[str]:
    """`sync` every ref store a session of `work_item_id` used, for a session
    that ended without `run_task` there to sync it: one adopted, or found
    dead, after a restart. Returns the problems, if any."""
    problems = []
    for meta_path in sorted((base / "sandbox-git").glob("*.json")):
        shadow = meta_path.with_suffix("")
        data = _read_meta(shadow)
        if data.get("work_item_id") != work_item_id or not data.get("branch"):
            continue
        store = RefStore(
            shadow, Path(data["common"]), Path(data["worktree_gitdir"]), data["branch"]
        )
        if store.shadow.is_dir() and (problem := sync(store)):
            problems.append(problem)
    return problems


def discard(base: Path, cwd: Path) -> None:
    """Forget `cwd`'s ref store, once its worktree is going away. Best effort:
    a store left behind is rebuilt before it is ever mounted again."""
    dirs = _sandbox.linked_gitdirs(Path(cwd))
    if dirs is None:
        return
    shadow = shadow_dir(base, dirs[1])
    with _locked(shadow):
        shutil.rmtree(shadow, ignore_errors=True)
        _meta_path(shadow).unlink(missing_ok=True)
    (shadow.parent / f"{shadow.name}.lock").unlink(missing_ok=True)
