"""What the client is told: written next to the session's log, read by `kraft admin acp-client`.

Imports only pydantic, so the client starts light."""

from __future__ import annotations

from pydantic import BaseModel

LINE_LIMIT = 8 * 1024 * 1024  # = wire.LINE_LIMIT, which takes it from here


class LaunchSpec(BaseModel):
    agent_argv: list[str]
    cwd: str
    prompt: str
    context: str
    result_path: str
    resume: str | None = None
    model: str | None = None
    effort: str | None = None
    #: Kraft option (`model`, `effort`) -> the agent's session config option id, copied
    #: from the entry's `options`.
    options: dict[str, str] = {}
    #: How long the client waits for the agent's answer to `session/cancel`. Strictly
    #: less than `run_task`'s `flush_grace` (2.0): after a pause's SIGINT Kraft waits
    #: that long and then kills the group, so the client must have logged `stop` and
    #: the usage first.
    cancel_wait_s: float = 1.5
    #: How long the client waits, after closing the agent's stdin, for it to exit.
    grace_s: float = 3.0
    #: How long the client waits for the answer to each request before the turn: `initialize`,
    #: `session/new|resume|load` and `session/set_config_option`. The turn itself is not bounded
    #: here (Kraft's idle timeout is its bound).
    handshake_timeout_s: float = 120.0
    line_limit: int = LINE_LIMIT
