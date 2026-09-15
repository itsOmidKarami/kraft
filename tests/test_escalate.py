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


async def _seed_paused(database, rd, wid: str) -> None:
    """A `paused` item with a current node but no `needs_human` reason to
    its name -- the Kraft-k5ol shape `escalate_work_item` now accepts."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="the thing that's paused",
            description="",
            repo=str(rd.base),
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    await database.write(lambda c: store.enter_node(c, wid, "implementation"))
    await database.write(lambda c: store.pause_work_item(c, wid, []))


def test_dispatch_on_a_paused_item_does_not_claim_needs_human_or_offer_retry(tmp_path, monkeypatch):
    """The prompt built for a `paused` item must not lie about its status or
    tell the agent to run a self-action route that 409s on it: `kraft item
    retry` refuses anything but `needs_human`, and `kraft item resume` 409s
    while this very escalation session is running (Kraft-k5ol code-review
    finding)."""
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
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
            await _seed_paused(database, rd, wid)
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database,
                rd,
                work_item_id=wid,
                message="any update?",
                launch=launch,
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    instruction = seen["task_instruction"]
    assert "Status: paused" in instruction
    assert "Status: needs_human" not in instruction
    assert "Why it is stopped" not in instruction
    assert "run `kraft item retry` yourself" not in instruction
    assert "will fail here" in instruction


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


def test_dispatch_default_continues_the_latest_thread(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(
        db, run_dirs, *, session_id, work_item_id, node_id, hook_point, cwd, task_instruction, **kw
    ):
        seen["kwargs"] = kw
        # Mirrors what `_subprocess.run_task` really does on every dispatch --
        # `store.latest_escalation_thread`, which the *next* dispatch() call
        # reads, only sees this turn once its row actually lands.
        await db.write(
            lambda c: store.create_session(
                c,
                id=session_id,
                work_item_id=work_item_id,
                node_id=node_id,
                hook_point=hook_point,
                log_path=str(run_dirs.logs / f"{session_id}.log"),
                result_path=str(run_dirs.logs / f"{session_id}.json"),
                thread=kw["thread"],
            )
        )
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": "cli-1"}) + "\n"
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
            await escalate.dispatch(database, rd, work_item_id=wid, message="m1", launch=launch)
            assert seen["kwargs"]["thread"] == 1
            assert seen["kwargs"]["resume_session_id"] is None  # first-ever turn
            await escalate.dispatch(database, rd, work_item_id=wid, message="m2", launch=launch)
            assert seen["kwargs"]["thread"] == 1  # still thread 1, no new_thread asked
            assert seen["kwargs"]["resume_session_id"] == "cli-1"  # resumes the CLI thread
            from kraft import events as events_mod

            evts = database.read(lambda c: events_mod.read_after(c, 0, wid))
            turns = [e["payload"]["turn"] for e in evts if e["type"] == "escalation_message"]
            return turns
        finally:
            await database.close()

    import asyncio

    turns = asyncio.run(scenario())
    assert turns == [1, 2]


def test_dispatch_new_thread_starts_fresh_and_bumps_thread_number(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(
        db, run_dirs, *, session_id, work_item_id, node_id, hook_point, cwd, task_instruction, **kw
    ):
        seen["kwargs"] = kw
        await db.write(
            lambda c: store.create_session(
                c,
                id=session_id,
                work_item_id=work_item_id,
                node_id=node_id,
                hook_point=hook_point,
                log_path=str(run_dirs.logs / f"{session_id}.log"),
                result_path=str(run_dirs.logs / f"{session_id}.json"),
                thread=kw["thread"],
            )
        )
        log_path = run_dirs.logs / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"type": "system", "subtype": "init", "session_id": f"cli-{session_id[:4]}"})
            + "\n"
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
            await escalate.dispatch(database, rd, work_item_id=wid, message="m1", launch=launch)
            assert seen["kwargs"]["thread"] == 1
            await escalate.dispatch(
                database, rd, work_item_id=wid, message="m2", launch=launch, new_thread=True
            )
            assert seen["kwargs"]["thread"] == 2
            # A fresh CLI session despite escalation_session_id already being
            # set from the first turn.
            assert seen["kwargs"]["resume_session_id"] is None
            from kraft import events as events_mod

            evts = database.read(lambda c: events_mod.read_after(c, 0, wid))
            msg_events = [e for e in evts if e["type"] == "escalation_message"]
            return msg_events[-1]["payload"]
        finally:
            await database.close()

    import asyncio

    payload = asyncio.run(scenario())
    assert (payload["thread"], payload["turn"]) == (2, 1)  # turn restarts in the new thread


def test_dispatch_new_thread_on_first_ever_escalation_is_still_thread_one(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, cwd, task_instruction, **kw):
        seen["kwargs"] = kw
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
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database, rd, work_item_id=wid, message="m1", launch=launch, new_thread=True
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    # No prior thread to be "new" from -- there is no prior thread at all.
    assert seen["kwargs"]["thread"] == 1
    assert seen["kwargs"]["resume_session_id"] is None  # same as it would have been either way


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


def test_dispatch_auto_uses_the_synthesized_message_and_auto_opening(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
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
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database,
                rd,
                work_item_id=wid,
                message="(Auto-escalated: no one has looked at this yet.)",
                launch=launch,
                auto=True,
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    assert "This is an automatic escalation" in seen["task_instruction"]
    assert "Kraft itself is asking you" in seen["task_instruction"]


def test_dispatch_manual_is_unchanged_by_the_auto_param(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, task_instruction, **kw):
        seen["task_instruction"] = task_instruction
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
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database, rd, work_item_id=wid, message="please look", launch=launch
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    assert "This is an escalation: a human is asking" in seen["task_instruction"]
    assert "automatic escalation" not in seen["task_instruction"]


def test_dispatch_auto_tags_the_event(tmp_path, monkeypatch):
    async def fake_run_agent_task(db, run_dirs, *, session_id, **kw):
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
            launch = executor.LaunchContext(repo_entry=None, steering_dir=None, skills_dir=None)
            await escalate.dispatch(
                database, rd, work_item_id=wid, message="msg", launch=launch, auto=True
            )
            from kraft import events as events_mod

            evs = database.read(lambda c: events_mod.read_after(c, 0, wid))
            msg_events = [e for e in evs if e["type"] == "escalation_message"]
            assert msg_events[0]["payload"]["auto"] is True
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())


def test_dispatch_forwards_the_repo_s_resolved_sandbox(tmp_path, monkeypatch):
    """Kraft-rki: `resolve_invocation` folds a repo's `sandbox:` into `inv`,
    but a dispatch that builds `inv` and then forgets to pass it on is the
    silent 'sometimes not actually sandboxed' case the spec rules out.
    """
    seen = {}

    async def fake_run_agent_task(db, run_dirs, *, session_id, sandbox, **kw):
        seen["sandbox"] = sandbox
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
            launch = executor.LaunchContext(
                repo_entry={"sandbox": {"kind": "docker", "image": "kraft-worker:py"}},
                steering_dir=None,
                skills_dir=None,
            )
            return await escalate.dispatch(
                database, rd, work_item_id=wid, message="msg", launch=launch
            )
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
    assert seen["sandbox"] == {"kind": "docker", "image": "kraft-worker:py"}


def test_escalation_running_reports_a_pending_or_running_session(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            assert escalate.escalation_running(database, wid) is None
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.logs / "s1.json"),
                )
            )
            assert escalate.escalation_running(database, wid) == "s1"
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())


def test_last_escalation_status_is_the_newest_sessions_status(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await Database.open(rd.db)
        try:
            wid = "w1"
            await _seed_needs_human(database, rd, wid)
            assert escalate.last_escalation_status(database, wid) is None
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.logs / "s1.json"),
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "needs_context"))
            assert escalate.last_escalation_status(database, wid) == "needs_context"
        finally:
            await database.close()

    import asyncio

    asyncio.run(scenario())
