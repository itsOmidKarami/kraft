"""A person takes over a stopped item's agent session (`kraft item takeover`).

A takeover is a lock on the item's row, held by the CLI's own pid. The server
checks and takes it in one write (`prepare`), the CLI runs the agent in the
foreground on the person's terminal, and the lock is released when the CLI
exits (`release`) -- or, when the CLI was killed hard, by the next check that
finds its pid gone (`live`). No `worker_sessions` row is written: a row would
be adopted by `reattach`, counted by the local worker, stopped by every stop
loop and shown by the board, none of which is a person's terminal.

The item's status never changes while a person has it.
"""

from __future__ import annotations

import asyncio
import glob
import os
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from kraft import escalate, events, proc_identity, store
from kraft import harness as _harness
from kraft.executor import dispatch
from kraft.remote import placement
from kraft.templates.models import AgentTask
from kraft.vocab import LIVE, WorkItemEvent
from kraft.vocab.sql import in_list, marks

#: A session row that names no harness was written before the column existed,
#: and is read as claude (`worker_sessions.harness`).
_DEFAULT_HARNESS = "claude"


class TakeoverRefused(ValueError):
    """The takeover cannot start; the message says why and what to do."""


@dataclass(frozen=True)
class Prepared:
    argv: list[str]
    cwd: str
    session_id: str
    #: The worker session row the takeover forks, or None for a fresh session.
    resumed_from: str | None


@dataclass(frozen=True)
class Released:
    status: str
    #: Whether the takeover's session is left on the worker's row for the next
    #: `resume` to continue.
    recorded: bool
    warnings: list[str]


def cwd_of(db, run_dirs, row) -> Path:
    """The folder the agent runs in: a workspace's root, else the item's own
    folder (its worktree, or the checkout when it runs in place)."""
    from kraft import inplace

    root = db.read(
        lambda c: c.execute(
            "SELECT repo_path FROM work_item_repos WHERE work_item_id = ? AND role = 'root'",
            (row["id"],),
        ).fetchone()
    )
    return Path(root["repo_path"]) if root else inplace.item_folder(run_dirs, row)


def _find_node(nodes, node_id: str):
    for node in nodes:
        if node.id == node_id:
            return node
        if (inner := _find_node(node.body, node_id)) is not None:
            return inner
    return None


def _latest_agent_session(db, row, node):
    """The node's most recent agent session row, which picks the task and the
    harness, or None when no agent task of it has run."""
    paths = [t.path for t in node.tasks() if isinstance(t.task, AgentTask)]
    if not paths:
        return None, {}
    session = db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            f"AND hook_point IN ({marks(paths)}) ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (row["id"], node.id, *paths),
        ).fetchone()
    )
    return session, {t.path: t for t in node.tasks()}


def _since(lock) -> str:
    started = lock["takeover_started_at"] or ""
    return started[11:16] or "earlier"


def _live_worker(conn: sqlite3.Connection, wid: str) -> bool:
    rows = conn.execute(
        "SELECT pid, pid_start_time FROM worker_sessions WHERE work_item_id = ? "
        f"AND status IN ({in_list(LIVE)}) AND pid IS NOT NULL",
        (wid,),
    ).fetchall()
    return any(proc_identity.identity_ok(r["pid"], r["pid_start_time"]) for r in rows)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def _tip_of(cwd: Path, branch: str) -> str:
    done = _git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return done.stdout.strip() if done.returncode == 0 else ""


async def prepare(
    db,
    run_dirs,
    row,
    harnesses: _harness.HarnessSet,
    *,
    launch,
    pid: int,
    pid_start: float,
    starting: set[str],
    walk_running: bool,
) -> Prepared:
    """Refuse a takeover that can't go ahead, or take the lock and say how to
    start the agent. Raises `TakeoverRefused`.

    What can change under the check -- the item's status, another takeover, a
    worker still dying -- is checked again inside the write that takes the lock,
    so two takeovers at once leave exactly one of them holding it."""
    wid = row["id"]
    status = row["status"]
    if status not in ("paused", "needs_human"):
        raise TakeoverRefused(f"work item is {status}; takeover needs paused or needs_human")
    try:
        policy = dispatch.item_sandbox(row, launch)
    except dispatch.InPlaceSandboxed:
        policy = None  # an in-place item runs unsandboxed whatever is configured
    except dispatch.SandboxUnresolved as exc:
        raise TakeoverRefused(str(exc)) from exc
    if policy is not None:
        raise TakeoverRefused(f"takeover doesn't support sandboxed items yet ({policy['kind']})")

    chain = store.materialized_chain_of(row)
    node = (
        _find_node(chain.chain.nodes, row["current_node_id"])
        if chain and row["current_node_id"]
        else None
    )
    session, tasks = _latest_agent_session(db, row, node) if node is not None else (None, {})
    if session is None:
        raise TakeoverRefused("nothing has run on this step yet; resume it instead")
    task = tasks[session["hook_point"]]

    machine = session["runner"] or placement.runner_for(row, task.runner, node.id)
    if machine not in (placement.LOCAL, placement.ANY):
        raise TakeoverRefused(f"this step ran on {machine}; takeover needs a local worktree")

    h = harnesses.valid.get(session["harness"] or _DEFAULT_HARNESS)
    if h is None:
        raise TakeoverRefused(f"harness {session['harness']!r} is not available")
    if h.interactive is None:
        # The same words `build_interactive_argv` refuses with.
        raise TakeoverRefused(f"takeover isn't supported for {h.id} yet")

    step = next((s for s in node.steps_in() if any(t.path == task.path for t in s.tasks)), None)
    agents = [t.path for t in step.tasks if isinstance(t.task, AgentTask)] if step else []
    if len(agents) > 1:
        raise TakeoverRefused(
            f"this step has several tasks ({', '.join(agents)}); takeover needs one"
        )

    if (lock := await live(db, run_dirs, wid, harnesses)) is not None:
        raise TakeoverRefused(f"a person has this item's session open (since {_since(lock)})")
    still_stopping = "the item's last session is still stopping; try again in a moment"
    if walk_running or wid in starting:
        raise TakeoverRefused(still_stopping)
    if escalate.escalation_running(db, wid):
        raise TakeoverRefused("an escalation is running on this item")

    # The session to fork: the task's own paused one, found as a resume finds it.
    resumed = None
    if h.interactive.resume is not None:
        resumed = dispatch._resumable_session(db, wid, node, task, h)
    cwd = cwd_of(db, run_dirs, row)
    branch = store.branch_for(row)
    tip = await asyncio.to_thread(_tip_of, cwd, branch)
    minted = str(uuid.uuid4())
    argv = _harness.build_interactive_argv(
        h, session_id=minted, resume=resumed[1] if resumed else None
    )
    resumed_from = resumed[0] if resumed else None

    def take(conn: sqlite3.Connection) -> None:
        current = conn.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
        if current is None or current["status"] != status:
            raise TakeoverRefused(
                f"work item is {current['status'] if current else 'gone'}; "
                "takeover needs paused or needs_human"
            )
        if (held := store.takeover_lock(conn, wid)) is not None:
            raise TakeoverRefused(f"a person has this item's session open (since {_since(held)})")
        if _live_worker(conn, wid):
            raise TakeoverRefused(still_stopping)
        store.take_takeover_lock(
            conn,
            wid,
            pid=pid,
            pid_start=pid_start,
            session_id=minted,
            resumed_from=resumed_from,
            branch=branch,
            tip=tip,
        )
        events.append(
            conn,
            wid,
            WorkItemEvent.TAKEOVER_STARTED,
            {"session_id": minted, "resumed_from": resumed_from},
        )

    await db.write(take)
    return Prepared(argv=argv, cwd=str(cwd), session_id=minted, resumed_from=resumed_from)


