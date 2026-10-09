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
    StopKind,
    Verb,
    WorkItemStatus,
    admitting,
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
        (Verb.PAUSE, {S.ACTIVE, S.WAITING, S.RATE_LIMITED, S.QUEUED}),
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
            },
        ),
        (Verb.ARCHIVE, {S.COMPLETED, S.ABANDONED}),
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
}


@pytest.mark.parametrize("door", sorted(DOOR_VERB))
def test_a_verbs_statuses_are_the_statuses_of_the_states_its_door_takes(door):
    taken = {DOORS["states"][s]["status"] for s in DOORS["doors"][door]["takes"]}
    assert taken == {str(s) for s in admitting(DOOR_VERB[door])}


def test_every_status_has_a_state_in_the_door_table():
    assert {st["status"] for st in DOORS["states"].values()} == {str(s) for s in WorkItemStatus}


def test_every_verb_has_a_door():
    assert set(DOOR_VERB.values()) == set(Verb)
