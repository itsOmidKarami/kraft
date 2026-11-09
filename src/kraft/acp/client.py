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

from kraft.acp import log, wire
from kraft.acp.spec import LaunchSpec

CONFIG_ERROR_EXIT = 78  # EX_CONFIG; `agent._resolve_status` reads the logged event, not this


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
    def __init__(self, spec: LaunchSpec, out: TextIO):
        self.spec, self.out = spec, out
        self.proc: asyncio.subprocess.Process | None = None
        self._ids = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.replay = False
        self.gone = False  # the agent's stdout closed or its pump failed: nothing will answer
        self.interrupted = asyncio.Event()

    # -- output ------------------------------------------------------------------------
    def emit(self, line: str) -> None:
        self.out.write(line + "\n")
        self.out.flush()

    # -- plumbing ----------------------------------------------------------------------
    async def send(self, msg: dict) -> None:
        self.emit(log.message("to_agent", msg))
        assert self.proc and self.proc.stdin
        with suppress(BrokenPipeError, ConnectionResetError):
            self.proc.stdin.write(wire.encode(msg))
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict) -> dict:
        if self.gone:  # never register a future nobody can resolve
            raise AgentGone()
        self._ids += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._ids] = fut
        await self.send({"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params})
        return await fut

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
        assert self.proc and self.proc.stderr
        while raw := await self.proc.stderr.readline():
            self.emit(log.event("stderr", text=raw.decode("utf-8", "replace").rstrip("\r\n")))

    async def _dispatch(self, msg: dict) -> None:
        method, id_ = msg.get("method"), msg.get("id")
        if method is None and id_ in self._pending:
            fut = self._pending.pop(id_)
            if "error" in msg:
                fut.set_exception(RpcError(msg["error"] or {}))
            else:
                fut.set_result(msg.get("result") or {})
        elif method is not None and id_ is not None:
            await self._answer(msg)
        # a notification (`session/update`, `$/cancel_request`) is only logged

    async def _answer(self, req: dict) -> None:
        if req["method"] == "session/request_permission":
            params = req.get("params") or {}
            option = wire.select_option(params.get("options") or [])
            outcome = (
                {"outcome": "selected", "optionId": option} if option else {"outcome": "cancelled"}
            )
            call = params.get("toolCall") or {}
            self.emit(
                log.event(
                    "ask",
                    tool=call.get("title") or call.get("kind"),
                    answer="allow_once" if option else "cancelled",
                )
            )
            await self.send(wire.result(req["id"], {"outcome": outcome}))
        else:
            await self.send(
                wire.error(req["id"], wire.METHOD_NOT_FOUND, "kraft's client offers no such method")
            )

    # -- the conversation --------------------------------------------------------------
    async def conversation(self) -> int:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("kraft")
        except PackageNotFoundError:
            ver = "0"
        spec = self.spec
        try:
            init = await self.request("initialize", wire.initialize_params(ver))
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
        self.emit(
            log.event(
                "session",
                session_id=sid,
                model=model,
                resumed=how,
                capabilities={**caps, "config_options": [o.get("id") for o in offered]},
            )
        )
        text = spec.prompt if caps["system_prompt"] else f"{spec.context}\n\n{spec.prompt}"
        self.emit(log.event("prompting"))
        prompt = asyncio.ensure_future(
            self.request("session/prompt", {"sessionId": sid, "prompt": wire.prompt_blocks(text)})
        )
        interrupted = asyncio.ensure_future(self.interrupted.wait())
        await asyncio.wait({prompt, interrupted}, return_when=asyncio.FIRST_COMPLETED)
        interrupted.cancel()
        if not prompt.done():  # Task 8: SIGINT -> session/cancel
            return await self.cancel(sid, prompt)
        try:
            result = prompt.result()
        except AgentGone:
            return await self.agent_exit()
        except RpcError as exc:
            raise Fail(f"the agent refused the prompt: {exc}") from exc
        reason = result.get("stopReason")
        self.emit(log.event("stop", stop_reason=reason))
        if reason != "end_turn":
            print(f"kraft: the agent's turn ended with stopReason={reason}", flush=True)
        return 0

    async def open_session(self, params: dict, caps: dict):
        """`session/new`, or on a resume `session/resume`, else `session/load`."""
        spec = self.spec
        try:
            if not spec.resume:
                res = await self.request("session/new", params)
                return res["sessionId"], None, list(res.get("configOptions") or [])
            if caps["resume"]:
                res = await self.request("session/resume", {"sessionId": spec.resume, **params})
                return spec.resume, "resume", list(res.get("configOptions") or [])
            if caps["load_session"]:
                self.replay = True  # history replayed before the answer is marked in the log
                try:
                    res = await self.request("session/load", {"sessionId": spec.resume, **params})
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
            try:
                res = await self.request(
                    "session/set_config_option",
                    {"sessionId": sid, "configId": cid, "value": value},
                )
            except RpcError as exc:
                raise Fail(
                    f"the agent refused {name} {value!r} (config option {cid!r}); it offers: "
                    f"{', '.join(_values(offered, cid)) or 'nothing'} ({exc})",
                    config=True,
                ) from exc
            offered[:] = res.get("configOptions") or offered
        cid = self.spec.options.get("model")
        return self.spec.model or next(
            (o.get("currentValue") for o in offered if o.get("id") == cid), None
        )

    async def cancel(self, sid: str, prompt) -> int:
        """Task 8."""
        raise NotImplementedError

    async def agent_exit(self) -> int:
        assert self.proc
        try:  # EOF means it is exiting; a failed pump leaves it running, so end it after the grace
            await asyncio.wait_for(self.proc.wait(), self.spec.grace_s)
        except TimeoutError:
            with suppress(ProcessLookupError):
                self.proc.terminate()
        rc = _code(await self.proc.wait())
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
        path = Path(self.spec.result_path)
        if not path.exists():
            tmp = path.with_name(f".{path.name}.acp")
            with suppress(OSError):
                tmp.write_text(json.dumps({"status": "failed", "error": message}))
                os.replace(tmp, path)
        return CONFIG_ERROR_EXIT if config else 1

    async def run(self) -> int:
        self.emit(log.event("starting", agent=self.spec.agent_argv))
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.spec.agent_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self.spec.line_limit,
            )
        except OSError as exc:
            return self.fail(
                f"cannot start the agent {self.spec.agent_argv[0]!r}: {exc}", config=True
            )
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self.interrupted.set)  # Task 8 gives it meaning
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
            await self.end()
            for pump in pumps:
                pump.cancel()
        return code

    def on_term(self) -> None:  # Task 8 forwards it to the agent
        raise SystemExit(143)


def main(spec_path: str) -> int:
    spec = LaunchSpec.model_validate_json(Path(spec_path).read_text())
    return asyncio.run(Client(spec, sys.stdout).run())
