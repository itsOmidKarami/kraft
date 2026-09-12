from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: `_dispatch` returned without launching because a spend cap was already over.
#: It is not "failed" — the agent never ran, so nothing about it failed — and it
#: outranks a co-task's failure for exactly that reason.
BUDGET = "budget"

#: `"rate_limited"` (`_subprocess.run_task`, via Task 4's `_rate_limit_rejection`).
#: Ranked above `BUDGET`/`"failed"` in `_measure_node` -- a rate limit is not
#: evidence of bad code -- but below `"paused"`, which is always a human's own
#: SIGTERM.
RATE_LIMITED = "rate_limited"

#: A measuring task that never launched (Kraft-579). Terminal like a pause: no
#: fix cycle, no counter bump, no on_failure repair -- none of those can install
#: a missing binary.
CONFIG_ERROR = "config_error"

#: The node is waiting on something outside Kraft — today, a pipeline that has
#: not settled. Returned by `adapters/forge`'s `ci_poll` instead of sleeping the
#: whole `poll_timeout` in-process (Kraft-ru98). Ranked with `RATE_LIMITED`: a
#: pipeline that has not finished is not evidence about the code either. Below
#: `"paused"`, which is always a human's own instruction.
WAITING = "waiting"

#: A settled pipeline whose every failed job is the forge's own fault
#: (Kraft-h81i, Kraft-s8ul). `ci_poll` retries it internally, through the
#: forge, up to a small cap; this is what it returns once retries are
#: exhausted and it is still red -- straight to `needs_human`, spending
#: no agent turn on infrastructure a fix loop cannot fix.
INFRA_STOP = "infra_stop"


#: What one gate approval must also do, and the chain it leaves behind:
#: `kraft.api.routes.gates.apply_approval`, partially applied over the app state. `None` means
#: no door is wired up, and an agent may not approve at all -- see
#: `kraft.executor.gates.review_gates`.
OnApprove = Callable[[Any, str], Awaitable[tuple[dict | None, str | None]]]


@dataclass(frozen=True)
class LaunchContext:
    """The repo config an agent dispatch resolves against.

    `None` on any field means "nothing configured", not "look elsewhere" —
    `agent.resolve_invocation` already treats a missing repo entry and a missing
    steering dir as empty. Threaded keyword-only, `launch: LaunchContext | None
    = None`, from `kraft.api` down through every walk/resume path so a work item's
    repo config reaches its agent launches, including reattach and the fix cycle.

    `skills_dir` is where an operator may override a bundled method file;
    `None` means the packaged copies only.
    """

    repo_entry: dict | None
    steering_dir: Path | None
    skills_dir: Path | None = None


class Steer:
    """A steer note, good for exactly one agent launch.

    Both entry points mean the same thing by it — "say this to the next agent you
    start" — so it is carried down the walk and consumed by whichever dispatch
    gets there first, rather than each caller guessing which task that will be.
    """

    def __init__(self, text: str | None = None) -> None:
        self._text = text or None

    def take(self) -> str | None:
        text, self._text = self._text, None
        return text

    def __bool__(self) -> bool:
        return self._text is not None
