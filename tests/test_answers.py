"""`kraft.answers`: what both front doors say, worded once. The CLI's own
printing of these lines is pinned in `tests/cli/test_admin_running_agents.py`."""

from __future__ import annotations

import pytest

from kraft import answers

_IN_PLACE = [
    {"id": "a1b2", "title": "Add the parser", "current_node_id": "implement", "in_place": True},
    {"id": "c3d4", "title": "Not started yet", "current_node_id": None, "in_place": True},
]
_WORKTREE = {"id": "e5f6", "title": "In a worktree", "current_node_id": "verify", "in_place": False}


@pytest.mark.parametrize(
    ("items", "now", "lines"),
    [
        ([], False, []),
        ([_WORKTREE], False, []),
        (
            [*_IN_PLACE, _WORKTREE],
            False,
            [
                "stopping the server ends the agent of any item running in place (2):",
                "  a1b2  implement  Add the parser",
                "  c3d4  -  Not started yet",
            ],
        ),
        (
            [*_IN_PLACE, _WORKTREE],
            True,
            [
                "stopping the server ends the agent of any active item (3):",
                "  a1b2  implement  Add the parser",
                "  c3d4  -  Not started yet",
                "  e5f6  verify  In a worktree",
            ],
        ),
    ],
    ids=["nothing-active", "worktree-items-keep-running", "in-place-ends", "now-ends-all"],
)
def test_the_running_agents_warning_names_each_item_a_stop_ends(items, now, lines):
    """Nothing ending is no warning at all, not a heading over an empty list; an item between
    nodes still gets a column, so the ids stay aligned."""
    said = answers.running_agents_warning("stopping", items, now=now)
    assert said[: len(lines)] == lines
    assert len(said) == (len(lines) + 1 if lines else 0)


WHOLE = {"id": "a1", "status": "active", "chain_definition": {}}


@pytest.mark.parametrize(
    ("done", "small", "result", "said"),
    [
        ("rejected {id}", None, {"id": "a1"}, "rejected a1"),
        (lambda r: f"did {r['id']}", None, {"id": "a1"}, "did a1"),
        ("rejected {id}", lambda r: f"small {r['id']}", {"id": "a1"}, "small a1"),
        ("rejected {id}", lambda r: "unused", WHOLE, "rejected a1; the item is now running"),
    ],
    ids=["small-says-done-alone", "done-as-a-function", "small-wins", "whole-row-says-status"],
)
def test_an_item_action_says_what_was_done_and_where_the_item_stands(done, small, result, said):
    """A whole row adds the item's status; any other answer is `small`'s, or,
    with none, `done` alone (the console's toasts take this path)."""
    assert answers.item_action(done, small=small)(result) == said
