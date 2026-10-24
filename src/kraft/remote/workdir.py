"""The machine's copy of an item: a linked worktree kept across the item's steps."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

from kraft.remote import bundle
from kraft.worker import sandbox as _sandbox


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
    def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        _sandbox.harden_host_git_env(env)  # a repo's hooks still must not fire
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            env=env,
            check=check,
        )

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._run(self.root, *args, check=check)

    def _bare(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._run(self.bare, *args, check=check)

    def _is_worktree(self) -> bool:
        return _sandbox.linked_gitdirs(self.root) is not None

    def _ensure_bare(self) -> None:
        if (self.bare / "HEAD").exists():
            return
        self.bare.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True, capture_output=True)
        self._bare("config", "user.email", "kraft@machine")
        self._bare("config", "user.name", "kraft")

    def commits(self, limit: int = 500) -> list[str]:
        if not (self.bare / "HEAD").exists():
            return []
        return self._bare("rev-list", "-n", str(limit), "--all", check=False).stdout.split()

    def update(self, bundle_path: Path | None, *, branch: str, head: str) -> None:
        """Take the commits in `bundle_path` (None: nothing new) and check `head` out on `branch`,
        resetting tracked files and leaving ignored ones (a venv, node_modules) where they are."""
        self._ensure_bare()
        if bundle_path is not None:
            self._bare(
                "fetch",
                "--no-tags",
                "-q",
                str(bundle_path),
                f"+refs/heads/{branch}:refs/kraft/server/{branch}",
            )
        if self._is_worktree():
            self._git("checkout", "-q", "-f", "-B", branch, head)
            return
        # an old plain clone (or half of one) is replaced, not converted: its ignored files go
        shutil.rmtree(self.root, ignore_errors=True)
        self._bare("worktree", "prune")
        self._bare("worktree", "add", "-q", "-f", "-B", branch, str(self.root), head)

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
        tip = self._bare("rev-parse", "--verify", f"refs/heads/{branch}", check=False).stdout.strip()
        exclude = list(exclude)
        if not tip or tip in exclude:
            return None
        return (
            tip if bundle.make(self.bare, branch, bundle.usable(self.bare, exclude), dest) else None
        )
