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


def chain_marks(row: dict) -> list[dict]:
    """`chainMarks`: each node of the item's chain, done, the one it stands
    on (`hot` when it needs you), or still to run; gates marked."""
    nodes = (row.get("chain_definition") or {}).get("nodes") or []
    ended = row.get("display_status") in ("done", "cancelled", "archived")
    at = next((k for k, n in enumerate(nodes) if n.get("id") == row.get("current_node_id")), -1)
    hot = group_of(row) == "needs" or row.get("display_status") == "escalated"

    def state(k: int) -> str:
        if ended or 0 <= at and k < at:
            return "done"
        if k == at:
            return "hot" if hot else "current"
        return "todo"

    return [
        {"node": n.get("id"), "gate": n.get("kind") == "gate", "state": state(k)}
        for k, n in enumerate(nodes)
    ]


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


def elapsed(seconds: float) -> str:
    """`format.ts`'s `elapsed`, from seconds: "59s", "12m", "3h 12m", "1d 4h"."""
    # floor(x + 0.5): JavaScript's Math.round, which Python's round() is not at .5.
    s = max(0, math.floor(seconds + 0.5)) if math.isfinite(seconds) else 0
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 24:
        return f"{h}h {m % 60}m" if m % 60 else f"{h}h"
    d = h // 24
    return f"{d}d {h % 24}h" if h % 24 else f"{d}d"


#: `status.ts`: a budget stop the item could raise, but not now.
RAISE_REFUSED = "Kraft takes no raise for it right now."
#: `status.ts`: a budget stop the item cannot raise.
NOT_RAISABLE = (
    "The item can't raise this cap: the policy or the chain sets it. Retry once it is "
    "raised there, or after local midnight for the daily cap."
)
#: `cause.tsx`: a refused forge credential, fixed where the server runs.
FORGE_LOGIN_HINT = (
    "Sign the forge CLI in on the server's machine: `gh auth login`, or `glab auth login` "
    "for GitLab. If git itself can't authenticate, check its credentials there too "
    "(`gh auth setup-git`, your SSH key or credential helper). Then Retry."
)
#: `bannerWords.ts`: a failure the repo's settings can answer.
REPO_FIX = "Check the repo settings."


def _admits(item: dict, verb: str, rule: bool) -> bool:
    """`admits`: the server's `actions` when it sends them (an empty list
    too), else `rule`, for a server older than them."""
    actions = item.get("actions")
    return verb in actions if actions is not None else rule


def _budget_raise(stop: dict) -> str | None:
    """`budgetRaise`, from the stop: `limit`, `item`, or None."""
    if stop.get("kind") != "budget":
        return None
    if stop.get("limit"):
        return "limit"
    return "item" if stop.get("scope") == "work_item" else None


def _offers_raise(item: dict) -> bool:
    """`offersRaise`: the item can raise the cap and the server admits the door."""
    how = _budget_raise(item.get("stop") or {})
    if how == "item":
        return _admits(item, "raise_budget", True)
    return _admits(item, "retry", True) if how == "limit" else False


def _failed_fix(stop: dict) -> str:
    """`failedFix`: `forge_login`, `retry` or `repo`."""
    cause = (stop.get("facts") or {}).get("cause")
    if cause == "forge_auth":
        return "forge_login"
    if stop.get("kind") != "infra" or cause == "stranded":
        return "retry"
    return "repo"


def sentence(text: str) -> str:
    """`sentence`: a capital first letter and one full stop."""
    return text[:1].upper() + re.sub(r"\.*\Z", "", text[1:], count=1) + "."


def banner_of(item: dict) -> dict | None:
    """`bannerOf`: a failed or needs-you item's banner as `{kind, node, text,
    way}` (`""` for none), or None for any other item."""
    stop = item.get("stop")
    if not stop or item.get("display_status") not in ("needs_you", "failed"):
        return None
    node = _or(stop.get("node"), "")
    if item["display_status"] == "failed":
        fix = _failed_fix(stop)
        way = {"forge_login": FORGE_LOGIN_HINT, "repo": REPO_FIX}.get(fix, "")
        text = sentence(reason) if (reason := _or(stop.get("reason"), "")) else ""
        return {"kind": "failed", "node": node, "text": text, "way": way}
    kind = stop.get("kind")
    if kind == "gate":
        return {"kind": "gate", "node": _or(item.get("pending_gate"), node), "text": "", "way": ""}
    if kind == "question":
        asked = _or(
            item.get("needs_context_question"),
            re.sub(r"^needs_context:\s*", "", _or(stop.get("reason"), "")),
        )
        return {"kind": "question", "node": node, "text": asked, "way": ""}
    if kind in ("cap", "budget"):
        default = "The budget ran out" if kind == "budget" else "A limit was reached"
        text = sentence(_or(stop.get("reason"), default))
        way = ""
        if kind == "budget" and not _offers_raise(item):
            way = RAISE_REFUSED if _budget_raise(stop) else NOT_RAISABLE
        return {"kind": "limit", "node": node, "text": text, "way": way}
    text = sentence(reason) if (reason := _or(stop.get("reason"), "")) else ""
    return {"kind": "stop", "node": node, "text": text, "way": ""}


