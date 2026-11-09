"""The conformance probe's judgement of one session log (spec §9)."""

import pytest

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
    [("refusal", 0, "mismatches", "stopReason"), ("end_turn", 3, "problems", "exited 3")],
    ids=["refusal", "client-exit"],
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
