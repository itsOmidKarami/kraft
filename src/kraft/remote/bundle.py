"""Git bundles between the server's worktree and a machine's copy. What comes back is
untrusted: it is fetched under host git hardening, may not add a nested repository, and
moves the item branch only by compare-and-swap."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kraft.worker import refstore
from kraft.worker import sandbox as _sandbox


class BundleRefused(Exception):
    """str() is the reason, for the session log."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    _sandbox.harden_host_git_env(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False
    )


def usable(repo: Path, shas: Iterable[str]) -> list[str]:
    """The ones `repo` has: git refuses `^<unknown>`, so a machine's list is filtered first."""
    shas = [s for s in shas if len(s) in (40, 64) and all(c in "0123456789abcdef" for c in s)]
    if not shas:
        return []
    done = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch-check"],
        input="\n".join(shas) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    # `--batch-check` answers `<sha> commit <size>` or `<sha> missing`, one line per input
    return [
        s
        for s, line in zip(shas, done.stdout.splitlines(), strict=False)
        if line.split()[1:2] == ["commit"]
    ]


def make(repo: Path, branch: str, exclude: Iterable[str], dest: Path) -> bool:
    """Bundle `branch` without the commits in `exclude`; False when there is nothing new."""
    done = _git(
        repo, "bundle", "create", str(dest), f"refs/heads/{branch}", *(f"^{s}" for s in exclude)
    )
    if done.returncode == 0:
        return True
    if "empty bundle" in done.stderr:
        return False
    raise BundleRefused(f"could not bundle {branch}: {done.stderr.strip()[:300]}")


def fetch(repo: Path, bundle: Path, *, branch: str, base: str, into_ref: str) -> str:
    """Fetch the machine's bundle into `into_ref`; return the new tip or raise `BundleRefused`."""
    heads = _git(repo, "bundle", "list-heads", str(bundle))
    names = [line.split(" ", 1)[1] for line in heads.stdout.splitlines() if " " in line]
    if heads.returncode != 0 or names != [f"refs/heads/{branch}"]:
        raise BundleRefused(
            f"the machine's bundle is not just refs/heads/{branch} "
            f"({(heads.stderr or ' '.join(names))[:200].strip()})"
        )
    got = _git(
        repo,
        "-c",
        "fetch.fsckObjects=true",
        "-c",
        "transfer.fsckObjects=true",
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        str(bundle),
        f"+refs/heads/{branch}:{into_ref}",
    )
    if got.returncode != 0:
        raise BundleRefused(
            f"the machine's commits could not be fetched: {got.stderr.strip()[:300]}"
        )
    new = _git(repo, "rev-parse", "--verify", into_ref).stdout.strip()
    if _git(repo, "merge-base", "--is-ancestor", base, new).returncode != 0:
        _git(repo, "update-ref", "-d", into_ref)
        raise BundleRefused(
            f"the machine's commits do not continue from {base[:12]}, the head it was sent"
        )
    now, before = _sandbox._gitlinks_at(repo, new), _sandbox._gitlinks_at(repo, base)
    if now is None or before is None:
        _git(repo, "update-ref", "-d", into_ref)
        raise BundleRefused("could not read the tree of the machine's commits")
    added = {p: s for p, s in now.items() if before.get(p) != s}
    if added:
        _git(repo, "update-ref", "-d", into_ref)
        raise BundleRefused(
            f"the machine's commits add a git repository Kraft did not create: "
            f"{', '.join(sorted(added))}"
        )
    return new


@dataclass(frozen=True)
class Published:
    ok: bool
    ref: str | None = None  # refs/kraft/unsynced/... when the branch moved
    reason: str | None = None


def publish(repo: Path, branch: str, *, old: str, new: str, key: str) -> Published:
    """Move `refs/heads/<branch>` from `old` to `new` only if it still is `old`; else keep `new`."""
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return _keep(
            repo, branch, new, key, "the server's worktree has uncommitted changes to tracked files"
        )
    moved = _git(repo, "update-ref", "-m", "kraft: remote step", f"refs/heads/{branch}", new, old)
    if moved.returncode != 0:
        return _keep(
            repo,
            branch,
            new,
            key,
            f"{branch} moved on the server since the step started, so Kraft did not overwrite it: "
            f"{moved.stderr.strip()[:200]}",
        )
    reset = _git(repo, "reset", "--hard", "-q", new)
    if reset.returncode != 0:
        _git(repo, "update-ref", f"refs/heads/{branch}", old, new)  # put it back
        return _keep(
            repo,
            branch,
            new,
            key,
            f"the worktree could not follow {branch}: {reset.stderr.strip()[:200]}",
        )
    return Published(ok=True)


def _keep(repo: Path, branch: str, new: str, key: str, reason: str) -> Published:
    ref = f"{refstore.UNSYNCED_PREFIX}{branch}/{key.replace('/', '-')}"
    _git(repo, "update-ref", ref, new)
    return Published(ok=False, ref=ref, reason=reason)
