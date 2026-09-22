"""The MCP front door. Registration is checked in-process; the transport is
checked against a real process, because an in-process check cannot see a missing
transport dependency (the lesson from tests/test_ws.py)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from support.harness import make_repo
from support.server import child_env

from kraft import client, mcp


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
        "complete_work_item",
        "cancel_work_item",
        "escalate_work_item",
        "set_mr_labels",
        "set_chain_template",
        "set_attachments",
        "set_agent_overrides",
        "set_node_overrides",
        "set_work_item_policy",
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
    # and it cannot ask otherwise: autostart is a person's flag (Kraft-s7c04.31)
    assert "autostart" not in create.input_schema["properties"]


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
        env=child_env({"KRAFT_HOME": str(tmp_path / "home")}),
        cwd=Path(__file__).resolve().parents[1],
    )
    assert '"serverInfo"' in proc.stdout, proc.stderr


def test_create_work_item_forwards_auto_gate(monkeypatch):
    """The tool declared `auto_gate` and called the client positionally, one
    argument short, so an agent asking for `auto_gate=False` silently got
    `True` and had no way to find out."""
    seen = {}

    async def fake(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {"id": "w1", "status": "paused", "title": "t"}

    monkeypatch.setattr(mcp.client, "create_work_item", fake)
    asyncio.run(
        mcp.build().call_tool("create_work_item", {"title": "t", "repo": "/r", "auto_gate": False})
    )
    assert seen["kwargs"].get("auto_gate") is False
    assert seen["args"] == ("t",), "every other argument should be passed by keyword"


def test_the_items_own_policy_reaches_the_api_from_both_tools(monkeypatch):
    """Kraft-ab1bh: `create_work_item(policy=)` and `set_work_item_policy`
    hand the override to the client as given."""
    seen = []

    async def fake(*args, **kwargs):
        seen.append(kwargs.get("policy", args[0] if args else None))
        return {"id": "w1", "status": "paused", "title": "t"}

    monkeypatch.setattr(mcp.client, "create_work_item", fake)
    monkeypatch.setattr(mcp.client, "set_work_item_policy", fake)
    override = {"paths": {"verification": {"max_attempts": 2}}}
    server = mcp.build()
    asyncio.run(server.call_tool("create_work_item", {"title": "t", "policy": override}))
    asyncio.run(server.call_tool("set_work_item_policy", {"policy": override}))
    assert seen == [override, override]


def test_a_worker_cannot_set_its_own_policy_through_mcp(monkeypatch):
    """Kraft-j89jc: the MCP door reaches the same guard as the client's."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")
    with pytest.raises(Exception, match="set_work_item_policy") as refused:
        asyncio.run(mcp.build().call_tool("set_work_item_policy", {"policy": {"max_attempts": 9}}))
    cause = refused.value.__cause__
    assert isinstance(cause, PermissionError) and "its own work item" in str(cause)


def test_create_work_item_forwards_the_base_branch(monkeypatch):
    """Kraft-v9gbi: the MCP door names an item's base branch too."""
    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return {"id": "w1", "status": "paused", "title": "t"}

    monkeypatch.setattr(mcp.client, "create_work_item", fake)
    asyncio.run(
        mcp.build().call_tool(
            "create_work_item", {"title": "t", "repo": "/r", "base_branch": "release"}
        )
    )
    assert seen["base_branch"] == "release"


def test_create_work_item_forwards_skip_nodes_budget_and_node_overrides(monkeypatch):
    """Kraft-s7c04.33: the MCP door takes the same intake fields as the CLI.
    An omitted `budget_usd` is not sent as None: None would be an explicit
    "no cap", and leaving it out is "the policy default"."""
    seen = []

    async def fake(*args, **kwargs):
        seen.append(kwargs)
        return {"id": "w1", "status": "paused", "title": "t"}

    monkeypatch.setattr(mcp.client, "create_work_item", fake)
    server = mcp.build()
    fields = {
        "skip_nodes": ["spec"],
        "budget_usd": 5.0,
        "node_overrides": {"plan": {"auto_escalate": True}},
    }
    asyncio.run(server.call_tool("create_work_item", {"title": "t", **fields}))
    asyncio.run(server.call_tool("create_work_item", {"title": "t"}))
    assert fields.items() <= seen[0].items()
    assert "budget_usd" not in seen[1]


def test_set_node_overrides_forwards_model_effort_and_extra_prompt(monkeypatch):
    """Kraft-a7ers: the MCP door names the same per-node fields the CLI does."""
    seen = []

    async def fake(*args, **kwargs):
        seen.append(kwargs)
        return {"id": "w1"}

    monkeypatch.setattr(mcp.client, "set_node_overrides", fake)
    fields = {"model": "opus", "effort": "high", "extra_prompt": "Mind the order."}
    asyncio.run(mcp.build().call_tool("set_node_overrides", {"node_id": "plan", **fields}))
    assert fields.items() <= seen[0].items()


def test_ensure_repo_through_the_mcp_tool_returns_the_stored_entry(app, tmp_path):
    """Kraft-xs3ri: the MCP door, not only the client, hands back what
    `GET /repos` stores for an already-connected repo -- never the probe,
    whose `submodules`/`has_beads` keys no stored entry carries."""
    repo = make_repo(tmp_path)
    server = mcp.build()

    async def scenario():
        await server.call_tool("ensure_repo", {"path": str(repo)})
        await client.transport._patch(
            f"/repos?path={quote(str(repo))}", {"test_command": "just ci-test"}
        )
        return await server.call_tool("ensure_repo", {"path": str(repo)})

    out = json.loads(asyncio.run(scenario()).content[0].text)
    assert out["already_connected"] is True
    assert out["test_command"] == "just ci-test", "got the probed command, not the stored one"
    assert "submodules" not in out


def test_approve_gate_forwards_the_digest_the_artifact_carried(monkeypatch):
    """Kraft-ec66w: a chain revision's approval is refused without it."""
    seen = {}

    async def fake(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {"id": "w1", "status": "active"}

    monkeypatch.setattr(mcp.client, "approve_gate", fake)
    asyncio.run(mcp.build().call_tool("approve_gate", {"work_item_id": "w1", "digest": "d1"}))
    assert seen["kwargs"].get("digest") == "d1"
