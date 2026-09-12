import asyncio

from support.store_fixtures import mk_item, open_db

from kraft import db, events, store


def test_session_lifecycle(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_session_unknown_sets_status_and_event(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_create_session_stores_the_head_sha(tmp_path):
    """Kraft-lu2: the gate compares a measurement's sha against this."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await mk_item(database, "w2")
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
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_session_exited_carries_concerns_and_question_on_the_event(tmp_path):
    """The result file's free text is the only channel these two fields have —
    there is no `concerns` column (Task 1's migration deliberately adds none) —
    so `session_exited` must stamp them onto `worker_session_exited` itself,
    where `GET /work-items/{wid}` can read them back without touching disk."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_create_session_reuses_a_waiting_row_for_the_same_wait_episode(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            first_id, log1, result1 = await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    log_path="/l1",
                    result_path="/r1",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "waiting"))

            second_id, log2, result2 = await database.write(
                lambda c: store.create_session(
                    c,
                    id="s2",
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    log_path="/l2",
                    result_path="/r2",
                    reuse_if_waiting=True,
                )
            )

            assert (second_id, log2, result2) == (first_id, log1, result1)
            count = database.read(
                lambda c: c.execute(
                    "SELECT count(*) FROM worker_sessions WHERE work_item_id='w1'"
                ).fetchone()[0]
            )
            assert count == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_session_inserts_fresh_when_reuse_if_waiting_finds_nothing(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            new_id, log, result = await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    log_path="/l1",
                    result_path="/r1",
                    reuse_if_waiting=True,
                )
            )
            assert (new_id, log, result) == ("s1", "/l1", "/r1")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_session_does_not_reuse_a_settled_session(tmp_path):
    """A 'done'/'failed' prior session must not be mistaken for the same wait
    episode -- only 'waiting' is reusable."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    log_path="/l1",
                    result_path="/r1",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))

            new_id, _, _ = await database.write(
                lambda c: store.create_session(
                    c,
                    id="s2",
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    log_path="/l2",
                    result_path="/r2",
                    reuse_if_waiting=True,
                )
            )
            assert new_id == "s2"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_running_sessions_for_node_includes_an_escalation_on_another_node(tmp_path):
    """`escalate.escalation_running` is not node-scoped, so a live escalation
    row whose node_id has drifted from current_node_id (a stale row after a
    restart) is live to the caller's check and must not be missed by the kill
    (code review finding)."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
            for sid, node, hook in (
                ("s_stale", "verify", "escalation"),
                ("s_other", "verify", "on.implementation.start"),
            ):
                await database.write(
                    lambda c, sid=sid, node=node, hook=hook: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id=node,
                        hook_point=hook,
                        log_path="/l",
                        result_path="/r",
                    )
                )
            found = database.read(lambda c: store.running_sessions_for_node(c, "w1"))
            assert [r["id"] for r in found] == ["s_stale"]
        finally:
            await database.close()

    asyncio.run(scenario())
