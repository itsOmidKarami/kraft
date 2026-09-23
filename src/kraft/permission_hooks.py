"""A harness's pre-tool hook, answered by Kraft's permission gate
(Kraft-4in7z, spec 2026-09-23-permission-gate-for-every-harness-design).

One translator per CLI turns its hook payload into `(tool, input)` and
Kraft's answer back into what that CLI expects. `answer_hook` is the whole
of what `kraft admin permission-hook` runs, and never raises: the hook sits
in front of the CLI's own classifier, so a Kraft failure is *no opinion*
(the classifier decides) -- or *deny*, when the session is fail-closed
(`--fail-closed` or KRAFT_PERMISSION_FAIL_CLOSED=1, set by a launch whose
task holds an allowlist).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Where the launch put the worker (adapters.hook_install.WORKTREE_ENV).
WORKTREE_ENV = "KRAFT_WORKTREE"

Answer = Literal["allow", "deny", "no_opinion"]


@dataclass(frozen=True)
class Translator:
    #: stdin -> (CLI tool name, its input, the CLI's id for this call or
    #: None), or None for a call that is not the worker's: no opinion, unasked.
    parse: Callable[[str], tuple[str, dict, str | None] | None]
    render: Callable[[Answer, str], tuple[str, int]]


def _cursor_parse(stdin: str) -> tuple[str, dict, str | None]:
    payload = json.loads(stdin)
    tool, input = payload["tool_name"], payload.get("tool_input") or {}
    if not isinstance(tool, str) or not isinstance(input, dict):
        raise ValueError("preToolUse payload without tool_name/tool_input")
    use_id = payload.get("tool_use_id")
    return tool, input, use_id if isinstance(use_id, str) else None


def _cursor_render(answer: Answer, reason: str) -> tuple[str, int]:
    # No opinion is `{}`: Cursor's own classifier decides (Task 1 probe).
    if answer == "no_opinion":
        return "{}", 0
    # ponytail: a hook `allow` does not override --auto-review (Task 1 probe),
    # so a grant only takes effect once the launch writes a matching rule --
    # Kraft-4in7z.6, beside Task 9's hook install. Sent anyway.
    body = {"permission": answer}
    if answer == "deny":
        # Cursor does not surface agent_message to the agent today (probe);
        # sent anyway so a later Cursor that does gets the reason.
        body["agent_message"] = body["user_message"] = f"Kraft: {reason}"
    return json.dumps(body), 0


def _codex_parse(stdin: str) -> tuple[str, dict, str | None] | None:
    """Claude-shaped, tool names already Kraft's (codex-cli 0.155.0 probe).
    The hook also fires for codex's own background agents (the memory
    agent runs in ~/.codex/memories): a call outside the worker's worktree
    (KRAFT_WORKTREE, set by the launch) is none of Kraft's business."""
    call = _cursor_parse(stdin)
    worktree = os.environ.get(WORKTREE_ENV)
    cwd = json.loads(stdin).get("cwd")
    if worktree and not (
        isinstance(cwd, str) and Path(cwd).resolve().is_relative_to(Path(worktree).resolve())
    ):
        return None
    return call


def _codex_render(answer: Answer, reason: str) -> tuple[str, int]:
    # No opinion is `{}`: codex's approve-for-me reviewer decides.
    if answer == "no_opinion":
        return "{}", 0
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": answer,
                "permissionDecisionReason": f"Kraft: {reason}",
            }
        }
    ), 0


TRANSLATORS: dict[str, Translator] = {
    "cursor": Translator(_cursor_parse, _cursor_render),
    "codex": Translator(_codex_parse, _codex_render),
}

Ask = Callable[..., Awaitable[dict]]


def answer_hook(
    harness: str,
    stdin: str,
    tool_names: Mapping[str, tuple[str, ...]],
    *,
    fail_closed: bool,
    ask: Ask | None = None,
) -> tuple[str, int]:
    """Answer one hook call. `unresolved` (the gate could not read the
    task's policy, and was not told `fail_closed`) is no opinion; a Kraft
    that cannot be reached (`unavailable`), an unreadable payload or any
    other failure is deny when `fail_closed`, else no opinion."""
    t = TRANSLATORS[harness]
    failed: Answer = "deny" if fail_closed else "no_opinion"
    try:
        call = t.parse(stdin)
    except Exception as exc:  # noqa: BLE001 -- a hook must answer, whatever it was given
        print(f"kraft permission-hook: unreadable {harness} payload: {exc}", file=sys.stderr)
        return t.render(failed, "Kraft could not read this call")
    if call is None:
        return t.render("no_opinion", "")
    cli_tool, input, tool_use_id = call
    if ask is None:
        from kraft.client import reads

        ask = reads.permission_request
    tool, *also = tool_names.get(cli_tool, (cli_tool,))
    try:
        got = asyncio.run(
            ask(
                tool,
                input,
                tool_use_id,
                also=tuple(also),
                mode="enforce",
                harness=harness,
                cli_tool=cli_tool,
                fail_closed=fail_closed,
            )
        )
        behavior, reason = got.get("behavior"), got.get("message", "")
    except Exception as exc:  # noqa: BLE001
        behavior, reason = "unavailable", str(exc)
    if behavior in ("allow", "deny", "no_opinion"):
        return t.render(behavior, reason)
    print(f"kraft permission-hook: {behavior}: {reason}", file=sys.stderr)
    # The gate already turned `unresolved` into a deny if we asked fail_closed.
    return t.render(
        "no_opinion" if behavior == "unresolved" else failed,
        f"Kraft's permission gate is {behavior}",
    )
