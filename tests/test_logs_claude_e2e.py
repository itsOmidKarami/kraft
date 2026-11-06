"""The keys `kraft.logs` reads to tag a Claude Code sub-agent's rows, pinned
against the real CLI (claude 2.1.316; headless `stream-json` run that spawns
one `general-purpose` sub-agent). The unit tests in `tests/test_logs.py` are
built from that run's lines; this is what notices when a CLI release changes
them.

Re-run on claude 2.1.319, it passes, with two things the 2.1.316 run did not
show. A prompt that says to wait gets a foreground sub-agent
(`is_backgrounded: false`); Kraft reads both kinds the same. And a
`general-purpose` sub-agent has no Agent tool of its own (asked to start one,
it says so and does the work itself; `subagent_stats.spawned_by_subagents` is
0), so this test has no depth-2 case. The CLI does count nested spawns and a
depth limit, so a custom agent given the tool may nest; that is unmeasured."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

PROMPT = (
    "Use the Agent tool to start exactly one general-purpose sub-agent that runs "
    "`ls /` with Bash and reports the first entry. Wait for its answer, then reply "
    "with that entry and nothing else."
)


@pytest.mark.e2e("claude")
def test_a_subagent_run_carries_the_keys_kraft_reads(tmp_path):
    # Tests run on a temp HOME, so `claude login` is not seen (as `just smoke-models`).
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        pytest.skip("export ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)")
    run = subprocess.run(
        [
            "claude",
            "-p",
            PROMPT,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            "haiku",
            "--allowedTools",
            "Agent,Task,Bash",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]

    started = [x for x in lines if x.get("subtype") == "task_started"]
    agents = [x for x in started if x.get("task_type") == "local_agent"]
    assert len(agents) == 1, run.stdout
    task = agents[0]
    assert task["description"] and task["subagent_type"]

    spoken = [x for x in lines if "agent_id" in x and x["type"] in ("assistant", "user")]
    assert spoken, "no line carries agent_id"
    for line in spoken:
        assert line["agent_id"] == task["task_id"]
        assert line["parent_tool_use_id"] == task["tool_use_id"]
        assert line["task_description"]

    done = [
        x
        for x in lines
        if x.get("subtype") == "task_notification" and x.get("task_id") == task["task_id"]
    ]
    assert done and done[0]["status"] and done[0]["summary"]
