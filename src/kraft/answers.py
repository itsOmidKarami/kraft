"""What Kraft says back, worded once for both front doors: the CLI prints it,
the console draws it (spec §4.3). Plain text, no colour, no terminal."""

from __future__ import annotations

import math
from collections.abc import Callable

from kraft import render
from kraft.vocab import WorkItemStatus
from kraft.vocab.total import total


def running_agents_warning(doing: str, items: list[dict], *, now: bool = False) -> list[str]:
    """The lines naming the active `items` whose agents a server stop ends,
    `doing` being what ends them ("restarting"); none when none ends.

    A clean stop leaves the local steps of worktree items running: only an item running in
    place (`in_place`) ends with it. `now` is a stop that ends every active item's agent
    (`kraft admin stop --now`)."""
    ending = items if now else [item for item in items if item.get("in_place")]
    if not ending:
        return []
    which = "any active item" if now else "any item running in place"
    return [
        f"{doing} the server ends the agent of {which} ({len(ending)}):",
        *(
            f"  {item['id']}  {item.get('current_node_id') or '-'}  {item['title']}"
            for item in ending
        ),
        "  each one stops when Kraft starts again; retry it then. To avoid that, "
        "pause them first (kraft item pause ID) and resume them after.",
    ]


def kept_steps_note(count: int) -> str:
    """What `kraft admin stop` says of the local steps it leaves running."""
    return (
        f"{count} local step{'s' if count != 1 else ''} keep{'' if count != 1 else 's'} running "
        "without Kraft. Until Kraft is back nothing answers their permission prompts or "
        "enforces their caps; kraft admin stop --now stops them."
    )


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
        WorkItemStatus.RATE_LIMITED: "waiting for the agent's or the forge's rate limit to reset",
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


#: What a queued start's answer carries to say what holds it.
QUEUED_KEYS = ("slots", "why", "held_by", "held_by_title")


def hold_reason(held: dict, storage: dict | None) -> str | None:
    """Why a queued start waits when it is not a busy slot, in the web UI's
    words: another in-place item is using its checkout (`held`'s `why`,
    `held_by_title`), or the worktree storage limit holds it (`storage`, the
    figures `slots.storage` and `queued.storage` carry). None for a slot."""
    if held.get("why") == "checkout":
        title = held.get("held_by_title")
        who = f"“{render.plain_text(str(title))}”" if title is not None else "another item"
        return f"waiting for the checkout: {who} is using it"
    if storage:
        used = render.human_size(storage["used_bytes"])
        limit = render.human_size(storage["limit_bytes"])
        return f"held by the worktree storage limit: {used} counted against the {limit} limit"
    return None


def queued(result: dict) -> str | None:
    """A start the board had no slot for, or None for any other answer."""
    if result.get("status") != WorkItemStatus.QUEUED:
        return None
    slots = result.get("slots") or {}
    storage = slots.get("storage")
    why = hold_reason(result, storage)
    pause = f"kraft item pause {result['id']} takes it out of the queue"
    if result.get("why") == "checkout":
        return f"queued {result['id']}: {why}. It starts when the checkout frees; {pause}"
    if why:
        return (
            f"queued {result['id']}: {why}. It starts when space frees; "
            f"kraft item archive --reclaimable archives finished items to make room, {pause}"
        )
    return (
        f"queued {result['id']}: {slots.get('busy')} of {slots.get('limit')} slots are busy. "
        f"It starts when one frees; {pause}"
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


def filed(result: dict) -> str | None:
    """What a create that asked to start says when every slot was busy:
    queued for one, or filed paused until one frees. None for any other
    create. `kraft item create` prints it under the item; the console's New
    item says it in its toast."""
    slots = result.get("slots")
    if not slots:
        return None
    return held(result) or (
        f"filed paused: {slots['busy']} of {slots['limit']} slots are busy. "
        f"Start it when one frees: kraft item resume {result['id']}"
    )


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


def unblocked(result: dict) -> str:
    """unblock's answer: what the item no longer comes after, and what it
    still waits on."""
    dropped = result.get("dropped") or []
    if not dropped:
        return f"{result['id']} comes after nothing unfinished: nothing was dropped"
    line = f"{result['id']} no longer comes after {', '.join(dropped)}"
    if left := result.get("waiting_on"):
        line += f"; it still waits on {', '.join(d['id'] for d in left)}"
    return line


def dollars(raw: str) -> float | None:
    """A cap as typed: dollars, or `none` for an explicit no-cap. ValueError
    names what it could not read."""
    if raw.lower() == "none":
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):  # inf and nan cannot go in the JSON body
        raise ValueError(f"dollars or `none`, not {raw!r}")
    return value


def raised_budget(usd: float | None) -> Callable[[dict], str]:
    """raise-budget's sentence for a cap of `usd`: raised and retried, or
    raised and where the retry starts, or that it did not start."""
    cap = "no cap" if usd is None else f"${usd:g}"
    return item_action(
        f"raised the cap on {{id}} to {cap} and retried it",
        small=lambda r: (
            f"raised the cap on {r['id']} to {cap}; " + (held(r) or f"retried at {rerun_at(r)}")
        ),
    )


#: escalate's sentence; its small answer is `escalated`.
ESCALATED = "asked an agent about {id}"


def escalated(result: dict) -> str:
    return f"asked an agent about {result['id']}'s stop; kraft view show {result['id']} follows it"


#: cancel's sentence: what stays, and the verb that deletes it.
CANCELLED = (
    "cancelled {id}; its worktree and branch stay "
    "(kraft item abandon {id} --reason '...' --yes deletes them)"
)
#: complete's sentence.
COMPLETED = "marked {id} complete"


def skipped(what: str) -> str:
    """skip's sentence, `{id}` left for the answer: what it passed over."""
    return f"skipped {what} on {{id}}"


def reload_summary(result: dict) -> str:
    """What a reload took and what it refused: `kraft admin reload` prints it,
    the console's `l` on the Server says it."""
    refused = result.get("refused_policy")
    refused_intake = result.get("refused_intake")
    refused_workers = result.get("refused_workers")
    refused_forges = result.get("refused_forges")
    files = [
        n
        for n, bad in (
            ("policy.yaml", refused),
            ("intake.yaml", refused_intake),
            ("workers.yaml", refused_workers),
            ("forges.yaml", refused_forges),
        )
        if not bad
    ]
    also = f" and {' and '.join(files)}" if files else ""
    lines = [f"reloaded {len(result.get('valid', []))} template(s){also}"]
    for name, reason in (result.get("invalid_templates") or {}).items():
        lines.append(f"  invalid: {name}: {reason}")
    if refused:
        lines.append(f"  refused: policy.yaml: {refused} (the running policy is kept)")
    if refused_intake:
        # `ConfigError` names the file itself.
        lines.append(
            f"  refused: {refused_intake} (the running auto-intake and schedules are kept)"
        )
    if refused_workers:
        lines.append(f"  refused: {refused_workers} (the running machines are kept)")
    if refused_forges:
        lines.append(f"  refused: {refused_forges} (the running accounts are kept)")
    return "\n".join(lines)


def reload_refused(result: dict) -> bool:
    """Whether a reload left a template invalid or refused a file: `kraft
    admin reload` exits 1, and the console says it in the error colour."""
    return bool(
        result.get("invalid_templates")
        or result.get("refused_policy")
        or result.get("refused_intake")
        or result.get("refused_workers")
        or result.get("refused_forges")
    )
