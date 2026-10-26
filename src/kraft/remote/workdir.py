"""The machine's copy of an item: a linked worktree kept across the item's steps."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

from kraft.remote import bundle, lfs
from kraft.worker import sandbox as _sandbox

#: A file in the bare repo marking a copy whose worktree a sandboxed worker planted a repository
#: in: its next `update` replaces the worktree rather than check out over it.
_REBUILD = "kraft-rebuild"

#: A checkout never fetches LFS content from anywhere; `lfs.checkout` fills it from the local store.
_NO_SMUDGE = {"GIT_LFS_SKIP_SMUDGE": "1"}


class Copy:
    """`root` is a linked worktree of the bare repo beside it (`<root>.git`). A sandboxed worker
    is given a private ref store in place of that repo's refs (`worker/refstore.py`), so what it
    writes under its checkout's `.git` is never read by the git that updates, sweeps and bundles."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def bare(self) -> Path:
        return self.root.with_name(self.root.name + ".git")

    @staticmethod
    def _run(
        cwd: Path, *args: str, check: bool = True, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        full = dict(os.environ)
        _sandbox.harden_host_git_env(full)  # a repo's hooks still must not fire
        full.update(env or {})
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            env=full,
            check=check,
        )

    def _git(
        self, *args: str, check: bool = True, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        return self._run(self.root, *args, check=check, env=env)

    def _bare(
        self, *args: str, check: bool = True, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        return self._run(self.bare, *args, check=check, env=env)

    def _is_worktree(self) -> bool:
        return _sandbox.linked_gitdirs(self.root) is not None

    def _ensure_bare(self) -> None:
        if (self.bare / "HEAD").exists():
            return
        self.bare.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.bare)], check=True, capture_output=True
        )
        self._bare("config", "user.email", "kraft@machine")
        self._bare("config", "user.name", "kraft")

    def take_lfs(self, objects: Mapping[str, Path]) -> None:
        """Store the LFS objects the server sent, `oid: file`, each checked against its oid."""
        if not objects:
            return
        self._ensure_bare()
        lfs.take(lfs.store(self.bare), {oid: (path, None) for oid, path in objects.items()})

    def held_lfs(self, oids: Iterable[str]) -> list[str]:
        """Which of `oids` the copy's LFS store holds (none when it has no bare repo yet)."""
        if not (self.bare / "HEAD").exists():
            return []
        have = set(lfs.held(lfs.store(self.bare)))
        return [o for o in oids if o in have]

    def new_lfs(self, branch: str, tip: str, exclude: Iterable[str]) -> dict[str, Path]:
        """The LFS objects the commits up to `tip` (not in `exclude`) point to that the store holds,
        `oid: file in the store`. Only for a repo that uses LFS."""
        if not lfs.uses_lfs(self.root):
            return {}
        store = lfs.store(self.bare)
        return {
            oid: lfs.object_path(store, oid)
            for oid in lfs.new_pointers(self.bare, tip, exclude)
            if lfs.object_path(store, oid).is_file()
        }

    def commits(self, limit: int = 500) -> list[str]:
        if not (self.bare / "HEAD").exists():
            return []
        return self._bare("rev-list", "-n", str(limit), "--all", check=False).stdout.split()

    def update(self, bundle_path: Path | None, *, branch: str, head: str) -> None:
        """Take the commits in `bundle_path` (None: nothing new) and check `head` out on `branch`,
        resetting tracked files and leaving ignored ones (a venv, node_modules) where they are."""
        self._ensure_bare()
        has_lfs = lfs.available()
        if has_lfs:
            lfs.configure(self.bare)  # every update: a copy made before git-lfs came gets it too
        if bundle_path is not None:
            self._bare(
                "fetch",
                "--no-tags",
                "-q",
                str(bundle_path),
                f"+refs/heads/{branch}:refs/kraft/server/{branch}",
            )
        rebuild = self.bare / _REBUILD
        if self._is_worktree() and not rebuild.exists():
            self._git("checkout", "-q", "-f", "-B", branch, head, env=_NO_SMUDGE)
            if has_lfs:
                lfs.checkout(self.root)
            return
        # an old plain clone (or half of one), or a worktree a sandboxed worker planted a
        # repository in (host git would read its config), is replaced, not converted or checked
        # out over: its ignored files go
        shutil.rmtree(self.root, ignore_errors=True)
        self._bare("worktree", "prune")
        self._bare(
            "worktree", "add", "-q", "-f", "-B", branch, str(self.root), head, env=_NO_SMUDGE
        )
        if has_lfs:
            lfs.checkout(self.root)
        rebuild.unlink(missing_ok=True)

    def planted(self, base: str) -> list[str] | None:
        """The git repositories in the worktree Kraft did not create (None: git cannot say)."""
        return _sandbox.planted_repos(self.root, base)

    def rebuild_on_update(self) -> None:
        """Do not trust this worktree again: the next `update` makes a fresh one."""
        (self.bare / _REBUILD).touch()

    async def sweep(
        self, *, branch: str, base: str, node_id: str, identity: Mapping[str, str]
    ) -> bool:
        from kraft.adapters.forge import git as forge_git  # Kraft's own sweep, the host's rule

        return await forge_git.commit_stragglers(
            self.root,
            branch=branch,
            base=base,
            message=f"wip: uncommitted work from {node_id}",
            identity=dict(identity),
        )

    def bundle_since(self, branch: str, exclude: Iterable[str], dest: Path) -> str | None:
        """Bundle `branch` without `exclude`; its tip, or None when nothing is new."""
        tip = self._bare(
            "rev-parse", "--verify", f"refs/heads/{branch}", check=False
        ).stdout.strip()
        exclude = list(exclude)
        if not tip or tip in exclude:
            return None
        return (
            tip if bundle.make(self.bare, branch, bundle.usable(self.bare, exclude), dest) else None
        )
