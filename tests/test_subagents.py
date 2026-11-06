"""The sub-agent list a session log folds into (claude 2.1.316).

The lines are the spec's Appendix A cut to the keys `kraft.subagents` reads."""

from __future__ import annotations

import pytest

from kraft.subagents import SUMMARY_MAX, Fold

ID, TOOL = "af2a65", "toolu_01EX"
PROMPT = "Use the Bash tool to run `ls /` and report back the first entry"

CALL = {
    "type": "assistant",
    "parent_tool_use_id": None,
    "message": {
        "content": [
            {
                "type": "tool_use",
                "id": TOOL,
                "name": "Agent",
                "input": {
                    "description": "List root",
                    "subagent_type": "general-purpose",
                    "prompt": PROMPT,
                },
            }
        ]
    },
}
STARTED = {
    "type": "system",
    "subtype": "task_started",
    "task_id": ID,
    "tool_use_id": TOOL,
    "description": "List root directory entries",
    "subagent_type": "general-purpose",
    "spawn_depth": 1,
    "task_type": "local_agent",
    "prompt": PROMPT,
}
MESSAGE = {
    "type": "assistant",
    "parent_tool_use_id": TOOL,
    "agent_id": ID,
    "subagent_type": "general-purpose",
    "task_description": "List root directory entries",
    "timestamp": "2026-11-02T23:38:52.555Z",
    "message": {"content": [{"type": "text", "text": "bin"}]},
}
PROGRESS = {
    "type": "system",
    "subtype": "task_progress",
    "task_id": ID,
    "tool_use_id": TOOL,
    "description": "Running List root directory entries",
    "subagent_type": "general-purpose",
    "usage": {"total_tokens": 31067, "tool_uses": 1, "duration_ms": 3452},
    "last_tool_name": "Bash",
}
UPDATED = {
    "type": "system",
    "subtype": "task_updated",
    "task_id": ID,
    "patch": {"status": "completed", "end_time": 1793662734269},
}


def _notified(status: str = "completed", answer: str = "bin") -> dict:
    return {
        "type": "system",
        "subtype": "task_notification",
        "task_id": ID,
        "tool_use_id": TOOL,
        "status": status,
        "summary": answer,
        "usage": {"total_tokens": 31902, "tool_uses": 1, "duration_ms": 4992},
    }


INIT = {"type": "system", "subtype": "init"}
RESULT = {"type": "result", "subtype": "success"}
FULL = [CALL, STARTED, MESSAGE, PROGRESS, UPDATED, _notified()]


def _fold(lines: list[dict | None]) -> Fold:
    fold = Fold()
    for line in lines:
        fold.feed(line)
    return fold


@pytest.mark.parametrize(
    ("lines", "ended", "expected"),
    [
        pytest.param([*FULL, INIT, RESULT, RESULT], True, "completed", id="completed"),
        pytest.param([STARTED, MESSAGE], False, "running", id="running-live"),
        pytest.param([STARTED, MESSAGE], True, "cut_off", id="no-result-line"),
        pytest.param(
            [STARTED, RESULT, _notified("stopped")], True, "cut_off", id="killed-after-result"
        ),
        pytest.param(
            [STARTED, RESULT, _notified("stopped")], False, "killed", id="killed-after-result-live"
        ),
        pytest.param(
            [RESULT, INIT, STARTED, MESSAGE, _notified()],
            True,
            "completed",
            id="finished-after-earlier-result",
        ),
        pytest.param(
            [STARTED, _notified("stopped"), RESULT], True, "killed", id="stopped-before-result"
        ),
        pytest.param([STARTED, _notified("exploded"), RESULT], True, "failed", id="unknown-word"),
        pytest.param([STARTED, _notified("failed"), RESULT], True, "failed", id="failed"),
        pytest.param([STARTED, None, MESSAGE], True, "cut_off", id="undecodable-line"),
    ],
)
def test_status(lines, ended, expected):
    assert [e["status"] for e in _fold(lines).entries(ended=ended)] == [expected]


def test_a_job_is_not_a_subagent():
    job = {
        "type": "system",
        "subtype": "task_started",
        "task_id": "bg1",
        "description": "sleep 600",
    }
    assert _fold([job]).entries(ended=True) == []


def test_entry_fields():
    (entry,) = _fold(FULL).entries(ended=True)
    assert entry == {
        "id": ID,
        "tool_use_id": TOOL,
        "description": "List root directory entries",
        "type": "general-purpose",
        "depth": 1,
        "status": "completed",
        "started_at": "2026-11-02T23:38:52.555Z",
        "ended_at": "2026-11-02T23:38:54.269Z",
        "tokens": 31902,
        "tool_uses": 1,
        "duration_ms": 4992,
        "last_tool": "Bash",
        "summary": "bin",
    }


def test_two_sub_agents_keep_their_own_entries():
    other = {**STARTED, "task_id": "b2", "description": "Review diff"}
    entries = _fold([STARTED, other, _notified()]).entries(ended=False)
    assert [(e["id"], e["status"]) for e in entries] == [(ID, "completed"), ("b2", "running")]


@pytest.mark.parametrize(
    ("lines", "prompt"),
    [
        pytest.param([STARTED], PROMPT, id="prompt-from-task-started"),
        pytest.param([CALL, {**STARTED, "prompt": None}], PROMPT, id="prompt-from-tool-use"),
        pytest.param(
            [
                {
                    **CALL,
                    "message": {"content": [{**CALL["message"]["content"][0], "name": "Task"}]},
                },
                MESSAGE,
            ],
            PROMPT,
            id="prompt-from-task-tool",
        ),
        pytest.param([MESSAGE], None, id="prompt-missing"),
        pytest.param([{**STARTED, "prompt": "p" * 3000}], "p" * 3000, id="long-prompt-whole"),
    ],
)
def test_entry_prompt(lines, prompt):
    assert _fold(lines).entry(ID)["prompt"] == prompt


def test_entry_carries_the_whole_answer_and_an_unknown_id_is_none():
    fold = _fold([STARTED, _notified(answer="a" * 700)])
    assert fold.entry(ID)["answer"] == "a" * 700
    assert fold.entry("nope") is None


def test_summary_is_cut_at_500():
    (entry,) = _fold([STARTED, _notified(answer="a" * 700)]).entries(ended=False)
    assert entry["summary"] == "a" * SUMMARY_MAX
