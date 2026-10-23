"""The machine's copy of an item: a standalone clone kept across the item's steps."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

from kraft.remote import bundle
from kraft.worker import sandbox as _sandbox


class Copy:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        _sandbox.harden_host_git_env(env)  # a repo's hooks still must not fire
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            env=env,
            check=check,
        )

    def _ensure(self) -> None:
        if (self.root / ".git").exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self._git("config", "user.email", "kraft@machine")
        self._git("config", "user.name", "kraft")

    def commits(self, limit: int = 500) -> list[str]:
        if not (self.root / ".git").exists():
            return []
        return self._git("rev-list", "-n", str(limit), "--all", check=False).stdout.split()

    def update(self, bundle_path: Path | None, *, branch: str, head: str) -> None:
        """Take the commits in `bundle_path` (None: nothing new) and check `head` out on `branch`,
        resetting tracked files and leaving ignored ones (a venv, node_modules) where they are."""
        self._ensure()
        if bundle_path is not None:
            self._git(
                "fetch",
                "--no-tags",
                "-q",
                str(bundle_path),
                f"+refs/heads/{branch}:refs/kraft/server/{branch}",
            )
        self._git("checkout", "-q", "-f", "-B", branch, head)

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
        tip = self._git("rev-parse", "--verify", f"refs/heads/{branch}", check=False).stdout.strip()
        exclude = list(exclude)
        if not tip or tip in exclude:
            return None
        return (
            tip if bundle.make(self.root, branch, bundle.usable(self.root, exclude), dest) else None
        )
