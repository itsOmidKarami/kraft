"""`kraft.answers`: what both front doors say, worded once. The CLI's own
printing of these lines is pinned in `tests/cli/test_admin_running_agents.py`."""

from __future__ import annotations

import pytest

from kraft import answers


@pytest.mark.parametrize(
    ("items", "lines"),
    [
        ([], []),
        (
            [
                {"id": "a1b2", "title": "Add the parser", "current_node_id": "implement"},
                {"id": "c3d4", "title": "Not started yet", "current_node_id": None},
            ],
            [
                "stopping the server ends the agent of any active item (2):",
                "  a1b2  implement  Add the parser",
                "  c3d4  -  Not started yet",
            ],
        ),
    ],
    ids=["nothing-active", "two-active"],
)
def test_the_running_agents_warning_names_each_active_item(items, lines):
    """Nothing active is no warning at all, not a heading over an empty list;
    an item between nodes still gets a column, so the ids stay aligned."""
    said = answers.running_agents_warning("stopping", items)
    assert said[: len(lines)] == lines
    assert len(said) == (len(lines) + 1 if items else 0)
