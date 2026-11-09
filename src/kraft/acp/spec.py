"""What the client is told: written next to the session's log, read by `kraft admin acp-client`.

Imports only pydantic, so the client starts light."""

from __future__ import annotations

from pydantic import BaseModel, Field

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
    harness_id: str = ""
    #: `asks_everything.env`: set for the agent only, over the client's own environment.
    agent_env: dict[str, str] = {}
    asks_config: dict[str, str] = {}  # `asks_everything.config`: option id -> value
    asks_mode: str | None = None  # `asks_everything.mode`
    tool_names: dict[str, list[str]] = {}  # the entry's ACP name/kind -> Kraft tool names
    always_deny: list[str] = []
    deny_tools: list[str] = []  # the task's own deny list: only to name the limit in a stop
    settles_itself: list[str] = []
    policy: bool = False  # the launch holds a limit: allowlist, deny list or always_deny
    allowlist: bool = False  # ... and that includes an allowlist (even an empty one)
    #: Seconds between `heartbeat` log lines while an ask waits on the gate, so `idle_s` never
    #: fires.
    heartbeat_s: float = Field(60.0, gt=0)  # 0 would busy-loop
    #: How many times, per launch, the client sends another `session/prompt` after the agent ends
    #: its turn `cancelled` right after a rejected ask (OpenCode does; spec §10). Past it the
    #: launch ends as a failure naming the tool and the limit.
    max_continuations: int = Field(3, ge=0)
