"""Items that run in their repository's own checkout instead of a worktree.

Whether an item runs in place is frozen on its chain snapshot at intake
(`WorkItemTarget.in_place`); everything here reads it from there.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kraft import events, item_paths, store
from kraft.adapters.forge import git
from kraft.config import ConfigError, git_read
from kraft.vocab import ChainEvent, StopKind

is_in_place = store.is_in_place


def item_folder(run_dirs, row) -> Path:
    """The folder the item's work happens in: its repository's checkout when
    it runs in place, else its worktree."""
    return folder_for(run_dirs, row, store.materialized_chain_of(row))


def folder_for(run_dirs, row, chain) -> Path:
    """`item_folder` for a caller that already parsed the row's snapshot."""
    in_place = chain is not None and chain.target.in_place
    return Path(row["repo"]) if in_place else run_dirs.worktrees / row["id"]


class CheckoutRefused(RuntimeError):
    """The checkout cannot be taken without forcing it: a `checkout` stop
    (`StopKind.CHECKOUT`), never a failure and never auto-escalated. The
    message names the files or branch and what the person does next; `paths`
    are the files. `moved_from` is set when the checkout was switched before
    the refusal, so the switch is still recorded."""

    def __init__(self, message: str, paths: list[str] | None = None):
        super().__init__(message)
        self.paths = paths or []
        self.moved_from: dict | None = None


async def prepare(db, run_dirs, row, *, base: str) -> Path:
    """`ensure_worktree` for an in-place item: on its first call, pin
    `base_ref` and make the item's branch ref there. The checkout itself is
    untouched: no switch, no setup command, no carried files and no git
    config written. A branch deleted later is never made again: the take
    stops on it."""
    from kraft import builtins

    folder = item_folder(run_dirs, row)
    if not folder.is_dir():
        raise ConfigError(
            f"{folder}, the checkout this item runs in, is gone: put it back, or abandon the item"
        )
    if row["base_ref"] is not None:
        return folder
    base_ref = await builtins.upstream_head(folder, base)
    if not base_ref:
        return folder
    # Before the first take commits them: a missing stored copy fails the
    # item here, as `ensure_worktree` does, not as a failed task.
    builtins.stored_attachments(run_dirs, row["id"], attachments_of(row), row["repo"])
    branch = store.branch_for(row)
    if _ref(folder, branch) is None:
        done = subprocess.run(
            ["git", "branch", branch, base_ref], cwd=folder, capture_output=True, text=True
        )
        if done.returncode != 0:
            raise RuntimeError(f"could not make {branch} in {folder}: {done.stderr.strip()}")
    await db.write(lambda c: store.set_base_ref(c, row["id"], base_ref))
    return folder


async def take(db, run_dirs, row, *, attachments) -> None:
    """Put the checkout on the item's branch, right before a task that needs
    the folder: never on the walk's entry, which re-runs on every CI poll.
    Never forced: anything git would refuse, or that would clobber a file,
    raises `CheckoutRefused`. The person's uncommitted edits ride along. On
    the first take (the branch still at `base_ref`) the attachments are
    committed on the branch, by path. A switch is always recorded, even one a
    later step refused."""
    from kraft import builtins

    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (row["id"],)).fetchone()
    )
    folder = item_folder(run_dirs, row)
    branch = store.branch_for(row)
    first = (
        bool(attachments)
        and row["base_ref"] is not None
        and _ref(folder, branch) == row["base_ref"]
    )
    attachments = (
        builtins.stored_attachments(run_dirs, row["id"], attachments, row["repo"]) if first else []
    )
    base = await builtins.base_branch(db, row["id"], folder) if attachments else ""
    running = asyncio.ensure_future(
        asyncio.to_thread(_take, folder, branch, row["base_ref"], attachments, row["id"], base)
    )
    # A cancel (a pause) cannot stop git in its thread, and a checkout left to
    # finish never loses the person's work: wait it out, however many cancels
    # arrive, so a switch it made is recorded; then honour the cancel.
    cancelled = False
    while not running.done():
        try:
            await asyncio.shield(running)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # read off `running` below
            pass
    failed = running.exception()
    moved = failed.moved_from if isinstance(failed, CheckoutRefused) else None
    if failed is None:
        moved = running.result()
    if moved is not None:
        await db.write(
            lambda c: events.append(
                c, row["id"], ChainEvent.CHECKOUT_SWITCHED, {**moved, "to": branch}
            )
        )
    if cancelled:
        raise asyncio.CancelledError
    if failed is not None:
        raise failed


