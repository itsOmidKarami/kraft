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
from support.harness import own_files

from kraft import caps, store, usage
from kraft.adapters import subprocess as sp
from kraft.vocab import SessionEvent
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


def _subagent_started(task_id: str, tool_use_id: str, label: str) -> dict:
    """claude 2.1.316: a sub-agent is a `local_agent` task, backgrounded by default."""
    return {
        "type": "system",
        "subtype": "task_started",
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "description": label,
        "subagent_type": "general-purpose",
        "task_type": "local_agent",
        "is_backgrounded": True,
    }


def _agent_call(tool_use_id: str, name: str = "Agent") -> dict:
    block = {"type": "tool_use", "id": tool_use_id, "name": name}
    return {
        "type": "assistant",
        "message": {"content": [{**block, "input": {"prompt": "p", "run_in_background": True}}]},
    }


_RESULT = {"type": "result", "subtype": "success", "usage": {"input_tokens": 1}}
_NOTE = "kraft: note: 1 sub-agent was cut off before it reported back: Explore codebase"
_CUT_OFF = [_subagent_started("a1", "tu_a1", "Explore codebase"), _RESULT]

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


_ADOPT_CHAIN = (
    "- id: implementation\n  kind: exec\n"
    "  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]\n"
)


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
        # A sub-agent is no background job (subagents.Fold reads it).
        (_CUT_OFF, []),
        ([_agent_call("tu1"), _RESULT], []),
        ([_agent_call("tu1", "Task"), _RESULT], []),
    ],
    ids=[
        "incident",
        "run-in-background",
        "waited-on",
        "foreground",
        "no-turn-end",
        "subagent-is-not-a-job",
        "background-agent-call",
        "task-named-call",
    ],
)
def test_the_claude_reader_names_the_jobs_still_running_when_the_turn_ended(
    tmp_path, lines, expected
):
    path = tmp_path / "s.log"
    _log(path, lines)
    assert usage.READERS["claude-stream-json"].unfinished_jobs(path) == expected


def _child_writing(tmp_path, lines, result: dict | None, then: str = "") -> list[str]:
    """A command that prints `lines` (into the session log), writes `result`
    to $KRAFT_RESULT_PATH unless it is None, runs `then`, then exits 0."""
    src = tmp_path / "stream.jsonl"
    _log(src, lines)
    script = f"cat {shlex.quote(str(src))}"
    if result is not None:
        script += f'; printf %s {shlex.quote(json.dumps(result))} > "$KRAFT_RESULT_PATH"'
    if then:
        script += f"; {then}"
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

    async def go(lines, result, then="", **kw):
        kw = {"reader": "claude-stream-json", "require_result_file": True, **kw}
        status = await sp.run_task(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            node_id="implementation",
            hook_point="implementation.main.implement",
            cmd=_child_writing(tmp_path, lines, result, then),
            cwd=tmp_path,
            **kw,
        )
        found = database.read(
            lambda c: c.execute(
                "SELECT type, payload FROM events WHERE type = ?",
                (SessionEvent.BACKGROUND_JOBS_ABANDONED,),
            ).fetchall()
        )
        return status, own_files(run_dirs, "s1").log.read_text(), [json.loads(r[1]) for r in found]

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
    it = await item_on(_ADOPT_CHAIN, "implementation")
    await it.session("s1", "implementation.main.implement")
    _log(run_dirs.logs / "s1.log", _INCIDENT)
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    proc = subprocess.Popen(["true"])
    proc.wait()

    await reattach._adopt(database, "s1", proc.pid, work_item_id=it.id, poll_s=0.01)

    assert it.sessions()[0]["status"] == "failed"
    assert [e["payload"]["jobs"] for e in it.events(SessionEvent.BACKGROUND_JOBS_ABANDONED)] == [
        [SUITE, WAIT]
    ]


@pytest.mark.parametrize(
    "result, expected",
    [({"status": "done"}, "done"), (None, "failed")],
    ids=["done", "no-result-file"],
)
async def test_a_cut_off_subagent_is_noted_not_failed(run_agent, result, expected):
    status, log, found = await run_agent(_CUT_OFF, result)

    assert (status, found) == (expected, [])
    assert log.rstrip().splitlines()[-1] == _NOTE
    assert "kraft: failed:" not in log


async def test_a_finished_subagent_gets_no_note(run_agent):
    done = _finished("a1", "tu_a1")
    _, log, _ = await run_agent(
        [_subagent_started("a1", "tu_a1", "x"), done, _RESULT], {"status": "done"}
    )

    assert "kraft: note:" not in log


