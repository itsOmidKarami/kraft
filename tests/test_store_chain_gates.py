import asyncio

from support.store_fixtures import mk_item, open_db

from kraft import events, store


def test_node_lifecycle_events_and_current_node(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_complete_node_is_idempotent(tmp_path):
    """Resume can re-enter an already-completed node; only one node_completed."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.load_chain(c, "w1", "env_setup"))
            await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
            await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
            assert types.count("node_completed") == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_row_and_event_are_atomic(tmp_path):
    """A write that mutates a row then raises leaves neither row nor event."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)

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


def test_last_rejection_reads_the_note_and_the_target_back(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.reject_gate(
                    c, "w1", "plan_approval", "task 4 has no test", reopen=False, node="plan"
                )
            )
            got = database.read(lambda c: store.last_rejection(c, "w1"))
            assert got == {
                "gate": "plan_approval",
                "note": "task 4 has no test",
                "node": "plan",
                "by": "human",
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_rejection_is_spent_once_a_node_starts_on_it(tmp_path):
    """The note is the *pending* rejection's, not the newest one ever. A
    rejection whose re-run already launched has had its note delivered; handing
    it to an unrelated retry three nodes later would steer with stale text."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.reject_gate(
                    c, "w1", "plan_approval", "old news", reopen=True, node="plan"
                )
            )
            await database.write(lambda c: store.enter_node(c, "w1", "plan"))
            assert database.read(lambda c: store.last_rejection(c, "w1")) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_skip_node_marks_active_and_appends_event(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.mark_needs_human(c, "w1", "verify", "boom"))
            await database.write(
                lambda c: store.skip_node(c, "w1", "verify", None, "flaky, known issue")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            evs = database.read(lambda c: events.read_after(c, 0, "w1"))
            return row, [e for e in evs if e["type"] == "node_skipped"]
        finally:
            await database.close()

    row, skipped = asyncio.run(scenario())
    assert row["status"] == "active"
    assert row["retry_at"] is None
    assert skipped == [
        {
            "seq": skipped[0]["seq"],
            "work_item_id": "w1",
            "type": "node_skipped",
            "payload": {"node_id": "verify", "gate": None, "note": "flaky, known issue"},
            "created_at": skipped[0]["created_at"],
        }
    ]


def test_skip_node_records_the_gate_it_bypassed(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.skip_node(c, "w1", "env_setup", "spec_approval", None)
            )
            evs = database.read(lambda c: events.read_after(c, 0, "w1"))
            return next(e for e in evs if e["type"] == "node_skipped")
        finally:
            await database.close()

    ev = asyncio.run(scenario())
    assert ev["payload"] == {"node_id": "env_setup", "gate": "spec_approval", "note": None}


def test_skip_node_marks_a_running_session_paused_before_it_can_be_read_as_failed(tmp_path):
    """Same race `pause_work_item` guards against (test_pause_resume.py's
    `test_a_paused_row_and_its_event_never_disagree`): the session's own exit
    handler must see 'paused' already there, or a `_terminate`d session reads
    back as 'failed' forever."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="l",
                    result_path="r",
                )
            )
            await database.write(
                lambda c: store.skip_node(c, "w1", "verify", None, None, session_ids=["s1"])
            )
            await database.write(lambda c: store.session_exited(c, "s1", "failed"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
            return row["status"], types
        finally:
            await database.close()

    status, types = asyncio.run(scenario())
    assert status == "paused"
    assert "worker_session_exited" not in types
    assert "worker_session_paused" in types


def _seeded(tmp_path, wid, chain, node_id, node_overrides=None):
    import json

    async def scenario():
        database = await open_db(tmp_path)
        await database.write(
            lambda c: store.create_work_item(
                c,
                id=wid,
                bead_id=None,
                title="t",
                repo="/r",
                chain_template="default",
                chain_definition=json.dumps(chain),
            )
        )
        await database.write(lambda c: store.enter_node(c, wid, node_id))
        if node_overrides is not None:
            await database.write(lambda c: store.set_node_overrides(c, wid, node_overrides))
        return database

    return asyncio.run(scenario())


def test_effective_auto_escalate_stuck_falls_back_to_the_default(tmp_path):
    chain = {"nodes": [{"id": "implementation", "tasks": []}]}
    database = _seeded(tmp_path, "w1", chain, "implementation")
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_stuck(row, True) is True
    assert store.effective_auto_escalate_stuck(row, False) is False


def test_effective_auto_escalate_stuck_the_node_value_beats_the_default(tmp_path):
    chain = {"nodes": [{"id": "implementation", "tasks": [], "auto_escalate_stuck": False}]}
    database = _seeded(tmp_path, "w1", chain, "implementation")
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_stuck(row, True) is False


def test_effective_auto_escalate_stuck_the_override_beats_the_node_value(tmp_path):
    chain = {"nodes": [{"id": "implementation", "tasks": [], "auto_escalate_stuck": False}]}
    database = _seeded(
        tmp_path,
        "w1",
        chain,
        "implementation",
        node_overrides={"implementation": {"auto_escalate_stuck": True}},
    )
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_stuck(row, False) is True


def test_effective_auto_escalate_stuck_survives_a_bare_chain_definition_with_an_override(tmp_path):
    database = _seeded(
        tmp_path,
        "w1",
        {},
        "implementation",
        node_overrides={"implementation": {"auto_escalate_stuck": True}},
    )
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    # Regression: `effective_chain` indexes `chain_definition["nodes"]`
    # directly once `node_overrides` is non-empty, so a bare `{}`
    # chain_definition plus any override used to raise KeyError before this
    # function's own guard ever ran. No node in the chain to apply the
    # override to -> the caller's default, not a crash.
    assert store.effective_auto_escalate_stuck(row, True) is True
    assert store.effective_auto_escalate_stuck(row, False) is False


def test_effective_auto_escalate_delay_s_falls_back_to_the_default(tmp_path):
    chain = {"nodes": [{"id": "implementation", "tasks": []}]}
    database = _seeded(tmp_path, "w1", chain, "implementation")
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_delay_s(row, 0) == 0
    assert store.effective_auto_escalate_delay_s(row, 300) == 300


def test_effective_auto_escalate_delay_s_the_node_value_beats_the_default(tmp_path):
    chain = {"nodes": [{"id": "implementation", "tasks": [], "auto_escalate_delay_s": 60}]}
    database = _seeded(tmp_path, "w1", chain, "implementation")
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_delay_s(row, 0) == 60


def test_effective_auto_escalate_delay_s_the_override_beats_the_node_value(tmp_path):
    chain = {"nodes": [{"id": "implementation", "tasks": [], "auto_escalate_delay_s": 60}]}
    database = _seeded(
        tmp_path,
        "w1",
        chain,
        "implementation",
        node_overrides={"implementation": {"auto_escalate_delay_s": 5}},
    )
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_delay_s(row, 0) == 5


def test_effective_auto_escalate_delay_s_survives_a_bare_chain_definition_with_an_override(
    tmp_path,
):
    database = _seeded(
        tmp_path,
        "w1",
        {},
        "implementation",
        node_overrides={"implementation": {"auto_escalate_delay_s": 5}},
    )
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", ("w1",)).fetchone()
    )
    assert store.effective_auto_escalate_delay_s(row, 30) == 30
