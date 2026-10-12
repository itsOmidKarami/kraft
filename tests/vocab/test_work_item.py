import json
from pathlib import Path

import pytest

from kraft.vocab import (
    COMMANDS_MAY_START,
    ENDED,
    HOLDS_SLOT,
    MR_WATCHED,
    RUNNING,
    STOP_TRAITS,
    STOPPED,
    TRAITS,
    DisplayStatus,
    DoorFacts,
    ItemAction,
    StopKind,
    Verb,
    WorkItemStatus,
    admitting,
    open_doors,
)

S = WorkItemStatus


@pytest.mark.parametrize(
    ("group", "members"),
    [
        (ENDED, {S.COMPLETED, S.ABANDONED}),
        (STOPPED, {S.NEEDS_HUMAN, S.WAITING, S.RATE_LIMITED}),
        (HOLDS_SLOT, {S.ACTIVE}),
        (RUNNING, {S.ACTIVE, S.WAITING}),
        (COMMANDS_MAY_START, {S.ACTIVE, S.PAUSED, S.WAITING, S.RATE_LIMITED}),
        (MR_WATCHED, {S.WAITING, S.NEEDS_HUMAN}),
    ],
    ids=["ended", "stopped", "holds_slot", "running", "commands_may_start", "mr_watched"],
)
def test_a_group_is_the_set_the_audit_found(group, members):
    assert isinstance(group, tuple)
    assert set(group) == members
    assert list(group) == [s for s in WorkItemStatus if s in members]  # declaration order


@pytest.mark.parametrize(
    ("verb", "members"),
    [
        (Verb.PAUSE, {S.ACTIVE, S.WAITING, S.RATE_LIMITED, S.QUEUED, S.BLOCKED}),
        (Verb.RESUME, {S.PAUSED, S.NEEDS_HUMAN}),
        (Verb.STEER, {S.PAUSED, S.NEEDS_HUMAN}),
        (Verb.RETRY, {S.NEEDS_HUMAN}),
        (Verb.RAISE_BUDGET, {S.NEEDS_HUMAN}),
        (Verb.SKIP, {S.ACTIVE, S.WAITING, S.PAUSED, S.NEEDS_HUMAN}),
        (Verb.ESCALATE, {S.NEEDS_HUMAN, S.PAUSED}),
        (
            Verb.ABANDON,
            {
                S.WAITING,
                S.RATE_LIMITED,
                S.NEEDS_HUMAN,
                S.PAUSED,
                S.COMPLETED,
                S.ABANDONED,
                S.QUEUED,
                S.BLOCKED,
            },
        ),
        (Verb.ARCHIVE, {S.COMPLETED, S.ABANDONED}),
        (Verb.UNBLOCK, {S.PAUSED, S.BLOCKED}),
    ],
    ids=[v.value for v in Verb],
)
def test_a_verb_admits_the_statuses_its_door_takes(verb, members):
    assert set(admitting(verb)) == members


def test_every_verb_is_admitted_somewhere():
    assert all(admitting(v) for v in Verb)


@pytest.mark.parametrize("status", list(WorkItemStatus))
def test_row_invariants(status):
    t = TRAITS[status]
    assert not (t.ended and t.stopped)
    assert not t.holds_slot or t.running
    # implied_stop_kind is set iff the status is stopped and is not needs_human
    assert (t.implied_stop_kind is not None) == (t.stopped and status is not S.NEEDS_HUMAN)
    if t.implied_stop_kind is not None:
        assert STOP_TRAITS[t.implied_stop_kind].status == status.value
    # display is declared for every status except needs_human, whose badge depends on the stop
    assert (t.display is None) == (status is S.NEEDS_HUMAN)


def test_only_wait_and_rate_limit_belong_to_other_statuses():
    other = {k for k, t in STOP_TRAITS.items() if t.status != S.NEEDS_HUMAN.value}
    assert other == {StopKind.WAIT, StopKind.RATE_LIMIT}


def test_every_display_status_is_reachable_from_the_table_or_the_stop_rules():
    from_table = {t.display for t in TRAITS.values() if t.display is not None}
    from_rules = {
        DisplayStatus.NEEDS_YOU,
        DisplayStatus.ESCALATED,
        DisplayStatus.FAILED,
        DisplayStatus.ARCHIVED,
    }
    assert from_table | from_rules == set(DisplayStatus)