async def test_a_job_and_a_cut_off_subagent(run_agent):
    status, log, found = await run_agent([*_CUT_OFF[:1], *_INCIDENT], {"status": "done"})

    assert status == "failed"
    assert [e["jobs"] for e in found] == [[SUITE, WAIT]]
    failed = next(line for line in log.splitlines() if line.startswith("kraft: failed:"))
    assert "Explore codebase" not in failed
    assert log.rstrip().splitlines()[-1] == _NOTE


async def test_two_cut_off_subagents_share_one_note(run_agent):
    lines = [
        _subagent_started("a1", "t1", "Explore codebase"),
        _subagent_started("a2", "t2", "Review diff"),
        _RESULT,
    ]
    _, log, _ = await run_agent(lines, {"status": "done"})

    assert log.rstrip().splitlines()[-1] == (
        "kraft: note: 2 sub-agents were cut off before they reported back: "
        "Explore codebase; Review diff"
    )


async def test_a_crash_with_no_result_line_is_still_a_generic_failure(run_agent):
    status, log, found = await run_agent([_bg_call("tu1", "sleep 600")], None)

    assert (status, found) == ("failed", [])
    assert "kraft: failed:" not in log and "kraft: note:" not in log


async def test_a_paused_session_gets_no_note(run_agent, monkeypatch):
    real = sp.store.session_status

    def paused_once_running(*a, **k):
        return "paused" if real(*a, **k) == "running" else real(*a, **k)

    monkeypatch.setattr(sp.store, "session_status", paused_once_running)

    status, log, _ = await run_agent(_CUT_OFF, {"status": "done"})

    assert status == "paused"
    assert "kraft: note:" not in log


async def test_a_rate_limited_session_gets_no_note(run_agent):
    limited = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "resetsAt": 1788968400,
            "rateLimitType": "five_hour",
        },
    }
    status, log, _ = await run_agent([*_CUT_OFF, limited], {"status": "done"})

    assert status == "rate_limited"
    assert "kraft: note:" not in log


async def test_the_note_on_a_time_cap(run_agent):
    hit = caps.Hit(scope="", field="time_cap_minutes", minutes=1, remaining_s=0.5)
    cap = caps.Deadline(at=caps.monotonic() + 0.5, hit=hit)

    status, log, _ = await run_agent(_CUT_OFF[:1], None, then="sleep 30", time_cap=cap)

    assert status == caps.TIME_CAPPED
    assert log.rstrip().splitlines()[-1] == _NOTE


async def test_the_note_on_a_time_cap_for_a_task_with_no_reader(run_agent):
    hit = caps.Hit(scope="", field="time_cap_minutes", minutes=1, remaining_s=0.5)
    cap = caps.Deadline(at=caps.monotonic() + 0.5, hit=hit)

    status, log, _ = await run_agent(
        _CUT_OFF[:1], None, then="sleep 30", time_cap=cap, reader=None, require_result_file=False
    )

    assert status == caps.TIME_CAPPED
    assert "kraft: note:" not in log


async def test_a_session_adopted_with_a_cut_off_subagent_is_noted(item_on, database, run_dirs):
    it = await item_on(_ADOPT_CHAIN, "implementation")
    await it.session("s1", "implementation.main.implement")
    _log(run_dirs.logs / "s1.log", _CUT_OFF)
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    proc = subprocess.Popen(["true"])
    proc.wait()

    await reattach._adopt(database, "s1", proc.pid, work_item_id=it.id, poll_s=0.01)

    assert it.sessions()[0]["status"] == "done"
    assert (run_dirs.logs / "s1.log").read_text().rstrip().splitlines()[-1] == _NOTE
    assert it.events(SessionEvent.BACKGROUND_JOBS_ABANDONED) == []


async def test_an_adopted_session_stopped_at_its_cap_is_noted(item_on, database, run_dirs):
    it = await item_on(_ADOPT_CHAIN, "implementation")
    await it.session("s1", "implementation.main.implement")
    _log(run_dirs.logs / "s1.log", _CUT_OFF[:1])
    row = database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    hit = caps.Hit(scope="", field="time_cap_minutes", minutes=1, remaining_s=0.0)

    await reattach._stop_at_cap(database, row, 2_000_000_000, hit)

    assert (run_dirs.logs / "s1.log").read_text().rstrip().splitlines()[-1] == _NOTE


def test_every_agent_launch_is_told_the_rule_and_where_the_full_suite_runs(run):
    """The prompt half: the rule it is now held to, and that the full suite is
    the verification node's job. A plan that asked for `--no-testmon` is what
    put a 14-minute run in the incident's turn."""
    cmd = run()["cmd"]
    prompt = cmd[cmd.index("--append-system-prompt") + 1]

    assert "a turn that ends with a background job still running fails" in prompt
    assert "Sub-agents are not background jobs" in prompt
    assert "not the whole suite" in prompt and "verification" in prompt
