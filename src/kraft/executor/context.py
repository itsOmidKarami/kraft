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

#: Statuses that let the chain advance. `done_with_concerns` is deliberately
#: here: the agent finished the work — its doubts are information for the human
#: at the next gate, not a control-flow change. Shared by `resuming.py` (whole-
#: node reconciliation) and `dispatch.measure_node` (per-group), so a status
#: that counts as "moved the node forward" cannot drift between the two.
_ADVANCING = ("done", "done_with_concerns")

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

    `source` says who wrote it, and two different readers ask two different
    questions of it. It replaced a `human` boolean that was answering both at
    once, which is why a gate reviewer's verdict could not be fixed without
    breaking something else (Kraft-s7c04.6).

    * `"human"` -- a typed `/retry --steer` or `/resume --steer`.
    * `"seeded"` -- Kraft's own recap of the last review's unresolved findings
      (Kraft-7sec second half), and the rebase-drift note it writes for itself
      mid-bounce. Nobody typed these, and a prompt that says a person did is
      the misattribution this field exists to keep out.
    * `"gate_review"` -- an automated gate review's own verdict, carried into
      the re-run it triggered. Not a human's, and not Kraft's own recap either:
      an agent's judgement about this artifact, which may have committed in the
      worktree itself.
    """

    #: Sources whose note reaches a dispatch without the fix-loop judge getting
    #: a say. See `exempts_judge`.
    _JUDGE_EXEMPT = ("human", "gate_review")

    def __init__(self, text: str | None = None, *, source: str = "human") -> None:
        self._text = text or None
        self.source = source

    @property
    def human(self) -> bool:
        """Whether a prompt may say a person wrote this. `prompts.steer_prefix`
        picks its template from `source` directly; this stays for readers that
        only need the yes/no."""
        return self.source == "human"

    @property
    def exempts_judge(self) -> bool:
        """Whether this steer reaches a dispatch without the fix-loop judge
        getting a say (`walk.walk_node`'s `judge_due`).

        The note lives only in this object -- `take()` empties it and nothing
        re-delivers it -- so a `stop_needs_human` before it is delivered
        discards it and re-strands the item on the very trend the steer was the
        answer to. That is why a human's steer is exempt, and it is just as true
        of a gate reviewer's verdict, whose `concerns` is the entire content of
        a `fixed`/`reject` decision.

        `seeded` is the exception, and keeps today's behaviour: it is Kraft's
        own recap of findings the next measurement will re-report anyway, so
        nothing is lost by letting the judge stop first.
        """
        return self.source in self._JUDGE_EXEMPT

    def take(self) -> str | None:
        text, self._text = self._text, None
        return text

    def __bool__(self) -> bool:
        return self._text is not None