#: One lock per checkout: co-tasks of one step take it at once, and git's
#: index lock would refuse the second.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(folder: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(folder.resolve()), threading.Lock())


def _ref(folder: Path, branch: str) -> str | None:
    return git_read(
        folder, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
    )


def _git_lines(folder: Path, command: str, *args: str) -> list[str]:
    """`git <command> -z *args`, one path a line, unquoted. Fails closed: a
    read git refuses is a `CheckoutRefused`, never an empty answer."""
    done = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "--literal-pathspecs"]
        + [command, "-z", *args],
        cwd=folder,
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    if done.returncode != 0:
        raise CheckoutRefused(
            f"Kraft could not read {folder} (`git {command}` said: {done.stderr.strip()}), "
            "and does not go on without it: fix that, then retry the item"
        )
    return [p for p in done.stdout.split("\0") if p]


def _take(folder, branch, base_ref, attachments, wid, base) -> dict | None:
    """`take`'s git, under the checkout's lock. Returns `{"from": ...}` (and
    git's `output` when a hook failed after switching) when it switched."""
    with _lock(folder):
        try:
            git.assert_no_operation(folder)
        except git.UnsafeWorktree:
            raise CheckoutRefused(
                f"{folder} has a git operation in progress (a rebase, merge, cherry-pick, "
                "revert or bisect): finish or abort it yourself, then retry the item"
            ) from None
        unmerged = _git_lines(folder, "diff", "--name-only", "--diff-filter=U")
        if unmerged:
            raise CheckoutRefused(
                f"{folder} has unmerged paths: {', '.join(unmerged)}; resolve them yourself, "
                "then retry the item",
                unmerged,
            )
        if _ref(folder, branch) is None:
            raise CheckoutRefused(
                f"the branch {branch} is gone from {folder}, and Kraft does not make it again: "
                "restore it (`git reflog` may still have its commits) and retry, or abandon "
                "the item"
            )
        moved = None
        if git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True) != (
            f"refs/heads/{branch}"
        ):
            moved = _switch(folder, branch)
        if attachments and base_ref and _ref(folder, branch) == base_ref:
            try:
                _attach(folder, attachments, wid, base)
            except CheckoutRefused as exc:
                exc.moved_from = moved
                raise
        return moved


def _switch(folder: Path, branch: str) -> dict:
    """A plain `git checkout <branch>`: never `-f`, and refused before it
    would overwrite an ignored file the branch tracks, which git itself
    would replace without a word."""
    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    moved_from = (head or "").removeprefix("refs/heads/") or git_read(folder, "rev-parse", "HEAD")
    changed = _git_lines(folder, "diff", "--name-only", "--no-renames", "HEAD", branch)
    ignored = _ignored_in_the_way(folder, changed)
    if ignored:
        raise CheckoutRefused(
            f"switching {folder} from {moved_from} to {branch} would overwrite "
            f"{', '.join(ignored)}, ignored here but tracked on {branch}. Move them aside "
            "yourself, then retry the item",
            ignored,
        )
    # Run to the end, never killed: git refuses before it would overwrite an
    # edit, so only a checkout stopped part way could leave a tree half one
    # branch and half the other. A hung filter or hook holds this checkout's
    # lock alone.
    done = subprocess.run(
        ["git", "-c", "core.quotePath=false", "checkout", branch, "--"],
        cwd=folder,
        capture_output=True,
        text=True,
        errors="surrogateescape",
        env={**os.environ, "LC_ALL": "C"},
        stdin=subprocess.DEVNULL,
    )
    said = done.stderr.strip() or done.stdout.strip()
    if done.returncode == 0:
        return {"from": moved_from}
    if _on(folder, branch):
        # A post-checkout hook failed after git had switched.
        return {"from": moved_from, "output": said}
    # git lists each file it would overwrite on a tab-indented line.
    paths = [ln.strip() for ln in done.stderr.splitlines() if ln.startswith("\t")]
    what = f"it would overwrite {', '.join(paths)}" if paths else f"git said: {said}"
    advice = "Commit, stash or move those changes" if paths else "Sort that out"
    raise CheckoutRefused(
        f"git would not switch {folder} from {moved_from} to {branch}: {what}. "
        f"{advice} yourself, then retry the item",
        paths,
    )


