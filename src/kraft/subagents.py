"""A session log's Claude Code sub-agents, worked out from its lines.

Nothing here is stored: `Fold` is fed the decoded lines of a log, in order, and
answers with the list the item page shows. `logs.Tail` feeds it the window it
reads; `adapters.subprocess.note_cut_off_subagents` feeds it a whole log at
session end. The line shapes are the ones claude 2.1.316 writes under
`--output-format stream-json` (see the sub-agents spec, Appendix A).
"""

from __future__ import annotations

from datetime import UTC, datetime

from kraft import logs

#: Claude Code's terminal words for a sub-agent, as the list says them. A word
#: not here is a failure.
TERMINAL = {"completed": "completed", "failed": "failed", "killed": "killed", "stopped": "killed"}

#: A sub-agent's answer in the list is cut here; `Fold.entry` has it whole.
SUMMARY_MAX = 500

#: The tool that starts a sub-agent: `Agent`, or `Task` in older Claude Code.
_SPAWNERS = ("Agent", "Task")


def _iso(epoch_ms: object) -> str | None:
    """`epoch_ms` as ISO 8601 UTC, to the millisecond."""
    if not isinstance(epoch_ms, int | float) or isinstance(epoch_ms, bool):
        return None
    secs, ms = divmod(int(epoch_ms), 1000)
    try:
        stamp = datetime.fromtimestamp(secs, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class Fold:
    """The sub-agents of one log, learned a line at a time."""

    def __init__(self) -> None:
        self._seen = 0  # lines fed so far
        self._agents: dict[str, tuple[str, str | None]] = {}  # logs.agent_fields' memory
        self._entries: dict[str, dict] = {}
        self._prompts: dict[str, str] = {}  # tool_use_id -> the Agent call's prompt
        self._started_prompts: dict[str, str] = {}  # agent id -> task_started's prompt
        self._answers: dict[str, str] = {}
        self._last_result = -1  # index of the last `result` line
        self._terminal: dict[str, tuple[int, str]] = {}  # id -> (line index, word)

    def feed(self, obj: dict | None, fields: dict | None = None) -> None:
        """One decoded log line (`None` if it did not decode), in order.
        `fields` is its `logs.agent_fields`, when the caller already has them."""
        index, self._seen = self._seen, self._seen + 1
        if obj is None:
            return
        if obj.get("type") == "result":
            self._last_result = index
            return
        if fields is None:
            fields = logs.agent_fields(obj, self._agents)
        self._remember_prompts(obj)
        agent_id = fields.get("agent")
        if not agent_id:
            return
        entry = self._entry_for(agent_id)
        entry["description"] = fields["agent_label"]
        if fields.get("agent_type"):
            entry["type"] = fields["agent_type"]
        if obj.get("type") == "system":
            self._system(obj, entry, index)
        else:
            self._message(obj, entry)

    def _remember_prompts(self, obj: dict) -> None:
        for block in logs._blocks(obj):
            inp = block.get("input")
            if block.get("type") == "tool_use" and block.get("name") in _SPAWNERS:
                prompt = inp.get("prompt") if isinstance(inp, dict) else None
                if isinstance(prompt, str) and isinstance(block.get("id"), str):
                    self._prompts[block["id"]] = prompt

    def _entry_for(self, agent_id: str) -> dict:
        if agent_id not in self._entries:
            self._entries[agent_id] = {
                "id": agent_id,
                "tool_use_id": None,
                "description": "sub-agent",
                "type": None,
                "depth": None,
                "status": "running",
                "started_at": None,
                "ended_at": None,
                "tokens": None,
                "tool_uses": None,
                "duration_ms": None,
                "last_tool": None,
                "summary": None,
            }
        return self._entries[agent_id]

    def _message(self, obj: dict, entry: dict) -> None:
        if not entry["tool_use_id"] and isinstance(obj.get("parent_tool_use_id"), str):
            entry["tool_use_id"] = obj["parent_tool_use_id"]
        stamp = obj.get("timestamp")
        if entry["started_at"] is None and isinstance(stamp, str):
            entry["started_at"] = stamp
        # claude 2.1.319: the report is the sub-agent's own handback call;
        # task_notification's summary then only points at it.
        for block in logs._blocks(obj):
            inp = block.get("input")
            if block.get("type") == "tool_use" and block.get("name") == "SubagentHandback":
                answer = inp.get("message") if isinstance(inp, dict) else None
                if isinstance(answer, str):
                    self._answer(entry, answer)

    def _answer(self, entry: dict, answer: str) -> None:
        self._answers[entry["id"]] = answer
        entry["summary"] = answer[:SUMMARY_MAX]

    def _system(self, obj: dict, entry: dict, index: int) -> None:
        subtype = obj.get("subtype")
        if isinstance(obj.get("tool_use_id"), str):
            entry["tool_use_id"] = obj["tool_use_id"]
        if subtype == "task_started":
            entry["depth"] = _int(obj.get("spawn_depth"))
            if isinstance(obj.get("prompt"), str):
                self._started_prompts[entry["id"]] = obj["prompt"]
        elif subtype == "task_progress":
            if obj.get("last_tool_name"):
                entry["last_tool"] = str(obj["last_tool_name"])
            self._usage(obj, entry)
        elif subtype == "task_updated":
            patch = obj.get("patch")
            patch = patch if isinstance(patch, dict) else {}
            ended = _iso(patch.get("end_time"))
            word = patch.get("status")
            if ended:
                entry["ended_at"] = ended
            if word in TERMINAL or ended:
                self._finish(entry["id"], index, word)
        elif subtype == "task_notification":
            self._usage(obj, entry)
            answer = obj.get("summary")
            if isinstance(answer, str) and entry["id"] not in self._answers:
                self._answer(entry, answer)
            self._finish(entry["id"], index, obj.get("status"))

    def _usage(self, obj: dict, entry: dict) -> None:
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            return
        for key, field in (
            ("total_tokens", "tokens"),
            ("tool_uses", "tool_uses"),
            ("duration_ms", "duration_ms"),
        ):
            if _int(usage.get(key)) is not None:
                entry[field] = usage[key]

    def _finish(self, agent_id: str, index: int, word: object) -> None:
        if agent_id not in self._terminal:
            status = TERMINAL.get(word, "failed") if isinstance(word, str) else "failed"
            self._terminal[agent_id] = (index, status)

    def cut_off(self) -> list[str]:
        """Ids with no terminal line, or whose first terminal line comes after
        the last `result` line and is a kill. A pause, cap or idle kill writes no
        `result` at all, and what the CLI writes after one doesn't count."""
        return [
            agent_id
            for agent_id in self._entries
            if agent_id not in self._terminal
            or (
                self._terminal[agent_id][0] > self._last_result
                and self._terminal[agent_id][1] == "killed"
            )
        ]

    def entries(self, *, ended: bool) -> list[dict]:
        """The sub-agents, first seen first. With `ended` (the session is no
        longer running) one that `cut_off` names reads `cut_off`."""
        cut = set(self.cut_off()) if ended else set()
        out = []
        for agent_id, entry in self._entries.items():
            row = dict(entry)
            if agent_id in cut:
                row["status"] = "cut_off"
            elif agent_id in self._terminal:
                row["status"] = self._terminal[agent_id][1]
            out.append(row)
        return out

    def entry(self, agent_id: str, *, ended: bool = False) -> dict | None:
        """One sub-agent with its whole `prompt` and `answer` (each `None` when
        the window holds neither)."""
        found = next((e for e in self.entries(ended=ended) if e["id"] == agent_id), None)
        if found is None:
            return None
        prompt = self._started_prompts.get(agent_id)
        if prompt is None and found["tool_use_id"]:
            prompt = self._prompts.get(found["tool_use_id"])
        return {**found, "prompt": prompt, "answer": self._answers.get(agent_id)}
