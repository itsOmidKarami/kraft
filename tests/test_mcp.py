"""The MCP front door. Registration is checked in-process; the transport is
checked against a real process, because an in-process check cannot see a missing
transport dependency (the lesson from tests/test_ws.py)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kraft import mcp


def _tools():
    return asyncio.run(mcp.build().list_tools())


def test_the_tools_are_registered():
    assert {t.name for t in _tools()} == {
        "list_work_items",
        "get_work_item",
        "get_gate_artifact",
        "search",
        "create_work_item",
        "ensure_repo",
        "approve_gate",
        "reject_gate",
        "pause_work_item",
        "report_progress",
        "resume_work_item",
        "retry_work_item",
        "skip_work_item",
        "escalate_work_item",
        "set_mr_labels",
        "set_chain_template",
        "set_agent_overrides",
        "set_node_overrides",
        "permission_request",
    }


def test_the_permission_tool_says_it_is_not_for_the_agent_to_call():
    """It is wired as `--permission-prompt-tool`; an agent calling it directly
    would be asking Kraft's opinion about a tool use that is not happening."""
    tool = next(t for t in _tools() if t.name == "permission_request")
    assert "not for you to call" in tool.description.lower()


def test_no_standalone_steer_tool_is_exposed():
    """`/steer` 409s unless the item is already paused, so the one moment an
    agent would reach for it is the one moment it fails. pause() then
    resume(steer=...) is the honest surface (design §9 phase 3)."""
    assert "steer" not in {t.name for t in _tools()}


def test_create_work_item_tells_the_agent_it_will_not_run():
    """An agent that thinks create means start will file work and walk away."""
    create = next(t for t in _tools() if t.name == "create_work_item")
    assert "paused" in create.description.lower()


def test_create_work_item_offers_a_description_and_says_what_it_is_for():
    """An agent that cannot see the parameter keeps packing intent into the title,
    which is the behavior this field exists to end."""
    create = next(t for t in _tools() if t.name == "create_work_item")
    assert "description" in create.input_schema["properties"]
    assert "brief" in create.description.lower()


def test_create_work_item_offers_attachments_and_says_what_they_are_for():
    """An agent that cannot see the parameter hands over a title and lets Kraft
    re-run a spec node over a spec that is already written (Kraft-82gz)."""
    create = next(t for t in _tools() if t.name == "create_work_item")
    assert "attachments" in create.input_schema["properties"]
    assert "spec" in create.description.lower()


def test_every_tool_has_a_description_an_agent_can_act_on():
    """The docstring is what an agent reads to decide whether to call the tool.
    A one-word description is a tool that never gets used correctly."""
    for tool in _tools():
        assert tool.description and len(tool.description) > 30, tool.name


@pytest.mark.slow
def test_kraft_mcp_starts_over_real_stdio(tmp_path):
    """`kraft mcp` answers an MCP initialize on stdin/stdout as a real process."""
    env = {**os.environ, "KRAFT_HOME": str(tmp_path / "home")}
    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "kraft", "admin", "mcp"],
        input=request,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert '"serverInfo"' in proc.stdout, proc.stderr
