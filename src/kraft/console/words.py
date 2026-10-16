"""The web's wording, ported (spec §4.5): where a board row sits and what it
says, as `frontend/src/ng/board/model.ts`, `rowText.ts` and `format.ts` say
it. `tests/console/wording_cases.json` holds both to the same cases, so a
change on one side that the other lacks fails CI. Moving this wording to the
server is Kraft-w12dy."""

from __future__ import annotations

import math
import re
from datetime import datetime

#: The board's groups, in order, with their headings (`STATUS_GROUPS`).
GROUPS = (
    ("needs", "Needs you"),
    ("running", "Running"),
    ("not_started", "Not started"),
    ("done", "Done"),
)

#: A display status's group; None is decided by the current node (`GROUP`).
_GROUP = {
    "needs_you": "needs",
    "failed": "needs",
    "paused": None,
    "blocked": None,
    "done": "done",
    "cancelled": "done",
    "archived": "done",
    "running": "running",
    "waiting": "running",
    "escalated": "running",
    "queued": "running",
}


def _or(value, fallback):
    """TypeScript's `??`: `fallback` only for a missing value, not for `""`."""
    return fallback if value is None else value


def group_of(row: dict) -> str:
    """`groupOf`: a paused item with no current node was filed and never
    started; one paused mid-chain waits on a person. A blocked item with no
    current node has not started either; one blocked mid-chain stays with the
    running."""
    # None, or a display status from a newer server, reads as running.
    group = _GROUP.get(row.get("display_status"), "running")
    if group is not None:
        return group
    if not row.get("current_node_id"):
        return "not_started"
    return "running" if row["display_status"] == "blocked" else "needs"


def sort_rows(rows: list[dict]) -> list[dict]:
    """`sorter("attention")`: what needs you first, then the newest change."""
    newest = sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)
    return sorted(newest, key=lambda row: group_of(row) != "needs")


def chain_of(row: dict) -> str:
    """`chainOf`: the chain an item runs, `default` when it names none."""
    named = row.get("chain_template") or (row.get("chain_definition") or {}).get("template_id")
    return _or(named, "default")


def node_words(node: str) -> str:
    """`nodeWords`: `merge_request` or `merge-request` reads "merge request"."""
    return re.sub(r"[_-]+", " ", node)


def gate_words(gate: str) -> str:
    """`gateWords`: `spec_approval` reads "spec", so the row says "approve spec"."""
    return node_words(re.sub(r"[_-]approval$", "", gate))


def _node_of(row: dict) -> str:
    return _or(_or((row.get("stop") or {}).get("node"), row.get("current_node_id")), "")


def _all_done(progress: dict) -> bool:
    tasks = progress.get("tasks") or []
    return bool(tasks) and all(task.get("state") == "done" for task in tasks)


def _first_sentence(text: str) -> str:
    """Up to the first full stop that ends a sentence, not one inside "$0.04"."""
    return re.split(r"\.(?:\s|$)", text)[0]


def _fallback_name(to: object) -> str:
    """What a fallback switched to: its model when it names one, else its harness."""
    if isinstance(to, str):
        return to
    if not isinstance(to, dict):
        return ""
    model, harness = to.get("model"), to.get("harness")
    if isinstance(model, str) and model:
        return model
    return harness if isinstance(harness, str) else ""


def reason_tail(row: dict, now: float) -> str:
    """`reasonTail`: the row's one short line, per status and stop kind.
    `now` is seconds since the epoch."""
    node = node_words(_node_of(row))
    stop = row.get("stop") or {}
    tail = _tail(row, stop, node, now)
    on = _fallback_name((row.get("fallback") or {}).get("to"))
    return f"{tail} · on {on}" if on else tail


def _tail(row: dict, stop: dict, node: str, now: float) -> str:
    match row.get("display_status"):
        case "needs_you":
            reason = stop.get("reason")
            match stop.get("kind"):
                case "gate":
                    return f"approve {gate_words(_or(row.get('pending_gate'), _node_of(row)))}"
                case "question":
                    asked = re.sub(r"^needs_context:\s*", "", _or(reason, ""))
                    return f"agent asks: {asked}".strip()
                # A cap or budget stop's reason goes on to what was not
                # interrupted: the row keeps its first sentence.
                case "cap" | "budget":
                    return _first_sentence(reason) if reason else f"waiting for you at {node}"
                case _:
                    return _or(reason, f"waiting for you at {node}")
        case "failed":
            return f"failed at {node}"
        case "paused":
            return f"paused at {node}" if row.get("current_node_id") else "created paused"
        case "running":
            step, progress = row.get("step"), row.get("progress")
            if step:
                if step.get("name") and step.get("task"):
                    return f"{step['index']} of {step['count']} · {step['name']} › {step['task']}"
                return f"{node} · step {step['index']} of {step['count']}"
            # A finished plan has no task left to count.
            if progress and not _all_done(progress):
                return f"{node} · task {progress['current']} of {progress['total']}"
            return node
        case "waiting":
            at = _or(stop.get("resume_at"), row.get("retry_at"))
            if not at:
                return f"waiting at {node}"
            # A CI wait checks again; only a rate limit retries.
            again = "next check" if stop.get("kind") == "wait" else "retry"
            return f"{node} · {again} {until(at, now)}"
        case "queued":
            return "waiting to start"
        case "blocked":
            return "waiting on another item"
        case "escalated":
            return "escalation running"
        case "done":
            mr = row.get("mr_ref")
            return f"merged !{mr['number']}" if mr else "completed"
        case "cancelled":
            return "cancelled"
        case _:
            return ""


def _seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def ago(iso: str | None, now: float) -> str:
    """`ago`: "just now", "12m ago", "3h ago", "4d ago"; "" for no time."""
    then = _seconds(iso)
    if then is None:
        return ""
    minutes = math.floor((now - then) / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago" if hours < 24 else f"{hours // 24}d ago"


def until(iso: str | None, now: float) -> str:
    """`until`: the same scale pointed forwards; "expired" once it has passed."""
    then = _seconds(iso)
    if then is None:
        return ""
    if then - now <= 0:
        return "expired"
    minutes = math.floor((then - now) / 60)
    if minutes < 60:
        return f"in {max(minutes, 1)}m"
    hours = minutes // 60
    return f"in {hours}h" if hours < 24 else f"in {hours // 24}d"
