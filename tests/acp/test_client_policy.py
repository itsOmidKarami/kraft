"""Kraft's client under a tool policy: the gate decides each ask, the backstop catches the rest
(`kraft.acp.client`'s permission half, with the fake agent and a fake gate)."""

import json

import pytest
from support.acp import events as _events
from support.acp import fake_gate, recorded, run_inprocess

TOOLS = {"read": ["Read"], "shell": ["Bash"], "execute": ["Bash"]}


def _answers(tmp_path):
    return [
        m for m in recorded(tmp_path) if str(m.get("id", "")).startswith("ask-") and "result" in m
    ]


@pytest.mark.parametrize(
    ("call", "decisions", "answer", "gate_saw"),
    [
        ({"id": "c1", "name": "read", "kind": "read"}, {}, "once", "Read"),
        ({"id": "c1", "name": "shell", "kind": "execute"}, {"Bash": "deny"}, "no", "Bash"),
        ({"id": "c1", "kind": "execute"}, {}, "once", "Bash"),
        ({"id": "c1", "name": "Read", "kind": "read"}, {}, "once", "Read"),
    ],
    ids=["named-and-allowed", "named-and-denied", "keyed-on-kind", "kraft-name-passes-through"],
)
def test_an_ask_is_decided_by_the_gate_on_krafts_tool_names(
    tmp_path, monkeypatch, call, decisions, answer, gate_saw
):
    seen = []
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate(decisions, seen),
        calls=[call],
        policy=True,
        tool_names=TOOLS,
        harness_id="fake-acp",
    )
    assert code == 0
    assert _answers(tmp_path)[0]["result"]["outcome"] == {"outcome": "selected", "optionId": answer}
    tool, kw = seen[0]
    assert (tool, kw["mode"], kw["fail_closed"], kw["harness"]) == (
        gate_saw,
        "enforce",
        True,
        "fake-acp",
    )
    ask = _events(recs, "ask")[0]
    assert ask["kraft_tool"] == [gate_saw]
    assert ask["answer"] == ("allow_once" if answer == "once" else "reject_once")


def test_an_allowing_ask_with_only_an_always_option_is_answered_cancelled_never_always(
    tmp_path, monkeypatch
):
    run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,only_always",
        ask=fake_gate(),
        calls=[{"id": "c1", "name": "read", "kind": "read"}],
        policy=True,
        tool_names=TOOLS,
    )
    assert _answers(tmp_path)[0]["result"]["outcome"] == {"outcome": "cancelled"}


def test_without_a_tool_policy_an_ask_is_allowed_locally_and_never_reaches_the_gate(
    tmp_path, monkeypatch
):
    seen = []
    run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate({"Read": "deny"}, seen),
        calls=[{"id": "c1", "name": "read", "kind": "read"}],
        policy=False,
        tool_names=TOOLS,
    )
    assert seen == []
    assert _answers(tmp_path)[0]["result"]["outcome"]["optionId"] == "once"


def test_a_rejection_that_ends_the_agents_turn_is_a_clean_exit_with_its_stop_reason_logged(
    tmp_path, monkeypatch
):
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,end_on_reject",
        ask=fake_gate({"Bash": "deny"}),
        calls=[{"id": "c1", "name": "shell", "kind": "execute"}],
        policy=True,
        tool_names=TOOLS,
    )
    assert code == 0 and _events(recs, "stop")[0]["stop_reason"] == "cancelled"


async def _gate_down(*a, **kw):
    raise ConnectionError("kraft is not listening")


@pytest.mark.parametrize(
    ("fields", "call", "gate", "answer"),
    [
        ({}, {"id": "c1", "name": "read", "kind": "read"}, _gate_down, "no"),
        (
            {},
            {"id": "c1", "name": "read", "kind": "read"},
            fake_gate(default="deny", message="cannot resolve this session's policy"),
            "no",
        ),
        ({"allowlist": True}, {"id": "c1", "kind": "other"}, fake_gate(), "no"),
        ({"allowlist": True}, {"id": "c1", "kind": "fetch"}, fake_gate(), "no"),
        ({"allowlist": False}, {"id": "c1", "kind": "other"}, fake_gate(), "once"),
        (
            {"always_deny": ["Bash"]},
            {"id": "c1", "name": "shell", "kind": "execute"},
            fake_gate(),
            "no",
        ),
    ],
    ids=[
        "gate-unreachable",
        "gate-cannot-read-the-policy",
        "allowlist-unnameable-kind-other",
        "allowlist-unmapped-kind",
        "deny-list-lets-the-unnameable-through",
        "always-deny-wins-over-an-allowing-gate",
    ],
)
def test_the_client_fails_closed_and_rejects_what_the_policy_cannot_name(
    tmp_path, monkeypatch, fields, call, gate, answer
):
    run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=gate,
        calls=[call],
        policy=True,
        tool_names=TOOLS,
        **fields,
    )
    assert _answers(tmp_path)[0]["result"]["outcome"]["optionId"] == answer


