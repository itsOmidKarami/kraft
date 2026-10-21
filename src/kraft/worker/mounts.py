"""The one mount list a sandboxed session runs with, backend-neutral: the
worktree, the files a session may write, the gitdirs behind a linked
worktree. A backend renders it (`docker_args` for `-v`) and adds what is its
own -- the shim, `home`, the CA bundle, the working directory.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from kraft.worker.sandbox import (
    _RW_SHADOW_DIRS,
    SHADOW_DIRS,
    SHADOW_FILES,
    SandboxRefused,
    _gitdir_of,
    linked_gitdirs,
)

if TYPE_CHECKING:
    from kraft.worker.refstore import RefStore

#: Where the relay listens in the network namespace it shares with its
#: worker, and so the proxy every worker under `network:` is told to use.
RELAY_PORT = 3128
#: The worker's proxy environment under `network:`, both spellings, and
#: `NODE_USE_ENV_PROXY` for Node's own fetch. `NO_PROXY` is empty: the
#: worker has no route but this one.
RELAY_PROXY_ENV = {
    **{
        name: f"http://127.0.0.1:{RELAY_PORT}"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    },
    "NO_PROXY": "",
    "no_proxy": "",
    "NODE_USE_ENV_PROXY": "1",
}


class Mount(NamedTuple):
    src: Path
    dst: Path
    ro: bool


def docker_args(ms: Iterable[Mount]) -> list[str]:
    """`-v` arguments for `ms`."""
    return [a for m in ms for a in ("-v", f"{m.src}:{m.dst}" + (":ro" if m.ro else ""))]


def session_mounts(
    cwd: Path,
    results: Iterable[Path] | None,
    *,
    result_path: Path | str | None = None,
    rw_files: Iterable[Path | str] = (),
    rw_dirs: Iterable[Path | str] = (),
    refstores: Sequence[RefStore] = (),
    ro_paths: Iterable[Path | str] = (),
) -> list[Mount]:
    """The worktree (`cwd`, resolved, read-write), earlier sessions' result
    files read-only, the session's own files, `rw_dirs`, the gitdirs, then
    `ro_paths` read-only."""
    if isinstance(results, str | Path):
        # Iterated, a path is its characters, and the first is `/`.
        raise TypeError("`results` is the files to mount, never the results folder itself")
    cwd = cwd.resolve()
    mounts = [Mount(cwd, cwd, False)]
    own = list(dict.fromkeys(str(p) for p in (*([result_path] if result_path else ()), *rw_files)))
    for path in dict.fromkeys(str(p) for p in results or ()):
        # Its own are mounted read-write below; docker refuses a target twice.
        if path not in own:
            mounts.append(Mount(Path(path), Path(path), True))
    # Each its own file mount: a bind-mounted file cannot be removed or
    # swapped for a symlink from inside the container.
    mounts += [Mount(Path(p), Path(p), False) for p in own]
    mounts += [Mount(Path(p), Path(p), False) for p in dict.fromkeys(str(d) for d in rw_dirs)]
    mounts += _gitdir_mounts(cwd, refstores)
    mounts += [Mount(Path(p), Path(p), True) for p in ro_paths]
    return mounts


def _alternates(objects: Path) -> list[Path]:
    """Object directories `objects/info/alternates` borrows from (`clone
    --shared`, `--reference`): outside every other mount, so without their own
    the container cannot read those objects at all."""
    try:
        lines = (objects / "info" / "alternates").read_text().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            path = Path(line) if Path(line).is_absolute() else objects / line
            if path.resolve().is_dir():
                found.append(path.resolve())
    return found


def _gitdir_mounts(cwd: Path, refstores: Sequence[RefStore] = ()) -> list[Mount]:
    """Mounts for the gitdirs a linked worktree's `.git` file points at,
    the worktree's own and then each workspace member's (`_linked_mounts`).

    Empty for the worktree's own when `cwd` is not a linked worktree: an
    ordinary `.git` directory is inside `cwd`, already mounted with it.

    A member's come from its store alone -- the common gitdir and admin dir
    Kraft derived from the operator's repository -- never from reading
    `<member>/.git`, which the worker writes: a gitfile it repointed would
    otherwise get any host `<x>/worktrees/<y>` mounted read-write (Kraft-ju36l).
    """
    own = next((s for s in refstores if s.checkout is None), None)
    mounts = _worktree_mounts(cwd, own)
    pinned: set[Path] = set()
    for store in refstores:
        if store.checkout is not None:
            mounts += _pin_path(cwd, store.checkout, pinned)
            mounts += _linked_mounts(
                store.checkout / ".git", store.common, store.worktree_gitdir, store
            )
    return mounts


def _pin_path(cwd: Path, checkout: Path, pinned: set[Path]) -> list[Mount]:
    """Every directory from `cwd` down to `checkout`, bound onto itself, top
    first. The member's `.git` is read-only, but it sits in the read-write
    worktree: a worker that renamed any directory above it (`mv repos
    repos.x`) and rebuilt the path would hand host git a `.git` of its own.
    A mount point cannot be renamed or removed (EBUSY), so the path to the
    member stays the one Kraft checked, for as long as any container has it
    mounted (Kraft-ju36l, J6).

    Each must be a real directory when the launch is built: a symlink there
    would have docker mount whatever it names."""
    path, mounts = cwd, []
    for part in checkout.relative_to(cwd).parts:
        path = path / part
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise SandboxRefused(f"{path} is not a directory; the member under it is not mounted")
        if path not in pinned:
            pinned.add(path)
            mounts.append(Mount(path, path, False))
    return mounts


def _worktree_mounts(cwd: Path, refstore: RefStore | None) -> list[Mount]:
    dirs = linked_gitdirs(cwd)
    if dirs is None:
        # A `.git` file naming some other gitdir (a submodule's checkout, say):
        # nothing to commit through a ref store, so both stay read-only.
        other = _gitdir_of(cwd)
        if other is None:
            return []
        return [Mount(cwd / ".git", cwd / ".git", True), Mount(other, other, True)]
    return _linked_mounts(cwd / ".git", *dirs, refstore)


def _linked_mounts(
    git_file: Path, common: Path, worktree_gitdir: Path, refstore: RefStore | None
) -> list[Mount]:
    """One linked worktree's gitdirs: `git_file`, its `.git`, names
    `worktree_gitdir` (`W`) in the repository whose common gitdir is `common`.

    `W` is read-write (a commit writes its index, HEAD and lockfiles there),
    with the files git redirects config and the common dir through shadowed
    read-only, created first so a worker cannot dodge a shadow by deleting
    its file. The common gitdir is the private ref store `refstore` when
    given, with the real `objects/` and `lfs/` inside it read-write and
    `HEAD`, `config`, `info/` and `shallow` read-only; the worker's refs,
    reflogs, `packed-refs` and `FETCH_HEAD` all land in the store. Without a
    store it is read-only whole, and a commit cannot move a ref at all.
    """
    # `.git` itself lives inside the read-write `cwd` mount. Left alone, a
    # sandboxed worker repoints it at a gitdir it builds inside the
    # worktree -- HEAD, objects/, refs/, a config with hooks -- and the next
    # host-side git command run there executes the worker's hook as the
    # invoking host user. It never legitimately changes for the life of the
    # worktree, so shadowing it read-only costs nothing.
    mounts = [Mount(git_file, git_file, True)]
    if refstore is None:
        mounts.append(Mount(common, common, True))
    else:
        mounts.append(Mount(refstore.shadow, common, False))
        for name in (*SHADOW_FILES, *SHADOW_DIRS):
            path = common / name
            if path.exists():
                mounts.append(Mount(path, path, name not in _RW_SHADOW_DIRS))
        # `objects/info` is where `alternates` lives: read-write, a worker
        # names any host directory there and every later sandboxed launch on
        # this repository mounts it (and host git reads objects from it).
        # Made first when the repository has none: absent, the worker would
        # create it inside the read-write `objects/`, alternates and all.
        info = common / "objects" / "info"
        info.mkdir(exist_ok=True)
        mounts.append(Mount(info, info, True))
        for alternate in _alternates(common / "objects"):
            mounts.append(Mount(alternate, alternate, True))
    mounts.append(Mount(worktree_gitdir, worktree_gitdir, False))
    # `commondir` names where `hooks/` and `config` resolve to, and
    # `config.worktree` is read as part of the config stack whenever
    # `extensions.worktreeConfig` is on: either one, rewritten, hands the next
    # host-side git in this worktree a config of the worker's choosing.
    # `gitdir` is the backlink `git worktree prune` checks: rewritten to a
    # path that does not exist, the next host prune deletes this worktree's
    # admin directory.
    for name in ("commondir", "config.worktree", "gitdir"):
        path = worktree_gitdir / name
        if name != "gitdir":
            try:
                path.touch(exist_ok=True)
            except OSError:
                pass
        if path.exists():
            mounts.append(Mount(path, path, True))
    return mounts