#: `events.ts`'s `QUIET`: bookkeeping that would drown the run's story.
QUIET = frozenset(
    {
        "worker_session_created",
        "worker_session_exited",
        "chain_loaded",
        "plan_progress",
        "worktree_prepared",
        "steer_context_set",
        "permission_decision",
        "external_wait_observed",
        "findings_measured",
    }
)


def _s(value: object) -> str:
    """`events.ts`'s `s`: JavaScript's `String(v)`, null and undefined as ""."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _cut(text: str, n: int = 70) -> str:
    # shortcut: counts code points where JavaScript counts UTF-16 units; they
    # differ only past the Basic Multilingual Plane, at the cut.
    return f"{text[: n - 1]}…" if len(text) > n else text


def _round(value: object) -> str:
    """`Number(cycle ?? 0) + 1`: a number as JavaScript reads it ("" is 0,
    "Infinity" is infinite, "inf" is no number), else "NaN"."""
    try:
        text = value.strip() if isinstance(value, str) else None
        if text is None:
            n = float(0 if value is None else value)
        elif text.lstrip("+-") == "Infinity":
            n = float(text.replace("Infinity", "inf"))
        elif text and (text.lstrip("+-").lower() in ("inf", "infinity", "nan") or "_" in text):
            return "NaN"  # Python reads these as numbers, JavaScript does not
        else:
            n = float(text or 0)
    except (TypeError, ValueError):
        return "NaN"
    if n != n:
        return "NaN"
    return ("-" if n < 0 else "") + "Infinity" if math.isinf(n) else _s(n + 1)


def task_name(path: str) -> str:
    """`taskName`: a task path's last part, `verification.review.code_review` → `code_review`."""
    return path.split(".")[-1]


def event_line(event: dict) -> str | None:
    """`eventLine`: one event as one line, or None for bookkeeping. A type
    not listed reads as its own name."""
    kind = event.get("type") or ""
    if kind in QUIET:
        return None
    p = event.get("payload") or {}
    node = _s(_or(event.get("node_id"), p.get("node_id")))
    on = f" on {node}" if node else ""
    gate = _s(p.get("gate")) or node
    match kind:
        case "work_item_created":
            return "filed"
        case "node_started":
            return f"{node} started"
        case "node_completed":
            return f"{node} finished"
        case "node_skipped":
            return f"{node} skipped"
        case "worker_session_started":
            hook = p.get("hook_point")
            task = task_name(_s(hook)) if hook else "a session"
            return f"{task} started on {node}"
        case "gate_requested":
            return f"{gate} is waiting for you"
        case "gate_approved":
            by = "passed on its own" if p.get("by") in ("agent", "kraft") else "approved by you"
            return f"{gate} · {by}"
        case "gate_rejected":
            note = f": {_cut(_s(p.get('note')), 50)}" if p.get("note") else ""
            return f"{gate} · rejected{note}"
        case "fix_cycle_started":
            return f"{node} · fix loop round {_round(p.get('cycle'))}"
        case "fix_cycle_finished":
            # `=== true` and `=== false`: None means git could not say.
            committed = p.get("committed")
            did = ""
            if committed is True:
                did = " · the fix committed a change"
            elif committed is False:
                did = " · the fix changed nothing"
            return f"{node} · fix loop round {_round(p.get('cycle'))}{did}"
        case "work_item_needs_human":
            reason = re.sub(r"^needs_context:\s*", "", _s(p.get("reason")))
            return f"stopped{' at ' + node if node else ''}: {_cut(reason, 240)}"
        case "escalation_message":
            return f"escalation{on}: {_cut(_s(p.get('message')), 240)}"
        case "pause_requested":
            return "paused"
        case "work_item_resumed":
            return "resumed"
        case "work_item_retried":
            return f"retried{' ' + node if node else ''}"
        case "work_item_rate_limited":
            return f"{node} hit a rate limit"
        case "work_item_waiting":
            return f"{node} is waiting"
        case "work_item_cancelled":
            return "cancelled"
        case "work_item_completed":
            return "finished"
        case "work_item_archived":
            if not p.get("kept_branch"):
                return "archived"
            commits = p.get("unpushed_commits")
            plural = "" if commits == 1 else "s"
            kept = f"kept {_s(p['kept_branch'])}: {_s(commits)} unpushed commit{plural}"
            return f"archived · {kept}"
        case "work_item_restored":
            return "restored"
        case "mr_opened":
            return f"MR !{_s(p.get('number'))} opened"
        case "mr_closed":
            by = p.get("by")
            closer = f" by {_s(by)}" if by and by != "cancel" else ""
            return f"MR !{_s(p.get('ref'))} closed{closer}"
        case "mr_reopened":
            return f"MR !{_s(p.get('ref'))} reopened"
        case "budget_raised" | "budget_changed":
            return f"budget set to ${_s(p.get('budget_usd'))}"
        case "work_item_title_edited":
            return "title edited"
        case "work_item_description_edited":
            return "brief edited"
        case _:
            return kind.replace("_", " ")


def event_age(iso: str | None, now: float) -> str:
    """`events.ts`'s `age`: "now", "6m", "1h 10m", "2h"."""
    then = _seconds(iso)
    if then is None or now - then < 60:
        return "now"
    minutes = math.floor((now - then) / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m" if rest else f"{hours}h"
