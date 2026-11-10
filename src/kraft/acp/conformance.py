"""The conformance probe (spec §9): one scripted ACP session, and what it showed against what the
entry declares. `analyze` is pure over the session log; `run` (below) makes the session."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from kraft.acp import launch, log, tools

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
    if stop is None:
        if not problems:  # a log cut off before the prompt's result is not a clean session
            problems.append(
                "the session sent no stopReason: its log ends before the prompt's result"
            )
    elif stop != "end_turn":
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


NOTES = "kraft conformance probe\n"
PROMPT = (
    "Do these five things in this order, each with your own tool, and do each even if an earlier "
    "one is refused: 1. read the file probe-notes.txt; 2. create the file probe-out.txt "
    "containing the word kraft-probe; 3. run the shell command `echo kraft-probe`; 4. list the "
    "files here that match *.txt with your Glob tool; 5. search the file probe-notes.txt for "
    "the word kraft with your Grep tool. Then say done."
)


async def run(
    h, *, executable: str | None = None, model: str | None = None, timeout_s: float = 300.0
) -> Report:
    """One scripted session of Kraft's own client against the agent, in a scratch folder, with
    the entry's asking switch applied (spec §9). It is not a real launch in two ways: it enforces
    nothing (`policy` is off, so every ask is answered `allow_once` with no gate and no server, and
    the log is judged afterwards), and the client and the agent run with this process's whole
    environment, where a real launch's is built from an allowlist. Spends a few tokens on the
    agent's default model (or `model`). If this coroutine is cancelled (Ctrl-C), the client's
    process group is killed: it runs in its own session, so the terminal never signals it."""
    with tempfile.TemporaryDirectory(prefix="kraft-acp-probe-") as scratch:
        root = Path(scratch)
        work = root / "work"
        work.mkdir()
        (work / "probe-notes.txt").write_text(NOTES)
        spec = launch.spec_for(
            h,
            agent_argv=launch.agent_argv(h, executable),
            cwd=work,
            prompt=PROMPT,
            context="",
            result_path=root / "result.json",
            model=model,
        ).model_copy(update={"policy": False, "allowlist": False})
        spec_path = root / "acp-launch.json"
        spec_path.write_text(spec.model_dump_json())
        proc = await asyncio.create_subprocess_exec(
            *launch.client_prefix(),
            "--spec",
            str(spec_path),
            cwd=work,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "KRAFT_RESULT_PATH": str(root / "result.json")},
            start_new_session=True,  # the client and the agent share a group we can kill whole
        )
        lines: list[str] = []

        async def pump() -> None:  # read as it comes, so a killed session keeps its partial log
            async for raw in proc.stdout:
                lines.append(raw.decode("utf-8", "replace").rstrip("\n"))
            await proc.wait()

        timed_out = False
        try:
            await asyncio.wait_for(pump(), timeout_s)
        except TimeoutError:
            timed_out = True
        finally:
            # Whatever ended the session (timeout, cancel, interrupt or the client exiting on its
            # own) leave nothing running: the agent or a grandchild can outlive the client.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            # Reap the group leader: a zombie leader keeps its group "alive" on macOS.
            await proc.wait()
        if timed_out:
            # Only the timeout is a finding: the steps the agent did not get to are not mismatches.
            seen = analyze(lines, h)
            return replace(
                seen,
                problems=(f"the session did not end within {timeout_s:g}s",),
                mismatches=(),
                notes=(),
                log=tuple(lines),
            )
        return replace(analyze(lines, h, exit_code=proc.returncode or 0), log=tuple(lines))


class ProbeError(Exception):
    """The probe cannot start: no such harness, a file that is not an entry, or not `kind: acp`."""


def load_entry(harness_id: str | None, file: str | None):
    """The harness to probe: `--file PATH` (an entry not yet installed, such as
    fixtures/claude-acp.yaml), else `harness_id` from the live config."""
    import yaml

    from kraft import harness
    from kraft.yamlload import safe_load

    if file:
        path = Path(file)
        try:
            h = harness.Harness.from_mapping(
                safe_load(path.read_text()), where=str(path), path=path
            )
        except (OSError, harness.HarnessError, ValueError, yaml.YAMLError) as exc:
            # yaml.YAMLError is not a ValueError: a malformed file is a message, not a traceback
            raise ProbeError(f"cannot load {file}: {exc}") from exc
    elif harness_id:
        found = harness.load(None)
        h = found.valid.get(harness_id)
        if h is None:
            raise ProbeError(found.invalid.get(harness_id) or f"no harness {harness_id!r}")
    else:
        raise ProbeError("name a harness, or give --file PATH")
    if h.kind != "acp":
        raise ProbeError(f"harness {h.id!r} is kind: {h.kind}; the probe needs kind: acp")
    return h


def render(r: Report) -> str:
    rows = [f"agent: {r.agent or 'unknown'}", f"stop reason: {r.stop_reason or 'none'}"]
    for c in r.calls:
        tool = "/".join(c.kraft) or f"? ({c.key or c.id})"
        rows.append(f"  {tool}: {'asked' if c.asked else 'NOT asked'}, {c.status or 'no status'}")
    rows.append(f"tokens reported: {'yes' if r.tokens_reported else 'no'}")
    rows.append(f"cost reported: {'yes' if r.cost_reported else 'no'}")
    rows += [f"PROBLEM: {p}" for p in r.problems]
    rows += [f"MISMATCH: {m}" for m in r.mismatches]
    rows += [f"note: {n}" for n in r.notes]
    rows.append("conforms to its entry" if r.ok else "does NOT conform to its entry")
    return "\n".join(rows)
