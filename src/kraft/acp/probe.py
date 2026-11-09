"""What an ACP agent offers, asked without a prompt: `kraft admin doctor`'s handshake (spec §9).

It starts the agent itself, never Kraft's client, sends `initialize` and `session/new` and kills
the agent. No prompt goes out, so it spends nothing."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from kraft import update
from kraft.acp import wire

AUTH_ERROR_CODE = -32000


@dataclass(frozen=True)
class Handshake:
    ok: bool
    error: str | None = None
    resume: bool = False
    load_session: bool = False
    system_prompt: bool = False
    config_options: tuple[str, ...] = ()
    needs_login: bool = False


class _Failed(Exception):
    def __init__(self, error: dict | str):
        self.error = error
        super().__init__(str(error))


async def _write(proc: asyncio.subprocess.Process, method: str, msg: dict) -> None:
    """One line to the agent's stdin; an agent that already exited is a failed handshake."""
    assert proc.stdin is not None
    try:
        proc.stdin.write(wire.encode(msg))
        await proc.stdin.drain()
    except ConnectionError:  # BrokenPipeError, ConnectionResetError
        raise _Failed(f"the agent exited before answering {method}") from None


async def _ask(proc: asyncio.subprocess.Process, id_: int, method: str, params: dict) -> dict:
    """The `result` of our request `id_`; an agent-to-client request meanwhile is answered
    "method not found" (this client offers nothing), anything else is skipped."""
    assert proc.stdin is not None and proc.stdout is not None
    await _write(proc, method, {"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
    while True:
        kind, msg = await wire.read_line(proc.stdout)
        if kind == "eof":
            raise _Failed(f"the agent exited before answering {method}")
        if kind != "json":
            continue
        assert isinstance(msg, dict)
        if "method" in msg:
            if "id" in msg:
                await _write(
                    proc, method, wire.error(msg["id"], wire.METHOD_NOT_FOUND, "method not found")
                )
            continue
        if msg.get("id") != id_:
            continue
        if "error" in msg:
            raise _Failed(msg["error"] if isinstance(msg["error"], dict) else str(msg["error"]))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}


def _needs_login(error: dict | str) -> bool:
    if not isinstance(error, dict):
        return False
    return error.get("code") == AUTH_ERROR_CODE or "auth" in str(error.get("message", "")).lower()


def _message(error: dict | str) -> str:
    return str(error.get("message", error)) if isinstance(error, dict) else error


async def handshake(argv: list[str], *, cwd: str, timeout_s: float = 20) -> Handshake:
    """Start `argv` (the agent's own command); report what `initialize` and `session/new` say."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=wire.LINE_LIMIT,
        )
    except OSError as exc:
        return Handshake(ok=False, error=f"cannot start {argv[0]!r}: {exc}")
    try:
        try:
            async with asyncio.timeout(timeout_s):
                init = await _ask(proc, 1, "initialize", wire.initialize_params(update.installed()))
                caps = init.get("agentCapabilities")
                caps = caps if isinstance(caps, dict) else {}
                session_caps = caps.get("sessionCapabilities")
                session_caps = session_caps if isinstance(session_caps, dict) else {}
                offers = {
                    "resume": "resume" in session_caps,
                    "load_session": bool(caps.get("loadSession")),
                    "system_prompt": "systemPrompt" in session_caps,
                }
                try:
                    new = await _ask(proc, 2, "session/new", {"cwd": cwd, "mcpServers": []})
                except _Failed as exc:
                    if _needs_login(exc.error):
                        return Handshake(
                            ok=False, error=_message(exc.error), needs_login=True, **offers
                        )
                    return Handshake(ok=False, error=_message(exc.error), **offers)
        except TimeoutError:
            return Handshake(ok=False, error=f"no answer within {timeout_s:g}s")
        except _Failed as exc:
            return Handshake(ok=False, error=_message(exc.error))
        options = tuple(
            str(o["id"])
            for o in new.get("configOptions") or []
            if isinstance(o, dict) and "id" in o
        )
        return Handshake(ok=True, config_options=options, **offers)
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
