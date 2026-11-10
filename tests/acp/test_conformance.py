"""The conformance probe's judgement of one session log (spec §9)."""

import asyncio
import json
import os

import pytest
from support.acp import PROBE_CALLS, probe_entry

from kraft import harness
from kraft.acp import conformance, log

TOOLS = {"read": "Read", "edit": "Write", "execute": "Bash"}


def entry(*, asks=True, reports=("tokens", "cost"), settles=()):
    body = {
        "id": "x",
        "kind": "acp",
        "command": ["x"],
        "reports": list(reports),
        "tool_names": TOOLS,
        "settles_itself": list(settles),
    }
    if asks:
        body["asks_everything"] = {"mode": "default"}
    return harness.Harness.from_mapping(body, where="test", path=None)


def session(calls, *, asked=(), tokens=True, cost=True, stop="end_turn"):
    """The log lines of one session: `calls` is [(id, name, kind, status)]; `asked` the ids that
    sent session/request_permission."""

    def from_agent(msg):
        return log.message("from_agent", {"jsonrpc": "2.0", **msg})

    out = [
        log.event("starting", agent=["x"]),
        from_agent({"id": 1, "result": {"agentInfo": {"name": "bridge", "version": "1.2.3"}}}),
    ]
    for cid, name, kind, status in calls:
        update = {"sessionUpdate": "tool_call", "toolCallId": cid, "kind": kind, "status": status}
        out.append(
            from_agent(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {**update, **({"name": name} if name else {})},
                    },
                }
            )
        )
        if cid in asked:
            out.append(
                from_agent(
                    {
                        "id": f"ask-{cid}",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "s",
                            "toolCall": {"toolCallId": cid},
                            "options": [],
                        },
                    }
                )
            )
    if cost:
        out.append(
            from_agent(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "usage_update",
                            "used": 9,
                            "size": 10,
                            "cost": {"amount": 0.01, "currency": "USD"},
                        },
                    },
                }
            )
        )
    result = {
        "stopReason": stop,
        **({"usage": {"inputTokens": 5, "outputTokens": 2}} if tokens else {}),
    }
    if stop is not None:  # None: the log is cut off before the prompt's result
        out.append(from_agent({"id": 3, "result": result}))
    return out


ALL = [
    ("c1", "read", "read", "completed"),
    ("c2", "edit", "edit", "completed"),
    ("c3", "execute", "execute", "completed"),
]


def test_a_session_that_matches_its_entry_is_clean():
    got = conformance.analyze(session(ALL, asked={"c1", "c2", "c3"}), entry())
    assert got.ok and got.agent == "bridge 1.2.3" and got.stop_reason == "end_turn"
    assert [(c.kraft, c.asked) for c in got.calls] == [
        (("Read",), True),
        (("Write",), True),
        (("Bash",), True),
    ]


@pytest.mark.parametrize(
    ("asked", "settles", "mismatch", "note"),
    [
        ({"c1", "c2"}, (), "Bash ran without asking", None),
        ({"c2", "c3"}, ("Read",), None, None),
        ({"c1", "c2", "c3"}, ("Read",), None, "asked about Read although"),
    ],
    ids=["unasked-and-undeclared", "unasked-and-declared-settled", "settled-but-asked"],
)
def test_the_probe_judges_each_call_by_what_the_entry_declares(asked, settles, mismatch, note):
    got = conformance.analyze(session(ALL, asked=asked), entry(settles=settles))
    assert (got.ok, any(mismatch in m for m in got.mismatches)) == (
        (mismatch is None),
        mismatch is not None,
    )
    assert (note is None) or any(note in n for n in got.notes)


def test_the_report_lists_every_tool_that_ran_unasked():
    got = conformance.analyze(session(ALL, asked={"c2"}), entry())
    assert got.unasked == ("Bash", "Read")


def test_an_entry_with_no_asks_everything_is_not_judged_on_asking():
    got = conformance.analyze(session(ALL), entry(asks=False))
    assert got.ok and any("asks_everything" in n for n in got.notes)


@pytest.mark.parametrize(
    ("calls", "mismatch"),
    [
        (ALL[:2], "no call for the shell command"),
        ([ALL[0], ALL[1], ("c3", "", "other", "completed")], "maps to no Kraft tool"),
        ([ALL[0], ALL[1], ("c3", "execute", "execute", "failed")], "Bash ended failed"),
    ],
    ids=["a-step-never-ran", "unmappable-call", "a-call-failed"],
)
def test_the_probe_names_a_step_that_did_not_happen(calls, mismatch):
    got = conformance.analyze(session(calls, asked={c[0] for c in calls}), entry())
    assert any(mismatch in m for m in got.mismatches)


@pytest.mark.parametrize(
    ("reports", "tokens", "cost", "mismatch", "note"),
    [
        (("tokens", "cost"), False, True, "declares tokens", None),
        (("tokens", "cost"), True, False, "declares cost", None),
        (("tokens",), True, True, None, "reports cost"),
        ((), True, True, None, "reports tokens"),
    ],
    ids=["tokens-missing", "cost-missing", "undeclared-cost", "undeclared-tokens"],
)
def test_the_probe_judges_reported_usage_by_the_entry(reports, tokens, cost, mismatch, note):
    lines = session(ALL, asked={"c1", "c2", "c3"}, tokens=tokens, cost=cost)
    got = conformance.analyze(lines, entry(reports=reports))
    assert (mismatch is None) or any(mismatch in m for m in got.mismatches)
    assert (note is None) or any(note in n for n in got.notes)
    assert (got.tokens_reported, got.cost_reported) == (tokens, cost)


