"""A reply round: one more measurement of a node on the head it was about to complete on, to
read what the bots answered Kraft. It is not a restart of the node's run (`ci_reruns.node_run_start`
does not move), so the per-head limit counts its events in the run."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kraft import events, inplace, store
from kraft.ci_reruns import node_run_start
from kraft.config import git_read
from kraft.templates.models import AgentInput, AgentTask
from kraft.vocab import ADVANCING, ChainEvent
from kraft.vocab.sql import marks


@dataclass(frozen=True)
class ReplyRound:
    seq: int
    head: str
    round: int
    threads: tuple[str, ...]  # the threads it was started for
    deadline: datetime | None = None  # its start plus the reply timeout: bounds the CI look


def when(value) -> datetime | None:
    """An ISO time as the forges and `read_at` write it (a trailing Z allowed), or None."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def has_new_bot_note(collected: list, read_at: datetime | None, threads: tuple[str, ...]) -> bool:
    """Whether a configured bot's note, created after the latest triage read the bots, stands in
    a thread the round was started for. `read_at` None: no triage has read them."""
    for r in collected:
        for t in (r.get("threads") or ()) if isinstance(r, dict) else ():
            if t.get("id") not in threads:
                continue
            for n in t.get("notes") or ():
                at = when(n.get("created_at"))
                if n.get("by") == "bot" and at is not None and (read_at is None or at > read_at):
                    return True
    return False


def item_head(row, worktree) -> str | None:
    """The item's head, as every reader of it reads it: the checkout's HEAD, or an in-place
    item's branch tip (`inplace.head_ref`)."""
    return git_read(Path(worktree), "rev-parse", inplace.head_ref(row)) if worktree else None


async def current(db, work_item_id: str, node_id: str, head_of) -> ReplyRound | None:
    """The reply round `node_id` is in: its newest `reply_round_started` of this run, no
    `findings_measured` of the node after it, and the item's head (`head_of()`) still the head
    the event names. `head_of` is called only once an open event is found, so a repository
    that never starts a round pays a DB read and no `git rev-parse`; it runs on a thread, since
    that is a git call and the caller is on the event loop."""

    def read(c) -> ReplyRound | None:
        found = None
        for e in events.read_after(c, node_run_start(c, work_item_id, node_id), work_item_id):
            if e["payload"].get("node_id") != node_id:
                continue
            if e["type"] == ChainEvent.REPLY_ROUND_STARTED:
                p = e["payload"]
                found = ReplyRound(
                    e["seq"], p["head"], p["round"], tuple(p["threads"]), when(p.get("deadline"))
                )
            elif e["type"] == ChainEvent.FINDINGS_MEASURED:
                found = None
        return found

    found = db.read(read)
    if found is None:
        return None
    head = await asyncio.to_thread(head_of)
    return found if found.head == head else None


def started_count(c, work_item_id: str, node_id: str, head: str) -> int:
    """`reply_round_started` events for `node_id` and `head` in the node's current run."""
    return sum(
        1
        for e in events.read_after(c, node_run_start(c, work_item_id, node_id), work_item_id)
        if e["type"] == ChainEvent.REPLY_ROUND_STARTED
        and e["payload"].get("node_id") == node_id
        and e["payload"].get("head") == head
    )


async def begin(db, work_item_id: str, round_: ReplyRound, node_id: str) -> None:
    """Start the round: its event and the cursor at the node's first step, in one
    transaction, so a crash lands on the round's first step, never past it."""

    def write(c) -> None:
        events.append(
            c,
            work_item_id,
            ChainEvent.REPLY_ROUND_STARTED,
            {
                "node_id": node_id,
                "head": round_.head,
                "round": round_.round,
                "threads": list(round_.threads),
                "deadline": round_.deadline.isoformat() if round_.deadline else None,
            },
        )
        store.set_current_step(c, work_item_id, 0)

    await db.write(write)


def own(node) -> list:
    """The node's own tasks: never its recovery's or its fix loop's."""
    return [t for step in node.steps for t in step.tasks]


def triage_task(node):
    """The node's own agent task that reads the bots' feedback, or None."""
    return next(
        (
            t
            for t in own(node)
            if isinstance(t.task, AgentTask) and AgentInput.BOT_FEEDBACK in t.task.inputs
        ),
        None,
    )


def run_start(c, work_item_id: str, node_id: str) -> tuple[int, str]:
    """Where the node's current run began: its event's seq, and its time, since a
    session carries only a time (the comparison `bot_feedback_source` makes too)."""
    seq = node_run_start(c, work_item_id, node_id)
    row = (
        c.execute("SELECT created_at FROM events WHERE seq = ?", (seq,)).fetchone() if seq else None
    )
    return seq, row["created_at"] if row else ""


def latest_triage(c, work_item_id: str, node, since: str):
    """The node's newest triage session since `since` that started an agent and
    finished with a result: a head and an advancing status, `last_review_session`'s
    filter. A skipped session has no head, so it is never the latest triage."""
    hook = triage_task(node)
    if hook is None:
        return None
    return c.execute(
        "SELECT id, head_sha, result_path, round FROM worker_sessions "
        "WHERE work_item_id = ? AND node_id = ? AND hook_point = ? AND head_sha IS NOT NULL "
        f"AND status IN ({marks(ADVANCING)}) AND created_at >= ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (work_item_id, node.id, hook.path, *ADVANCING, since),
    ).fetchone()
