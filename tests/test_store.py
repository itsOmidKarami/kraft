import asyncio
import json
import subprocess
import uuid

from kraft import db, events, store
from kraft.policy import Cap

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


def test_auto_gate_defaults_off_and_round_trips(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition="{}",
                )
            )
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w2",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition="{}",
                    auto_gate=True,
                )
            )
            rows = {
                r["id"]: r["auto_gate"]
                for r in database.read(
                    lambda c: c.execute("SELECT id, auto_gate FROM work_items").fetchall()
                )
            }
            assert rows == {"w1": 0, "w2": 1}
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


def test_needs_human_names_the_stop_it_is_about_not_an_older_failure(tmp_path):
    """Kraft-eh6p's "view log" button hangs off `session_id`. A node that failed
    once, was retried, and then stopped for a *question* must not hand the human
    the older failure's log: it looks like the answer and is not. And a stop with
    no session to explain it (a budget breach) must offer no button at all."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database, "wh")
            for sid, status in (("older", "failed"), ("asked", "needs_context")):
                await database.write(
                    lambda c, sid=sid: store.create_session(
                        c,
                        id=sid,
                        work_item_id="wh",
                        node_id="verify",
                        hook_point="on.test.run",
                        log_path="/l",
                        result_path="/r",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )

            await database.write(
                lambda c: store.mark_needs_human(c, "wh", "verify", "needs_context: which db?")
            )
            asked = database.read(lambda c: events.read_after(c, 0, "wh"))[-1]

            # A later session that explains nothing (a budget-refused launch) —
            # the button goes away rather than pointing back at "asked".
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="refused",
                    work_item_id="wh",
                    node_id="verify",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.mark_needs_human(c, "wh", "verify", "over budget"))
            broke = database.read(lambda c: events.read_after(c, 0, "wh"))[-1]
            return asked["payload"], broke["payload"]
        finally:
            await database.close()

    asked, broke = asyncio.run(scenario())

    assert asked["session_id"] == "asked", "the stop names an older, unrelated failure"
    assert "session_id" not in broke, "a stop no session explains still offered a log"


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
                "attempt": 1,
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
                "attempt": 1,
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
                "attempt": 1,
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


def test_set_escalation_session(tmp_path):
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
    row = conn.execute("SELECT escalation_session_id FROM work_items WHERE id='w1'").fetchone()
    assert row[0] is None
    store.set_escalation_session(conn, "w1", "cli-session-abc")
    row = conn.execute("SELECT escalation_session_id FROM work_items WHERE id='w1'").fetchone()
    assert row[0] == "cli-session-abc"


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


def _mk_session(database, sid, *, wid="w1", node_id="verify", hook_point="on.test.run"):
    """One session row through the real writer, with the defaults every attempt
    test shares: work item w1, node verify, hook point on.test.run."""
    return database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id=wid,
            node_id=node_id,
            hook_point=hook_point,
            log_path=f"/l/{sid}",
            result_path=f"/r/{sid}",
        )
    )


def test_create_session_numbers_attempts_per_hook_point(tmp_path):
    """Re-running one node's hook point — a resumed pause, a retry, each on.ci.poll
    pass — has to count up. Every row used to read attempt 1 (Kraft-kq8m)."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            for sid in ("s1", "s2", "s3"):
                await _mk_session(database, sid)
            got = database.read(
                lambda c: [
                    (r["id"], r["attempt"])
                    for r in c.execute("SELECT id, attempt FROM worker_sessions ORDER BY id")
                ]
            )
            assert got == [("s1", 1), ("s2", 2), ("s3", 3)]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_session_attempts_are_scoped_to_node_and_hook_point(tmp_path):
    """The count is per (work_item_id, node_id, hook_point) — the scope
    CurrentNodePanel's per-row 'attempt N' already assumes. A sibling hook point on
    the same node, and the same hook point on another work item, both start at 1."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await _mk_item(database, "w2")
            for sid in ("s1", "s2", "s3"):
                await _mk_session(database, sid)
            await _mk_session(database, "sibling", hook_point="on.implementation.start")
            await _mk_session(database, "other", wid="w2")
            got = database.read(
                lambda c: {
                    r["id"]: r["attempt"]
                    for r in c.execute("SELECT id, attempt FROM worker_sessions")
                }
            )
            assert got == {"s1": 1, "s2": 2, "s3": 3, "sibling": 1, "other": 1}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_worker_session_events_carry_the_attempt(tmp_path):
    """Both session events carry the number, or the live board shows blankSession's
    hardcoded 1 until the next hydrate."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await _mk_session(database, "s1")
            await _mk_session(database, "s2")
            created = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert created["type"] == "worker_session_created"
            assert created["payload"]["session_id"] == "s2"
            assert created["payload"]["attempt"] == 2

            await database.write(lambda c: store.session_running(c, "s2", 4321, 111.5))
            started = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert started["type"] == "worker_session_started"
            assert started["payload"]["attempt"] == 2
        finally:
            await database.close()

    asyncio.run(scenario())


