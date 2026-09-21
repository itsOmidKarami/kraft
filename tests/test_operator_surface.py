"""Every door a human or an agent drives a work item through -- the HTTP route,
the CLI, the MCP tool -- offers the same operator controls, addressed the same
way (docs/templates-v1-design.md "Operator controls and run forks")."""

from __future__ import annotations

import asyncio

import pytest

from kraft import cli, mcp


def _tool(name):
    return next(t for t in asyncio.run(mcp.build().list_tools()) if t.name == name)


def _route_body(path):
    from kraft.api import app

    operation = app.openapi()["paths"][f"/api/work-items/{{wid}}/{path}"]["post"]
    return operation.get("requestBody")


def test_pause_is_a_work_item_control_only():
    """`pause-is-a-work-item-control`: no door lets pause address a task, a
    step or a node."""
    assert _route_body("pause") is None
    assert set(_tool("pause_work_item").input_schema["properties"]) == {"work_item_id"}
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["item", "pause", "w1", "--path", "verification"])


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("retry_work_item", {"path", "restart"}),
        ("skip_work_item", {"path"}),
        ("resume_work_item", {"steers"}),
        ("complete_work_item", {"reason"}),
        ("cancel_work_item", {"reason"}),
    ],
)
def test_the_mcp_tools_take_canonical_paths_and_reasons(tool, params):
    assert params <= set(_tool(tool).input_schema["properties"])


@pytest.mark.parametrize("verb", ["complete", "cancel"])
def test_a_terminal_action_is_a_work_item_action_that_needs_a_reason(verb, capsys):
    """No path on either, and the CLI refuses one without `--reason` before
    anything reaches the server."""
    assert "reason" in _tool(f"{verb}_work_item").input_schema["required"]
    assert "path" not in _tool(f"{verb}_work_item").input_schema["properties"]
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["item", verb, "w1"])
