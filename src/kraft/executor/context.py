from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
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

#: The node is waiting on something outside Kraft: a forge task's external
#: wait observed its condition still pending (`kraft.waits`), instead of
#: sleeping in-process (Kraft-ru98). Ranked with `RATE_LIMITED`: a condition
#: that has not settled is not evidence about the code either. Below
#: `"paused"`, which is always a human's own instruction.
WAITING = "waiting"

#: An external wait reached its timeout still pending
#: (`external-wait-timeout-needs-human`). The session status a loop cap
#: already uses for "this ran out" -- not `failed`, because nothing about the
#: code failed, so no recovery or fix cycle may spend on it.
WAIT_TIMED_OUT = "capped_out"

#: Statuses that let the chain advance. `done_with_concerns` is deliberately
#: here: the agent finished the work — its doubts are information for the human
#: at the next gate, not a control-flow change. Shared by `resuming.py` (whole-
#: node reconciliation) and `dispatch.measure_node` (per-group), so a status
#: that counts as "moved the node forward" cannot drift between the two.
_ADVANCING = ("done", "done_with_concerns")

#: A task moved the branch onto a newer origin tip, in a node that declares
#: `on_base_changed` (the forge reports it only there). Deliberately not in
#: `_ADVANCING`, so `measure_node` stops the node before its later steps run:
#: they must not run against a base that just moved. Not a failure --
#: `walk_node` hands it to `run_once`, which restarts the declared span.
BASE_MOVED = "base_moved"

#: A task could not rebase: the forge's `conflict` status, or a raised
#: `builtins.RebaseConflict`. A task failure like any other -- unless its node
#: declares `on_base_changed.on_conflict`, in which case `measure_node` answers
#: this for the node and `walk` hands it to that handler.
CONFLICT = "conflict"

#: The node's `on_conflict` handler resolved a rebase conflict and the base
#: moved (`walk._resolve_conflict`). A base change like `BASE_MOVED`, with one
#: difference `run_once` acts on: code changed that no gate in the restart span
#: saw, so the span's approved gates reopen (Ruling 162).
CONFLICT_RESOLVED = "conflict_resolved"

#: A settled pipeline whose every failed job is the forge's own fault
#: (Kraft-h81i, Kraft-s8ul). `ci_poll` retries it internally, through the
#: forge, up to a small cap; this is what it returns once retries are
#: exhausted and it is still red -- straight to `needs_human`, spending
#: no agent turn on infrastructure a fix loop cannot fix.
INFRA_STOP = "infra_stop"


#: The tier that handles each status, in one place so a new status cannot be
#: added without declaring where it is handled.
#:
#: * `advance` -- the node moves forward.
#: * `task`    -- the one task is repairable/retryable (binding `on_failure`,
#:   then node repair, then `fix_loop`).
#: * `chain`   -- handled by the chain walk itself, not a retry (a bounce).
#: * `stop`    -- no retry at any tier: a pause, a budget breach, a config
#:   error, a rate limit, a wait handed back to the scheduler, an infra-red
#:   pipeline that goes straight to `needs_human`.
SCOPE: dict[str, str] = {
    "done": "advance",
    "done_with_concerns": "advance",
    "failed": "task",
    "needs_context": "task",
    CONFLICT: "task",  # a failure, unless the node declares an explicit on_conflict handler
    "paused": "stop",  # a human's own SIGTERM
    BUDGET: "stop",  # nothing ran; a fix cycle would only spend more
    RATE_LIMITED: "stop",
    CONFIG_ERROR: "stop",
    WAITING: "stop",  # handed back to the scheduler; re-entry resumes, it does not retry
    WAIT_TIMED_OUT: "stop",  # the wait ran out; a person decides, not a fix loop
    INFRA_STOP: "stop",  # forge's own fault; a fix loop cannot fix it
    BASE_MOVED: "chain",  # the bounce, taken by run_once
    CONFLICT_RESOLVED: "chain",  # the same restart, reopening the span's gates
}


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
    #: Every connected repository entry with an `id`, by that id: what a task
    #: fanned out to a workspace member reads instead of `repo_entry` (its
    #: setup, test scopes, sandbox). Empty when nothing has an id.
    repositories: Mapping[str, dict] = field(default_factory=dict)


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

    def __init__(
        self, text: str | None = None, *, source: str = "human", to: dict[str, str] | None = None
    ) -> None:
        self._text = text or None
        self.source = source
        #: A steer addressed to tasks by path (`resuming.resume_steer`): each
        #: named task's launch takes its own text, once, and no other launch
        #: takes any (`steer-defaults-to-all-paused-agent-tasks`,
        #: `steer-can-address-paused-agent-tasks-individually`). `None` is the
        #: unaddressed note the first agent launch takes.
        self._to = dict(to) if to else None

    @property
    def targeted(self) -> bool:
        return self._to is not None

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

    def take(self, path: str | None = None) -> str | None:
        """The note for the launch of `path`. An addressed steer answers only a
        task it names; asked with no path, it gives up everything still
        undelivered (what `walk._report_if_undelivered` reports): one text
        every task was given as itself, different ones by task."""
        if self._to is not None:
            if path is not None:
                return self._to.pop(path, None)
            left, self._to = self._to, {}
            if len(set(left.values())) == 1:
                return next(iter(left.values()))
            return "\n".join(f"{p}: {t}" for p, t in left.items()) or None
        text, self._text = self._text, None
        return text

    def __bool__(self) -> bool:
        return bool(self._to) if self._to is not None else self._text is not None
