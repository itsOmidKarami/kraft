"""Chain and nodes: the walk through an item's chain."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["ChainEvent", "CHAIN_EVENT_TRAITS"]


class ChainEvent(StrEnum):
    CHAIN_LOADED = "chain_loaded"
    WORKTREE_PREPARED = "worktree_prepared"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_SKIPPED = "node_skipped"
    SCOPE_SKIPPED = "scope_skipped"
    NODE_RECOVERY_STARTED = "node_recovery_started"
    RUN_FORKED = "run_forked"
    BASE_CHANGE_RESTART = "base_change_restart"
    WORKTREE_REBASE_VERIFIED = "worktree_rebase_verified"
    READ_ONLY_VIOLATED = "read_only_violated"
    FIX_CYCLE_STARTED = "fix_cycle_started"
    FIX_CYCLE_FINISHED = "fix_cycle_finished"
    FIX_CYCLE_REFUNDED = "fix_cycle_refunded"
    FINDINGS_MEASURED = "findings_measured"
    TEST_SCOPES_SELECTED = "test_scopes_selected"
    JUDGE_VERDICT = "judge_verdict"
    SWEEP_FAILED = "sweep_failed"
    SWEEP_LEFT_OUT = "sweep_left_out"
    BEAD_NOT_FILED = "bead_not_filed"
    BEADS_LEFT_OPEN = "beads_left_open"


_PLAIN = EventTraits()

CHAIN_EVENT_TRAITS = total(
    ChainEvent,
    {
        ChainEvent.CHAIN_LOADED: _PLAIN,
        ChainEvent.WORKTREE_PREPARED: _PLAIN,
        ChainEvent.NODE_STARTED: _PLAIN,
        ChainEvent.NODE_COMPLETED: _PLAIN,
        ChainEvent.NODE_SKIPPED: EventTraits(closes_gate=True),
        ChainEvent.SCOPE_SKIPPED: _PLAIN,
        ChainEvent.NODE_RECOVERY_STARTED: _PLAIN,
        ChainEvent.RUN_FORKED: EventTraits(restarts_run=True),
        ChainEvent.BASE_CHANGE_RESTART: EventTraits(restarts_run=True),
        ChainEvent.WORKTREE_REBASE_VERIFIED: _PLAIN,
        ChainEvent.READ_ONLY_VIOLATED: _PLAIN,
        ChainEvent.FIX_CYCLE_STARTED: _PLAIN,
        ChainEvent.FIX_CYCLE_FINISHED: _PLAIN,
        ChainEvent.FIX_CYCLE_REFUNDED: _PLAIN,
        ChainEvent.FINDINGS_MEASURED: _PLAIN,
        ChainEvent.TEST_SCOPES_SELECTED: _PLAIN,
        ChainEvent.JUDGE_VERDICT: _PLAIN,
        ChainEvent.SWEEP_FAILED: _PLAIN,
        ChainEvent.SWEEP_LEFT_OUT: _PLAIN,
        ChainEvent.BEAD_NOT_FILED: _PLAIN,
        ChainEvent.BEADS_LEFT_OPEN: _PLAIN,
    },
    name="CHAIN_EVENT_TRAITS",
)
