"""`POST /worker-sessions/{id}/permission-hook`: a sandboxed session's hook,
answered in the daemon (sandbox part 2, P5). The same translators and gate
as the host's `kraft admin permission-hook`, with fail-closed taken from the
session's own policy rather than from the caller."""

from __future__ import annotations

import json

import pytest
from support.permissions import events_of, seed_session, templates

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