def _on(folder: Path, branch: str) -> bool:
    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    return head == f"refs/heads/{branch}"


def _ignored_in_the_way(folder: Path, changed: list[str]) -> list[str]:
    """The ignored files on disk a switch writing `changed` would replace: a
    changed path itself, or a file where it needs a directory."""
    if not changed:
        return []
    ignored = [
        e.rstrip("/")
        for e in _git_lines(
            folder, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory"
        )
    ]
    # On a case-insensitive filesystem `Gen.txt` on the branch is `gen.txt` here.
    insensitive = (
        git_read(folder, "config", "--bool", "core.ignorecase", expected_failure=True) == "true"
    )
    fold = str.casefold if insensitive else str
    hits = []
    for c in changed:
        blocker = _first_on_disk(folder, c)
        for e in ignored if blocker else []:
            if fold(blocker) == fold(e):
                hits.append(e)  # the person's own spelling
            elif fold(blocker).startswith(fold(e) + "/"):
                hits.append(blocker)
    return sorted(set(hits))


def _first_on_disk(folder: Path, path: str) -> str | None:
    """`path` when it is on disk, or the file (not directory) standing where
    one of its parents should be; None when nothing is in the way."""
    parts = path.split("/")
    for i in range(1, len(parts) + 1):
        p = "/".join(parts[:i])
        full = folder / p
        if not os.path.lexists(full):
            return None
        if i == len(parts) or full.is_symlink() or not full.is_dir():
            return p
    return None


def _attach(folder: Path, attachments: list[dict], wid: str, base: str) -> None:
    """Copy the attachments in and commit them on the branch, by path. A file
    already there with other content is the person's, and refuses; so does a
    checkout with no identity to commit as."""
    from kraft import builtins

    for key in ("user.name", "user.email"):
        if not git_read(folder, "config", "--get", key, expected_failure=True):
            raise CheckoutRefused(
                f"no {key} configured in {folder}, so Kraft cannot commit the item's "
                "attachments there: set it, then retry the item"
            )
    root = folder.resolve()
    for a in attachments:
        dest = folder / a["path"]
        src = Path(a["source"])
        if dest.is_symlink() or not dest.resolve().is_relative_to(root):
            clash = True
        else:
            clash = dest.exists() and dest.read_bytes() != src.read_bytes()
        if clash:
            raise CheckoutRefused(
                f"{a['path']} in {folder} is not the {a['kind']} this item was filed with, "
                "and Kraft will not overwrite it: move it aside, then retry the item",
                [a["path"]],
            )
    for a in attachments:
        dest = folder / a["path"]
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(a["source"], dest)
    builtins._commit_paths(
        folder,
        [a["path"] for a in attachments],
        f"chore: attach {'+'.join(a['kind'] for a in attachments)} for {wid}",
        base,
    )


def attachments_of(row) -> list[dict]:
    from kraft.executor import entry

    return entry.attachments_of(row)


@dataclass(frozen=True)
class TaskMark:
    """The checkout as an agent task found it: the branch's tip and the paths
    already dirty that are the person's, not the item's."""

    tip: str
    dirty: frozenset[str]
    started_at: str  # ISO, whole seconds: the reflog's resolution


@dataclass(frozen=True)
class TaskCheck:
    moved_to: str | None
    stray_commits: list[str]
    overlap: list[str]
    leftovers: dict[str, str]


#: How many paths a `task_marked` or `left_uncommitted` event names; the
#: whole set is in the item's folder (`item_paths.checkout_record`), since every event goes to
#: every client.
SAMPLE = 50


def _write(path: Path, data: dict) -> None:
    """Atomic, and safe for co-tasks writing the same file at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data))
    os.replace(tmp, path)


def save_mark(run_dirs, wid: str, task_path: str, m: TaskMark) -> None:
    data = {"tip": m.tip, "started_at": m.started_at, "dirty": sorted(m.dirty)}
    _write(item_paths.checkout_record(run_dirs, wid, f"{task_path}.mark.json"), data)


def load_mark(run_dirs, wid: str, task_path: str) -> TaskMark | None:
    try:
        d = json.loads(
            item_paths.checkout_record(run_dirs, wid, f"{task_path}.mark.json").read_text()
        )
    except FileNotFoundError:
        return None
    return TaskMark(tip=d["tip"], dirty=frozenset(d["dirty"]), started_at=d["started_at"])


def save_leftovers(run_dirs, wid: str, paths: Mapping[str, str]) -> None:
    _write(item_paths.checkout_record(run_dirs, wid, "leftovers.json"), dict(paths))


def leftovers(run_dirs, wid: str) -> dict[str, str]:
    """Path to blob sha of what the item's tasks last left uncommitted."""
    try:
        return json.loads(item_paths.checkout_record(run_dirs, wid, "leftovers.json").read_text())
    except FileNotFoundError:
        return {}


