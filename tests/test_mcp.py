"""The MCP front door. Registration is checked in-process; the transport is
checked against a real process, because an in-process check cannot see a missing
transport dependency (the lesson from tests/test_ws.py)."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from mcp import Client
from support.harness import make_repo
from support.server import child_env

from kraft import client, db, mcp
from kraft.update import installed
from kraft.vocab import WorkItemStatus


def _tools():
    return asyncio.run(mcp.build().list_tools())


def test_the_tools_are_registered():
    assert {t.name for t in _tools()} == {
        "list_work_items",
        "get_work_item",
        "get_gate_artifact",
        "get_attachment",
        "search",
        "create_work_item",
        "ensure_repo",
        "approve_gate",
        "reject_gate",
        "pause_work_item",
        "unblock_work_item",
        "report_progress",
        "reply_to_thread",
        "resume_work_item",
        "retry_work_item",
        "raise_budget",
        "skip_work_item",
        "complete_work_item",
        "cancel_work_item",
        "escalate_work_item",
        "set_mr_labels",
        "set_chain",
        "set_attachments",
        "set_agent_overrides",
        "set_node_overrides",
        "set_work_item_policy",
        "list_threads",
        "compare_changes",
        "add_review_comment",
        "resolve_thread",
        "reopen_thread",
        "submit_review",
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


def test_list_work_items_names_every_status_the_schema_allows():
    """The filter is an exact match, so a status the description leaves out is
    one an agent never asks for, and one it invents always returns []."""
    allowed = set(re.findall(r"'(\w+)'", re.search(r"status IN\s*\(([^)]*)\)", db.SCHEMA_SQL)[1]))
    listing = next(t for t in _tools() if t.name == "list_work_items")
    assert set(re.findall(r'"(\w+)"', listing.description)) == allowed


def test_list_work_items_schema_lists_the_statuses():
    listing = next(t for t in _tools() if t.name == "list_work_items")
    schema = json.dumps(listing.input_schema)
    assert all(f'"{s.value}"' in schema for s in WorkItemStatus)


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
    # Clients that show the server's version (Claude Code's /mcp, Codex) get it from here.
    version = json.loads(proc.stdout.splitlines()[0])["result"]["serverInfo"]["version"]
    assert version
    assert version == installed()


@pytest.mark.parametrize(
    "named, chain",
    [({}, None), ({"chain": "quick"}, "quick"), ({"chain_template": "quick"}, "quick")],
    ids=["unnamed", "chain", "the-1x-chain-template"],
)
def test_create_work_item_forwards_auto_gate(monkeypatch, named, chain):
    """The tool declared `auto_gate` and called the client positionally, one
    argument short, so an agent asking for `auto_gate=False` silently got
    `True` and had no way to find out. An agent written for 1.x names the
    chain `chain_template`, which must not be filed on the default chain."""
    seen = {}

    async def fake(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {"id": "w1", "status": "paused", "title": "t"}

    monkeypatch.setattr(mcp.client, "create_work_item", fake)
    asyncio.run(
        mcp.build().call_tool(
            "create_work_item", {"title": "t", "repo": "/r", "auto_gate": False, **named}
        )
    )
    assert seen["kwargs"].get("auto_gate") is False
    assert seen["args"] == ("t",), "every other argument should be passed by keyword"
    # unnamed, the server applies the repo's default chain (Kraft-9efnk.11)
    assert seen["kwargs"]["chain"] == chain


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


async def _agent_reads(session, tool: str, args: dict) -> str:
    """The text an agent gets back from a refused call, read through a real MCP
    client session: what crosses the wire, not what the server raised."""
    result = await session.call_tool(tool, args)
    assert result.is_error, f"{tool} was not refused: {result.content}"
    return result.content[0].text


def test_a_worker_cannot_set_its_own_policy_through_mcp(monkeypatch):
    """Kraft-j89jc: the MCP door reaches the same guard as the client's, and the
    agent reads the guard's reason. The SDK replaces any exception but its own
    `ToolError` with "Error executing tool <name>", so this once arrived empty."""
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", "mine")

    async def scenario():
        async with Client(mcp.build()) as session:
            return await _agent_reads(
                session, "set_work_item_policy", {"policy": {"max_attempts": 9}}
            )

    text = asyncio.run(scenario())
    assert "kraft: a worker session cannot act on its own work item (mine)" in text
    assert "report what you found instead" in text


def test_an_api_refusal_reaches_the_agent_as_the_line_the_cli_prints(app, repo):
    """A 404 and a 409 come back as the sentence `kraft <verb>` prints, once
    prefixed: the agent needs the reason to recover, and `kraft: kraft 404:`
    read as a stutter."""

    async def scenario():
        async with Client(mcp.build()) as session:
            unknown = await _agent_reads(session, "get_work_item", {"work_item_id": "nope"})
            await session.call_tool("ensure_repo", {"path": str(repo)})
            created = await session.call_tool("create_work_item", {"title": "t", "repo": str(repo)})
            wid = json.loads(created.content[0].text)["id"]
            again = await _agent_reads(session, "pause_work_item", {"work_item_id": wid})
            return unknown, again

    unknown, again = asyncio.run(scenario())
    assert unknown.endswith(": kraft: 404: unknown work item"), unknown
    assert again.endswith(": kraft: 409: work item is paused, not running"), again


def test_a_body_the_api_cannot_read_reaches_the_agent_as_one_line(app, repo):
    """FastAPI's own 422 is a list of pydantic errors, which reached the agent
    as a Python repr (`[{'type': 'literal_error', 'loc': [...`). It reads as
    the field and what is wrong with it, like every other refusal."""

    async def scenario():
        async with Client(mcp.build()) as session:
            await session.call_tool("ensure_repo", {"path": str(repo)})
            created = await session.call_tool("create_work_item", {"title": "t", "repo": str(repo)})
            wid = json.loads(created.content[0].text)["id"]
            return await _agent_reads(
                session,
                "add_review_comment",
                {"body": "b", "work_item_id": wid, "label": "must-fix"},
            )

    text = asyncio.run(scenario())
    assert text.endswith(": kraft: 422: label: Input should be 'must_fix', 'question' or 'nit'")
    assert "{'type'" not in text


def test_an_agent_cannot_file_an_item_with_a_blank_title(app, repo):
    """The board would show it as a bare dash. The CLI reaches the same
    route, so `kraft item create "   "` is refused the same way."""

    async def scenario():
        async with Client(mcp.build()) as session:
            await session.call_tool("ensure_repo", {"path": str(repo)})
            return await _agent_reads(session, "create_work_item", {"title": "", "repo": str(repo)})

    assert asyncio.run(scenario()).endswith(": kraft: 422: title cannot be empty")
    assert asyncio.run(client.list_work_items()) == []


#: A value of each JSON schema type, to fill a tool's required arguments.
_SAMPLE = {"string": "x", "integer": 1, "number": 1.0, "boolean": False, "object": {}, "array": []}


def _required_args(tool) -> dict:
    args = {}
    for name in tool.input_schema.get("required", []):
        prop = tool.input_schema["properties"][name]
        types = [prop["type"]] if "type" in prop else [a["type"] for a in prop["anyOf"]]
        args[name] = _SAMPLE[next(t for t in types if t != "null")]
    return args


def test_every_tool_hands_the_agent_a_refusals_reason(monkeypatch):
    """Not only the tools above: every client call refuses here, and every tool
    the server registers must hand the agent the reason."""

    async def refuse(*args, **kwargs):
        raise ValueError("kraft 409: refused for the test")

    for name, fn in list(vars(client).items()):
        if inspect.iscoroutinefunction(fn):
            monkeypatch.setattr(client, name, refuse)
    server = mcp.build()

    async def scenario():
        async with Client(server) as session:
            return {
                tool.name: await _agent_reads(session, tool.name, _required_args(tool))
                for tool in await server.list_tools()
            }

    for tool, text in asyncio.run(scenario()).items():
        assert text.endswith(": kraft: 409: refused for the test"), (tool, text)


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


def test_ensure_repo_through_the_mcp_tool_takes_the_repos_own_commands(app, tmp_path):
    """An agent that read the repo's README can connect it with the commands
    the repo documents, rather than editing repos.yaml after the probe."""
    repo = make_repo(tmp_path)
    args = {"path": str(repo), "test_command": "make check", "setup_command": "./configure"}
    out = json.loads(asyncio.run(mcp.build().call_tool("ensure_repo", args)).content[0].text)
    assert (out["test_command"], out["setup_command"]) == ("make check", "./configure")


def test_ensure_repo_with_no_tests_through_the_mcp_tool_is_saved_disabled(app, tmp_path):
    """No tests means every work item passes verification untested: a person
    enables that, not an agent's `test_command=""`."""
    repo = make_repo(tmp_path)
    args = {"path": str(repo), "test_command": ""}
    out = json.loads(asyncio.run(mcp.build().call_tool("ensure_repo", args)).content[0].text)
    assert (out["test_command"], out["enabled"]) == ("", False)


