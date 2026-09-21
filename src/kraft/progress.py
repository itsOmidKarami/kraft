"""Where the implementer is in its plan: "3 of 6 · title".

Derived on read, never stored. The plan's `## Task N` headings give the tasks;
the agent's latest `plan_progress` event and the highest `task N` its commits
name give the position. Tasks are numbered by position in the plan.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from kraft import events
from kraft import store as _store
from kraft.adapters.agent import artifact_path
from kraft.config import git_read

logger = logging.getLogger(__name__)

IMPLEMENTATION_HOOK = "on.implementation.start"

# `[ \t]`, not `\s`: under MULTILINE a `\s*` after the number would run across
# the newline and take the next line as a bare heading's title.
_TASK_HEADING = re.compile(
    r"^#{2,3}[ \t]+Task[ \t]+\d+\b[ \t]*[—:.\-]?[ \t]*(.*)$", re.MULTILINE | re.IGNORECASE
)
_COMMIT_TASK = re.compile(r"\btask\s+(\d+(?:\s*[+,&]\s*\d+)*)", re.IGNORECASE)
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def parse_tasks(text: str) -> list[tuple[str, bool]]:
    """The `(title, done)` pairs of a plan's `## Task N` / `### Task N`
    headings, in order. `done` is True iff the title ends with the exact
    tag `[DONE]` -- written by whoever edits the plan to say "already
    merged outside this work item, don't infer progress into it."

    Lines inside a fenced ```/~~~ code block are blanked out first -- a plan
    that quotes a `## Task N` heading in an example or test fixture (Kraft-szad)
    would otherwise count as a real one.
    """
    in_fence = False
    lines = []
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            lines.append("")
        else:
            lines.append("" if in_fence else line)
    titles = [m[1].strip() for m in _TASK_HEADING.finditer("\n".join(lines))]
    result = []
    for title in titles:
        done = title.endswith("[DONE]")
        if done:
            title = title[: -len("[DONE]")].rstrip()
        result.append((title, done))
    return result


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


def combine(tasks: list[tuple[str, bool]], reported: int, committed: int) -> dict | None:
    """A report means "starting task K"; a commit naming K means K is done, so
    the one after it is current -- unless that lands on a task the plan
    itself marks `[DONE]` (already merged outside this work item), in which
    case `current` walks back to the last task that isn't. Capped at the
    last task."""
    if not tasks:
        return None
    total = len(tasks)
    current = min(total, max(reported, committed + 1))
    while current > 1 and tasks[current - 1][1]:
        current -= 1
    return {
        "current": current,
        "total": total,
        "title": tasks[current - 1][0],
        "tasks": [
            {
                "n": n,
                "title": title,
                "state": "done"
                if done or n < current
                else "current"
                if n == current
                else "pending",
            }
            for n, (title, done) in enumerate(tasks, 1)
        ],
    }


def implementation_node(chain: dict) -> str | None:
    """By hook, not by name: a custom chain may call the node anything.

    Legacy chains only -- `chain` is the `chain_view`/`chain_definition` dict
    shape. A V1 chain has no hook-name strings on its nodes; see
    `implementing_nodes` for the V1 rule."""
    return next(
        (n["id"] for n in chain.get("nodes", []) if IMPLEMENTATION_HOOK in n.get("tasks", [])),
        None,
    )


def implementing_nodes(chain) -> list[str]:
    """Every node of a V1 `ResolvedChain` doing the work from the brief: a node
    whose own steps hold an `AgentTask` with no `skill` -- the predicate
    `prompts.py` depends on twice (`progress_note`, `scope_note`, both citing
    Kraft-s7c04.45 "never a node id"). No new marker field.

    The node's own steps only, the scope `ResolvedNode.produces()` uses. A
    fix loop's or `on_failure` pass's repair task is an agent task with no
    skill too (`merge_request_feedback` has two), but it repairs the node's
    output rather than saying what the node is for. Against the seeded
    library this is `implementation` alone."""
    from kraft.templates.models import AgentTask

    return [
        node.id
        for node in chain.nodes
        if any(
            isinstance(t.task, AgentTask) and t.task.skill is None
            for step in node.steps
            for t in step.tasks
        )
    ]


def v1_implementation_node(chain) -> str | None:
    """The one implementing node of a V1 chain, or None when it has none. Two
    is an authoring ambiguity: the first wins, and the log says so."""
    found = implementing_nodes(chain)
    if len(found) > 1:
        logger.warning(
            "chain %s has %d implementing nodes (%s); plan progress follows %s",
            chain.chain.id,
            len(found),
            ", ".join(found),
            found[0],
        )
    return found[0] if found else None


def chain_implementation_node(row) -> str | None:
    """The id of the node in `row`'s chain that implements the plan, whatever
    the item is doing now. None means the chain has no such node, so it has no
    plan progress to show at all."""
    v1 = _store.materialized_chain_of(row)
    if v1 is not None:
        return v1_implementation_node(v1.chain)
    return implementation_node(_store.chain_view(row))


def active_implementation_node(row) -> str | None:
    """The implementation node's id while `row` is running it, else None."""
    node_id = chain_implementation_node(row)
    if node_id and row["status"] == "active" and row["current_node_id"] == node_id:
        return node_id
    return None


def read_tasks(
    worktree: Path, attachments: list[dict], work_item_id: str
) -> list[tuple[str, bool]]:
    """The attached plan's tasks if there is one, else the plan node's artifact's."""
    rel = next((a["path"] for a in attachments if a.get("kind") == "plan"), None)
    try:
        return parse_tasks((worktree / (rel or artifact_path("plan", work_item_id))).read_text())
    except OSError, UnicodeDecodeError:
        return []


def tasks_for(row, worktree: Path) -> list[tuple[str, bool]]:
    return read_tasks(worktree, json.loads(row["attachments"] or "[]"), row["id"])


def run_state(conn, work_item_id: str, node_id: str) -> int:
    """The latest task the implementer reported starting on this node's current
    run -- everything after its latest `node_started`.

    Per-run on purpose: a progress report is a statement about the run that
    made it, and a bounced run has not reported anything yet. The *commit*
    signal is not per-run -- see `for_item` -- because a task committed before
    a bounce is still a task that is done.
    """
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
    return next(
        (e["payload"]["task"] for e in reversed(evs[start + 1 :]) if e["type"] == "plan_progress"),
        0,
    )


def for_item(db, row, worktree: Path) -> dict | None:
    """The API's `progress`, or None when there is nothing honest to show."""
    node_id = active_implementation_node(row)
    if node_id is None:
        return None
    tasks = tasks_for(row, worktree)
    if not tasks:
        return None
    reported = db.read(lambda c: run_state(c, row["id"], node_id))
    # The item's branch base, not this run's dispatch HEAD. `base_ref` is set at
    # worktree creation and re-set after every rebase, so this range is exactly
    # the commits this item has made -- across a reject bounce, which is when
    # the per-run range was empty by construction and floored `current` at 1.
    base = row["base_ref"]
    log = git_read(worktree, "log", "--format=%s", f"{base}..HEAD") if base else None
    return combine(tasks, reported, committed_task(log.splitlines() if log else []))
