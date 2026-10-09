import itertools

import pytest

from kraft.api.routes.board import display_status
from kraft.vocab import FAILURE_KINDS, DisplayStatus, StopKind, WorkItemStatus

S = WorkItemStatus


def row(status, archived=False):
    return {"archived_at": "t" if archived else None, "status": status.value}


# Hard-coded on purpose: reading TRAITS here would test the table against itself.
BADGE = {
    S.ACTIVE: DisplayStatus.RUNNING,
    S.WAITING: DisplayStatus.WAITING,
    S.RATE_LIMITED: DisplayStatus.WAITING,
    S.QUEUED: DisplayStatus.QUEUED,
    S.PAUSED: DisplayStatus.PAUSED,
    S.COMPLETED: DisplayStatus.DONE,
    S.ABANDONED: DisplayStatus.CANCELLED,
}


@pytest.mark.parametrize("status", list(BADGE), ids=lambda s: s.value)
def test_a_status_other_than_needs_human_shows_its_badge(status):
    assert display_status(row(status), None, False, None) == BADGE[status]


def test_the_badge_table_covers_every_status_but_needs_human():
    assert set(BADGE) == set(S) - {S.NEEDS_HUMAN}


def _expected(kind, escalated, gate):
    if gate:
        return DisplayStatus.NEEDS_YOU
    if escalated:
        return DisplayStatus.ESCALATED
    return DisplayStatus.FAILED if kind in FAILURE_KINDS else DisplayStatus.NEEDS_YOU


@pytest.mark.parametrize(
    ("kind", "escalated", "gate"),
    list(itertools.product([*StopKind, None, "a_future_kind"], [False, True], [None, "plan"])),
)
def test_a_needs_human_badge_follows_gate_then_escalation_then_kind(kind, escalated, gate):
    got = display_status(row(S.NEEDS_HUMAN), kind, escalated, gate)
    assert got == _expected(kind, escalated, gate)


def test_archived_wins_and_every_badge_is_reachable():
    assert (
        display_status(row(S.COMPLETED, archived=True), None, False, None) == DisplayStatus.ARCHIVED
    )
    seen = {display_status(row(s), None, False, None) for s in S}
    seen |= {
        display_status(row(S.NEEDS_HUMAN), k, e, g)
        for k in (None, StopKind.INFRA)
        for e in (False, True)
        for g in (None, "p")
    }
    seen.add(DisplayStatus.ARCHIVED)
    assert seen == set(DisplayStatus)