def test_branch_name_slugs_the_title_and_stays_a_legal_ref(tmp_path):
    """Every branch this produces has to survive `git check-ref-format`, over
    the shapes a Kraft title actually takes: punctuation, non-ASCII, git's
    reserved characters, and the 360-character paragraph `mr_title` notes."""
    wid = "b63d95be41884b6e83a423c114a97ce3"
    readable_merge_records = (
        "Readable merge records: branch slugs & GFM tables!",
        "kraft/readable-merge-records-branch-slugs-gfm-tables-b63d95be",
    )
    # git refuses ~ ^ : and spaces in a ref; .. is reserved
    caret_and_colon = (
        "Fix ~caret^ and :colon and .. spaces",
        "kraft/fix-caret-and-colon-and-spaces-b63d95be",
    )
    # clipped at 48 characters, with no trailing separator left behind
    clipped_title = (
        "Kraft-8mu.5.2 — session lifecycle: pause, abandon and reattach must run",
        "kraft/kraft-8mu-5-2-session-lifecycle-pause-abandon-an-b63d95be",
    )
    cases = dict(
        (
            readable_merge_records,
            caret_and_colon,
            clipped_title,
            ("A" * 360, "kraft/" + "a" * 48 + "-b63d95be"),
            # only the first line of a multi-line title
            ("Branch slugs\n\nand a second paragraph", "kraft/branch-slugs-b63d95be"),
            # nothing to slug: the pre-Kraft-nhps name, which is always valid
            ("我的任务", f"kraft/{wid}"),
            ("...", f"kraft/{wid}"),
            ("   ", f"kraft/{wid}"),
            ("", f"kraft/{wid}"),
        )
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for title, expected in cases.items():
        got = store.branch_name(title, wid)
        assert got == expected, f"{title!r} -> {got!r}"
        assert (
            subprocess.run(
                ["git", "check-ref-format", "--branch", got],
                cwd=repo,
                capture_output=True,
            ).returncode
            == 0
        ), f"{got!r} is not a legal branch name"


def test_create_work_item_stores_the_branch(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B-1",
                    title="Readable merge records",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=_CHAIN,
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["branch"] == "kraft/readable-merge-records-w1"
            assert store.branch_for(row) == "kraft/readable-merge-records-w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_branch_for_falls_back_when_the_row_predates_the_column(tmp_path):
    """The no-stranding guarantee: an in-flight item whose row was written
    before the migration keeps the `kraft/<id>` branch its worktree is on."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: c.execute("UPDATE work_items SET branch = NULL WHERE id='w1'")
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert store.branch_for(row) == "kraft/w1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_last_rejection_reads_the_note_and_the_target_back(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
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
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
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


def test_retry_after_cap_clears_the_gate_reject_counter_too(tmp_path):
    """Kraft-ko7j §A4: without this the cap becomes a dead end one step out —
    the counter is spent, the gate re-opens after every retry, and every
    rejection after that is refused forever."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            cap = Cap(attempts=1, wall_clock_s=3600)
            await database.write(
                lambda c: store.bump_counter(c, "w1", "spec_approval_reject_loop", cap)
            )
            await database.write(lambda c: store.bump_counter(c, "w1", "verify_fix_loop", cap))
            await database.write(
                lambda c: store.retry_after_cap(
                    c,
                    "w1",
                    "spec",
                    "verify_fix_loop",
                    None,
                    gate_key="spec_approval_reject_loop",
                )
            )
            assert database.read(lambda c: store.read_counter(c, "w1", "verify_fix_loop")) is None
            assert (
                database.read(lambda c: store.read_counter(c, "w1", "spec_approval_reject_loop"))
                is None
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_rate_limited_sets_status_and_retry_at(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "rate_limited"
            assert row["retry_at"] == "2026-09-10T00:00:00Z"
            ev = database.read(lambda c: events.read_after(c, 0))[-1]
            assert ev["type"] == "work_item_rate_limited"
            assert ev["payload"] == {
                "node_id": "implementation",
                "retry_at": "2026-09-10T00:00:00Z",
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_needs_human_clears_retry_at(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            await database.write(
                lambda c: store.mark_needs_human(c, "w1", "implementation", "boom")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["retry_at"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_retry_after_cap_clears_retry_at(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            await database.write(
                lambda c: store.retry_after_cap(c, "w1", "implementation", None, "go")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "active"
            assert row["retry_at"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_set_title_records_an_event(tmp_path):
    """A title edit gets its own event type. Not a shared `work_item_edited`
    with `set_description`: the description is prepended to every agent
    instruction and the title is a label, so a timeline that cannot tell them
    apart answers neither question."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(lambda c: store.set_title(c, "w1", "a better label"))
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["title"] == "a better label"

            evs = database.read(lambda c: events.read_after(c, 0, "w1"))
            edits = [e for e in evs if e["type"] == "work_item_title_edited"]
            assert [e["payload"]["title"] for e in edits] == ["a better label"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_merge_rank_order_puts_the_deepest_path_first():
    assert store.merge_rank_order(["libs/a", "vendor/deep/b", "x"]) == [
        "vendor/deep/b",
        "libs/a",
        "x",
    ]


def test_add_repo_and_repos_for_round_trip(tmp_path):
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
    sub_id = store.add_repo(
        conn,
        work_item_id="w1",
        repo_path="/wt/repos/pkg",
        role="submodule",
        submodule_path="repos/pkg",
        merge_rank=1,
    )
    store.add_repo(conn, work_item_id="w1", repo_path="/wt", role="root", merge_rank=2)

    repos = store.repos_for(conn, "w1")

    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["path"] == "/wt/repos/pkg"
    assert repos[0]["state"] == "pending"
    assert repos[0]["mr_ref"] is None

    store.update_repo_state(
        conn, sub_id, merge_state="merged", mr_ref={"number": 3, "url": "http://x/3"}
    )
    repos = store.repos_for(conn, "w1")
    assert repos[0]["state"] == "merged"
    assert repos[0]["mr_ref"] == {"number": 3, "url": "http://x/3"}


def test_repos_for_is_empty_for_a_single_repo_item(tmp_path):
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
    assert store.repos_for(conn, "w1") == []


def test_create_session_stores_the_head_sha(tmp_path):
    """Kraft-lu2: the gate compares a measurement's sha against this."""

    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-sha",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l",
                    result_path="/r",
                    head_sha="abc123",
                )
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT head_sha FROM worker_sessions WHERE id = 's-sha'"
                ).fetchone()
            )
            assert row["head_sha"] == "abc123"

            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-nosha",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l",
                    result_path="/r",
                )
            )
            row2 = database.read(
                lambda c: c.execute(
                    "SELECT head_sha FROM worker_sessions WHERE id = 's-nosha'"
                ).fetchone()
            )
            assert row2["head_sha"] is None
        finally:
            await database.close()

    asyncio.run(scenario())
