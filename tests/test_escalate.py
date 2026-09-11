"""One escalation turn, driven directly against a real DB — no subprocess,
no chain. `escalate.dispatch`'s only external dependency is
`_agent.run_agent_task`, monkeypatched the same way
`test_adapters_agent.py::_capture_cmd` does, plus a fake log file so
`_extract_cli_session_id` has something to read.
"""

from __future__ import annotations

import json

from kraft import escalate, executor, store
from kraft.db import Database
from kraft.paths import RunDirs


async def _seed_needs_human(database, rd, wid: str) -> None:
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the thing that broke",
            description="fix the widget so it stops throwing",
            repo=str(rd.base),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, "implementation"))
    await database.write(
        lambda c: store.mark_needs_human(
            c, wid, "implementation", "task failed in node implementation"
        )
    )


def test_dispatch_sends_the_message_and_state_and_marks_the_session_non_worker(
    tmp_path, monkeypatch
):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["kwargs"] = kw
        seen["task_instruction"] = task_instruction
        seen["cwd"] = cwd
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            status = await escalate.dispatch(
                database,
                rd,
                work_item_id=wid,
                message="the widget is at src/widget.py",
                launch=launch,
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT escalation_session_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            return status, row[0]
        finally:
            await database.close()

    import asyncio

    status, cli_session_id = asyncio.run(scenario())
    assert status == "done"
    assert cli_session_id == "cli-abc"
    assert seen["kwargs"]["identify_as_worker"] is False
    assert seen["kwargs"]["resume_session_id"] is None  # first turn
    assert "the widget is at src/widget.py" in seen["task_instruction"]
    assert "task failed in node implementation" in seen["task_instruction"]


def test_dispatch_resumes_an_existing_thread(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, resume_session_id, **kw):
        seen["resume_session_id"] = resume_session_id
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            await database.write(lambda c: store.set_escalation_session(c, wid, "cli-existing"))
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database, rd, work_item_id=wid, message="try again", launch=launch
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    assert seen["resume_session_id"] == "cli-existing"


def test_dispatch_records_the_message_as_an_event(tmp_path, monkeypatch):
    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-abc"}) + "\n"
        )
        return "done"

    monkeypatch.setattr("kraft.escalate._agent.run_agent_task", fake_run_agent_task)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database, rd, work_item_id=wid, message="look at src/widget.py", launch=launch
            )
            from kraft import events as events_mod

            evs = database.read(lambda c: events_mod.read_after(c, 0, wid))
            msg_events = [e for e in evs if e["type"] == "escalation_message"]
            assert len(msg_events) == 1
            assert msg_events[0]["payload"]["message"] == "look at src/widget.py"
            assert isinstance(msg_events[0]["payload"]["session_id"], str)
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
