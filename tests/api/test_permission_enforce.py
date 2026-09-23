"""The gate's enforce mode and named grants (Kraft-4in7z).

Enforce is what a before-every-call hook asks: policy decides deny or allow,
everything else is no opinion -- the CLI's own classifier decides -- and is
not logged. Grants are honoured in both modes; `prompt` (Claude's
`--permission-prompt-tool`) is otherwise unchanged.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from support.permissions import PATH, ask, events_of, seed_session, templates

from kraft import harness, permission_hooks

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
    """A fail-open hook renders `unresolved` as no opinion: not a decision,
    so nothing is logged."""
    seed_session(hook_point="implementation.main.nowhere")
    body = ask(client, mode="enforce").json()
    assert body["behavior"] == "unresolved"
    assert body["message"] == "cannot resolve implementation.main.nowhere's policy", body
    assert events_of(client, "permission_decision") == []


def test_enforce_fail_closed_denies_an_unresolvable_policy_and_logs_it(client):
    """A hook installed `--fail-closed` (its launch has an allowlist) gets a
    deny it can't mistake for no opinion, and the timeline records it."""
    seed_session(hook_point="implementation.main.nowhere")
    body = ask(client, mode="enforce", fail_closed=True).json()
    assert body["behavior"] == "deny"
    assert "cannot resolve" in body["message"], body
    [event] = events_of(client, "permission_decision")
    assert event["decision"] == "deny"
    assert "cannot resolve" in event["reason"] and "nowhere" in event["reason"], event
    # The exception's text is logged, never handed to the worker (CodeQL).
    assert body["message"] == "cannot resolve implementation.main.nowhere's policy", body


def test_prompt_mode_honours_a_grant_under_an_allowlist(client):
    """The gate allows a granted push under an allowlist that omits Bash, and
    only that. This pins the gate's decision, not a real Claude launch: such a
    launch has no Bash tool, so the push never reaches the gate
    (Kraft-4in7z.12)."""
    seed_session(policy={"allowed_tools": ["Read"], "grants": ["git-push"]})
    assert ask(client, input=_PUSH).json() == {"behavior": "allow", "updatedInput": _PUSH}
    assert ask(client, input=_LS).json()["behavior"] == "deny"
    assert [(e["decision"], e["grant"]) for e in events_of(client, "permission_decision")] == [
        ("allow", "git-push"),
        ("deny", None),
    ]


@pytest.mark.parametrize(
    ("hook_point", "behavior", "grant"),
    [("escalation", "allow", "git-push"), (PATH, "deny", None)],
    ids=["escalation", "chain-task"],
)
def test_an_escalation_turn_holds_the_default_push_grant(client, hook_point, behavior, grant):
    """Kraft-4in7z: an escalation turn's plain `git push` passes an allowlist
    without Bash on `defaults.escalation_grants`, which nothing set; a chain
    task at the same node gets no such default."""
    seed_session(hook_point=hook_point, policy={"allowed_tools": ["Read"]})
    assert ask(client, input=_PUSH).json()["behavior"] == behavior
    [event] = events_of(client, "permission_decision")
    assert (event["decision"], event["grant"]) == (behavior, grant)


def test_prompt_mode_never_lets_a_grant_lift_a_deny(client):
    seed_session(policy={"deny_tools": ["Bash"], "grants": ["git-push"]})
    assert ask(client, input=_PUSH).json()["behavior"] == "deny"


def test_cursor_shell_through_its_hook_is_denied_by_deny_tools_bash(client):
    """Review focus 5 (Kraft-4in7z): Cursor names its shell tool `Shell`. Its
    hook, with the tool names its bundled harness declares, asks the real
    gate, which checks it as `Bash` against `deny_tools: [Bash]` -- a deny
    Cursor receives, not a no opinion its classifier would wave through."""
    seed_session(policy={"deny_tools": ["Bash"]})

    async def gate(tool, input, tool_use_id=None, **kw):
        # The TestClient waits on its own portal thread: off this loop's.
        r = await asyncio.to_thread(ask, client, tool, input=input, tool_use_id=tool_use_id, **kw)
        return r.json()

    payload = {"tool_name": "Shell", "tool_input": {"command": "ls"}, "tool_use_id": "u1"}
    out, code = permission_hooks.answer_hook(
        "cursor",
        json.dumps(payload),
        harness.load(None).valid["cursor"].tool_names,
        fail_closed=False,
        ask=gate,
    )
    assert (json.loads(out)["permission"], code) == ("deny", 0)
    [event] = events_of(client, "permission_decision")
    assert (event["tool"], event["cli_tool"], event["decision"]) == ("Bash", "Shell", "deny")


@pytest.mark.parametrize(
    ("policy", "behavior"),
    [
        ({"deny_tools": ["Edit"]}, "deny"),
        ({"allowed_tools": ["Write"]}, "deny"),
        ({"allowed_tools": ["Write", "Edit"]}, "allow"),
    ],
    ids=["either-name-denied", "only-one-listed", "both-listed"],
)
def test_a_call_that_is_two_tools_needs_both(client, policy, behavior):
    """Cursor's `Write` also edits (probe 2026-09-23): it reaches the gate as
    Write plus `also: [Edit]`, denied if either is denied and allowed under an
    allowlist only when both are listed."""
    seed_session(policy=policy)
    body = ask(client, "Write", input={}, mode="enforce", also=["Edit"]).json()
    assert body["behavior"] == behavior, body