def test_approve_gate_forwards_the_digest_the_artifact_carried(monkeypatch):
    """Kraft-ec66w: a chain revision's approval is refused without it."""
    seen = {}

    async def fake(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {"id": "w1", "status": "active"}

    monkeypatch.setattr(mcp.client, "approve_gate", fake)
    asyncio.run(mcp.build().call_tool("approve_gate", {"work_item_id": "w1", "digest": "d1"}))
    assert seen["kwargs"].get("digest") == "d1"


def test_submit_review_says_only_a_human_should_decide():
    """The same warning `approve_gate`'s docstring carries."""
    tool = next(t for t in _tools() if t.name == "submit_review")
    assert "only a human should decide" in tool.description.lower()


@pytest.mark.parametrize(
    "tool, client_fn, args",
    [
        ("list_threads", "threads", {"work_item_id": "w1", "open_only": True}),
        (
            "compare_changes",
            "compare",
            {"work_item_id": "w1", "from_": "base", "to": "latest", "ignore_whitespace": True},
        ),
        (
            "add_review_comment",
            "add_review_comment",
            {"body": "hi", "work_item_id": "w1", "file_path": "a.py", "label": "must_fix"},
        ),
        ("resolve_thread", "resolve_thread", {"thread_id": "t1"}),
        ("reopen_thread", "reopen_thread", {"thread_id": "t1"}),
        ("submit_review", "submit_review", {"outcome": "comment", "work_item_id": "w1"}),
        (
            "add_review_comment",
            "add_review_comment",
            {
                "body": "hi",
                "work_item_id": "w1",
                "file_path": "a.py",
                "start_line": 3,
                "end_line": 2,
                "side": "new",
                "start_side": "old",
                "quote": "-a\n+b",
            },
        ),
    ],
)
def test_each_review_tool_delegates_to_its_client_function(monkeypatch, tool, client_fn, args):
    """Not just that some client function fired: the tool forwards `args` to
    it (each tool is a thin positional-argument relay, so a swapped or
    dropped parameter must show up in the real function's own bound names)."""
    real = getattr(client, client_fn)
    seen = {}
    result = [] if tool == "list_threads" else {"id": "w1"}

    async def fake(*fn_args, **fn_kwargs):
        bound = inspect.signature(real).bind(*fn_args, **fn_kwargs)
        seen.update(bound.arguments)
        return result

    monkeypatch.setattr(mcp.client, client_fn, fake)
    asyncio.run(mcp.build().call_tool(tool, args))
    assert seen and all(seen.get(k) == v for k, v in args.items())


def test_a_crash_is_still_a_crash_with_its_traceback_in_the_log(monkeypatch, caplog):
    """Only Kraft's refusals are turned into reasons. A bug's own text stays on
    the server, and its traceback reaches the log."""

    async def crash(*args, **kwargs):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(client, "get_work_item", crash)

    async def scenario():
        async with Client(mcp.build()) as session:
            return await _agent_reads(session, "get_work_item", {"work_item_id": "w1"})

    assert asyncio.run(scenario()) == "Error executing tool get_work_item"
    logged = [r for r in caplog.records if r.levelname == "ERROR" and r.exc_info]
    assert logged and isinstance(logged[0].exc_info[1].__cause__, RuntimeError)


def test_a_sync_tool_is_refused_when_it_is_registered():
    """The refusal wrapper awaits the tool, so a sync one would fail on every
    call; it fails here instead, where its author sees it."""

    def sync_tool() -> dict:
        return {}

    with pytest.raises(TypeError, match="sync_tool must be `async def`"):
        mcp._Server("kraft").tool()(sync_tool)


def test_get_attachment_reads_what_the_item_was_filed_with(monkeypatch):
    """The MCP door onto `GET /work-items/{id}/attachments/{kind}` (R10F-06),
    the only reader of a spec before its item starts."""
    seen = {}

    async def fake(kind, work_item_id=None):
        seen.update(kind=kind, work_item_id=work_item_id)
        return {"kind": kind, "content": "# spec"}

    monkeypatch.setattr(mcp.client, "attachment", fake)
    asyncio.run(mcp.build().call_tool("get_attachment", {"kind": "plan", "work_item_id": "w1"}))
    assert seen == {"kind": "plan", "work_item_id": "w1"}


@pytest.mark.parametrize(
    "tool, client_fn, args",
    [
        ("approve_gate", "approve_gate", {}),
        ("reject_gate", "reject_gate", {"note": "no"}),
        ("resume_work_item", "resume", {}),
        ("retry_work_item", "retry", {}),
        ("raise_budget", "raise_budget", {"budget_usd": 5.0}),
        ("skip_work_item", "skip", {}),
        ("complete_work_item", "complete", {"reason": "done"}),
        ("cancel_work_item", "cancel", {"reason": "no"}),
    ],
)
def test_an_action_tool_does_not_hand_the_agent_the_frozen_chain(
    monkeypatch, tool, client_fn, args
):
    """The route echoes the item's row, `materialized_chain` and
    all (~25 KB an agent pays for per decision). The tool answers as
    `get_work_item` does."""

    async def echo(*_a, **_kw):
        return {"id": "w1", "status": "active", "materialized_chain": "{" + "x" * 25_000 + "}"}

    async def trimmed(work_item_id=None, **_kw):
        return {"id": work_item_id, "status": "active", "next_node_id": "plan"}

    monkeypatch.setattr(mcp.client, client_fn, echo)
    monkeypatch.setattr(mcp.client, "get_work_item", trimmed)
    out = json.loads(asyncio.run(mcp.build().call_tool(tool, args)).content[0].text)
    assert out == {"id": "w1", "status": "active", "next_node_id": "plan"}
