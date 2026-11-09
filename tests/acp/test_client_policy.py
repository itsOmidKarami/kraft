"""Kraft's client under a tool policy: the gate decides each ask, the backstop catches the rest
(`kraft.acp.client`'s permission half, with the fake agent and a fake gate)."""

import json
import signal
import subprocess

import pytest
from pydantic import ValidationError
from support.acp import client_argv, client_env, fake_gate, recorded, run_inprocess, spec
from support.acp import events as _events

from kraft.acp import log
from kraft.acp.client import Client
from kraft.acp.spec import LaunchSpec

TOOLS = {"read": ["Read"], "write": ["Write"], "shell": ["Bash"], "execute": ["Bash"]}


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


def _prompts(tmp_path):
    return [m["params"] for m in recorded(tmp_path) if m.get("method") == "session/prompt"]


def _text(prompt):
    return prompt["prompt"][0]["text"]


def test_a_rejection_that_ends_the_turn_is_continued_on_the_same_session_until_done(
    tmp_path, monkeypatch
):
    # OpenCode ends the turn `cancelled` after a rejected ask (spec §10); the task is not over.
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,end_on_reject",
        ask=fake_gate({"Write": "deny", "Bash": "deny"}, message="not in this task's allowlist"),
        calls=[
            {"id": "c1", "name": "write", "kind": "edit"},
            {"id": "c2", "name": "shell", "kind": "execute"},
        ],
        policy=True,
        tool_names=TOOLS,
    )
    assert code == 0
    assert [e["stop_reason"] for e in _events(recs, "stop")] == [
        "cancelled",
        "cancelled",
        "end_turn",
    ]
    assert [a["kraft_tool"] for a in _events(recs, "ask")] == [["Write"], ["Bash"]]
    first, second, third = _prompts(tmp_path)
    assert first["sessionId"] == second["sessionId"] == third["sessionId"]
    assert "Write" in _text(second) and "not in this task's allowlist" in _text(second)
    assert "not available in this task" in _text(second) and "Bash" not in _text(second)
    # the agent also saw its other queued calls fail "declined": those were cancelled, not denied
    assert "cancelled with it, not denied by Kraft's policy" in _text(second)
    assert "can be tried again" in _text(second)
    assert "Bash" in _text(third) and "Write" not in _text(third)
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}
    assert not _events(recs, "policy_violation") and not _events(recs, "rejection_limit")


def test_continuations_are_capped_and_the_stop_names_the_tool_and_the_limit(tmp_path, monkeypatch):
    calls = [{"id": f"c{n}", "name": "shell", "kind": "execute"} for n in (1, 2, 3)]
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,end_on_reject",
        ask=fake_gate({"Bash": "deny"}),
        calls=calls,
        policy=True,
        tool_names=TOOLS,
        deny_tools=["Bash"],
        max_continuations=1,
    )
    message = "the agent ended its turn after Kraft rejected Bash (deny_tools) 2 times"
    assert code == 77 and len(_prompts(tmp_path)) == 2
    assert _events(recs, "rejection_limit")[0]["message"] == message
    assert json.loads((tmp_path / "result.json").read_text()) == {
        "status": "failed",
        "error": message,
    }
    text = "".join(json.dumps(r) + "\n" for r in recs).replace('"kraft_acp": 1', '"kraft_acp":1')
    assert log.cause(text) == message


def test_a_pause_that_lands_as_a_rejected_turn_ends_is_not_continued_past(tmp_path, monkeypatch):
    settle = Client.settle

    async def settle_then_pause(self):
        await settle(self)
        self.interrupted.set()

    monkeypatch.setattr(Client, "settle", settle_then_pause)
    code, _ = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,end_on_reject",
        ask=fake_gate({"Bash": "deny"}),
        calls=[{"id": "c1", "name": "shell", "kind": "execute"}],
        policy=True,
        tool_names=TOOLS,
    )
    assert code == 130 and len(_prompts(tmp_path)) == 1


