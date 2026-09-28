"""A sandboxed worker's private ref store.

A linked worktree commits through its repository's common gitdir, so a
container that can commit could also write every ref there: `git update-ref
refs/heads/main <its commit>` moved the operator's `main` (reproduced
2026-09-28). This module gives each sandboxed worktree a directory of its own,
`S`, which `sandbox.docker_argv` mounts *over* the common gitdir inside the
container: the worker sees every branch and tag as of its session's start and
may create, move, pack or delete refs, but all of it lands in `S`. Objects are
still shared (they are data, and a commit must write them).

Only the item's own branch travels back, through `sync`: Kraft reads it out
of `S` by parsing a file -- host git never runs with `S` as its gitdir, since
the worker writes everything there -- and moves the real branch with a
compare-and-swap `update-ref`. Nothing else the worker did to refs survives.

`S` is per worktree, not per session: co-tasks of one step run concurrently
in one worktree and commit onto one branch, and sharing one ref store keeps
their commits serialised exactly as a shared gitdir did. It is rebuilt from
the real repository whenever no other session of the item is live.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from kraft.worker import sandbox as _sandbox

logger = logging.getLogger(__name__)

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: The marker in `S` holding the branch value Kraft last published, or empty
#: when the branch did not exist. `sync` publishes only a move away from it.
_SYNCED = ".kraft-synced"


@dataclass(frozen=True)
class RefStore:
    shadow: Path
    common: Path
    worktree_gitdir: Path
    branch: str


def shadow_dir(base: Path, worktree_gitdir: Path) -> Path:
    digest = hashlib.sha256(str(worktree_gitdir).encode()).hexdigest()[:16]
    return base / "sandbox-git" / f"{worktree_gitdir.name}-{digest}"


def branch_from_head(worktree_gitdir: Path) -> str | None:
    """The branch `W/HEAD` names, parsed strictly, or None (detached, unreadable
    or anything but `ref: refs/heads/<name>`)."""
    try:
        text = (worktree_gitdir / "HEAD").read_text()
    except OSError:
        return None
    m = re.fullmatch(r"ref: refs/heads/(\S+)\n?", text)
    return m.group(1) if m else None


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


def _read_nofollow(path: Path, shadow: Path) -> str | None:
    """`path`'s text, only if it is a regular file genuinely inside `shadow`:
    the worker writes `S`, so a symlink there could point Kraft at any host
    file."""
    try:
        if not path.resolve().is_relative_to(shadow.resolve()):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd) as fh:
        return fh.read()


def read_branch(shadow: Path, branch: str) -> str | None:
    """The object id `branch` holds in `shadow`, or None. A loose ref wins
    over `packed-refs`, as in git; anything but a bare object id is None."""
    loose_path = shadow / "refs" / "heads" / branch
    if os.path.lexists(loose_path):
        loose = _read_nofollow(loose_path, shadow)
        value = loose.strip() if loose is not None else ""
        return value if _OID.fullmatch(value) else None
    packed = _read_nofollow(shadow / "packed-refs", shadow)
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


def _publish(store: RefStore) -> str | None:
    """Move the real branch to what `S` holds, if the worker moved it. The
    caller holds the lock. Returns why it could not, or None."""
    synced = _read_nofollow(store.shadow / _SYNCED, store.shadow)
    if synced is None:
        return None
    synced = synced.strip()
    new = read_branch(store.shadow, store.branch)
    if new is None or new == synced:
        return None
    if _git(store.common, "cat-file", "-e", f"{new}^{{commit}}").returncode != 0:
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
            f"{store.branch} moved in the repository while its sandboxed session ran, so "
            f"Kraft did not overwrite it with {new}: {moved.stderr.strip()}"
        )
    (store.shadow / _SYNCED).write_text(new)
    return None


def _build(store: RefStore) -> None:
    s, c = store.shadow, store.common
    s.mkdir(parents=True)
    listed = _git(c, "for-each-ref", "--format=%(objectname) %(refname)")
    if listed.returncode != 0:
        raise RuntimeError(f"could not list the refs of {c}: {listed.stderr.strip()}")
    (s / "packed-refs").write_text("# pack-refs with: sorted \n" + listed.stdout)
    for sub in ("refs/heads", "refs/tags", "logs"):
        (s / sub).mkdir(parents=True)
    (s / _SYNCED).write_text(_real_branch(c, store.branch))
    # Every mount point `docker_argv` puts inside `S`, made here by Kraft so
    # docker never creates one as root in a directory Kraft must later remove.
    for name in _sandbox.SHADOW_FILES:
        if (c / name).is_file():
            (s / name).touch()
    for name in _sandbox.SHADOW_DIRS:
        if (c / name).is_dir():
            (s / name).mkdir()
    (s / "worktrees" / store.worktree_gitdir.name).mkdir(parents=True)


def _meta_path(shadow: Path) -> Path:
    """Beside `S`, never inside it: the worker writes `S`, and what this file
    names is what `sync_item` runs `update-ref` against."""
    return shadow.parent / f"{shadow.name}.json"


def prepare(
    base: Path, cwd: Path, branch: str | None, *, reuse: bool, work_item_id: str | None = None
) -> RefStore | None:
    """The ref store a sandboxed launch in `cwd` mounts, or None when `cwd` is
    not a linked worktree (its `.git` is then inside the mounted worktree).

    `reuse` is whether another session of the item is live: its container
    already has `S` mounted, so `S` is left exactly as it is. Otherwise any
    commit a crashed session left unpublished is published first, then `S` is
    rebuilt from the repository, so the worker sees today's refs."""
    dirs = _sandbox.linked_gitdirs(Path(cwd))
    if dirs is None:
        return None
    common, worktree_gitdir = dirs
    branch = branch or branch_from_head(worktree_gitdir)
    if branch is None:
        raise RuntimeError(
            f"{cwd} is not on a branch, so Kraft cannot give its sandbox a ref store"
        )
    store = RefStore(shadow_dir(base, worktree_gitdir), common, worktree_gitdir, branch)
    with _locked(store.shadow):
        if not (reuse and store.shadow.exists()):
            if store.shadow.exists():
                if problem := _publish(store):
                    logger.warning("%s", problem)
                shutil.rmtree(store.shadow)
            _build(store)
        if work_item_id is not None:
            _meta_path(store.shadow).write_text(
                json.dumps(
                    {
                        "work_item_id": work_item_id,
                        "common": str(store.common),
                        "worktree_gitdir": str(store.worktree_gitdir),
                        "branch": store.branch,
                    }
                )
            )
    return store


def sync(store: RefStore) -> str | None:
    """Publish the item branch out of `S` after a session. Returns why it
    could not, for the caller to record; never raises for a git refusal."""
    with _locked(store.shadow):
        try:
            return _publish(store)
        except OSError as exc:
            return f"could not read {store.branch} back from its sandbox: {exc}"


def sync_item(base: Path, work_item_id: str) -> list[str]:
    """`sync` every ref store a session of `work_item_id` used, for a session
    that ended without `run_task` there to sync it: one adopted, or found
    dead, after a restart. Returns the problems, if any."""
    problems = []
    for meta in sorted((base / "sandbox-git").glob("*.json")):
        try:
            data = json.loads(meta.read_text())
        except OSError, ValueError:
            continue
        if data.get("work_item_id") != work_item_id:
            continue
        store = RefStore(
            meta.with_suffix(""),
            Path(data["common"]),
            Path(data["worktree_gitdir"]),
            data["branch"],
        )
        if store.shadow.is_dir() and (problem := sync(store)):
            problems.append(problem)
    return problems
