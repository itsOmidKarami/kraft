"""Kraft's half of an ACP session: the session's command, outside any sandbox (spec §4).

Run as `kraft admin acp-client --spec <file>` by Kraft, never by hand. It starts the agent as its
child on pipes, speaks ACP, writes every message as one JSON line to stdout (the session log), and
exits with the agent's exit code (0 after a completed turn)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import TextIO

from kraft.acp import log, tools, wire
from kraft.acp.spec import LaunchSpec

CONFIG_ERROR_EXIT = 78  # EX_CONFIG; `agent._resolve_status` reads the logged event, not this
POLICY_VIOLATION_EXIT = 77  # EX_NOPERM; `agent._resolve_status` reads the logged event, not this
SETTLE_S = 6.0  # a backstop check in flight may wait the gate's 5 s timeout
INTERRUPTED_EXIT = 130  # what a shell reports for SIGINT: a pause
TERMINATED_EXIT = 143  # ... and for SIGTERM
PUMP_DRAIN_S = 1.0  # how long the readers get, after the agent is gone, to finish what is buffered


class RpcError(Exception):
    def __init__(self, error: dict):
        super().__init__(error.get("message", "agent error"))
        self.code, self.data = error.get("code"), error.get("data")


class AgentGone(Exception):
    """The agent closed its stdout before answering."""


class Fail(Exception):
    def __init__(self, message: str, *, config: bool = False):
        super().__init__(message)
        self.config = config


def _values(offered: list, cid: str) -> list[str]:
    """The values the agent offers for config option `cid` (flat, or grouped)."""
    for option in offered:
        if option.get("id") == cid:
            flat = []
            for o in option.get("options") or []:
                flat += (
                    [x.get("value") for x in o["options"]] if "options" in o else [o.get("value")]
                )
            return [v for v in flat if isinstance(v, str)]
    return []


def _code(rc: int) -> int:
    return 128 - rc if rc < 0 else rc  # -9 -> 137, -15 -> 143: what a shell reports


class Client:
    def __init__(self, spec: LaunchSpec, out: TextIO, ask=None):
        self.spec, self.out = spec, out
        #: The permission gate: `reads.permission_request`'s signature; None means that function,
        #: imported on first use so a launch with no tool policy never imports it.
        self.ask = ask
        self.calls: dict[str, dict] = {}  # what the agent has said of each tool call, by id
        self.verdicts: dict[str, str] = {}  # toolCallId -> "allow" | "deny": the answer given
        #: toolCallId -> the Kraft names an allow was decided on: a `name` that arrives later may
        #: make the call another tool (`settled`).
        self._decided_on: dict[str, tuple[str, ...]] = {}
        self._asked: set[str] = set()  # tool calls the agent has asked about
        #: This turn's rejected asks: (Kraft tool, its Kraft names, the gate's reason).
        self._turn_rejects: list[tuple[str, tuple[str, ...], str]] = []
        self.continuations = 0  # prompts sent after a rejection ended a turn (`conversation`)
        self._asks: dict = {}  # request id -> the task answering that ask
        self._checked: set[str] = set()  # tool calls the backstop has looked at
        self._backstops: set[asyncio.Future] = set()
        self.violation: str | None = None
        self.violated = asyncio.Event()
        self._withdrawn: dict = {}  # request id -> Future set when the agent withdraws it
        self.proc: asyncio.subprocess.Process | None = None
        self._ids = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.replay = False
        self.gone = False  # the agent's stdout closed or its pump failed: nothing will answer
        self.interrupted = asyncio.Event()
        self.terminated = False  # SIGTERM arrived: whatever the agent exits with, this was a stop

    # -- output ------------------------------------------------------------------------
    def emit(self, line: str) -> None:
        self.out.write(line + "\n")
        self.out.flush()

    # -- plumbing ----------------------------------------------------------------------
    async def send(self, msg: dict) -> None:
        self.emit(log.message("to_agent", msg))
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write(wire.encode(msg))
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._eof()  # nothing reads what we write any more, so nothing will answer

    async def request(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        """The `result` of a request; `timeout` (None: wait for the turn) bounds the wait, and
        a late answer is then dropped (`_dispatch`)."""
        if self.gone:  # never register a future nobody can resolve
            raise AgentGone()
        self._ids += 1
        id_ = self._ids
        fut = asyncio.get_running_loop().create_future()
        self._pending[id_] = fut
        await self.send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            self._pending.pop(id_, None)
            raise Fail(f"the agent did not answer {method} within {timeout:g}s") from None

    async def notify(self, method: str, params: dict) -> None:
        await self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def _eof(self) -> None:
        self.gone = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AgentGone())

    async def pump(self) -> None:
        """`_stdout`, wrapped: an exception in it (a bad message `_dispatch` cannot handle) is
        logged and counts as EOF, so a waiting request fails with `AgentGone` instead of
        hanging behind a task that died silently."""
        try:
            await self._stdout()
        except Exception as exc:  # noqa: BLE001 -- whatever broke, the pump must end the waits
            self.emit(log.event("error", message=f"the client's reader failed: {exc!r}"))
        finally:
            self._eof()

    async def _stdout(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            kind, value = await wire.read_line(self.proc.stdout)
            if kind == "eof":
                return
            if kind == "stray":
                self.emit(log.stray(str(value)))
            elif kind == "long":
                self.emit(log.event("line_too_long", limit=self.spec.line_limit))
            else:
                assert isinstance(value, dict)
                # only the replayed history is marked, not the answer that ends the replay
                replayed = self.replay and "method" in value
                self.emit(log.message("from_agent", value, replay=replayed))
                await self._dispatch(value)

    async def _stderr(self) -> None:
        """Drain the agent's stderr to the log for as long as it writes: a reader that stops
        lets the pipe fill (64 KB), and the agent then blocks on its next write."""
        assert self.proc and self.proc.stderr
        while True:
            try:
                raw = await self.proc.stderr.readline()
            except ValueError:  # a line past the limit; its tail arrives as the next "line"
                self.emit(log.event("line_too_long", stream="stderr", limit=self.spec.line_limit))
                continue
            if not raw:
                return
            self.emit(log.event("stderr", text=raw.decode("utf-8", "replace").rstrip("\r\n")))

    async def _dispatch(self, msg: dict) -> None:
        method, id_ = msg.get("method"), msg.get("id")
        hashable = id_ is None or isinstance(id_, (str, int, float))
        if method is None:
            fut = self._pending.pop(id_, None) if hashable else None
            if fut is None or fut.done():  # unknown, or its waiter gave up (a cancel timeout)
                return
            if "error" in msg:
                fut.set_exception(RpcError(msg["error"] if isinstance(msg["error"], dict) else {}))
            else:
                result = msg.get("result")
                fut.set_result(result if isinstance(result, dict) else {})
        elif id_ is not None:
            if not hashable or not isinstance(method, str):
                await self.send(wire.error(None, wire.INVALID_REQUEST, "invalid request"))
            else:
                await self._answer(msg)
        elif method == "$/cancel_request":
            params = msg.get("params")  # {requestId}: ACP's CancelRequestNotification
            self._withdraw(params.get("requestId") if isinstance(params, dict) else None)
        elif method == "session/update":
            params = msg.get("params")
            self.watch(params.get("update") if isinstance(params, dict) else None)
        # any other notification is only logged

    def watch(self, update) -> None:
        """The runtime backstop's eyes (spec §6.5): every tool_call notification."""
        kind = update.get("sessionUpdate") if isinstance(update, dict) else None
        if kind not in ("tool_call", "tool_call_update"):
            return
        self.remember(update)
        cid, status = update.get("toolCallId"), update.get("status")
        if (
            not self.spec.policy
            or self.replay
            or status not in ("completed", "failed")  # a call that ran can still end failed
            or not isinstance(cid, str)
            or cid in self._checked
            or self.settled(cid, status)
        ):
            return
        self._checked.add(cid)
        task = asyncio.ensure_future(self.backstop(cid))
        self._backstops.add(task)
        task.add_done_callback(self._backstop_done)

    def settled(self, cid: str, status: str) -> bool:
        """Does an ask already cover this call's end, so the backstop has nothing to decide?"""
        verdict = self.verdicts.get(cid)
        if verdict == "allow":  # for the names it was asked on: a late `name` can change them
            return self._decided_on.get(cid) == self.names(cid)
        # asked and rejected, then failed: it did not run (a *completed* one did, `backstop`). So
        # did a call never asked about that fails once Kraft rejected a tool in this turn: the
        # agent ends the turn on the rejection and cancels what it had queued ("declined").
        return status == "failed" and (
            verdict == "deny" or cid in self._asked or bool(self._turn_rejects)
        )

    def names(self, cid: str) -> tuple[str, ...]:
        return tools.resolve(self.calls.get(cid, {"toolCallId": cid}), self.spec.tool_names).names

    def _backstop_done(self, task: asyncio.Future) -> None:
        self._backstops.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            self.violate(f"backstop failed: {exc!r}")  # fail closed: a check that broke is a stop

    async def backstop(self, cid: str) -> None:
        """A call completed with no allowing ask on record: is it one the policy allows?"""
        info = self.calls.get(cid, {"toolCallId": cid})
        tool = tools.resolve(info, self.spec.tool_names)
        what = tool.names[0] if tool.names else (tool.key or "an unnamed tool")
        if self.verdicts.get(cid) == "deny":
            self.violate(f"{what} ran although Kraft's permission gate rejected it")
            return
        if tool.names and all(n in self.spec.settles_itself for n in tool.names):
            return  # the launch refused any policy that denies it (`launch.tool_limit_refusal`)
        allow, why = await self.decide(info)
        self.emit(log.event("unasked_call", tool=what, allowed=allow, reason=why))
        if not allow:
            self.violate(
                f"{what} ran without asking, and the task's tool policy does not allow it ({why})"
            )

    def violate(self, message: str) -> None:
        if self.violation is None:
            self.violation = message
            self.violated.set()

    async def settle(self) -> None:
        """Let the checks in flight finish: a violation must not be missed because the turn
        ended first."""
        live = [t for t in self._backstops if not t.done()]
        if live:
            await asyncio.wait(live, timeout=SETTLE_S)

    def fail_policy(self, message: str) -> int:
        self.emit(log.event("policy_violation", message=message))
        print(f"kraft: tool policy violated: {message}", flush=True)
        self._write_failed(message)
        return POLICY_VIOLATION_EXIT

    def _withdraw(self, rid) -> None:
        """The agent withdrew an ask (`$/cancel_request`, seen from OpenCode): an ask already
        answered has no future."""
        fut = self._withdrawn.get(rid) if isinstance(rid, (str, int)) else None
        if fut is not None and not fut.done():
            fut.set_result(True)

    async def _answer(self, req: dict) -> None:
        params = req.get("params")
        if params is not None and not isinstance(params, dict):
            await self.send(wire.error(req["id"], wire.INVALID_REQUEST, "params must be an object"))
        elif req["method"] == "session/request_permission":
            # Not awaited: the gate's round trip can be long, and an agent may have two asks in
            # flight; the reader must keep reading.
            # The withdrawal future is made before the task: the agent's `$/cancel_request` can be
            # read before the task has taken its first step.
            rid = req["id"]
            call = (params or {}).get("toolCall")
            if isinstance(call, dict) and isinstance(call.get("toolCallId"), str):
                self._asked.add(call["toolCallId"])
            self._withdrawn[rid] = asyncio.get_running_loop().create_future()
            task = asyncio.ensure_future(self.permission(rid, params or {}))
            self._asks[rid] = task
            task.add_done_callback(
                lambda _t, rid=rid: (self._asks.pop(rid, None), self._withdrawn.pop(rid, None))
            )
        else:
            await self.send(
                wire.error(req["id"], wire.METHOD_NOT_FOUND, "kraft's client offers no such method")
            )

    # -- permission asks ---------------------------------------------------------------
    async def gate(self, *args, **kw) -> dict:
        if self.ask is None:
            from kraft.client import (
                reads,
            )  # only a launch with a policy asks: keeps the client light

            self.ask = reads.permission_request
        return await self.ask(*args, **kw)

    def remember(self, call: dict) -> None:
        """What the agent has said of a tool call so far: `name` comes with the first message."""
        cid = call.get("toolCallId")
        if isinstance(cid, str):
            known = self.calls.setdefault(cid, {"toolCallId": cid})
            for key in ("name", "kind", "title", "rawInput"):
                if call.get(key):
                    known[key] = call[key]

    def verdict(self, cid, value: str, names: tuple[str, ...] = ()) -> None:
        if isinstance(cid, str):
            self.verdicts[cid] = value
            self._decided_on[cid] = names

    async def decide(self, call: dict) -> tuple[bool, str]:
        """Allow this tool call? Never raises (a gate failure is a denial: fail-closed)."""
        spec = self.spec
        if not spec.policy:
            return True, "the launch holds no tool policy"
        tool = tools.resolve(call, spec.tool_names)
        what = tool.key or "this call"
        if not tool.names:
            if spec.allowlist:
                return (
                    False,
                    f"cannot tell which of Kraft's tools {what!r} is, and the task holds "
                    "an allowlist",
                )
            return True, f"{what!r} maps to no Kraft tool and the task holds no allowlist"
        if hit := next((n for n in tool.names if n in spec.always_deny), None):
            return False, f"{hit} is always denied on this harness"
        raw, cid = call.get("rawInput"), call.get("toolCallId")
        try:
            got = await self.gate(
                tool.names[0],
                raw if isinstance(raw, dict) else {},
                cid if isinstance(cid, str) else None,
                also=tool.names[1:],
                mode="enforce",
                harness=spec.harness_id or None,
                cli_tool=tool.key,
                fail_closed=True,
            )
            behavior, message = got.get("behavior"), str(got.get("message") or "")
        except Exception as exc:  # noqa: BLE001 -- whatever broke, the answer is a denial
            behavior, message = "unavailable", str(exc)
        if behavior in ("allow", "no_opinion"):
            return True, message or "the gate allows it"
        if behavior == "deny":
            return False, message
        return False, f"Kraft's permission gate is {behavior}: {message}"

    async def heartbeat(self, call: dict) -> None:
        """While an ask waits, the log must not go quiet: `idle_s` kills a session whose log
        stays unwritten."""
        while True:
            await asyncio.sleep(self.spec.heartbeat_s)
            self.emit(
                log.event(
                    "heartbeat", waiting_on="gate", tool=call.get("title") or call.get("kind")
                )
            )

    async def permission(self, rid, params: dict) -> None:
        call = params.get("toolCall")
        call = call if isinstance(call, dict) else {}
        self.remember(call)
        cid = call.get("toolCallId")
        info = self.calls.get(cid, call) if isinstance(cid, str) else call
        # Raced, not awaited, against the agent's withdrawal: cancelling the task instead would
        # skip every handler of a task that has not yet started, and no `cancelled` would be sent.
        beat = asyncio.ensure_future(self.heartbeat(call))
        decision = asyncio.ensure_future(self.decide(info))
        withdrawn = self._withdrawn.get(rid)
        try:
            await asyncio.wait(
                {decision, *([withdrawn] if withdrawn else [])},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            beat.cancel()
            if not decision.done():
                decision.cancel()  # withdrawn, or the client is ending
        if withdrawn is not None and withdrawn.done():  # withdrawn wins even if the gate answered
            self.emit(log.event("ask_withdrawn", tool=call.get("title") or call.get("kind")))
            self.verdict(cid, "deny")  # a call that then runs anyway is a violation
            await self.send(wire.result(rid, {"outcome": {"outcome": "cancelled"}}))
            return
        allow, why = decision.result()
        options = params.get("options") or []
        option = wire.select_option(options, ("allow_once",) if allow else ("reject_once",))
        if allow and option is None:  # only an *_always on offer: never take it
            allow, why = False, "the agent offered no allow_once option"
            option = wire.select_option(options, ("reject_once",))
        tool = tools.resolve(info, self.spec.tool_names)
        self.verdict(cid, "allow" if allow else "deny", tool.names)
        if option and not allow:
            what = tool.names[0] if tool.names else (tool.key or "an unnamed tool")
            self._turn_rejects.append((what, tool.names, why))
        outcome = (
            {"outcome": "selected", "optionId": option} if option else {"outcome": "cancelled"}
        )
        self.emit(
            log.event(
                "ask",
                tool=call.get("title") or call.get("kind"),
                kraft_tool=list(tool.names),
                reason=why,
                answer=("allow_once" if allow else "reject_once") if option else "cancelled",
            )
        )
        await self.send(wire.result(rid, {"outcome": outcome}))

    # -- the conversation --------------------------------------------------------------
    async def conversation(self) -> int:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("kraft")
        except PackageNotFoundError:
            ver = "0"
        spec = self.spec
        try:
            init = await self.request(
                "initialize", wire.initialize_params(ver), timeout=spec.handshake_timeout_s
            )
        except RpcError as exc:
            raise Fail(f"the agent refused initialize: {exc}") from exc
        agent_caps = init.get("agentCapabilities") or {}
        session_caps = agent_caps.get("sessionCapabilities") or {}
        caps = {
            "load_session": bool(agent_caps.get("loadSession")),
            "resume": "resume" in session_caps,
            "system_prompt": "systemPrompt" in session_caps,
        }
        params: dict = {"cwd": spec.cwd, "mcpServers": []}
        if caps["system_prompt"]:
            params["systemPrompt"] = wire.system_prompt(spec.context)
        sid, how, offered = await self.open_session(params, caps)
        model = await self.apply_options(sid, offered)
        await self.apply_asks(sid, offered)
        self.emit(
            log.event(
                "session",
                session_id=sid,
                model=model,
                resumed=how,
                capabilities={**caps, "config_options": [o.get("id") for o in offered]},
            )
        )
        if self.interrupted.is_set():  # a pause during the handshake: no turn is started
            self.emit(log.event("interrupted", before="prompt"))
            return INTERRUPTED_EXIT
        text = spec.prompt if caps["system_prompt"] else f"{spec.context}\n\n{spec.prompt}"
        self.emit(log.event("prompting"))
        while True:
            got = await self.turn(sid, text)
            if isinstance(got, int):
                return got
            reason = got.get("stopReason")
            self.emit(log.event("stop", stop_reason=reason))
            # OpenCode ends the turn `cancelled` once Kraft rejects a tool (spec §10): the task is
            # not over, so the same session is prompted again, naming what is not available.
            if reason != "cancelled" or not self._turn_rejects:
                if reason != "end_turn":
                    print(f"kraft: the agent's turn ended with stopReason={reason}", flush=True)
                return 0
            await self.settle()
            if self.violation:  # `run` turns it into the stop
                return 0
            if self.terminated:
                return TERMINATED_EXIT
            if self.interrupted.is_set():  # a pause is never continued past
                return INTERRUPTED_EXIT
            if self.continuations >= spec.max_continuations:
                return self.stop_after_rejections()
            self.continuations += 1
            text = self.continuation_prompt()
            self.emit(
                log.event(
                    "continuing",
                    n=self.continuations,
                    tools=list(dict.fromkeys(what for what, _, _ in self._turn_rejects)),
                )
            )

    async def turn(self, sid: str, text: str) -> dict | int:
        """One `session/prompt`: the agent's answer, or the exit code of a turn that ended
        otherwise (a pause, a violation, the agent gone)."""
        self._turn_rejects = []
        prompt = asyncio.ensure_future(
            self.request("session/prompt", {"sessionId": sid, "prompt": wire.prompt_blocks(text)})
        )
        interrupted = asyncio.ensure_future(self.interrupted.wait())
        violated = asyncio.ensure_future(self.violated.wait())
        await asyncio.wait({prompt, interrupted, violated}, return_when=asyncio.FIRST_COMPLETED)
        interrupted.cancel()
        violated.cancel()
        if not prompt.done():  # SIGINT: a pause; a violation: the turn is cancelled, usage lands
            code = await self.cancel(sid, prompt)
            return (
                POLICY_VIOLATION_EXIT if self.violation and not self.interrupted.is_set() else code
            )
        try:
            return prompt.result()
        except AgentGone:  # the group's SIGINT may take the agent first: still a pause
            return await self.agent_exit()
        except RpcError as exc:
            raise Fail(f"the agent refused the prompt: {exc}") from exc

    def continuation_prompt(self) -> str:
        """What the agent is told after a rejection ended its turn."""
        reasons = {what: why for what, _, why in self._turn_rejects}
        names = ", ".join(reasons)
        one = len(reasons) == 1
        detail = "; ".join(f"{what}: {why}" for what, why in reasons.items())
        return (
            f"Kraft's permission gate rejected {detail}. {names} "
            f"{'is' if one else 'are'} not available in this task: continue without "
            f"{'it' if one else 'them'}. Any other call of yours that failed as declined when the "
            "turn ended was cancelled with it, not denied by Kraft's policy: it can be tried again."
        )

    def stop_after_rejections(self) -> int:
        """The continuations ran out: a failure that names the tool and the limit, never a
        `failed` result with no cause (`log.cause` reads the event)."""
        spec = self.spec
        rejected = list(dict.fromkeys(what for what, _, _ in self._turn_rejects))

        def limit(names: tuple[str, ...]) -> str:
            if any(n in spec.always_deny for n in names):
                return "always_deny"
            if any(n in spec.deny_tools for n in names):
                return "deny_tools"
            return "allowed_tools" if spec.allowlist else "the task's tool policy"

        limits = list(dict.fromkeys(limit(names) for _, names, _ in self._turn_rejects))
        message = (
            f"{log.REJECTION_PREFIX}{', '.join(rejected)} ({', '.join(limits)}) "
            f"{self.continuations + 1} times"
        )
        self.emit(log.event("rejection_limit", message=message, tools=rejected))
        print(f"kraft: {message}", flush=True)
        self._write_failed(message)
        return POLICY_VIOLATION_EXIT  # never a clean exit; the status is the logged event's

    async def open_session(self, params: dict, caps: dict):
        """`session/new`, or on a resume `session/resume`, else `session/load`."""
        spec = self.spec
        limit = spec.handshake_timeout_s
        try:
            if not spec.resume:
                res = await self.request("session/new", params, timeout=limit)
                return res["sessionId"], None, list(res.get("configOptions") or [])
            if caps["resume"]:
                res = await self.request(
                    "session/resume", {"sessionId": spec.resume, **params}, timeout=limit
                )
                return spec.resume, "resume", list(res.get("configOptions") or [])
            if caps["load_session"]:
                self.replay = True  # history replayed before the answer is marked in the log
                try:
                    res = await self.request(
                        "session/load", {"sessionId": spec.resume, **params}, timeout=limit
                    )
                finally:
                    self.replay = False
                return spec.resume, "load", list(res.get("configOptions") or [])
        except RpcError as exc:
            raise Fail(f"the agent refused to open the session: {exc}") from exc
        raise Fail("the agent can resume neither with session/resume nor session/load")

    async def apply_options(self, sid: str, offered: list) -> str | None:
        """`session/set_config_option` for model and effort; returns the effective model."""
        for name, value in (("model", self.spec.model), ("effort", self.spec.effort)):
            if value is None:
                continue
            cid = self.spec.options.get(name)
            if cid is None:
                raise Fail(
                    f"this launch sets {name}={value!r} but the entry maps no {name!r} "
                    "under options:",
                    config=True,
                )
            await self.set_option(sid, cid, value, offered, name)
        cid = self.spec.options.get("model")
        return self.spec.model or next(
            (o.get("currentValue") for o in offered if o.get("id") == cid), None
        )

    async def set_option(self, sid: str, cid: str, value: str, offered: list, what: str) -> None:
        try:
            res = await self.request(
                "session/set_config_option",
                {"sessionId": sid, "configId": cid, "value": value},
                timeout=self.spec.handshake_timeout_s,
            )
        except RpcError as exc:
            raise Fail(
                f"the agent refused {what} {value!r} (config option {cid!r}); it offers: "
                f"{', '.join(_values(offered, cid)) or 'nothing'} ({exc})",
                config=True,
            ) from exc
        offered[:] = res.get("configOptions") or offered

    async def apply_asks(self, sid: str, offered: list) -> None:
        """`asks_everything`'s session half, new and resumed launches alike (its env half is the
        spawn env)."""
        spec = self.spec
        for cid, value in spec.asks_config.items():
            await self.set_option(sid, cid, value, offered, "asks_everything")
        if spec.asks_mode is not None:
            if any(o.get("id") == "mode" for o in offered):
                await self.set_option(sid, "mode", spec.asks_mode, offered, "asks_everything mode")
            else:
                try:
                    await self.request(
                        "session/set_mode",
                        {"sessionId": sid, "modeId": spec.asks_mode},
                        timeout=spec.handshake_timeout_s,
                    )
                except RpcError as exc:
                    raise Fail(
                        f"the agent refused asks_everything mode {spec.asks_mode!r}: {exc}",
                        config=True,
                    ) from exc
        if spec.agent_env or spec.asks_config or spec.asks_mode is not None:
            self.emit(
                log.event(
                    "asks_everything",
                    env=sorted(spec.agent_env),
                    config=spec.asks_config,
                    mode=spec.asks_mode,
                )
            )

    async def cancel(self, sid: str, prompt) -> int:
        """A pause: `session/cancel`, then the turn's answer (its usage) if it comes in time."""
        await self.notify("session/cancel", {"sessionId": sid})
        try:
            result = await asyncio.wait_for(prompt, self.spec.cancel_wait_s)
        except TimeoutError:
            self.emit(log.event("cancel_timeout", waited_s=self.spec.cancel_wait_s))
            return INTERRUPTED_EXIT
        except (AgentGone, RpcError):
            return INTERRUPTED_EXIT
        self.emit(log.event("stop", stop_reason=result.get("stopReason")))
        return INTERRUPTED_EXIT

    async def agent_exit(self) -> int:
        assert self.proc
        try:  # EOF means it is exiting; a failed pump leaves it running, so end it after the grace
            await asyncio.wait_for(self.proc.wait(), self.spec.grace_s)
        except TimeoutError:
            with suppress(ProcessLookupError):
                self.proc.terminate()
        rc = _code(await self.proc.wait())
        # Whatever the agent exits with, a signal sent to Kraft's group is why: SIGTERM is a stop,
        # SIGINT a pause, and a pause is never a failed result.
        if self.terminated:
            return TERMINATED_EXIT
        if self.interrupted.is_set():
            return INTERRUPTED_EXIT
        if rc == 0:
            raise Fail("the agent exited before answering the prompt")
        self.emit(log.event("error", message=f"the agent exited with code {rc} mid-turn"))
        return rc

    # -- ending ------------------------------------------------------------------------
    async def end(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with suppress(Exception):
            assert proc.stdin
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), self.spec.grace_s)
            return
        except TimeoutError:
            pass
        with suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    def fail(self, message: str, *, config: bool = False) -> int:
        """The client's own failure: the result file (only if the agent wrote none), the log."""
        self.emit(log.event("config_error" if config else "error", message=message))
        print(f"kraft: {message}", flush=True)
        self._write_failed(message)
        return CONFIG_ERROR_EXIT if config else 1

    def _write_failed(self, message: str) -> None:
        path = Path(self.spec.result_path)
        # Kraft pre-creates an empty result file at session start: empty is as good as absent.
        with suppress(OSError):
            if path.exists() and path.stat().st_size > 0:
                return
            tmp = path.with_name(f".{path.name}.acp")
            tmp.write_text(json.dumps({"status": "failed", "error": message}))
            os.replace(tmp, path)

    async def run(self) -> int:
        self.emit(log.event("starting", agent=self.spec.agent_argv))
        if not self.spec.agent_argv:
            return self.fail("the launch names no agent command", config=True)
        try:
            # the client's own environment, plus the entry's asking switch, for the agent only
            env = {**os.environ, **self.spec.agent_env} if self.spec.agent_env else None
            self.proc = await asyncio.create_subprocess_exec(
                *self.spec.agent_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=self.spec.line_limit,
            )
        except OSError as exc:
            return self.fail(
                f"cannot start the agent {self.spec.agent_argv[0]!r}: {exc}", config=True
            )
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self.interrupted.set)
        loop.add_signal_handler(signal.SIGTERM, self.on_term)
        pumps = [asyncio.ensure_future(self.pump()), asyncio.ensure_future(self._stderr())]
        try:
            code = await self.conversation()
        except Fail as exc:
            code = self.fail(str(exc), config=exc.config)
        except AgentGone:
            try:
                code = await self.agent_exit()
            except Fail as exc:
                code = self.fail(str(exc), config=exc.config)
        except Exception as exc:  # noqa: BLE001 -- a bug here is still a failed result file
            code = self.fail(f"the client failed: {exc!r}")
        finally:
            for task in list(self._asks.values()):
                task.cancel()
            await self.settle()
            await self.end()
            # The agent is gone: what it wrote last (a crash's traceback) may still be buffered.
            _, rest = await asyncio.wait(pumps, timeout=PUMP_DRAIN_S)
            for pump in rest:
                pump.cancel()
        if self.violation is not None:
            if code in (INTERRUPTED_EXIT, TERMINATED_EXIT):
                # a pause or a stop keeps its code, but the violation is still on record
                self.emit(log.event("policy_violation", message=self.violation))
            else:
                code = self.fail_policy(self.violation)  # never a clean exit, whatever else
        return code

    def on_term(self) -> None:
        """SIGTERM is forwarded; the conversation then sees the agent go and exits with its code."""
        self.terminated = True
        if self.proc is not None and self.proc.returncode is None:
            with suppress(ProcessLookupError):
                self.proc.terminate()


def main(spec_path: str) -> int:
    spec = LaunchSpec.model_validate_json(Path(spec_path).read_text())
    return asyncio.run(Client(spec, sys.stdout).run())
