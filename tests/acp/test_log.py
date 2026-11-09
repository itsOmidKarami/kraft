import json

import pytest

from kraft.acp import log


def test_records_keeps_only_this_formats_lines(tmp_path):
    lines = [
        log.event("starting", agent=["a"]),
        "plain text a CLI printed",
        json.dumps({"type": "result"}),
        log.message("from_agent", {"jsonrpc": "2.0", "id": 1, "result": {}}),
        log.stray("help text"),
    ]
    got = list(log.records(lines))
    assert [(r["dir"], r.get("event")) for r in got] == [
        ("client", "starting"),
        ("from_agent", None),
        ("stray", None),
    ]


def test_a_replayed_message_is_marked():
    rec = next(log.records([log.message("from_agent", {"jsonrpc": "2.0"}, replay=True)]))
    assert rec["replay"] is True


def test_config_error_names_the_first_one(tmp_path):
    path = tmp_path / "log"
    path.write_text(
        log.event("starting") + "\n" + log.event("config_error", message="bad model") + "\n"
    )
    assert log.config_error(path) == "bad model"
    assert log.config_error(tmp_path / "missing") is None


def test_policy_violation_names_the_first_one(tmp_path):
    path = tmp_path / "log"
    path.write_text(
        "".join(
            x + "\n"
            for x in (
                log.event("starting"),
                log.event("policy_violation", message="Bash ran"),
                log.event("policy_violation", message="later"),
            )
        )
    )
    assert log.policy_violation(path) == "Bash ran"
    assert log.policy_violation(tmp_path / "missing") is None


@pytest.mark.parametrize(
    ("lines", "cause"),
    [
        (
            [
                log.event("starting"),
                log.event("config_error", message="refused"),
                log.event("policy_violation", message="Bash ran"),
            ],
            "refused",
        ),
        (
            [log.event("starting"), log.event("policy_violation", message="Bash ran")],
            "tool policy violated: Bash ran",
        ),
        (
            [
                log.event("starting"),
                log.event("rejection_limit", message=log.REJECTION_PREFIX + "Bash (deny_tools) 2"),
            ],
            "the agent ended its turn after Kraft rejected Bash (deny_tools) 2",
        ),
        ([log.event("starting"), log.event("stop", stop_reason="end_turn")], ""),
        (["plain text", '{"type": "system"}'], None),
    ],
    ids=[
        "config-error-beats-violation",
        "violation-is-prefixed",
        "rejection-limit-has-its-own-prefix",
        "acp-log-with-no-cause",
        "not-an-acp-log-is-none",
    ],
)
def test_cause_of_an_acp_log(lines, cause):
    assert log.cause("".join(x + "\n" for x in lines)) == cause