def mark(checkout: Path, branch: str, leftovers: Mapping[str, str]) -> TaskMark:
    """Before an agent task: the tip and the person's dirty paths. A leftover
    of the item's own whose content is unchanged is the item's, so a later
    task committing it is no overlap."""
    tip = _ref(checkout, branch)
    if tip is None:
        raise CheckoutRefused(f"the branch {branch} is gone from {checkout}")
    dirty = _dirty(checkout)
    mine = [p for p in dirty if p in leftovers]
    hashes = _hashes(checkout, mine)
    return TaskMark(
        tip=tip,
        dirty=frozenset(p for p in dirty if p not in mine or hashes[p] != leftovers[p]),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


#: git 2.31 and later; older git refuses it, and `check` says so.
_DIFF_MERGES = "--diff-merges=dense-combined"


def check(checkout: Path, branch: str, mark: TaskMark) -> TaskCheck:
    """After an agent task: never a repair, only what a stop needs to say."""
    head = git_read(checkout, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    moved_to = None
    if head != f"refs/heads/{branch}":
        moved_to = (head or "").removeprefix("refs/heads/") or (
            f"detached at {git_read(checkout, 'rev-parse', 'HEAD')}"
        )
    since = datetime.fromisoformat(mark.started_at).timestamp()
    entries = []
    for entry in _git_lines(checkout, "log", "-g", "--date=unix", "--format=%H %gd %gs", "HEAD"):
        sha, selector, subject = entry.split(" ", 2)
        if int(selector.rsplit("{", 1)[1].rstrip("}")) >= since:
            entries.append((sha, subject))
    # Oldest first, following which branch HEAD was on (the take left it on
    # the item's): only what HEAD reached while it was elsewhere is stray, so
    # an amend, reset or rebase on the item's branch never is.
    on, made = branch, []
    for sha, subject in reversed(entries):
        if subject.startswith("checkout: moving from "):
            on = subject.rsplit(" to ", 1)[1]
        elif subject.startswith("rebase (finish): returning to refs/heads/"):
            on = subject.removeprefix("rebase (finish): returning to refs/heads/")
        elif on != branch and sha not in made:
            made.append(sha)
    made.reverse()  # newest first, as named
    off = (
        set(_git_lines(checkout, "log", "--format=%H", *made, "--not", f"refs/heads/{branch}"))
        if made
        else set()
    )
    # Each of the task's own commits against its first parent; a merge counts
    # only what it resolved, never the files the merged branch brought.
    try:
        touched = _git_lines(
            checkout,
            "log",
            "--first-parent",
            _DIFF_MERGES,
            "--no-renames",
            "--name-only",
            "--format=",
            f"{mark.tip}..refs/heads/{branch}",
        )
    except CheckoutRefused as exc:
        if "diff-merges" not in str(exc):
            raise
        raise CheckoutRefused(
            f"Kraft needs git 2.31 or newer to check what an in-place task committed "
            f"in {checkout} ({exc}): update git, then retry the item"
        ) from None
    new = [p for p in _dirty(checkout) if p not in mark.dirty]
    return TaskCheck(
        moved_to=moved_to,
        stray_commits=[s for s in made if s in off],
        overlap=sorted(mark.dirty.intersection(touched)),
        leftovers=_hashes(checkout, new),
    )


def _dirty(checkout: Path) -> list[str]:
    """Modified, staged or untracked, never ignored: one path an entry."""
    entries = _git_lines(
        checkout, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames"
    )
    return [e[3:] for e in entries]


def _hashes(checkout: Path, paths: list[str]) -> dict[str, str]:
    """Each path's blob sha; "" for one that is gone or is not a file."""
    out = dict.fromkeys(paths, "")
    files = [p for p in paths if (checkout / p).is_file()]
    for i in range(0, len(files), 500):
        chunk = files[i : i + 500]
        shas = subprocess.run(
            ["git", "hash-object", "--", *chunk],
            cwd=checkout,
            capture_output=True,
            text=True,
            errors="surrogateescape",
        ).stdout.split()
        if len(shas) == len(chunk):
            out.update(zip(chunk, shas, strict=True))
        elif len(chunk) > 1:
            # One unreadable file fails the whole call: the rest one by one.
            for p in chunk:
                out.update(_hashes(checkout, [p]))
    return out


async def mark_task(db, run_dirs, row, folder: Path, node_id: str, task_path: str) -> None:
    """Before an in-place agent task: what `check_task` compares against,
    kept in the item's folder so a restart's reattach reads the same mark."""
    wid = row["id"]
    m = await asyncio.to_thread(mark, folder, store.branch_for(row), leftovers(run_dirs, wid))
    save_mark(run_dirs, wid, task_path, m)
    dirty = sorted(m.dirty)
    payload = {"node_id": node_id, "task": task_path, "tip": m.tip, "started_at": m.started_at}
    payload |= {"dirty_count": len(dirty), "dirty": dirty[:SAMPLE]}
    await db.write(lambda c: events.append(c, wid, ChainEvent.TASK_MARKED, payload))


async def check_task(db, run_dirs, row, folder: Path, node_id: str, task_path: str):
    """After an in-place agent task, in place of the sweep: record what it
    left uncommitted, and stop the item as `checkout` when the checkout moved
    or the task committed a path the person had changed. Never a repair: no
    switch back, no commit, no undo. Answers the stop's reason, or None."""
    wid = row["id"]
    m = load_mark(run_dirs, wid, task_path)
    if m is None:  # launched before marks were recorded
        return None
    branch = store.branch_for(row)
    stray: list[str] = []
    try:
        found = await asyncio.to_thread(check, folder, branch, m)
    except CheckoutRefused as exc:
        reason = str(exc)
    else:
        stray = found.stray_commits
        if found.leftovers:
            save_leftovers(run_dirs, wid, found.leftovers)
            left = sorted(found.leftovers)
            payload = {"node_id": node_id, "task": task_path, "count": len(left)}
            payload["paths"] = left[:SAMPLE]
            await db.write(lambda c: events.append(c, wid, ChainEvent.LEFT_UNCOMMITTED, payload))
        reasons = []
        made = (
            f"commits made or moved onto during it that are not on {branch}: " + ", ".join(stray)
            if stray
            else ""
        )
        if found.moved_to:
            reasons.append(
                f"the checkout {folder} moved to {found.moved_to} during the run of "
                f"{task_path}{'; ' + made if made else ''}. Kraft did not switch it back, "
                f"nor move those commits: resume once {folder} may go back to {branch}"
            )
        elif stray:
            # Switched away, committed and switched back: the work may be elsewhere.
            reasons.append(
                f"{task_path} left {made}. Kraft did not move them: check where that work "
                "belongs, then resume"
            )
        if found.overlap:
            reasons.append(
                f"the agent committed {', '.join(found.overlap)}, which already had uncommitted "
                "changes before it started; check that the commit holds only the item's work, "
                "then resume"
            )
        reason = "; ".join(reasons)
    if not reason:
        return None
    last = db.read(lambda c: store.latest_session_per_task(c, wid, node_id, [task_path]))
    # Resume goes on past the task, unless its work may be on another branch.
    facts = {"task": task_path, "rerun": bool(stray)}
    if last:
        facts |= {"session": last[0]["id"], "round": last[0]["round"]}
    # Never `stuck`: an escalation would take the checkout straight back.
    await db.write(
        lambda c: store.mark_needs_human(
            c, wid, node_id, reason, kind=StopKind.CHECKOUT, facts=facts
        )
    )
    return reason


async def stop_refused(db, wid: str, node_id: str, exc: CheckoutRefused) -> None:
    """A person's request for an agent (an escalation, a reply) whose
    checkout could not be taken: the item stops for them, as a walk task's
    refused take does. Never `stuck`: an escalation would only take it again."""
    await db.write(
        lambda c: store.mark_needs_human(c, wid, node_id, str(exc), kind=StopKind.CHECKOUT)
    )
