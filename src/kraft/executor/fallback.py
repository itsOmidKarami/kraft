"""Launch fallback (Kraft-0a3h8): an agent task's launch that is rate-limited,
or whose harness is unavailable, moves to the next entry of its `fallback:`
list, and every skip or switch is logged as one `launch_fallback` event.

`dispatch.dispatch_node` owns the loop; this module owns what it walks (the
candidates), what it remembers (a harness+model limited until its reset, read
back from `rate_limit_hit` events across every item) and what it writes.

A task with no list never reaches any of this beyond `candidates` returning
its own launch alone (`fallback-is-opt-in`)."""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import time

from kraft import events
from kraft.templates.models import AgentTask

logger = logging.getLogger(__name__)

#: The note a fallback launch gets after an earlier candidate actually ran and
#: was rate-limited: its partial work may be in the worktree.
LIMITED_NOTE = (
    "\n\nAn earlier attempt at this task on `{who}` was stopped by an API rate limit. "
    "The worktree may contain its partial work: check `git status` and `git diff`, "
    "and continue from it rather than starting over."
)


def fallback_list(task: AgentTask, table=None) -> tuple[list, str | None]:
    """The entries that apply to `task`, and where they come from: its own
    `fallback:` when set (`[]` is none, overriding a profile's), else its
    agent profile's in `table` (a live `HarnessProfileTable`), else none."""
    if task.fallback is not None:
        return list(task.fallback), "the task's list"
    profile = table.agent_profiles.get(task.profile) if table and task.profile else None
    if profile is not None and profile.fallback:
        return list(profile.fallback), f"profile {task.profile!r}'s list"
    return [], None


def apply(task: AgentTask, entry) -> AgentTask:
    """`task` launched as `entry` says. The entry's route replaces the task's
    whole when either is a profile; two field routes merge per field. Its
    list is dropped, so lists never chain."""
    update: dict = {"fallback": None}
    if entry.harness is not None:
        update["harness"] = entry.harness
    if entry.profile is not None:
        update |= {"profile": entry.profile, "model": None, "effort": None}
    elif entry.model is not None or entry.effort is not None:
        if task.profile is not None:
            update |= {"profile": None, "model": entry.model, "effort": entry.effort}
        else:
            update |= {k: getattr(entry, k) for k in ("model", "effort") if getattr(entry, k)}
    return task.model_copy(update=update)


def candidates(task: AgentTask, table=None) -> list[AgentTask]:
    """The task's own launch, then one per entry of its list, in order."""
    return [task] + [apply(task, e) for e in fallback_list(task, table)[0]]


class Unavailable(Exception):
    """The candidate's executable is not on `PATH`."""


def require_on_path(inv, harnesses) -> None:
    """The check `kraft admin doctor` makes: the harness's `executable`, else
    its provider's command, found on `PATH`. Not under a sandbox, where the
    executable lives in the container."""
    exe = shlex.split(inv.command)[0] if inv.command else harnesses.valid[inv.harness].command[0]
    if shutil.which(exe) is None:
        raise Unavailable(f"`{exe}` is not on PATH")


def known_limited(conn, harness: str, model: str | None) -> str | None:
    """`resets_at_iso` of the newest `rate_limit_hit` for `harness`+`model`
    across every work item, when that reset is still ahead; else None
    (`known-limited-candidate-is-skipped-until-reset`). An event written
    before the hit carried a harness never matches."""
    row = conn.execute(
        "SELECT payload FROM events WHERE type = 'rate_limit_hit' "
        "AND json_extract(payload, '$.harness') = ? AND json_extract(payload, '$.model') IS ? "
        "ORDER BY seq DESC LIMIT 1",
        (harness, model),
    ).fetchone()
    if row is None:
        return None
    hit = json.loads(row["payload"])
    resets_at = hit.get("resets_at")
    if not isinstance(resets_at, int | float) or resets_at <= time.time():
        return None
    return hit.get("resets_at_iso")


def describe(task: AgentTask, inv=None) -> dict:
    """What a candidate runs on, for the event: resolved when `inv` is in hand,
    else as the task spells it (a candidate that never resolved)."""
    return {
        "harness": task.harness,
        "profile": task.profile,
        "model": inv.model if inv is not None else task.model,
        "effort": inv.effort if inv is not None else task.effort,
    }


def label(d: dict | None) -> str:
    if d is None:
        return "nothing"
    return f"{d['harness']} / {d['model']}" if d.get("model") else d["harness"]


class Attempts:
    """One task's walk down its candidates: the skip or switch waiting for
    the next candidate to say where it went, and the resets seen."""

    def __init__(self, db, work_item_id: str, node_id: str, task: str, overridden: bool):
        self.db, self.wid, self.node_id, self.task = db, work_item_id, node_id, task
        self.overridden = overridden
        self.pending: dict | None = None
        self.resets: list[str] = []
        self.unavailable: list[str] = []
        self.limited_run: dict | None = None

    async def skip(self, frm: dict, reason: str, **extra) -> None:
        """`frm` is not launched (or its launch was limited): the event is
        written once the next candidate is known (`launching`/`flush`), or
        here, when `frm` is that next candidate and is skipped too."""
        await self._write(frm, None)
        self.pending = {"from": frm, "reason": reason, **extra}
        if extra.get("resets_at_iso"):
            self.resets.append(extra["resets_at_iso"])
        if reason == "unavailable":
            self.unavailable.append(f"{label(frm)}: {extra.get('detail')}")
        if reason == "rate_limit_hit":
            self.limited_run = frm

    @property
    def note(self) -> str:
        """`LIMITED_NOTE`, only once a candidate actually ran and was limited."""
        return LIMITED_NOTE.format(who=label(self.limited_run)) if self.limited_run else ""

    async def launching(self, to: dict, session_id: str) -> None:
        await self._write(to, session_id)

    async def flush(self) -> None:
        await self._write(None, None)

    async def _write(self, to: dict | None, session_id: str | None) -> None:
        if self.pending is None:
            return
        payload = {
            "node_id": self.node_id,
            "task": self.task,
            **self.pending,
            "to": to,
            "session_id": session_id,
            "override_not_carried": self.overridden,
        }
        payload.setdefault("resets_at_iso", None)
        self.pending = None
        # `every-fallback-switch-is-logged`: the event and the server log.
        logger.info(
            "launch_fallback %s %s: %s -> %s (%s%s)",
            self.wid,
            self.task,
            label(payload["from"]),
            label(to),
            payload["reason"],
            f", until {payload['resets_at_iso']}" if payload["resets_at_iso"] else "",
        )
        await self.db.write(lambda c: events.append(c, self.wid, "launch_fallback", payload))
