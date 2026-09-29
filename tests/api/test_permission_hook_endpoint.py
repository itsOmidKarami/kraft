"""`POST /worker-sessions/{id}/permission-hook`: a sandboxed session's hook,
answered in the daemon (sandbox part 2, P5). The same translators and gate
as the host's `kraft admin permission-hook`, with fail-closed taken from the
session's own policy rather than from the caller."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import pytest
from support.permissions import events_of, seed_session, templates

from kraft.worker.egress import EgressSession, PhaseLists

_CURSOR_SHELL = {"tool_name": "Shell", "tool_input": {"command": "ls"}, "tool_use_id": "u1"}
_CODEX_BASH = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/work"}


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    return templates(tmp_path, monkeypatch)


def _hook(client, harness, stdin, sid="s1"):
    return client.post(
        f"/api/worker-sessions/{sid}/permission-hook",
        # A caller's own fail_closed is not a field: the session's decides.
        json={"harness": harness, "stdin": stdin, "fail_closed": False},
    )


@pytest.mark.parametrize(
    ("harness", "payload", "decision"),
    [
        ("cursor", _CURSOR_SHELL, lambda out: out["permission"]),
        ("codex", _CODEX_BASH, lambda out: out["hookSpecificOutput"]["permissionDecision"]),
    ],
    ids=["cursor", "codex"],
)
def test_the_hook_is_answered_in_the_harness_own_shape_from_the_session_policy(
    client, harness, payload, decision
):
    seed_session(policy={"deny_tools": ["Bash"]})
    reply = _hook(client, harness, json.dumps(payload))
    assert reply.status_code == 200, reply.text
    assert reply.json()["code"] == 0
    assert decision(json.loads(reply.json()["body"])) == "deny"
    [event] = events_of(client, "permission_decision")
    assert (event["session_id"], event["tool"], event["harness"]) == ("s1", "Bash", harness)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [({"allowed_tools": ["Read"]}, {"permission": "deny"}), ({}, {})],
    ids=["allowlist-fails-closed", "unbounded-is-no-opinion"],
)
def test_fail_closed_comes_from_the_session_not_the_caller(client, policy, expected):
    """An unreadable payload is a deny exactly when the session's task holds
    an allowlist, as `KRAFT_PERMISSION_FAIL_CLOSED` is on the host."""
    seed_session(policy=policy)
    out = json.loads(_hook(client, "cursor", "not json").json()["body"])
    assert {k: v for k, v in out.items() if k == "permission"} == expected


@pytest.mark.api_client(host="0.0.0.0")
def test_a_sandboxed_hook_reaches_the_gate_through_the_worker_api(client, monkeypatch):
    """The daemon's own proxy (its lifespan hands it the app) answers a
    hook posted to http://kraft/w/permission-hook as the channel's session,
    through the real perimeter: with a password set, on the daemon's token."""
    monkeypatch.setattr(
        client.app.state, "access", {**client.app.state.access, "password_hash": "x"}
    )
    seed_session(policy={"deny_tools": ["Bash"]})
    proxy = client.app.state.egress_channels.proxy
    form = urlencode({"harness": "cursor", "stdin": json.dumps(_CURSOR_SHELL)}).encode()

    async def through_the_channel() -> bytes:
        async def record(payload):
            raise AssertionError(f"refused: {payload}")

        session = EgressSession("s1", PhaseLists("runtime", (), ()), record, "w1")
        server = await asyncio.start_server(
            lambda r, w: proxy.handle(r, w, session), "127.0.0.1", 0
        )
        reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname()[:2])
        writer.write(
            b"POST http://kraft/w/permission-hook HTTP/1.1\r\nHost: kraft\r\n"
            b"Content-Length: %d\r\n\r\n%s" % (len(form), form)
        )
        answer = await asyncio.wait_for(reader.read(), 10)
        writer.close()
        server.close()
        return answer

    head, _, body = client.portal.call(through_the_channel).partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200 "), head + body
    assert json.loads(body)["permission"] == "deny"
    client.headers["authorization"] = f"Bearer {client.app.state.mcp_token}"
    [event] = events_of(client, "permission_decision")
    assert (event["session_id"], event["tool"], event["cli_tool"]) == ("s1", "Bash", "Shell")
