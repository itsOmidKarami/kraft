"""The conformance probe (spec §9): one scripted ACP session, and what it showed against what the
entry declares. `analyze` is pure over the session log; `run` (below) makes the session."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from kraft.acp import log, tools

#: What the probe asks the agent to do (spec §9): read a file, write a file, run a command.
STEPS = (
    ("read a file", ("Read",)),
    ("write a file", ("Write", "Edit")),
    ("shell command", ("Bash",)),
)


@dataclass(frozen=True)
class Call:
    id: str
    key: str
    kraft: tuple[str, ...]
    asked: bool
    status: str | None


@dataclass(frozen=True)
class Report:
    agent: str | None
    stop_reason: str | None
    calls: tuple[Call, ...]
    tokens_reported: bool
    cost_reported: bool
    problems: tuple[str, ...]
    mismatches: tuple[str, ...]
    notes: tuple[str, ...]
    log: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems and not self.mismatches

    @property
    def unasked(self) -> tuple[str, ...]:
        """The Kraft tools the agent ran without asking: what `settles_itself` must list."""
        return tuple(sorted({n for c in self.calls if not c.asked for n in (c.kraft or (c.key,))}))


def analyze(lines: Iterable[str], h, *, exit_code: int = 0) -> Report:
    from kraft import usage

    lines = list(lines)
    entry = h.acp
    tool_names = {k: list(v) for k, v in h.tool_names.items()}
    seen: dict[str, dict] = {}
    asked: set[str] = set()
    problems: list[str] = []
    agent = stop = None
    for rec in log.records(lines):
        if rec.get("replay"):
            continue
        if rec.get("dir") == "client":
            if rec.get("event") in ("error", "config_error"):
                problems.append(str(rec.get("message") or rec["event"]))
            continue
        msg = rec.get("msg") if rec.get("dir") == "from_agent" else None
        if not isinstance(msg, dict):
            continue
        result = msg.get("result")
        if isinstance(result, dict):
            info = result.get("agentInfo")
            if isinstance(info, dict):
                agent = " ".join(str(info[k]) for k in ("name", "version") if info.get(k)) or None
            if "stopReason" in result:
                stop = result["stopReason"]
            continue
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if msg.get("method") == "session/update":
            update = params.get("update")
            if isinstance(update, dict) and update.get("sessionUpdate") in (
                "tool_call",
                "tool_call_update",
            ):
                _merge(seen, update)
        elif msg.get("method") == "session/request_permission":
            call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
            _merge(seen, call)
            if isinstance(call.get("toolCallId"), str):
                asked.add(call["toolCallId"])

    calls = []
    for cid, info in seen.items():
        tool = tools.resolve(info, tool_names)
        calls.append(Call(cid, tool.key, tool.names, cid in asked, info.get("status")))

    used = usage.READERS["acp"].stream(lines, {})
    tokens = used is not None and (
        used.tokens_in + used.tokens_out + used.tokens_cache_read + used.tokens_cache_write > 0
    )
    cost = used is not None and used.cost_usd is not None

    mismatches: list[str] = []
    notes: list[str] = []
    if exit_code:
        problems.append(f"the client exited {exit_code}")
    if stop is not None and stop != "end_turn":
        mismatches.append(f"the turn ended with stopReason {stop!r}, not 'end_turn'")
    for label, names in STEPS:
        done = [c for c in calls if set(c.kraft) & set(names)]
        if not done and not problems:
            mismatches.append(
                f"no call for the {label} ({'/'.join(names)}) was seen: the agent did not do it, "
                "or its `tool_names` cannot map the call"
            )
        for c in done:
            if c.status == "failed":
                mismatches.append(f"{c.kraft[0]} ended failed ({label})")
    for c in calls:
        what = c.kraft[0] if c.kraft else (c.key or "an unnamed tool")
        if not c.kraft:
            mismatches.append(
                f"call {c.key or c.id!r} maps to no Kraft tool: add it to the entry's `tool_names`"
            )
        settles = bool(c.kraft) and all(n in entry.settles_itself for n in c.kraft)
        if entry.asks_everything is not None:
            if not c.asked and not settles:
                mismatches.append(
                    f"{what} ran without asking although the entry declares `asks_everything`: "
                    f"add {what} to `settles_itself` if the agent settles it itself, else its "
                    "asking switch does not hold"
                )
            if c.asked and settles:
                notes.append(
                    f"the agent asked about {what} although `settles_itself` names it: a task "
                    "that denies it refuses to launch for nothing"
                )
    if entry.asks_everything is None:
        notes.append("the entry declares no `asks_everything`: asking is not judged")
    for label, got in (("tokens", tokens), ("cost", cost)):
        declared = label in entry.reports
        if declared and not got:
            mismatches.append(f"the entry declares {label} in `reports`, but the session sent none")
        if got and not declared:
            notes.append(f"the agent reports {label}, which the entry's `reports` does not declare")
    return Report(
        agent=agent,
        stop_reason=stop if isinstance(stop, str) else None,
        calls=tuple(calls),
        tokens_reported=tokens,
        cost_reported=cost,
        problems=tuple(dict.fromkeys(problems)),
        mismatches=tuple(dict.fromkeys(mismatches)),
        notes=tuple(dict.fromkeys(notes)),
    )


def _merge(seen: dict[str, dict], call: dict) -> None:
    cid = call.get("toolCallId")
    if not isinstance(cid, str):
        return
    known = seen.setdefault(cid, {"toolCallId": cid})
    for key in ("name", "kind", "title", "status"):
        if call.get(key):
            known[key] = call[key]