@pytest.mark.parametrize(
    ("stop", "exit_code", "where", "text"),
    [
        ("refusal", 0, "mismatches", "stopReason"),
        ("end_turn", 3, "problems", "exited 3"),
        (None, 0, "problems", "no stopReason"),
    ],
    ids=["refusal", "client-exit", "log-cut-off-before-the-result"],
)
def test_a_turn_that_did_not_end_cleanly_is_reported(stop, exit_code, where, text):
    got = conformance.analyze(
        session(ALL, asked={"c1", "c2", "c3"}, stop=stop), entry(), exit_code=exit_code
    )
    assert not got.ok and any(text in m for m in getattr(got, where))


def test_a_client_error_event_is_a_problem_with_its_message():
    lines = [log.event("starting", agent=["x"]), log.event("error", message="login required")]
    got = conformance.analyze(lines, entry(), exit_code=1)
    assert not got.ok and "login required" in got.problems


def probe(monkeypatch, calls=PROBE_CALLS, flags="", **kw):
    monkeypatch.setenv("KRAFT_FAKE_ACP", flags)
    monkeypatch.setenv("KRAFT_FAKE_ACP_CALLS", json.dumps(calls))
    return asyncio.run(conformance.run(probe_entry(**kw.pop("entry", {})), **kw))


def test_the_probe_runs_the_client_against_the_agent_and_judges_the_log(monkeypatch):
    got = probe(monkeypatch)
    assert got.ok, (got.problems, got.mismatches)
    assert got.agent == "fake-acp-agent 0.0.0" and got.tokens_reported and got.cost_reported
    assert all(c.asked for c in got.calls)  # the entry's asking switch reached the agent


@pytest.mark.parametrize(
    ("flags", "calls", "entry", "where", "text"),
    [
        (
            "",
            [*PROBE_CALLS[:2], {**PROBE_CALLS[2], "ask": False}],
            {},
            "mismatches",
            "Bash ran without asking",
        ),
        (
            "",
            [*PROBE_CALLS[:2], {**PROBE_CALLS[2], "ask": False}],
            {"settles_itself": ["Bash"]},
            None,
            None,
        ),
        ("no_cost", PROBE_CALLS, {}, "mismatches", "declares cost"),
        ("auth", PROBE_CALLS, {}, "problems", "Authentication"),
    ],
    ids=["unasked-call", "unasked-but-declared", "no-cost-reported", "login-missing"],
)
def test_the_probe_reports_a_real_session_that_differs_from_its_entry(
    monkeypatch, flags, calls, entry, where, text
):
    got = probe(monkeypatch, calls, flags, entry=entry)
    if where is None:
        assert got.ok
    else:
        assert not got.ok and any(text in m for m in getattr(got, where))


def test_the_probe_sends_no_host_access_enforces_no_policy_and_sets_no_model(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAFT_FAKE_ACP_RECORD", str(tmp_path / "rec"))
    probe(monkeypatch)
    sent = [json.loads(x) for x in (tmp_path / "rec").read_text().splitlines()]
    assert sent[0]["params"]["clientCapabilities"] == {}
    assert not any(m.get("method") == "session/set_config_option" for m in sent)
    prompt = next(m for m in sent if m.get("method") == "session/prompt")
    text = prompt["params"]["prompt"][0]["text"]
    assert all(word in text for word in ("probe-notes.txt", "Glob", "Grep"))


def test_the_probe_enforces_nothing_even_when_the_entry_always_denies_a_tool(monkeypatch):
    # The mutation that matters: drop `policy=False` in `run` and the client, with an `always_deny`
    # entry, rejects Bash (reject_once, and the call that runs anyway is a policy violation).
    got = probe(monkeypatch, entry={"always_deny": ["Bash"]})
    assert got.ok, (got.problems, got.mismatches)
    recs = list(log.records(got.log))
    asks = [r for r in recs if r.get("event") == "ask"]
    assert len(asks) == 3 and all(a["answer"] == "allow_once" for a in asks)
    assert not [r for r in recs if r.get("event") == "policy_violation"]


def test_a_probe_session_that_never_ends_is_one_problem_not_a_hang_or_phantom_mismatches(
    monkeypatch,
):
    got = probe(monkeypatch, flags="slow", timeout_s=4)
    assert got.problems == ("the session did not end within 4s",)
    assert (got.mismatches, got.notes) == ((), ())  # not "no call for the read a file was seen"
    assert got.log  # what the client had logged before it was killed


def test_cancelling_the_probe_kills_the_client_and_the_agent(monkeypatch):
    """Ctrl-C reaches asyncio.run as a cancellation; the client runs in its own session, so the
    terminal's SIGINT never reaches it and `run` must kill its process group itself. The agent
    outlives its client (`outlive`: it ignores SIGTERM and stdin EOF), so a kill of the client
    alone leaves it running and this test fails."""
    started = []
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kw):
        started.append(await real(*args, **kw))
        return started[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    monkeypatch.setenv("KRAFT_FAKE_ACP", "slow,outlive")
    monkeypatch.setenv("KRAFT_FAKE_ACP_CALLS", json.dumps(PROBE_CALLS))

    def alive(pid):
        try:
            os.killpg(pid, 0)  # a group lives while any member, the agent included, does
        except ProcessLookupError:
            return False
        return True

    async def main():
        task = asyncio.ensure_future(conformance.run(probe_entry(), timeout_s=120))
        while not started:
            await asyncio.sleep(0.05)
        await asyncio.sleep(2)  # the client has started the agent
        pid = started[0].pid
        try:
            assert alive(pid)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(50):
                if not alive(pid):
                    return
                await asyncio.sleep(0.1)
            pytest.fail("the client's process group outlived the cancelled probe")
        finally:
            if alive(pid):
                os.killpg(pid, 9)  # never leave the slow agent behind, even when the test fails

    asyncio.run(main())
