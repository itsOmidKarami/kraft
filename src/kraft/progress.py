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
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from kraft import events
from kraft import store as _store
from kraft.adapters.agent import artifact_path
from kraft.config import git_read
from kraft.vocab import HOLDS_SLOT

logger = logging.getLogger(__name__)


class TaskProgress(BaseModel):
    n: int
    title: str
    state: Literal["done", "current", "pending"]
    # The short hash of the newest commit on the item's branch naming this
    # task; only the detail read (`for_detail`) fills it.
    sha: str | None = None


class ProgressReport(BaseModel):
    current: int
    total: int
    title: str
    tasks: list[TaskProgress]

    @model_validator(mode="after")
    def _current_capped(self) -> Self:
        if self.current > self.total:
            raise ValueError("current task index cannot exceed total")
        return self


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


def _named_tasks(subject: str) -> list[int]:
    """Every task number one commit subject names."""
    return [int(n) for m in _COMMIT_TASK.finditer(subject) for n in re.findall(r"\d+", m[1])]


def committed_task(subjects: list[str]) -> int:
    """The highest task number any commit subject names -- `Task 7: ...`,
    `(task 6, X)`, `Task 5+6: ...` -- or 0."""
    return max((n for subject in subjects for n in _named_tasks(subject)), default=0)


def combine(tasks: list[tuple[str, bool]], reported: int, committed: int) -> ProgressReport | None:
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
    return ProgressReport(
        current=current,
        total=total,
        title=tasks[current - 1][0],
        tasks=[
            TaskProgress(
                n=n,
                title=title,
                state="done" if done or n < current else "current" if n == current else "pending",
            )
            for n, (title, done) in enumerate(tasks, 1)
        ],
    )


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
    if node_id and row["status"] in HOLDS_SLOT and row["current_node_id"] == node_id:
        return node_id
    return None


def read_tasks(
    worktree: Path, attachments: list[dict], work_item_id: str
) -> list[tuple[str, bool]]:
    """The attached plan's tasks if there is one, else the plan node's artifact's."""
    rel = next((a["path"] for a in attachments if a.get("kind") == "plan"), None)
    try:
        return parse_tasks((worktree / (rel or artifact_path("plan", work_item_id))).read_text())
    except (OSError, UnicodeDecodeError):
        return []


def tasks_for(row, worktree: Path) -> list[tuple[str, bool]]:
    return read_tasks(worktree, json.loads(row["attachments"] or "[]"), row["id"])


def run_state(evs: list[dict], node_id: str) -> int:
    """The latest task the implementer reported starting on this node's current
    run -- everything after its latest `node_started`.

    Per-run on purpose: a progress report is a statement about the run that
    made it, and a bounced run has not reported anything yet. The *commit*
    signal is not per-run -- see `for_item` -- because a task committed before
    a bounce is still a task that is done.
    """
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


def is_rework(evs: list[dict], node_id: str) -> bool:
    """Whether `node_id`'s current run is rework a gate rejection sent it back
    for (Kraft-hj2q9): it completed once, a gate was rejected since, and the
    walk re-entered *here* first. That run follows the rejection note, not the
    plan. A rejection that re-entered at the plan node or earlier is not
    rework: the plan may have been rewritten, and this run follows it again.
    """
    done = max(
        (
            i
            for i, e in enumerate(evs)
            if e["type"] == "node_completed" and e["payload"].get("node_id") == node_id
        ),
        default=None,
    )
    if done is None:
        return False
    rejected = max(
        (i for i in range(done + 1, len(evs)) if evs[i]["type"] == "gate_rejected"), default=None
    )
    if rejected is None:
        return False
    first = next(
        (e["payload"].get("node_id") for e in evs[rejected + 1 :] if e["type"] == "node_started"),
        None,
    )
    return first == node_id


def rework_run(db, row) -> bool:
    """`is_rework` for `row`'s implementing node, read off its event timeline.
    The one check the progress field, the progress route and the implementer's
    prompt share, so none of them offers a plan the others have dropped."""
    node_id = chain_implementation_node(row)
    return node_id is not None and is_rework(
        db.read(lambda c: events.read_after(c, 0, row["id"])), node_id
    )


def for_item(db, row, worktree: Path) -> ProgressReport | None:
    """The API's `progress`, or None when there is nothing honest to show."""
    node_id = active_implementation_node(row)
    if node_id is None:
        return None
    # One read serves both checks (Kraft-dl5fl).
    # ponytail: full event scan per call, for active implementation items only;
    # index events on (work_item_id, type) if the board gets slow (Kraft-iytv).
    evs = db.read(lambda c: events.read_after(c, 0, row["id"]))
    if is_rework(evs, node_id):
        return None
    tasks = tasks_for(row, worktree)
    if not tasks:
        return None
    reported = run_state(evs, node_id)
    return combine(tasks, reported, committed_task([s for _, s in branch_log(row, worktree)]))


def branch_log(row, worktree: Path) -> list[tuple[str, str]]:
    """`(short sha, subject)` of every commit this item has made, newest first.

    The item's branch base, not a run's dispatch HEAD. `base_ref` is set at
    worktree creation and re-set after every rebase, so this range is exactly
    the commits this item has made -- across a reject bounce, which is when
    the per-run range was empty by construction and floored `current` at 1."""
    base = row["base_ref"]
    log = git_read(worktree, "log", "--format=%h %s", f"{base}..HEAD") if base else None
    return [(line.partition(" ")[0], line.partition(" ")[2]) for line in (log or "").splitlines()]


def for_detail(db, row, worktree: Path) -> ProgressReport | None:
    """The detail read's `progress`: `for_item`'s, kept once the item stops on
    the implementing node or moves past it, with each task's commit.

    None until the node has started, while the item stands before it (the plan
    is not being followed yet, or is being rewritten after a rejection), and
    for a plan with no `## Task N` headings. A completed node, or a rework run
    after a gate rejection, reads as every task done."""
    node_id = chain_implementation_node(row)
    if node_id is None:
        return None
    order = [n["id"] for n in _store.chain_view(row).get("nodes", [])]
    here = row["current_node_id"]
    if here in order and node_id in order and order.index(here) < order.index(node_id):
        return None
    evs = db.read(lambda c: events.read_after(c, 0, row["id"]))

    def last(kind: str) -> int:
        return max(
            (
                i
                for i, e in enumerate(evs)
                if e["type"] == kind and e["payload"].get("node_id") == node_id
            ),
            default=-1,
        )

    started = last("node_started")
    if started < 0:
        return None
    tasks = tasks_for(row, worktree)
    if not tasks:
        return None
    log = branch_log(row, worktree)
    if last("node_completed") > started or is_rework(evs, node_id):
        report = ProgressReport(
            current=len(tasks),
            total=len(tasks),
            title=tasks[-1][0],
            tasks=[
                TaskProgress(n=n, title=title, state="done")
                for n, (title, _) in enumerate(tasks, 1)
            ],
        )
    else:
        report = combine(tasks, run_state(evs, node_id), committed_task([s for _, s in log]))
        assert report is not None  # `tasks` is not empty
    shas: dict[int, str] = {}
    for sha, subject in log:
        for n in _named_tasks(subject):
            shas.setdefault(n, sha)
    for t in report.tasks:
        t.sha = shas.get(t.n)
    return report