async def live(db, run_dirs, work_item_id: str, harnesses: _harness.HarnessSet):
    """The item's takeover lock when the CLI that took it is still running.
    A lock whose pid is gone (a SIGKILLed CLI, a closed laptop) is released the
    way the CLI would have, so it never wedges the item; None then."""
    lock = db.read(lambda c: store.takeover_lock(c, work_item_id))
    if lock is None:
        return None
    if proc_identity.identity_ok(lock["takeover_pid"], lock["takeover_pid_start"]):
        return lock
    await release(db, run_dirs, work_item_id, harnesses)
    return None


def _warnings(cwd: Path, lock) -> list[str]:
    """What a person left behind that the next run would trip over."""
    if not cwd.is_dir():
        return []
    out = []
    branch, tip = lock["takeover_branch"], lock["takeover_tip"]
    if _git(cwd, "status", "--porcelain").stdout.strip():
        out.append("uncommitted changes in the worktree")
    if _git(cwd, "symbolic-ref", "--quiet", "HEAD").stdout.strip() != f"refs/heads/{branch}":
        out.append(f"HEAD is not on {branch}")
    if (
        tip
        and _git(cwd, "merge-base", "--is-ancestor", tip, f"refs/heads/{branch}").returncode == 1
    ):
        out.append("history was rewritten since the takeover started")
    return out


def _session_recorded(h: _harness.Harness | None, session_id: str) -> bool:
    """The takeover's session was written: a person who quit before any turn
    left no file, and there is nothing to continue."""
    pattern = h.interactive.session_file if h and h.interactive else None
    if not pattern:
        return False
    return bool(glob.glob(os.path.expanduser(pattern.replace("{value}", session_id))))


async def release(db, run_dirs, work_item_id: str, harnesses: _harness.HarnessSet) -> Released:
    """End the item's takeover: clear the lock, leave the session on the worker
    row it forked from when it was written, and report what to check. A
    release with no lock held is a no-op, so the CLI's own and a stale-lock
    sweep can race."""
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    lock = db.read(lambda c: store.takeover_lock(c, work_item_id))
    if row is None or lock is None:
        return Released(status=row["status"] if row else "", recorded=False, warnings=[])
    session_id = lock["takeover_session_id"]
    forked = lock["takeover_resumed_from"]
    warnings = await asyncio.to_thread(_warnings, cwd_of(db, run_dirs, row), lock)
    recorded = False
    if forked is not None:
        source = db.read(
            lambda c: c.execute(
                "SELECT harness FROM worker_sessions WHERE work_item_id = ? AND id = ?",
                (work_item_id, forked),
            ).fetchone()
        )
        h = harnesses.valid.get((source["harness"] if source else None) or _DEFAULT_HARNESS)
        recorded = await asyncio.to_thread(_session_recorded, h, session_id)

    def end(conn: sqlite3.Connection) -> bool:
        held = store.takeover_lock(conn, work_item_id)
        if held is None or held["takeover_session_id"] != session_id:
            return False  # released, or taken again, by someone else meanwhile
        store.clear_takeover_lock(conn, work_item_id)
        if recorded and forked is not None:
            store.set_cli_session_id(conn, work_item_id, forked, session_id)
        events.append(
            conn,
            work_item_id,
            WorkItemEvent.TAKEOVER_ENDED,
            {"recorded": recorded, "warnings": warnings},
        )
        return True

    await db.write(end)
    return Released(status=row["status"], recorded=recorded, warnings=warnings)
