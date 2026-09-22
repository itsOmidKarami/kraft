"""A worker turn that ends with a background job still running fails at once,
naming the job (Kraft-xvugd, Kraft-nxqft).

The incident: an implementer's full-suite run outlived the Bash tool's
timeout, Claude Code backgrounded it, and the agent ended its turn "waiting on
full test suite run". Its session exited with no result file, and the only
thing anyone saw was the generic missing-envelope failure. The log already
said which job was left behind; these pin that Kraft reads it.

The line shapes below are the ones claude 2.1.27x writes under
`--output-format stream-json`, cut down to the keys the reader uses."""

from __future__ import annotations

import json
import shlex
import subprocess

import pytest

from kraft import store, usage
from kraft.adapters import subprocess as sp
from kraft.worker import reattach

SUITE = "just test --no-testmon 2>&1 | tail -200"
WAIT = 'while kill -0 92262 2>/dev/null; do sleep 5; done; echo "test run finished"'


def _bg_call(tool_use_id: str, command: str) -> dict:
    """The agent asking for `run_in_background` itself."""
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "Bash",
                    "input": {"command": command, "run_in_background": True},
                }
            ]
        },
    }


def _started(task_id: str, tool_use_id: str, command: str, backgrounded: bool) -> dict:
    return {
        "type": "system",
        "subtype": "task_started",
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "description": command,
        "is_backgrounded": backgrounded,
    }


def _moved_to_background(task_id: str) -> dict:
    """Claude Code backgrounding a foreground command that outlived its timeout."""
    return {
        "type": "system",
        "subtype": "task_updated",
        "task_id": task_id,
        "patch": {"is_backgrounded": True},
    }


def _finished(task_id: str, tool_use_id: str, status: str = "completed") -> dict:
    return {
        "type": "system",
        "subtype": "task_notification",
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "status": status,
    }


_RESULT = {"type": "result", "subtype": "success", "usage": {"input_tokens": 1}}

#: The incident's own tail: one auto-backgrounded suite run and one explicit
#: `run_in_background` wait on it, both live when the turn ended, and both
#: killed by the CLI on its way out -- after the result line.
_INCIDENT = [
    _started("bq3", "tu_suite", SUITE, backgrounded=False),
    _moved_to_background("bq3"),
    _bg_call("tu_wait", WAIT),
    _started("bu3", "tu_wait", WAIT, backgrounded=True),
    _RESULT,
    _finished("bq3", "tu_suite", "stopped"),
    _finished("bu3", "tu_wait", "stopped"),
]


def _log(path, lines) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


@pytest.mark.parametrize(
    "lines, expected",
    [
        (_INCIDENT, [SUITE, WAIT]),
        ([_bg_call("tu1", "sleep 600"), _RESULT], ["sleep 600"]),
        # Waited on before the turn ended: nothing left behind.
        (
            [
                _bg_call("tu1", "sleep 1"),
                _started("t1", "tu1", "sleep 1", backgrounded=True),
                _finished("t1", "tu1"),
                _RESULT,
            ],
            [],
        ),
        # A foreground command is never a background job, however long it ran.
        ([_started("t1", "tu1", "pytest", False), _finished("t1", "tu1"), _RESULT], []),
        # No turn ended at all: the process died mid-turn, which is a different
        # failure and not this one to name.
        ([_bg_call("tu1", "sleep 600")], []),
    ],
    ids=["incident", "run-in-background", "waited-on", "foreground", "no-turn-end"],
)
def test_the_claude_reader_names_the_jobs_still_running_when_the_turn_ended(
    tmp_path, lines, expected
):
    path = tmp_path / "s.log"
    _log(path, lines)
    assert usage.READERS["claude-stream-json"].unfinished_jobs(path) == expected


def _child_writing(tmp_path, lines, result: dict | None) -> list[str]:
    """A command that prints `lines` (into the session log), writes `result`
    to $KRAFT_RESULT_PATH unless it is None, then exits 0."""
    src = tmp_path / "stream.jsonl"
    _log(src, lines)
    script = f"cat {shlex.quote(str(src))}"
    if result is not None:
        script += f'; printf %s {shlex.quote(json.dumps(result))} > "$KRAFT_RESULT_PATH"'
    return ["sh", "-c", script]


@pytest.fixture
async def run_agent(database, run_dirs, tmp_path):
    """`await run_agent(lines, result, **kw)` -> `(status, log text, events)`:
    one real `run_task` as `run_agent_task` makes it."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="default",
            chain_definition="{}",
        )
    )

    async def go(lines, result, **kw):
        kw = {"reader": "claude-stream-json", "require_result_file": True, **kw}
        status = await sp.run_task(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            node_id="implementation",
            hook_point="implementation.main.implement",
            cmd=_child_writing(tmp_path, lines, result),
            cwd=tmp_path,
            **kw,
        )
        found = database.read(
            lambda c: c.execute(
                "SELECT type, payload FROM events WHERE type = ?", (sp.JOBS_ABANDONED,)
            ).fetchall()
        )
        return status, (run_dirs.logs / "s1.log").read_text(), [json.loads(r[1]) for r in found]

    return go


@pytest.mark.parametrize(
    "result",
    [{"status": "done"}, {"status": "done_with_concerns", "concerns": "c"}, None],
    ids=["claims-done", "claims-done-with-concerns", "no-result-file"],
)
async def test_a_turn_left_with_a_running_job_fails_naming_it(run_agent, result):
    status, log, found = await run_agent(_INCIDENT, result)

    assert status == "failed"
    last = log.rstrip().splitlines()[-1]
    assert last.startswith("kraft: failed:") and SUITE in last and WAIT in last
    assert [(e["session_id"], e["jobs"]) for e in found] == [("s1", [SUITE, WAIT])]


async def test_a_question_keeps_its_stop_and_still_names_the_job(run_agent):
    """needs_context is a person's stop already, and failing it would lose the
    question (agent._resolve_status's reasoning): the job is named, the stop
    stands."""
    status, _, found = await run_agent(_INCIDENT, {"status": "needs_context", "question": "q"})

    assert status == "needs_context"
    assert [e["jobs"] for e in found] == [[SUITE, WAIT]]


@pytest.mark.parametrize(
    "lines, kw",
    [
        ([_RESULT], {}),
        # A harness whose log Kraft has no reader for: nothing to read, so
        # nothing is claimed.
        (_INCIDENT, {"reader": None}),
    ],
    ids=["nothing-left-running", "no-log-reader"],
)
async def test_a_clean_turn_is_left_alone(run_agent, lines, kw):
    status, log, found = await run_agent(lines, {"status": "done"}, **kw)

    assert (status, found) == ("done", [])
    assert "kraft: failed:" not in log


async def test_a_session_adopted_after_a_restart_is_held_to_the_same_rule(
    item_on, database, run_dirs
):
    """reattach's own exit path, `_exit_from_file`: the child outlived a
    restart, so `run_task` never saw it end."""
    it = await item_on(
        "- id: implementation\n  kind: exec\n"
        "  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]\n",
        "implementation",
    )
    await it.session("s1", "implementation.main.implement")
    _log(run_dirs.logs / "s1.log", _INCIDENT)
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    proc = subprocess.Popen(["true"])
    proc.wait()

    await reattach._adopt(database, "s1", proc.pid, poll_s=0.01)

    assert it.sessions()[0]["status"] == "failed"
    assert [e["payload"]["jobs"] for e in it.events(sp.JOBS_ABANDONED)] == [[SUITE, WAIT]]
