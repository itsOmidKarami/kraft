"""Where the implementer is in its plan: "Task 3 of 6".

Derived on read, never stored. The plan's `## Task N` headings give the tasks;
the agent's latest `task_progress` event and the highest `task N` its commits
name give the position. Tasks are numbered by position in the plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from kraft import events
from kraft.adapters.agent import artifact_path
from kraft.config import git_read

IMPLEMENTATION_HOOK = "on.implementation.start"

# `[ \t]`, not `\s`: under MULTILINE a `\s*` after the number would run across
# the newline and take the next line as a bare heading's title.
_TASK_HEADING = re.compile(
    r"^#{2,3}[ \t]+Task[ \t]+\d+\b[ \t]*[—:.\-]?[ \t]*(.*)$", re.MULTILINE | re.IGNORECASE
)
_COMMIT_TASK = re.compile(r"\btask\s+(\d+(?:\s*[+,&]\s*\d+)*)", re.IGNORECASE)


def parse_tasks(text: str) -> list[str]:
    """The titles of a plan's `## Task N` / `### Task N` headings, in order."""
    return [m[1].strip() for m in _TASK_HEADING.finditer(text)]


def committed_task(subjects: list[str]) -> int:
    """The highest task number any commit subject names -- `Task 7: ...`,
    `(task 6, X)`, `Task 5+6: ...` -- or 0."""
    return max(
        (
            int(n)
            for subject in subjects
            for m in _COMMIT_TASK.finditer(subject)
            for n in re.findall(r"\d+", m[1])
        ),
        default=0,
    )


def combine(tasks: list[str], reported: int, committed: int) -> dict | None:
    """A report means "starting task K"; a commit naming K means K is done, so
    the one after it is current. Capped at the last task."""
    if not tasks:
        return None
    total = len(tasks)
    current = min(total, max(reported, committed + 1))
    return {
        "current": current,
        "total": total,
        "title": tasks[current - 1],
        "tasks": [
            {
                "n": n,
                "title": title,
                "state": "done" if n < current else "current" if n == current else "pending",
            }
            for n, title in enumerate(tasks, 1)
        ],
    }


def implementation_node(chain: dict) -> str | None:
    """By hook, not by name: a custom chain may call the node anything."""
    return next(
        (n["id"] for n in chain.get("nodes", []) if IMPLEMENTATION_HOOK in n.get("tasks", [])),
        None,
    )


def active_implementation_node(row) -> str | None:
    """The implementation node's id while `row` is running it, else None."""
    node_id = implementation_node(json.loads(row["chain_definition"]))
    if node_id and row["status"] == "active" and row["current_node_id"] == node_id:
        return node_id
    return None


def read_tasks(worktree: Path, attachments: list[dict], work_item_id: str) -> list[str]:
    """The attached plan's tasks if there is one, else the plan node's artifact's."""
    rel = next((a["path"] for a in attachments if a.get("kind") == "plan"), None)
    try:
        return parse_tasks((worktree / (rel or artifact_path("plan", work_item_id))).read_text())
    except OSError, UnicodeDecodeError:
        return []


def tasks_for(row, worktree: Path) -> list[str]:
    return read_tasks(worktree, json.loads(row["attachments"] or "[]"), row["id"])


def run_state(conn, work_item_id: str, node_id: str) -> tuple[int, str | None]:
    """(latest reported task, HEAD the implementer was dispatched on) for the
    node's current run: everything after its latest `node_started`."""
    # ponytail: full event scan per call, for active implementation items only;
    # index events on (work_item_id, type) if the board gets slow (Kraft-iytv).
    evs = events.read_after(conn, 0, work_item_id)
    start = max(
        (
            i
            for i, e in enumerate(evs)
            if e["type"] == "node_started" and e["payload"].get("node_id") == node_id
        ),
        default=-1,
    )
    run = evs[start + 1 :]
    reported = next(
        (e["payload"]["task"] for e in reversed(run) if e["type"] == "task_progress"), 0
    )
    session_id = next(
        (
            e["payload"]["session_id"]
            for e in run
            if e["type"] == "worker_session_created"
            and e["payload"].get("hook_point") == IMPLEMENTATION_HOOK
        ),
        None,
    )
    if session_id is None:
        return reported, None
    row = conn.execute(
        "SELECT head_sha FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return reported, (row["head_sha"] if row else None)


def for_item(db, row, worktree: Path) -> dict | None:
    """The API's `progress`, or None when there is nothing honest to show."""
    node_id = active_implementation_node(row)
    if node_id is None:
        return None
    tasks = tasks_for(row, worktree)
    if not tasks:
        return None
    reported, base = db.read(lambda c: run_state(c, row["id"], node_id))
    log = git_read(worktree, "log", "--format=%s", f"{base}..HEAD") if base else None
    return combine(tasks, reported, committed_task(log.splitlines() if log else []))
