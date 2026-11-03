"""Kraft-5x45w: an unrecognised stream-json event kind summarises to its bare
`type`, not the raw JSON line.

The sub-agent rows are claude 2.1.316's `task_*` and message lines under
`--output-format stream-json --verbose`, cut down to the keys the reader uses."""

import json

import pytest

from kraft import logs
from kraft.logs import LABEL_MAX, agent_fields, classify, summary

NAME = "List root directory entries"
KNOWN = {"af2a65": (NAME, "general-purpose")}
FIELDS = {"agent": "af2a65", "agent_label": NAME, "agent_type": "general-purpose"}
_TASK = {"type": "system", "task_id": "af2a65", "tool_use_id": "toolu_01EX"}


def _line(**keys) -> dict:
    return {**_TASK, **keys}


@pytest.mark.parametrize(
    ("obj", "known", "expected", "fields"),
    [
        pytest.param({"type": "result", "is_error": False}, {}, "result: success", {}, id="result"),
        pytest.param(
            _line(
                subtype="task_started",
                task_type="local_agent",
                description=NAME,
                subagent_type="general-purpose",
            ),
            {},
            f"↳ {NAME}: started (general-purpose)",
            FIELDS,
            id="subagent-started",
        ),
        pytest.param(
            _line(
                subtype="task_progress",
                description=f"Running {NAME}",
                subagent_type="general-purpose",
                last_tool_name="Bash",
                usage={"total_tokens": 31067, "tool_uses": 1, "duration_ms": 3452},
            ),
            KNOWN,
            f"↳ {NAME}: Bash · 31k tokens · 1 tool use",
            FIELDS,
            id="subagent-progress",
        ),
        pytest.param(
            _line(subtype="task_updated", patch={"status": "completed", "end_time": 1793662734269}),
            KNOWN,
            f"↳ {NAME}: completed",
            {**FIELDS, "agent_status": "completed"},
            id="subagent-updated",
        ),
        pytest.param(
            _line(
                subtype="task_notification",
                status="completed",
                summary="bin",
                usage={"total_tokens": 31902, "tool_uses": 1, "duration_ms": 4992},
            ),
            KNOWN,
            f"↳ {NAME}: completed: bin · 32k tokens · 5s",
            {**FIELDS, "agent_status": "completed"},
            id="subagent-done",
        ),
        pytest.param(
            {
                "type": "assistant",
                "parent_tool_use_id": "toolu_01EX",
                "agent_id": "af2a65",
                "subagent_type": "general-purpose",
                "task_description": NAME,
                "message": {
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls /"}}]
                },
            },
            {},
            f"↳ {NAME}: Bash(ls /)",
            FIELDS,
            id="subagent-tool-call",
        ),
        pytest.param(
            _line(subtype="task_started", description="sleep 600"),
            KNOWN,
            "system task_started",
            {},
            id="job-started",
        ),
        pytest.param(
            _line(subtype="task_notification", task_id="b9z1", status="completed", summary="done"),
            KNOWN,
            "system task_notification",
            {},
            id="job-notification",
        ),
        pytest.param(
            _line(subtype="task_progress", task_id="b9z1", description="Running sleep 600"),
            KNOWN,
            "system task_progress",
            {},
            id="job-progress",
        ),
        pytest.param(
            {
                "type": "system",
                "subtype": "background_tasks_changed",
                "tasks": [{"task_id": "af2a65", "task_type": "local_agent"}],
            },
            KNOWN,
            "system background_tasks_changed",
            {},
            id="tasks-changed",
        ),
        pytest.param(
            _line(
                subtype="task_started",
                task_type="local_agent",
                description="a\nb\x1b[31m" + "x" * 10_000,
            ),
            {},
            "↳ a b" + "x" * (LABEL_MAX - 4) + "…: started",
            {"agent": "af2a65", "agent_label": "a b" + "x" * (LABEL_MAX - 4) + "…"},
            id="label-cut",
        ),
        pytest.param(
            _line(
                subtype="task_progress",
                description="Running Explore codebase",
                subagent_type="Explore",
            ),
            {},
            "↳ Explore codebase: progress",
            {"agent": "af2a65", "agent_label": "Explore codebase", "agent_type": "Explore"},
            id="label-from-progress",
        ),
    ],
)
def test_summary(obj, known, expected, fields):
    agents = dict(known)
    got = agent_fields(obj, agents)
    assert got == fields
    assert summary(obj, json.dumps(obj), got) == expected
    assert len(got.get("agent_label", "")) <= LABEL_MAX


def test_unknown_kind_falls_back_to_bare_type_not_raw_json():
    obj = {"type": "stream_event", "event": {"index": 0, "delta": {}}}
    line = json.dumps(obj)
    assert summary(obj, line) == "stream_event"


def test_non_json_line_falls_back_to_the_line_itself():
    assert summary(None, "plain stdout, not JSON") == "plain stdout, not JSON"


def test_a_line_nested_past_the_parser_reads_as_plain_stdout(monkeypatch):
    """The worker can print to the log: a line deeper than `json.loads`
    recurses must not break the log view. Raised directly, since how deep the
    parser goes before giving up differs between Python versions."""

    def too_deep(_text):
        raise RecursionError

    monkeypatch.setattr(logs.json, "loads", too_deep)
    assert classify('{"type": "assistant"}') == ("stdout", None)
