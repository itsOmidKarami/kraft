"""`cli.common`: how an action's answer reads without `--json`."""

from __future__ import annotations

from kraft.cli import common


def test_an_action_answer_prints_objects_as_json_and_nothing_as_a_dash():
    """`kraft item set-overrides --clear` printed `agent_overrides  None`, and
    an override object printed as Python's repr rather than the JSON `--json`
    shows."""
    text = common._render_action(
        {
            "id": "w1",
            "agent_overrides": {},
            "node_overrides": {"plan": {"effort": "high"}},
            "progress": None,
        }
    )
    assert text.splitlines() == [
        "id               w1",
        "agent_overrides  {}",
        'node_overrides   {"plan": {"effort": "high"}}',
        "progress         -",
    ]
