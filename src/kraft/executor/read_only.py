"""A `read_only` step or node, verified (Kraft-q2zvw).

Before a read_only step's first task launches -- or a read_only node's first
step -- each repository of the checkout (the root and every workspace member
mount) records its HEAD, `git status --porcelain=v1 -z` (ignored files stay
out, as by default) and a hash of `git diff HEAD`. After the step's last task
exits, or the node's last main step, the same is read again. Any difference
stops the item for a person, naming the changed files: a `read_only_violated`
event, then `READ_ONLY_VIOLATED`, a stop (`context.SCOPE`) -- never a failure
a recovery, fix loop or counter could spend on, because nothing about the
implementation's code failed. Harness-independent: it reads the worktree, not
what an agent says it did.

HEAD is part of the record because the straggler sweep commits whatever an
agent left behind before the step settles, so a write shows up as a commit.
A git that cannot answer is a change too: unverified is not read-only.

On a sandboxed item no host git runs while a session is live or once the
worktree holds a repository Kraft did not create (`stops.refuse_planted_repos`,
`refuse_live_sandboxed_session`): before the step that skips the check, and
the task's own dispatch stops on the same guard; after it, the guard's refusal
is the violation, naming the paths.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kraft import builtins as _builtins
from kraft import config as _config
from kraft import events, store
from kraft.executor import stops
from kraft.executor.context import LaunchContext
from kraft.worker import sandbox as _sandbox

EVENT = "read_only_violated"

#: `(HEAD, status, diff hash)` per repository, `""` for the root.
Snapshot = dict[str, tuple[str | None, str | None, str | None]]

_NAMED = 20


def _guard(db, row, launch: LaunchContext | None, worktree: Path) -> str | None:
    """Why host git may not read `worktree` now, or None."""
    try:
        stops.refuse_live_sandboxed_session(db, row, launch, what="the read-only check")
        stops.refuse_planted_repos(row, launch, worktree)
    except RuntimeError as exc:
        return str(exc)
    return None


def _read(cwd: Path) -> tuple[str | None, str | None, str | None]:
    unentered = _sandbox.SUBMODULES_UNENTERED
    diff = _config.git_read(cwd, "diff", unentered, "HEAD", strip=False)
    return (
        _config.git_read(cwd, "rev-parse", "HEAD"),
        _config.git_read(cwd, "status", "--porcelain=v1", "-z", unentered, strip=False),
        hashlib.sha256(diff.encode()).hexdigest() if diff is not None else None,
    )


def snapshot(db, row, launch: LaunchContext | None, worktree) -> Snapshot | None:
    """The worktree as it stands, or None when it cannot be read safely (the
    tasks' own dispatch then stops on the same guard)."""
    if worktree is None or _guard(db, row, launch, Path(worktree)) is not None:
        return None
    return {rel: _read(Path(worktree) / rel) for rel in ["", *_builtins.item_mounts(row)]}


def _entries(status: str) -> set[tuple[str, str]]:
    """`(XY, path)` per `-z` entry; a rename's or copy's source is its own
    token, and skipped."""
    tokens = iter(status.split("\0"))
    found = set()
    for token in tokens:
        if token:
            found.add((token[:2], token[3:]))
            if token[0] in "RC":
                next(tokens, None)
    return found


def _changed_in(cwd: Path, before, after) -> list[str]:
    # Unreadable first: git failing the same way twice is still unverified.
    if None in before or None in after:
        return ["(git could not read it)"]
    if before == after:
        return []
    names = set()
    if before[0] != after[0]:
        moved = _config.git_read(cwd, "diff", "--name-only", before[0], after[0])
        names |= set(moved.splitlines()) if moved is not None else {"(HEAD moved)"}
    now = _entries(after[1])
    names |= {path for _, path in _entries(before[1]) ^ now}
    # The same files dirty before and after, their content moved: one of them.
    return sorted(names or {path for _, path in now})


def changed(db, row, launch: LaunchContext | None, worktree, before: Snapshot) -> list[str]:
    """What changed since `before`, repository-relative to the checkout root."""
    refused = _guard(db, row, launch, Path(worktree))
    if refused is not None:
        return [f"(not compared: {refused})"]
    files = []
    for rel, was in before.items():
        cwd = Path(worktree) / rel
        files += [f"{rel}/{f}" if rel else f for f in _changed_in(cwd, was, _read(cwd))]
    return files


async def violation(db, row, launch, worktree, before: Snapshot | None, *, node_id, scope) -> bool:
    """Compare against `before` and record a violation of `scope` (a step's
    path or the node's id). False when nothing changed or nothing was read."""
    if before is None:
        return False
    files = changed(db, row, launch, worktree, before)
    if files:
        payload = {"node_id": node_id, "scope": scope, "files": files}
        await db.write(lambda c: events.append(c, row["id"], EVENT, payload))
    return bool(files)


async def stop(db, work_item_id: str, node_id: str) -> str:
    """The stop for a person, naming the scope and the files it changed."""
    raw = db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE work_item_id = ? AND type = ? "
            "ORDER BY seq DESC LIMIT 1",
            (work_item_id, EVENT),
        ).fetchone()
    )
    payload = json.loads(raw["payload"]) if raw else {"scope": node_id, "files": []}
    files = payload["files"]
    more = len(files) - _NAMED
    named = ", ".join(files[:_NAMED]) + (f" and {more} more" if more > 0 else "")
    reason = f"{payload['scope']} is read_only, but it changed the worktree: {named}"
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, reason))
    return "needs_human"