def test_an_always_denied_tool_never_reaches_the_gate(tmp_path, monkeypatch):
    seen = []
    run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate({}, seen),
        calls=[{"id": "c1", "name": "shell", "kind": "execute"}],
        policy=True,
        tool_names=TOOLS,
        always_deny=["Bash"],
    )
    assert seen == []


CALL = [{"id": "c1", "name": "read", "kind": "read"}]  # asks only when the agent was told to


@pytest.mark.parametrize(
    ("flags", "fields", "resume", "setter"),
    [
        ("", {"agent_env": {"FAKE_ACP_ASK_ALL": "1"}}, None, None),
        (
            "mode_option",
            {"asks_config": {"mode": "default"}},
            None,
            ("session/set_config_option", "value", "default"),
        ),
        (
            "mode_option",
            {"asks_mode": "default"},
            None,
            ("session/set_config_option", "value", "default"),
        ),
        ("", {"asks_mode": "default"}, None, ("session/set_mode", "modeId", "default")),
        (
            "mode_option",
            {"asks_mode": "default"},
            "old-session",
            ("session/set_config_option", "value", "default"),
        ),
    ],
    ids=[
        "env",
        "config",
        "mode-as-config-option",
        "mode-as-set-mode",
        "applied-on-a-resumed-launch-too",
    ],
)
def test_asks_everything_makes_the_agent_ask_on_every_launch(
    tmp_path, monkeypatch, flags, fields, resume, setter
):
    run_inprocess(
        tmp_path,
        monkeypatch,
        flags,
        ask=fake_gate(),
        calls=CALL,
        policy=True,
        tool_names=TOOLS,
        resume=resume,
        **fields,
    )
    assert len(_answers(tmp_path)) == 1  # the agent asked
    if setter:
        method, key, value = setter
        assert any(
            m.get("method") == method and m["params"].get(key) == value for m in recorded(tmp_path)
        )


def test_a_rejected_default_call_ends_failed_not_as_a_violation(tmp_path, monkeypatch):
    # No scripted calls: the fake's own default `tc-1` (execute -> Bash) asks because the agent
    # was told to.
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate({"Bash": "deny"}),
        policy=True,
        tool_names=TOOLS,
    )
    updates = [
        r["msg"]["params"]["update"]
        for r in recs
        if r.get("dir") == "from_agent" and r.get("msg", {}).get("method") == "session/update"
    ]
    tc1 = [u["status"] for u in updates if u.get("toolCallId") == "tc-1" and u.get("status")]
    assert tc1[-1] == "failed"  # the rejection held: the call did not run
    assert code == 0 and not [r for r in recs if r.get("event") == "policy_violation"]


def test_the_same_launch_without_asks_everything_makes_no_ask(tmp_path, monkeypatch):
    run_inprocess(
        tmp_path, monkeypatch, "", ask=fake_gate(), calls=CALL, policy=False, tool_names=TOOLS
    )
    assert _answers(tmp_path) == []


def test_an_agent_that_refuses_the_asking_mode_ends_the_launch_as_config_error(
    tmp_path, monkeypatch
):
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "mode_option",
        ask=fake_gate(),
        calls=CALL,
        policy=True,
        tool_names=TOOLS,
        asks_mode="paranoid",
    )
    assert code == 78 and "asks_everything mode" in _events(recs, "config_error")[0]["message"]


def test_a_withdrawn_ask_is_answered_cancelled_while_the_gate_is_still_thinking(
    tmp_path, monkeypatch
):
    # The gate never answers: only an ask that races the gate against its withdrawal can end.
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate(delay=3600),
        calls=[{"id": "c1", "name": "read", "kind": "read", "withdraw": True}],
        policy=True,
        tool_names=TOOLS,
    )
    assert code == 0
    assert _answers(tmp_path)[0]["result"]["outcome"] == {"outcome": "cancelled"}
    assert _events(recs, "ask_withdrawn") and not _events(recs, "ask")