def test_a_pause_during_a_continuation_still_ends_130(tmp_path):
    # No gate in a bare subprocess: every ask is rejected (fail-closed), which ends the turn.
    env = client_env(
        tmp_path,
        "ask_all,end_on_reject,slow_continue",
        KRAFT_FAKE_ACP_CALLS=json.dumps([{"id": "c1", "name": "shell", "kind": "execute"}]),
    )
    proc = subprocess.Popen(
        client_argv(spec(tmp_path, policy=True, tool_names=TOOLS)),
        stdout=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    for line in proc.stdout:  # the second prompt is out: the continuation is under way
        if (rec := next(log.records([line]), None)) and rec.get("event") == "continuing":
            break
    proc.send_signal(signal.SIGINT)
    proc.communicate(timeout=30)
    assert proc.returncode == 130 and len(_prompts(tmp_path)) == 2


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
        timeout=20,  # a regression waits on the gate for good: fail fast, not after minutes
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
        (
            [{"id": "c1", "name": "shell", "kind": "execute", "ask": False, "end": "failed"}],
            {"Bash": "deny"},
            {},
            True,
        ),
        (
            [{"id": "c1", "name": "shell", "kind": "execute", "ask": False, "end": "failed"}],
            {},
            {},
            False,
        ),
        ([{"id": "c1", "kind": "read", "late_name": "shell"}], {"Bash": "deny"}, {}, True),
        ([{"id": "c1", "kind": "read", "late_name": "read"}], {"Bash": "deny"}, {}, False),
        (
            [
                {"id": "c1", "name": "glob", "kind": "search", "end": "declined"},
                {"id": "c2", "name": "shell", "kind": "execute"},
            ],
            {"Glob": "deny", "Bash": "deny"},
            {"allowlist": True, "flags": "ask_all,end_on_reject"},
            False,
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
        "unasked-and-failed-after-running",
        "unasked-failed-but-allowed",
        "a-late-name-makes-an-allowed-call-another-tool",
        "a-late-name-that-is-the-same-tool",
        "unasked-and-cancelled-by-a-rejection-that-ended-the-turn",
    ],
)
def test_the_backstop_ends_a_session_whose_agent_ran_what_the_policy_does_not_allow(
    tmp_path, monkeypatch, calls, decisions, fields, violated
):
    # delay: a withdrawn ask must always be read before the gate answers (else `runs-anyway`
    # could see allow_once recorded first and expect 77 for the wrong reason, or flake).
    fields = dict(fields)
    flags = fields.pop("flags", "ask_all")
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        flags,
        ask=fake_gate(decisions, delay=0.3),
        calls=calls,
        policy=True,
        tool_names={**TOOLS, "search": ["Glob"], "glob": ["Glob"]},
        **fields,
    )
    assert bool(_violation(recs)) is violated
    assert code == (77 if violated else 0)
    if violated:
        # the agent's own result is never overwritten: the stop comes from the logged event
        assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


@pytest.mark.parametrize("precreated", [False, True], ids=["absent", "pre-created-empty"])
def test_a_violation_with_no_result_writes_a_failed_one_naming_it(
    tmp_path, monkeypatch, precreated
):
    if precreated:  # Kraft creates the session's empty result file before the agent starts
        (tmp_path / "result.json").write_text("")
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


def test_a_violation_that_meets_a_pause_keeps_the_pauses_code_and_is_still_logged(
    tmp_path, monkeypatch
):
    violate = Client.violate

    def violate_and_pause(self, message):
        violate(self, message)
        self.interrupted.set()

    monkeypatch.setattr(Client, "violate", violate_and_pause)
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all,slow",  # the turn is still running when both arrive
        ask=fake_gate({"Bash": "deny"}),
        calls=[{"id": "c1", "name": "shell", "kind": "execute", "ask": False}],
        policy=True,
        tool_names=TOOLS,
    )
    assert code == 130
    assert "Bash ran without asking" in _violation(recs)[0]["message"]


def test_a_backstop_that_raises_is_a_violation_not_silence(tmp_path, monkeypatch):
    async def broken(self, cid):
        raise RuntimeError("boom")

    monkeypatch.setattr(Client, "backstop", broken)
    code, recs = run_inprocess(
        tmp_path,
        monkeypatch,
        "ask_all",
        ask=fake_gate(),
        calls=[{"id": "c1", "name": "read", "kind": "read", "ask": False}],
        policy=True,
        tool_names=TOOLS,
    )
    assert code == 77 and "backstop failed: RuntimeError('boom')" in _violation(recs)[0]["message"]


@pytest.mark.parametrize(
    "field", [{"heartbeat_s": 0}, {"max_continuations": -1}], ids=["heartbeat-zero", "negative-cap"]
)
def test_a_launch_spec_refuses_a_busy_loop_or_a_negative_cap(field):
    with pytest.raises(ValidationError):
        LaunchSpec(agent_argv=["a"], cwd="/", prompt="p", context="c", result_path="/r", **field)
