import asyncio
import json

from kraft import db, events, store

_CHAIN = json.dumps(
    {
        "template_id": "quick-task",
        "nodes": [
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
        ],
    }
)


async def _open(tmp_path):
    return await db.Database.open(tmp_path / "orchestrator.db")


def _mk_item(database, wid="w1"):
    return database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B-1",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition=_CHAIN,
        )
    )


def test_create_work_item_writes_row_and_event(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "active"
            assert row["current_node_id"] is None
            assert row["bead_id"] == "B-1"
            evs = database.read(lambda c: events.read_after(c, 0))
            assert [e["type"] for e in evs] == ["work_item_created"]
            assert evs[0]["payload"] == {"title": "t", "repo": "/r", "chain_template": "quick-task"}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_node_lifecycle_events_and_current_node(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(lambda c: store.load_chain(c, "w1", "env_setup"))
            await database.write(lambda c: store.enter_node(c, "w1", "env_setup"))
            await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
            await database.write(lambda c: store.enter_node(c, "w1", "verify"))
            row = database.read(
                lambda c: c.execute(
                    "SELECT current_node_id FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["current_node_id"] == "verify"
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
            assert types == [
                "work_item_created",
                "chain_loaded",
                "node_started",
                "node_completed",
                "node_started",
            ]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_needs_human_and_completed(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database, "wh")
            await database.write(lambda c: store.mark_needs_human(c, "wh", "verify", "boom"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='wh'").fetchone()
            )
            assert row["status"] == "needs_human"
            ev = database.read(lambda c: events.read_after(c, 0))[-1]
            assert ev["type"] == "work_item_needs_human"
            assert ev["payload"] == {"node_id": "verify", "reason": "boom"}

            await _mk_item(database, "wc")
            await database.write(lambda c: store.mark_completed(c, "wc"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='wc'").fetchone()
            )
            assert row["status"] == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_session_lifecycle(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="env_setup",
                    hook_point="on.env.prepare",
                    log_path="/l",
                    result_path="/r",
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "pending"
            assert row["pid"] is None
            # create_session emits no event
            assert (
                database.read(lambda c: events.read_after(c, 0, "w1"))[-1]["type"]
                == "work_item_created"
            )

            await database.write(lambda c: store.session_running(c, "s1", 4321, 111.5))
            row = database.read(
                lambda c: c.execute("SELECT * FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert (row["status"], row["pid"], row["pid_start_time"]) == ("running", 4321, 111.5)
            started = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert started["type"] == "worker_session_started"
            assert started["payload"] == {
                "session_id": "s1",
                "node_id": "env_setup",
                "hook_point": "on.env.prepare",
                "pid": 4321,
            }

            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, exited_at FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["status"] == "done"
            assert row["exited_at"] is not None
            assert (
                database.read(lambda c: events.read_after(c, 0, "w1"))[-1]["type"]
                == "worker_session_exited"
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_row_and_event_are_atomic(tmp_path):
    """A write that mutates a row then raises leaves neither row nor event."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)

            def bad(c):
                store.enter_node(c, "w1", "env_setup")
                raise RuntimeError("boom")

            try:
                await database.write(bad)
                raise AssertionError("expected RuntimeError")
            except RuntimeError:
                pass

            row = database.read(
                lambda c: c.execute(
                    "SELECT current_node_id FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["current_node_id"] is None  # enter_node rolled back
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
            assert types == ["work_item_created"]  # no node_started event
        finally:
            await database.close()

    asyncio.run(scenario())


def test_session_unknown_sets_status_and_event(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="env_setup",
                    hook_point="on.env.prepare",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_unknown(c, "s1"))
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, exited_at FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["status"] == "unknown"
            assert row["exited_at"] is not None
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "session_unknown"
            assert ev["payload"] == {"session_id": "s1"}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_session_reattached_emits_event_without_row_change(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="env_setup",
                    hook_point="on.env.prepare",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_running(c, "s1", 4321, 111.5))
            await database.write(lambda c: store.session_reattached(c, "s1"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "running"  # unchanged
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "session_reattached"
            assert ev["payload"] == {"session_id": "s1", "pid": 4321}
        finally:
            await database.close()

    asyncio.run(scenario())
