"""`kraft.worker.session_mcp` at the protocol level: a real MCP client
reaching http://kraft/mcp through one session's channel, served by the
daemon's own proxy (its lifespan runs the server), answered by its gate."""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from support.permissions import events_of, seed_session, templates

from kraft.worker.egress import EgressSession, PhaseLists


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    return templates(tmp_path, monkeypatch)


@pytest.mark.api_client(host="0.0.0.0")
def test_a_permission_ask_reaches_the_gate_as_the_channel_session_alone(client, monkeypatch):
    """Through the real perimeter (a password set), on the daemon's token.
    The worker names another session, whose unbounded policy would allow
    the call: the gate is asked about the channel's session all the same."""
    monkeypatch.setattr(
        client.app.state, "access", {**client.app.state.access, "password_hash": "x"}
    )
    seed_session(policy={"deny_tools": ["Bash"]})
    seed_session(sid="s2", wid="w2")
    proxy = client.app.state.egress_channels.proxy

    async def through_the_channel():
        async def record(payload):
            raise AssertionError(f"refused: {payload}")

        session = EgressSession("s1", PhaseLists("runtime", (), ()), record, "w1")
        server = await asyncio.start_server(
            lambda r, w: proxy.handle(r, w, session), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        http = httpx2.AsyncClient(
            proxy=f"http://127.0.0.1:{port}",
            headers={"X-Kraft-Session-Id": "s2", "Authorization": "Bearer stolen"},
        )
        try:
            async with (
                http,
                streamable_http_client("http://kraft/mcp", http_client=http) as (read, write),
                ClientSession(read, write) as mcp,
            ):
                await mcp.initialize()
                tools = await mcp.list_tools()
                answer = await mcp.call_tool(
                    "permission_request", {"tool_name": "Bash", "input": {"command": "ls"}}
                )
        finally:
            server.close()
        return [t.name for t in tools.tools], answer

    names, answer = client.portal.call(through_the_channel)
    assert names == ["permission_request"]
    assert json.loads(answer.content[0].text)["behavior"] == "deny"
    client.headers["authorization"] = f"Bearer {client.app.state.mcp_token}"
    [event] = events_of(client, "permission_decision")
    assert (event["session_id"], event["tool"], event["decision"]) == ("s1", "Bash", "deny")
