"""What Kraft says back, worded once for both front doors: the CLI prints it,
the console draws it (spec §4.3). Plain text, no colour, no terminal."""

from __future__ import annotations

from collections.abc import Callable

from kraft.vocab import WorkItemStatus
from kraft.vocab.total import total


def running_agents_warning(doing: str, items: list[dict]) -> list[str]:
    """The lines naming the active `items` whose agents a server stop ends,
    `doing` being what ends them ("restarting"); none when nothing is active."""
    if not items:
        return []
    return [
        f"{doing} the server ends the agent of any active item ({len(items)}):",
        *(
            f"  {item['id']}  {item.get('current_node_id') or '-'}  {item['title']}"
            for item in items
        ),
        "  each one stops when Kraft starts again; retry it then. To avoid that, "
        "pause them first (kraft item pause ID) and resume them after.",
    ]


def is_item_row(result: object) -> bool:
    """A whole `work_items` row, which the gate and lifecycle routes hand back
    (`deps.work_item_answer`): some 30 KB with its frozen chain, which a person
    who typed `kraft item approve` never asked to read (R7a-05)."""
    return isinstance(result, dict) and "chain_definition" in result and "status" in result


#: What a row's `status` reads as, after "the item is now".
_NOW = total(
    WorkItemStatus,
    {
        WorkItemStatus.ACTIVE: "running",
        WorkItemStatus.PAUSED: "paused",
        WorkItemStatus.COMPLETED: "complete",
        # Cancel and abandon both store `abandoned`: only the verb that ended it can tell.
        WorkItemStatus.ABANDONED: "ended",
        WorkItemStatus.WAITING: "waiting on something outside Kraft",
        WorkItemStatus.RATE_LIMITED: "waiting for the agent's rate limit to reset",
        WorkItemStatus.QUEUED: "queued for a free slot",
        WorkItemStatus.BLOCKED: "blocked until the items it comes after complete",
        WorkItemStatus.NEEDS_HUMAN: None,  # item_now words it, with the id
    },
    name="_NOW",
)


def item_now(row: dict) -> str:
    """Where the item stands, as the end of a sentence."""
    if row["status"] == WorkItemStatus.NEEDS_HUMAN:
        return f"stopped for a person: kraft view show {row['id']} says why"
    return _NOW.get(row["status"]) or row["status"]


def item_action(
    done: str | Callable[[dict], str],
    *,
    status: bool = True,
    small: Callable[[dict], str] | None = None,
) -> Callable[[dict], str]:
    """The sentence for a verb whose answer may be the item's whole row: what
    was done (`done`, a format string taking `{id}`, or a function of the
    answer) and, unless `status` is off, what the item is now. Any other
    answer is small: `small` says it when given, else `done` alone does."""

    def say(result: dict) -> str:
        row = is_item_row(result)
        if small is not None and not row:
            return small(result)
        head = done(result) if callable(done) else done.format(id=result["id"])
        return f"{head}; the item is now {item_now(result)}" if status and row else head

    return say


def queued(result: dict) -> str | None:
    """A start the board had no slot for, or None for any other answer."""
    if result.get("status") != WorkItemStatus.QUEUED:
        return None
    slots = result.get("slots") or {}
    return (
        f"queued {result['id']}: {slots.get('busy')} of {slots.get('limit')} slots are busy. "
        f"It starts when one frees; kraft item pause {result['id']} takes it out of the queue"
    )


def blocked(result: dict) -> str | None:
    """A start held behind unfinished items, or None for any other answer."""
    if result.get("status") != WorkItemStatus.BLOCKED:
        return None
    on = ", ".join(f"{d['id']} ({d['status']})" for d in result.get("waiting_on") or [])
    return (
        f"blocked {result['id']}: it comes after {on}. It starts when they complete; "
        f"kraft item unblock {result['id']} drops them"
    )


def held(result: dict) -> str | None:
    """A start that did not start: queued for a slot, or blocked."""
    return queued(result) or blocked(result)


def resumed(result: dict) -> str:
    """resume's usual answer: the node it went on from, and the agent tasks
    a steer reached. No node is an item that had never started."""
    if waiting := held(result):
        return waiting
    node = result.get("node_id")
    line = f"resumed {result['id']} at {node}" if node else f"started {result['id']}"
    if steered := result.get("steered"):
        line += f"; steered {', '.join(steered)}"
    return line


def rerun_at(result: dict) -> str:
    """Where a retry's run starts, from its usual answer, and its attempt."""
    where = result.get("path") or result.get("node_id") or "its first node"
    attempt = result.get("attempt")
    return f"{where}, attempt {attempt}" if attempt else str(where)


def retried(result: dict) -> str:
    """retry's usual answer: where its run starts, unless it did not start."""
    return held(result) or f"retried {result['id']} at {rerun_at(result)}"


def paused(result: dict) -> str:
    """pause's usual answer: the sessions it stopped, and how to carry on."""
    stopped = len(result.get("paused_sessions") or [])
    how = f", stopping {stopped} running session{'s' if stopped != 1 else ''}" if stopped else ""
    return f"paused {result['id']}{how}; kraft item resume {result['id']} carries on"
