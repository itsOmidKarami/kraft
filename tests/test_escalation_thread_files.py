"""An escalation thread's result and summary files are one pair of names for
every turn in it (Kraft-s7c04.54), driven through the real `escalate.dispatch`,
`run_agent_task` and `run_task`, with only the harness's argv swapped for a
shell script that plays the turn."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from support.harness import v1_chain, write_harness_profiles

from kraft import escalate, executor, store
from kraft.adapters import subprocess as sp

_CHAIN = v1_chain(
    [
        {
            "id": "implementation",
            "kind": "exec",
            "tasks": [{"id": "work", "kind": "subprocess", "command": "true"}],
        }
    ],
    repo="/r",
)
_INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "cli-1"})
_LAUNCH = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)


@pytest.fixture(autouse=True)
def escalation_profile(tmp_path, monkeypatch):
    templates = tmp_path / "escalation-templates"
    write_harness_profiles(templates, {"claude": {"provider": "claude"}})
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))


@pytest.fixture
def turns(monkeypatch, database, run_dirs):
    """`await turns(script, ...)`: one escalation turn per shell script, each
    run in place of the harness argv. Returns each turn's status, its argv
    (the context it was handed) and its session id."""
    played = []

    async def play(db, run_dirs, *, cmd, **kw):
        played.append({"argv": " ".join(cmd), "session_id": kw["session_id"]})
        script = f"printf '%s\\n' {shlex.quote(_INIT)}; {scripts.pop(0)}"
        return await real_run_task(db, run_dirs, cmd=["sh", "-c", script], **kw)

    real_run_task = sp.run_task
    monkeypatch.setattr("kraft.adapters.agent._subprocess.run_task", play)
    scripts: list[str] = []

    async def go(*steps: str) -> list[dict]:
        await database.write(
            lambda c: store.create_work_item(
                c,
                id="w1",
                bead_id=None,
                title="t",
                repo=str(run_dirs.base),
                chain_template="quick-task",
                chain_definition="{}",
                materialized_chain=_CHAIN.to_json(),
            )
        )
        await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
        await database.write(lambda c: store.mark_needs_human(c, "w1", "implementation", "x"))
        (run_dirs.worktrees / "w1" / ".engineering" / "sessions").mkdir(parents=True)
        scripts.extend(steps)
        for i, _ in enumerate(steps):
            status = await escalate.dispatch(
                database, run_dirs, work_item_id="w1", message=f"turn {i + 1}", launch=_LAUNCH
            )
            played[i]["status"] = status
        return played

    return go


def _row(database, sid):
    return database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = ?", (sid,)).fetchone()
    )


_SUMMARY = ".engineering/sessions/escalation-w1-1.md"
_DONE = shlex.quote(json.dumps({"status": "done"}))


async def test_a_resumed_turn_that_writes_where_it_remembers_is_done(turns, run_dirs):
    """`a-resumed-escalation-turn-writes-where-it-remembers` (b5afe84c): the
    second turn ignores this turn's instruction and writes its result to the
    literal path the first turn wrote to. That is this turn's path too."""
    remembered = run_dirs.results / "memory"
    first = f'echo "$KRAFT_RESULT_PATH" > {remembered}; echo {_DONE} > "$KRAFT_RESULT_PATH"'
    second = f'echo {_DONE} > "$(cat {remembered})"'

    played = await turns(first, second)

    assert [t["status"] for t in played] == ["done", "done"]
    assert _SUMMARY in played[0]["argv"] and _SUMMARY in played[1]["argv"]
    assert "--resume cli-1" in played[1]["argv"]


async def test_a_turn_starts_with_no_result_file_from_the_last(turns):
    """The shared name must not hand a turn that wrote nothing the last
    turn's result: it ends `failed`, as a turn with no result file does."""
    played = await turns('echo \'{"status":"done"}\' > "$KRAFT_RESULT_PATH"', "true")

    assert [t["status"] for t in played] == ["done", "failed"]


async def test_each_turns_result_and_summary_stay_readable_from_its_row(turns, database, run_dirs):
    """Per-turn history survives the shared name: the last turn's files go
    back under its own session id before the next turn writes, its row
    following them, and the current turn's row points at the shared name."""
    wt = run_dirs.worktrees / "w1"

    def turn(n):
        result = json.dumps({"status": "done", "session_summary_ref": _SUMMARY, "n": n})
        summary = f"echo 'turn {n}' > {wt / _SUMMARY}"
        return f'{summary}; echo {shlex.quote(result)} > "$KRAFT_RESULT_PATH"'

    first, second = await turns(turn(1), turn(2))

    one, two = _row(database, first["session_id"]), _row(database, second["session_id"])
    assert one["result_path"] == str(run_dirs.results / f"{first['session_id']}.json")
    assert json.loads(Path(one["result_path"]).read_text())["n"] == 1
    assert Path(one["result_path"]).with_suffix(".exit").read_text().strip() == "0"
    assert one["session_summary_ref"] == f".engineering/sessions/{first['session_id']}.md"
    assert (wt / one["session_summary_ref"]).read_text() == "turn 1\n"
    assert two["result_path"] == str(run_dirs.results / "escalation-w1-1.json")
    assert json.loads(Path(two["result_path"]).read_text())["n"] == 2
    assert two["session_summary_ref"] == _SUMMARY
    assert (wt / _SUMMARY).read_text() == "turn 2\n"
