"""The gate's enforce mode and named grants (Kraft-4in7z).

Enforce is what a before-every-call hook asks: policy decides deny or allow,
everything else is no opinion -- the CLI's own classifier decides -- and is
not logged. Grants are honoured in both modes; `prompt` (Claude's
`--permission-prompt-tool`) is otherwise unchanged.
"""

from __future__ import annotations

import pytest
from support.permissions import ask, events_of, seed_session, templates

_PUSH = {"command": "git push"}
_LS = {"command": "ls"}


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    return templates(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("policy", "tool", "input", "behavior", "grant"),
    [
        ({"deny_tools": ["Bash"]}, "Bash", _LS, "deny", None),
        ({"grants": ["git-push"]}, "Bash", _PUSH, "allow", "git-push"),
        ({"allowed_tools": ["Read"]}, "Read", {}, "allow", None),
        ({"allowed_tools": ["Read"]}, "Bash", _LS, "deny", None),
        ({}, "Bash", _LS, "no_opinion", None),
        # A grant never lifts a deny.
        ({"deny_tools": ["Bash"], "grants": ["git-push"]}, "Bash", _PUSH, "deny", "git-push"),
        # A call the grant doesn't cover is no opinion, not the grant's allow.
        ({"grants": ["git-push"]}, "Bash", {"command": "git push; rm -rf x"}, "no_opinion", None),
    ],
    ids=[
        "denied",
        "granted",
        "allowlisted",
        "outside-the-allowlist",
        "unbounded",
        "deny-beats-grant",
        "not-the-granted-call",
    ],
)
def test_enforce_answers_from_policy_and_logs_only_its_decisions(
    client, policy, tool, input, behavior, grant
):
    seed_session(policy=policy)
    body = ask(client, tool, input=input, mode="enforce").json()
    assert body["behavior"] == behavior, body
    logged = events_of(client, "permission_decision")
    if behavior == "no_opinion":
        assert body == {"behavior": "no_opinion"}
        assert logged == []
    else:
        [event] = logged
        assert (event["tool"], event["decision"], event["grant"]) == (tool, behavior, grant)


def test_enforce_events_name_the_harness_and_its_own_tool_name(client):
    seed_session(policy={"deny_tools": ["Write"]})
    ask(client, "Bash", mode="enforce", harness="cursor", cli_tool="Shell")
    ask(client, "Write", input={}, mode="enforce", harness="cursor", cli_tool="Edit")
    [event] = events_of(client, "permission_decision")
    assert (event["tool"], event["decision"], event["harness"], event["cli_tool"]) == (
        "Write",
        "deny",
        "cursor",
        "Edit",
    )


def test_enforce_reports_an_unresolvable_policy_rather_than_deciding(client):
    """The hook, not the route, knows whether its launch had an allowlist
    (`--fail-closed`), so the route reports and logs nothing."""
    seed_session(hook_point="implementation.main.nowhere")
    body = ask(client, mode="enforce").json()
    assert body["behavior"] == "unresolved"
    assert "cannot resolve" in body["message"], body
    assert events_of(client, "permission_decision") == []


def test_prompt_mode_honours_a_grant_under_an_allowlist(client):
    """A Claude task whose allowlist omits Bash still gets its granted push,
    and only that."""
    seed_session(policy={"allowed_tools": ["Read"], "grants": ["git-push"]})
    assert ask(client, input=_PUSH).json() == {"behavior": "allow", "updatedInput": _PUSH}
    assert ask(client, input=_LS).json()["behavior"] == "deny"
    assert [(e["decision"], e["grant"]) for e in events_of(client, "permission_decision")] == [
        ("allow", "git-push"),
        ("deny", None),
    ]


def test_prompt_mode_never_lets_a_grant_lift_a_deny(client):
    seed_session(policy={"deny_tools": ["Bash"], "grants": ["git-push"]})
    assert ask(client, input=_PUSH).json()["behavior"] == "deny"