DOORS = json.loads((Path(__file__).parents[1] / "api" / "lifecycle_doors.json").read_text())
DOOR_VERB = {
    "pause": Verb.PAUSE,
    "resume": Verb.RESUME,
    "steer": Verb.STEER,
    "retry": Verb.RETRY,
    "skip": Verb.SKIP,
    "escalate": Verb.ESCALATE,
    "budget_raise": Verb.RAISE_BUDGET,
    "abandon": Verb.ABANDON,
    "archive": Verb.ARCHIVE,
    "unblock": Verb.UNBLOCK,
}


@pytest.mark.parametrize("door", sorted(DOOR_VERB))
def test_a_verbs_statuses_are_the_statuses_of_the_states_its_door_takes(door):
    taken = {DOORS["states"][s]["status"] for s in DOORS["doors"][door]["takes"]}
    assert taken == {str(s) for s in admitting(DOOR_VERB[door])}


def test_every_status_has_a_state_in_the_door_table():
    assert {st["status"] for st in DOORS["states"].values()} == {str(s) for s in WorkItemStatus}


def test_every_verb_has_a_door():
    assert set(DOOR_VERB.values()) == set(Verb)


def test_every_verb_is_an_item_action():
    assert {str(v) for v in Verb} <= {str(a) for a in ItemAction}


F = DoorFacts
NH = S.NEEDS_HUMAN
STOPPED_ON = {"started": True, "on_chain": True, "skippable": True}
BUDGET = {**STOPPED_ON, "budget_stop": True, "raisable": True}


@pytest.mark.parametrize(
    ("facts", "want"),
    [
        (F(NH, **BUDGET), "retry raise_budget skip complete cancel abandon"),
        (F(NH, **{**BUDGET, "raisable": False}), "retry skip complete cancel abandon"),
        (
            F(NH, started=True, on_chain=True, pending_gate=True, walk_live=True),
            "approve reject complete cancel abandon",
        ),
        (
            F(NH, started=True, on_chain=True, pending_gate=True, escalation_live=True),
            "approve reject retry complete cancel abandon",
        ),
        (
            F(NH, **STOPPED_ON, pending_gate=True, must_fix_open=True),
            "reject retry skip escalate complete cancel abandon",
        ),
        (
            F(NH, **STOPPED_ON, question=True),
            "resume steer retry skip escalate complete cancel abandon",
        ),
        (
            F(S.PAUSED, **STOPPED_ON, dead_dependency=True),
            "unblock skip escalate complete cancel abandon",
        ),
        (
            F(S.PAUSED, **STOPPED_ON, agent_paused=True),
            "resume steer unblock skip escalate complete cancel abandon",
        ),
        (F(S.PAUSED), "resume steer unblock complete cancel abandon"),
        (
            F(NH, **STOPPED_ON, mr_closed=True),
            "retry reopen_mr skip escalate complete cancel abandon",
        ),
        (F(S.QUEUED, started=True, pending_gate=True), "pause complete cancel abandon"),
        (F(S.COMPLETED, started=True, archived=True), "restore abandon"),
        (F(S.COMPLETED, started=True), "archive abandon"),
        (F(NH, **STOPPED_ON, dead_dependency=True), "skip escalate complete cancel abandon"),
        (F(NH, **BUDGET, dead_dependency=True), "skip complete cancel abandon"),
        (
            F(NH, **STOPPED_ON, mr_closed=True, dead_dependency=True),
            "skip escalate complete cancel abandon",
        ),
        (
            F(NH, started=True, budget_stop=True, raisable=True, mr_closed=True),
            "complete cancel abandon",
        ),
        (
            F(NH, **BUDGET, walk_live=True, escalation_live=True),
            "retry complete cancel abandon",
        ),
    ],
    ids=[
        "budget-closes-escalate",
        "daily-cap-not-raisable",
        "walk-closes-retry",
        "escalation-reopens-retry",
        "must-fix-closes-approve",
        "question",
        "dead-dependency",
        "paused-agent-steer",
        "not-started",
        "mr-closed",
        "queued-at-gate",
        "archived",
        "done",
        "dead-dependency-closes-retry",
        "dead-dependency-closes-raise-budget",
        "dead-dependency-closes-reopen-mr",
        "off-chain-closes-retry-and-its-composites",
        "walk-closes-raise-budget-not-retry",
    ],
)
def test_open_doors_reads_the_second_facts(facts, want):
    assert open_doors(facts) == tuple(ItemAction(a) for a in want.split())
