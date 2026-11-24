"""The state mapping: Kraft's display status to a state Herdr accepts."""

from __future__ import annotations

import pytest

import kraft_herdr


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("running", "working"),
        ("escalated", "working"),
        ("needs_you", "blocked"),
        ("failed", "blocked"),
        ("paused", "idle"),
        ("waiting", "idle"),
        ("queued", "idle"),
        ("blocked", "idle"),
        ("done", "idle"),
        ("cancelled", "idle"),
        ("archived", "idle"),
    ],
)
def test_state_for(status, state):
    assert kraft_herdr.state_for({"display_status": status, "title": "t"})[0] == state


@pytest.mark.parametrize(
    ("row", "label"),
    [
        ({"display_status": "running", "current_node_id": "implementation"}, "implementation"),
        ({"display_status": "running"}, "running"),
        ({"display_status": "needs_you", "pending_gate": "plan_review"}, "plan_review"),
        ({"display_status": "blocked"}, "after another item"),
        ({"display_status": "done"}, "merged"),
        ({"display_status": "a-status-from-the-future"}, "a-status-from-the-future"),
    ],
)
def test_state_for_label(row, label):
    assert kraft_herdr.state_for(row)[1] == label


def test_a_status_it_has_never_heard_of_is_unknown_not_a_crash():
    assert kraft_herdr.state_for({"display_status": "from-the-future"})[0] == "unknown"
    assert kraft_herdr.state_for({})[0] == "unknown"


def test_every_kraft_display_status_is_mapped():
    display = pytest.importorskip("kraft.vocab.display")
    assert set(kraft_herdr.STATES) == {s.value for s in display.DisplayStatus}


def test_no_state_is_done():
    assert set(kraft_herdr.STATES.values()) <= {"idle", "working", "blocked", "unknown"}
