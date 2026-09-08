import asyncio
import json
import uuid

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


def test_create_work_item_round_trips_a_description(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B-1",
                    title="short label",
                    description="the long brief the spec is written from",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=_CHAIN,
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["description"] == "the long brief the spec is written from"

            # The event payload stays a scannable label: the brief does not go in it.
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            created = [e for e in evts if e["type"] == "work_item_created"]
            assert len(created) == 1
            assert "description" not in created[0]["payload"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_work_item_without_a_description_stores_null(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["description"] is None
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


def test_complete_node_is_idempotent(tmp_path):
    """Resume can re-enter an already-completed node; only one node_completed."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(lambda c: store.load_chain(c, "w1", "env_setup"))
            await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
            await database.write(lambda c: store.complete_node(c, "w1", "env_setup"))
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0))]
            assert types.count("node_completed") == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_sessions_capped_out_scoped_to_measuring_hook_points(tmp_path):
    """Only the node's measuring sessions are capped_out, not the fix task's."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            for sid, hook in (("measure", "on.test.run"), ("fix", "on.implementation.start")):
                await database.write(
                    lambda c, sid=sid, hook=hook: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point=hook,
                        log_path="/l",
                        result_path="/r",
                    )
                )
            await database.write(
                lambda c: store.mark_sessions_capped_out(c, "w1", "verify", ["on.test.run"])
            )
            rows = database.read(
                lambda c: c.execute(
                    "SELECT id, status FROM worker_sessions WHERE work_item_id='w1'"
                ).fetchall()
            )
            status = {r["id"]: r["status"] for r in rows}
            assert status == {"measure": "capped_out", "fix": "pending"}

            # The SPA only learns session status from worker_session_* events, so
            # the cap must emit one for each session it flipped (and only those).
            evts = database.read(lambda c: events.read_after(c, 0, "w1"))
            capped_evts = [
                e
                for e in evts
                if e["type"] == "worker_session_exited" and e["payload"]["status"] == "capped_out"
            ]
            assert [e["payload"]["session_id"] for e in capped_evts] == ["measure"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_done_with_concerns_is_not_capped_out_by_a_sibling_breach(tmp_path):
    """A `done_with_concerns` session on a capping node keeps its status and text;
    only the still-pending sibling that actually breached becomes capped_out."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            for sid, hook in (("concerns", "on.test.run"), ("pending", "on.test.run")):
                await database.write(
                    lambda c, sid=sid, hook=hook: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point=hook,
                        log_path="/l",
                        result_path="/r",
                    )
                )
            await database.write(
                lambda c: store.session_exited(c, "concerns", "done_with_concerns")
            )
            await database.write(
                lambda c: store.mark_sessions_capped_out(c, "w1", "verify", ["on.test.run"])
            )
            rows = database.read(
                lambda c: c.execute(
                    "SELECT id, status FROM worker_sessions WHERE work_item_id='w1'"
                ).fetchall()
            )
            status = {r["id"]: r["status"] for r in rows}
            assert status == {"concerns": "done_with_concerns", "pending": "capped_out"}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_session_exited_carries_concerns_and_question_on_the_event(tmp_path):
    """The result file's free text is the only channel these two fields have —
    there is no `concerns` column (Task 1's migration deliberately adds none) —
    so `session_exited` must stamp them onto `worker_session_exited` itself,
    where `GET /work-items/{wid}` can read them back without touching disk."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-concerns",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(
                lambda c: store.session_exited(
                    c, "s-concerns", "done_with_concerns", concerns="the retry path is untested"
                )
            )
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "worker_session_exited"
            assert ev["payload"]["concerns"] == "the retry path is untested"
            assert "question" not in ev["payload"]

            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-question",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(
                lambda c: store.session_exited(
                    c, "s-question", "needs_context", question="which branch is the target?"
                )
            )
            ev2 = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev2["payload"]["question"] == "which branch is the target?"
            assert "concerns" not in ev2["payload"]
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
            # create_session announces the session (Kraft-dce)
            created = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert created["type"] == "worker_session_created"
            assert created["payload"] == {
                "session_id": "s1",
                "node_id": "env_setup",
                "hook_point": "on.env.prepare",
                "round": 0,
            }

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
                # the round rides both session events: the client merges them, and
                # without it here the merge would reset a fix cycle to round 0
                "round": 0,
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


def test_create_session_announces_the_session(tmp_path):
    """Every session must announce itself at creation, whatever the hook kind.

    `worker_session_started` is emitted only by `session_running`, which only the
    subprocess adapter calls. A builtin hook goes create_session -> session_exited,
    so the SPA is never told the session exists; `worker_session_exited` carries
    only {session_id, status}, with no node_id or hook_point to build a row from.
    The client then has no session for the current node, decides no gate is
    awaiting, and renders no Approve button until someone reloads (Kraft-dce).
    """

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s9",
                    work_item_id="w1",
                    node_id="chain_review",
                    hook_point="on.chain.review_ready",
                    log_path="/l",
                    result_path="/r",
                )
            )
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "worker_session_created"
            assert ev["payload"] == {
                "session_id": "s9",
                "node_id": "chain_review",
                "hook_point": "on.chain.review_ready",
                "round": 0,
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_work_item_stores_attachments_and_events_them(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/repo",
        chain_template="default",
        chain_definition="{}",
        attachments=[{"kind": "plan", "path": ".engineering/plans/p.md"}],
    )
    row = conn.execute("SELECT attachments FROM work_items WHERE id='w1'").fetchone()
    assert json.loads(row["attachments"]) == [{"kind": "plan", "path": ".engineering/plans/p.md"}]
    types = [e["type"] for e in events.read_after(conn, 0, "w1")]
    assert "work_item_attachments" in types


def test_create_work_item_without_attachments_stores_null(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/repo",
        chain_template="default",
        chain_definition="{}",
    )
    row = conn.execute("SELECT attachments FROM work_items WHERE id='w1'").fetchone()
    assert row["attachments"] is None
    types = [e["type"] for e in events.read_after(conn, 0, "w1")]
    assert "work_item_attachments" not in types


def test_set_base_ref(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="quick-task",
        chain_definition="{}",
    )
    assert conn.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()[0] is None
    store.set_base_ref(conn, "w1", "abc123")
    assert conn.execute("SELECT base_ref FROM work_items WHERE id='w1'").fetchone()[0] == "abc123"


def test_sessions_for_round_filters_by_node_and_round(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    store.create_work_item(
        conn,
        id="w1",
        bead_id="B",
        title="t",
        repo="/r",
        chain_template="default",
        chain_definition="{}",
    )
    # A second work item at the same (node_id, round) proves the work_item_id
    # filter actually does something — every other seeded session shares "w1", so
    # dropping "AND work_item_id = ?" from the query would still pass.
    store.create_work_item(
        conn,
        id="w2",
        bead_id="B2",
        title="t2",
        repo="/r",
        chain_template="default",
        chain_definition="{}",
    )
    store.create_session(
        conn,
        id="other",
        work_item_id="w2",
        node_id="verify",
        hook_point="on.test.run",
        log_path="/l/other",
        result_path="/r/other",
        round=0,
    )
    for sid, node, rnd in [
        ("s1", "verify", 0),
        ("s2", "verify", 0),
        ("s3", "verify", 1),
        ("s4", "merge", 0),
    ]:
        store.create_session(
            conn,
            id=sid,
            work_item_id="w1",
            node_id=node,
            hook_point="on.test.run",
            log_path=f"/l/{sid}",
            result_path=f"/r/{sid}",
            round=rnd,
        )
    got = [r["id"] for r in store.sessions_for_round(conn, "w1", "verify", 0)]
    assert got == ["s1", "s2"]


async def _spend_fixture_one(database, *, costs: list[float | None]) -> str:
    """One work item with one session per entry in `costs`. Returns its id."""
    wid = uuid.uuid4().hex
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo="/tmp/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    for i, cost in enumerate(costs):
        sid = uuid.uuid4().hex
        await database.write(
            lambda c, sid=sid, i=i: store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id="n",
                hook_point=f"on.h{i}",
                log_path="/tmp/l",
                result_path="/tmp/r",
            )
        )
        await database.write(
            lambda c, sid=sid, cost=cost: c.execute(
                "UPDATE worker_sessions SET cost_usd = ? WHERE id = ?", (cost, sid)
            )
        )
    return wid


async def _spend_fixture(database, *, mine: list[float], theirs: list[float]) -> tuple[str, str]:
    return (
        await _spend_fixture_one(database, costs=mine),
        await _spend_fixture_one(database, costs=theirs),
    )


def test_budget_spend_sums_only_this_items_sessions(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            mine, _theirs = await _spend_fixture(database, mine=[1.5, 2.25], theirs=[99.0])
            item_usd, daily_usd = database.read(lambda c: store.budget_spend(c, mine))
            assert item_usd == 3.75
            assert daily_usd == 102.75  # daily is instance-wide, not per item
        finally:
            await database.close()

    asyncio.run(scenario())


def test_null_cost_sessions_count_as_zero(tmp_path):
    """A running session, or a subprocess/builtin task, has no cost yet.

    Under-counting in-flight spend is a direct consequence of the design: cost
    only exists once the agent's session has exited.
    """

    async def scenario():
        database = await _open(tmp_path)
        try:
            wid = await _spend_fixture_one(database, costs=[1.0, None, None])
            item_usd, _daily_usd = database.read(lambda c: store.budget_spend(c, wid))
            assert item_usd == 1.0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_no_sessions_is_zero_not_none(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            wid = await _spend_fixture_one(database, costs=[])
            assert database.read(lambda c: store.budget_spend(c, wid)) == (0.0, 0.0)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_daily_window_excludes_yesterday(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            wid = await _spend_fixture_one(database, costs=[5.0])
            # backdate the only session a day
            await database.write(
                lambda c: c.execute(
                    "UPDATE worker_sessions SET created_at = ?", ("2020-01-01T00:00:00+00:00",)
                )
            )
            item_usd, daily_usd = database.read(
                lambda c: store.budget_spend(c, wid, since="2020-06-01T00:00:00+00:00")
            )
            assert daily_usd == 0.0
            # the per-item figure is NOT windowed: an item's budget spans its whole life
            assert item_usd == 5.0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_local_midnight_is_utc_and_sorts_against_stored_timestamps():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    noon_in_tokyo = datetime(2026, 9, 5, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    midnight = store.local_midnight_utc(noon_in_tokyo)
    # 2026-09-05T00:00+09:00 is 2026-09-04T15:00Z
    assert midnight == "2026-09-04T15:00:00+00:00"
    # string comparison is the whole point: it is what the SQL does
    assert "2026-09-04T15:00:00.123456+00:00" >= midnight
    assert "2026-09-04T14:59:59.999999+00:00" < midnight
