"""Kraft answers a worker's permission prompts from its task's grant (Kraft-oor).

The policy is the `allowed_tools` the session's own launch resolved -- the V1
task, at the canonical path the session row carries, through the same
`resolve_agent_task` the launch used (Kraft-hwrks). The audit trail is a
`permission_decision` event, which is the only way the orchestrator ever learns
what a worker decided it was allowed to do.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from support.harness import (
    fake_harness_home,
    fake_templates_dir,
    v1_chain,
    write_harness_profiles,
)

from kraft import client as client_mod
from kraft import store
from kraft.adapters import agent as agent_mod
from kraft.client import transport

#: A real V1 hook point: the canonical path dispatch writes to
#: `worker_sessions.hook_point` (`executor/dispatch.py`), never a legacy hook.
_PATH = "implementation.main.work"


def _templates(tmp_path, monkeypatch):
    d = fake_templates_dir(tmp_path, "claude")
    monkeypatch.setenv("KRAFT_HOME", str(fake_harness_home(tmp_path, ["true"])))
    write_harness_profiles(d, {"fake": {"provider": "fake"}})
    return d


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """The `client` fixture's templates: a `fake` harness profile on `true`."""
    return _templates(tmp_path, monkeypatch)


def _seed_session(sid="s1", wid="w1", hook_point=_PATH, harness="fake"):
    """A V1 work item whose chain has one agent task at `_PATH`, and one
    pending session on `hook_point`, written straight to the database."""
    chain = v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "work", "kind": "agent", "harness": harness, "prompt": "Do it."},
                    {"id": "check", "kind": "subprocess", "command": "true"},
                ],
            }
        ],
        repo="/r",
    )
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        store.create_work_item(
            conn,
            id=wid,
            bead_id="B",
            title="t",
            repo="/r",
            chain_template=chain.chain.id,
            chain_definition="{}",
            materialized_chain=chain.to_json(),
        )
        store.create_session(
            conn,
            id=sid,
            work_item_id=wid,
            node_id="implementation",
            hook_point=hook_point,
            log_path="/tmp/kraft-test.log",
            result_path="/tmp/kraft-test.json",
        )
        conn.commit()
    finally:
        conn.close()


def _ask(client, tool_name="Bash", sid="s1", **extra):
    return client.post(
        f"/api/worker-sessions/{sid}/permission",
        json={"tool_name": tool_name, "input": {"command": "ls"}, **extra},
    )


def _resolving_to(monkeypatch, allowed):
    """The session's launch resolving `allowed` -- what a task or profile that
    declares an allowlist would hand `run_agent_task` as `--allowedTools`."""
    real = agent_mod.resolve_agent_task

    def resolve(*a, **kw):
        return real(*a, **kw)._replace(allowed_tools=tuple(allowed))

    monkeypatch.setattr(agent_mod, "resolve_agent_task", resolve)


def test_permission_request_allows_when_the_task_declares_no_allowlist(client):
    """`--permission-mode auto` resolves these asks silently today, and a V1
    task declares no allowlist of its own -- an unset `maxima.allowed_tools`
    bounds nothing (`policy-has-defaults-and-administrator-maxima`). The event
    says so, about the task the session actually is."""
    _seed_session()
    body = _ask(client).json()
    reasons = [
        e["payload"]["reason"]
        for e in client.get("/api/work-items/w1/events").json()
        if e["type"] == "permission_decision"
    ]
    assert body == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    assert reasons == [f"{_PATH} declares no allowed_tools"]


def test_permission_request_denies_a_tool_outside_the_task_allowlist(monkeypatch, client):
    """Kraft-hwrks: the gate read the legacy registry by `hook_point`, which on
    V1 is a task path no registry hook has, so it allowed everything."""
    _resolving_to(monkeypatch, ["Read", "Grep"])
    _seed_session()
    denied = _ask(client, "Bash").json()
    allowed = _ask(client, "Read").json()
    assert denied["behavior"] == "deny"
    assert _PATH in denied["message"], "a denial has to name the task"
    assert "Bash" in denied["message"]
    assert allowed == {"behavior": "allow", "updatedInput": {"command": "ls"}}


@pytest.mark.parametrize(
    ("hook_point", "harness"),
    [
        ("implementation.main.nowhere", "fake"),
        ("on.implementation.start", "fake"),
        (_PATH, "no_such_profile"),
        ("implementation.main.check", "fake"),
    ],
    ids=["unknown-path", "legacy-hook", "unresolvable-profile", "not-an-agent-task"],
)
def test_permission_request_denies_a_session_whose_grant_cannot_be_resolved(
    client, hook_point, harness
):
    """Not knowing the grant is not a grant: a session whose task or profile
    no longer resolves is denied, naming why, rather than read as declaring
    nothing."""
    _seed_session(hook_point=hook_point, harness=harness)
    body = _ask(client).json()
    assert body["behavior"] == "deny"
    assert "cannot resolve" in body["message"], body
    if hook_point.endswith(".check"):
        assert "is no agent task" in body["message"], body


def test_permission_request_allows_the_escalation_turn(client):
    """An escalation turn is not a chain task: it launches on a minimal binding
    with no allowlist (`escalate.dispatch`), so it declares none."""
    _seed_session(hook_point="escalation")
    body = _ask(client).json()
    assert body["behavior"] == "allow"


def test_permission_decision_is_recorded_as_an_event(monkeypatch, client):
    _resolving_to(monkeypatch, ["Read"])
    _seed_session()
    _ask(client, "Bash")
    _ask(client, "Read")
    events = [
        e
        for e in client.get("/api/work-items/w1/events").json()
        if e["type"] == "permission_decision"
    ]
    assert [e["payload"]["decision"] for e in events] == ["deny", "allow"]
    assert events[0]["payload"] == {
        "session_id": "s1",
        "node_id": "implementation",
        "tool": "Bash",
        "decision": "deny",
        "reason": events[0]["payload"]["reason"],
    }


def test_permission_request_on_an_unknown_session_is_404(monkeypatch, client):
    assert _ask(client, sid="nope").status_code == 404


def test_the_worker_side_fails_closed(monkeypatch):
    """A Kraft restart while an adopted worker runs on must not become a
    permission grant. Denying is exactly today's behaviour for an unanswered
    ask, so it regresses nothing."""

    async def dead(*_a, **_kw):
        raise ValueError("no Kraft server at http://127.0.0.1:8765 — start one with `kraft`")

    monkeypatch.setenv("KRAFT_SESSION_ID", "s1")
    monkeypatch.setattr(transport, "_post", dead)
    answer = asyncio.run(client_mod.permission_request("Bash", {"command": "ls"}))
    assert answer["behavior"] == "deny"
    assert "no Kraft server" in answer["message"]


def test_the_worker_side_denies_when_it_is_not_a_kraft_session(monkeypatch):
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    answer = asyncio.run(client_mod.permission_request("Bash", {}))
    assert answer["behavior"] == "deny"
