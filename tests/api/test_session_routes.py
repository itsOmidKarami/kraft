"""A session's routes are under its item. The flat routes stay for a client
built before per-item ids, and answer only an id exactly one session has
(spec "Item files and IDs", part 3)."""

from __future__ import annotations

import json

import pytest
from support import permissions
from support.permissions import templates_dir  # noqa: F401 -- the fixture

W1, W2 = "k7m2x9qa", "b4t1z8re"
_ASK = {"tool_name": "Bash", "input": {"command": "ls"}}
_SHELL = {"tool_name": "Shell", "tool_input": {"command": "ls"}, "tool_use_id": "u1"}
_HOOK = {"harness": "cursor", "stdin": json.dumps(_SHELL)}
#: Every ask is a decision, so every one is recorded: an unbounded policy
#: answers a hook with no opinion, which is not.
_POLICY = {"deny_tools": ["Bash"]}


def _sessions(tmp_path):
    """`abc123` in both items, `def456` in `W1` only, each logging `<wid>`."""
    permissions.seed_session(sid="seed1", wid=W1, policy=_POLICY)
    permissions.seed_session(sid="seed2", wid=W2, policy=_POLICY)
    for wid, sid in ((W1, "abc123"), (W2, "abc123"), (W1, "def456")):
        log = tmp_path / f"{wid}-{sid}.log"
        log.write_text(f"hello from {wid}\n")
        permissions.add_session(sid, wid, log_path=str(log))


@pytest.mark.parametrize(
    ("method", "path", "status", "owner"),
    [
        ("GET", f"/api/work-items/{W1}/sessions/abc123/log", 200, W1),
        ("GET", f"/api/work-items/{W2}/sessions/abc123/log", 200, W2),
        ("GET", f"/api/work-items/{W2}/sessions/def456/log", 404, None),
        ("GET", f"/api/work-items/{W1[:4]}/sessions/def456/log", 200, W1),
        ("GET", f"/api/work-items/{W1}/sessions/def4/log", 404, None),
        ("POST", f"/api/work-items/{W2}/sessions/abc123/permission", 200, W2),
        ("POST", f"/api/work-items/{W1}/sessions/abc123/permission-hook", 200, W1),
        ("GET", "/api/worker-sessions/def456/log", 200, W1),
        ("POST", "/api/worker-sessions/def456/permission", 200, W1),
        ("GET", "/api/worker-sessions/abc123/log", 404, None),
        ("POST", "/api/worker-sessions/abc123/permission", 404, None),
        ("POST", "/api/worker-sessions/abc123/permission-hook", 404, None),
        ("GET", "/api/worker-sessions/zzz999/log", 404, None),
    ],
    ids=[
        "nested",
        "nested-other-item",
        "nested-wrong-item",
        "nested-item-by-its-start",
        "nested-session-never-by-its-start",
        "nested-permission",
        "nested-permission-hook",
        "flat-unique",
        "flat-unique-permission",
        "flat-ambiguous",
        "flat-ambiguous-permission",
        "flat-ambiguous-permission-hook",
        "flat-unknown",
    ],
)
def test_a_session_route_names_one_session(client, tmp_path, method, path, status, owner):
    """The route answers for the one session it names, recording a decision
    on that session's own item; a flat route given an id two items share,
    or none has, points at the nested route instead of picking one."""
    _sessions(tmp_path)
    body = _HOOK if path.endswith("-hook") else _ASK if method == "POST" else None

    r = client.request(method, path, json=body)

    assert r.status_code == status, r.text
    if path.endswith("/log") and owner:
        assert f"hello from {owner}" in r.text
    elif owner:
        [event] = permissions.events_of(client, "permission_decision", wid=owner)
        assert event["session_id"] == path.split("/")[-2]
    elif path.startswith("/api/worker-sessions/"):
        assert "use /work-items/{wid}/sessions/{sid}" in r.json()["detail"]
    for other in {W1, W2} - {owner}:
        assert permissions.events_of(client, "permission_decision", wid=other) == []
