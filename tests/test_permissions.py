"""Kraft answers a worker's permission prompts from its task's grant (Kraft-oor).

The policy is the `allowed_tools` the session's own launch resolved -- the V1
task, at the canonical path the session row carries, through the same
`resolve_agent_task` the launch used (Kraft-hwrks). The audit trail is a
`permission_decision` event, which is the only way the orchestrator ever learns
what a worker decided it was allowed to do.
"""

from __future__ import annotations

import asyncio

import pytest
from support.permissions import PATH as _PATH
from support.permissions import ask as _ask
from support.permissions import events_of
from support.permissions import seed_session as _seed_session
from support.permissions import templates as _templates

from kraft import client as client_mod
from kraft.adapters import agent as agent_mod
from kraft.client import transport


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """The `client` fixture's templates: a `fake` harness profile on `true`."""
    return _templates(tmp_path, monkeypatch)


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
    reasons = [e["reason"] for e in events_of(client, "permission_decision")]
    assert body == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    assert reasons == [f"no layer of {_PATH}'s policy sets allowed_tools"]


@pytest.mark.parametrize(
    ("policy", "tool", "decision"),
    [
        ({"allowed_tools": ["Read"]}, "Read", "allow"),
        ({"allowed_tools": ["Read"]}, "Bash", "deny"),
        # An empty allowlist allows nothing -- it is not an absent one.
        ({"allowed_tools": []}, "Read", "deny"),
        # A deny list narrows whatever the allowlist permits, unbounded included.
        ({"deny_tools": ["Bash"]}, "Bash", "deny"),
        ({"deny_tools": ["Bash"]}, "Read", "allow"),
        ({"allowed_tools": ["Read", "Bash"], "deny_tools": ["Bash"]}, "Bash", "deny"),
    ],
    ids=[
        "allowlisted",
        "outside-the-allowlist",
        "empty-allowlist",
        "denied",
        "not-denied",
        "denied-beats-allowlisted",
    ],
)
def test_permission_request_answers_from_the_tasks_resolved_policy(client, policy, tool, decision):
    """Kraft-v4nrd: a V1 task's tool grant is its resolved policy's -- here
    the node scope's, which the task inherits -- not a field of its own."""
    _seed_session(policy=policy)
    body = _ask(client, tool).json()
    assert body["behavior"] == decision, body
    if decision == "deny":
        assert _PATH in body["message"] and tool in body["message"], body


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


@pytest.mark.parametrize(
    ("policy", "decision"),
    [(None, "allow"), ({"allowed_tools": ["Read"]}, "deny")],
    ids=["node-sets-no-allowlist", "outside-the-nodes-allowlist"],
)
def test_permission_request_answers_the_escalation_turn_from_its_nodes_policy(
    client, policy, decision
):
    """Kraft-l8ype: an escalation turn is node-scoped, so its asks are answered
    from the policy of the node its session sits at -- the one
    `escalate.dispatch` launched it under -- not allowed wholesale."""
    _seed_session(hook_point="escalation", policy=policy)
    body = _ask(client).json()
    assert body["behavior"] == decision, body


def test_permission_decision_is_recorded_as_an_event(monkeypatch, client):
    _resolving_to(monkeypatch, ["Read"])
    _seed_session()
    _ask(client, "Bash")
    _ask(client, "Read")
    events = events_of(client, "permission_decision")
    assert [e["decision"] for e in events] == ["deny", "allow"]
    assert events[0] == {
        "session_id": "s1",
        "node_id": "implementation",
        "tool": "Bash",
        "decision": "deny",
        "reason": events[0]["reason"],
        "harness": None,
        "cli_tool": None,
        "grant": None,
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
