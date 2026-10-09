from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kraft.vocab.total import total

__all__ = [
    "SessionStatus",
    "SessionTraits",
    "SESSION_TRAITS",
    "LIVE",
    "UNFINISHED",
    "ADVANCING",
    "AGENT_REPORTABLE",
    "CAP_SWEEP_LEAVES",
    "COMMAND_FINISHED",
    "UNREADABLE_EXIT",
]


class SessionStatus(StrEnum):
    """`worker_sessions.status`, in the CHECK's order."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CAPPED_OUT = "capped_out"
    PAUSED = "paused"
    UNKNOWN = "unknown"
    DONE_WITH_CONCERNS = "done_with_concerns"
    NEEDS_CONTEXT = "needs_context"
    RATE_LIMITED = "rate_limited"
    CONFIG_ERROR = "config_error"
    WAITING = "waiting"
    CONFLICT = "conflict"
    INFRA = "infra"
    INFRA_STOP = "infra_stop"


@dataclass(frozen=True)
class SessionTraits:
    #: Still running: {pending, running}.
    live: bool = False
    #: Has not finished (a scope command's `passed` is None).
    unfinished: bool = False
    #: Lets the chain advance; also "a real judgement" for the fix-loop judge.
    advancing: bool = False
    #: What an agent may write in its result file.
    agent_reportable: bool = False
    #: The cap sweep leaves the row alone.
    cap_sweep_leaves: bool = False
    #: A scope command's recorded result.
    command_finished: bool = False
    #: An exit the log could not explain; reads as config_error when launch failed.
    unreadable_exit: bool = False


_T = SessionTraits
S = SessionStatus

SESSION_TRAITS = total(
    SessionStatus,
    {
        S.PENDING: _T(live=True, unfinished=True),
        S.RUNNING: _T(live=True, unfinished=True),
        S.DONE: _T(
            advancing=True, agent_reportable=True, cap_sweep_leaves=True, command_finished=True
        ),
        S.FAILED: _T(agent_reportable=True, command_finished=True, unreadable_exit=True),
        S.CAPPED_OUT: _T(cap_sweep_leaves=True),
        S.PAUSED: _T(unfinished=True),
        S.UNKNOWN: _T(unreadable_exit=True),
        S.DONE_WITH_CONCERNS: _T(advancing=True, agent_reportable=True, cap_sweep_leaves=True),
        S.NEEDS_CONTEXT: _T(unfinished=True, agent_reportable=True),
        S.RATE_LIMITED: _T(unfinished=True),
        S.CONFIG_ERROR: _T(),
        S.WAITING: _T(unfinished=True, cap_sweep_leaves=True),
        S.CONFLICT: _T(),
        S.INFRA: _T(),
        S.INFRA_STOP: _T(),
    },
    name="SESSION_TRAITS",
)


def _group(trait: str) -> tuple[SessionStatus, ...]:
    return tuple(s for s, t in SESSION_TRAITS.items() if getattr(t, trait))


LIVE = _group("live")
UNFINISHED = _group("unfinished")
ADVANCING = _group("advancing")
AGENT_REPORTABLE = _group("agent_reportable")
CAP_SWEEP_LEAVES = _group("cap_sweep_leaves")
COMMAND_FINISHED = _group("command_finished")
UNREADABLE_EXIT = _group("unreadable_exit")
