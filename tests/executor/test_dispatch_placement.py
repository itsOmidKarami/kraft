"""`dispatch`: a step placed on a machine stops until steps can run on one."""

from pathlib import Path

import pytest
from support.harness import v1_chain, v1_walk


@pytest.mark.parametrize(
    ("node_fields", "item_fields"),
    [
        ({"runner": "gpu"}, {}),
        ({}, {"node_overrides": {"run": {"runner": "gpu"}}}),
    ],
    ids=["library", "node-override"],
)
async def test_a_placed_step_stops_until_steps_can_run_on_a_machine(
    tmp_path, repo, node_fields, item_fields
):
    """A step placed on a machine never runs on the server instead: it stops
    `config_error` naming the machine, and its command is never run."""
    marker = tmp_path / "ran"
    task = {"id": "t", "kind": "subprocess", "command": f"touch {marker}"}
    node = {"id": "run", "kind": "exec", "tasks": [task], **node_fields}

    status, _evts, sessions, _row = await v1_walk(
        tmp_path, v1_chain([node], repo=repo), repo=repo, **item_fields
    )

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["config_error"]
    assert "this step is placed on gpu" in Path(sessions[0]["log_path"]).read_text()
    assert not marker.exists()