def test_two_asks_in_flight_are_both_answered(tmp_path, monkeypatch):
    # A serial client (one ask handled to the end before the next is read) answers both too, so
    # the answers alone cannot fail it: assert the two gate calls were *in flight together*.
    calls = [
        {"id": "c1", "name": "read", "kind": "read", "wait": False},
        {"id": "c2", "name": "shell", "kind": "execute", "wait": False},
    ]
    gate = fake_gate({"Bash": "deny"}, delay=0.3)
    run_inprocess(
        tmp_path, monkeypatch, "ask_all", ask=gate, calls=calls, policy=True, tool_names=TOOLS
    )
    got = {m["id"]: m["result"]["outcome"]["optionId"] for m in _answers(tmp_path)}
    assert got == {"ask-c1": "once", "ask-c2": "no"}
    assert gate.peak == 2


def test_the_log_is_not_silent_while_an_ask_waits_on_the_gate(tmp_path, monkeypatch):
    _, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate(delay=0.6),
        calls=[{"id": "c1", "name": "read", "kind": "read"}],
        policy=True,
        tool_names=TOOLS,
        heartbeat_s=0.1,
    )
    assert len(_events(recs, "heartbeat")) >= 3


def _violation(recs):
    return [r for r in recs if r.get("event") == "policy_violation"]


@pytest.mark.parametrize(
    ("calls", "decisions", "fields", "violated"),
    [
        (
            [{"id": "c1", "name": "shell", "kind": "execute", "ask": False}],
            {"Bash": "deny"},
            {},
            True,
        ),
        ([{"id": "c1", "name": "read", "kind": "read", "ask": False}], {}, {}, False),
        (
            [{"id": "c1", "name": "read", "kind": "read", "ask": False}],
            {"Read": "deny"},
            {"settles_itself": ["Read"]},
            False,
        ),
        ([{"id": "c1", "kind": "other", "ask": False}], {}, {"allowlist": True}, True),
        (
            [{"id": "c1", "name": "shell", "kind": "execute", "run_anyway": True}],
            {"Bash": "deny"},
            {},
            True,
        ),
        ([{"id": "c1", "name": "read", "kind": "read"}], {}, {}, False),
        ([{"id": "c1", "name": "shell", "kind": "execute"}], {"Bash": "deny"}, {}, False),
        ([{"id": "c1", "name": "read", "kind": "read", "withdraw": True}], {}, {}, False),
        (
            [{"id": "c1", "name": "read", "kind": "read", "withdraw": True, "run_anyway": True}],
            {},
            {},
            True,
        ),
    ],
    ids=[
        "unasked-and-denied",
        "unasked-but-allowed",
        "unasked-but-settled-by-the-agent",
        "unasked-and-unnameable-under-an-allowlist",
        "ran-after-a-rejection",
        "asked-and-allowed-after-an-early-in_progress",
        "asked-rejected-and-failed",
        "withdrawn-ask-that-stays-unrun",
        "withdrawn-ask-that-runs-anyway",
    ],
)
def test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow(
    tmp_path, monkeypatch, calls, decisions, fields, violated
):
    # delay: a withdrawn ask must always be read before the gate answers (else `runs-anyway`
    # could see allow_once recorded first and expect 77 for the wrong reason, or flake).
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate(decisions, delay=0.3),
        calls=calls,
        policy=True,
        tool_names=TOOLS,
        **fields,
    )
    assert bool(_violation(recs)) is violated
    assert code == (77 if violated else 0)
    if violated:
        # the agent's own result is never overwritten: the stop comes from the logged event
        assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


def test_a_violation_with_no_result_file_writes_a_failed_one_naming_it(tmp_path, monkeypatch):
    code, _ = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,noresult",
        ask=fake_gate({"Bash": "deny"}),
        calls=[{"id": "c1", "name": "shell", "kind": "execute", "ask": False}],
        policy=True,
        tool_names=TOOLS,
    )
    body = json.loads((tmp_path / "result.json").read_text())
    assert code == 77 and body["status"] == "failed" and "Bash ran without asking" in body["error"]


def test_a_launch_without_a_policy_has_no_backstop(tmp_path, monkeypatch):
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "",
        ask=fake_gate(default="deny"),
        calls=[{"id": "c1", "name": "shell", "kind": "execute", "ask": False}],
        policy=False,
        tool_names=TOOLS,
    )
    assert code == 0 and not _violation(recs)
