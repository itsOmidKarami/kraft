import asyncio

import pytest
from support.store_fixtures import mk_item, open_db

from kraft import db, events, store
from kraft.usage import Usage


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
                "thread": 1,
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
                "thread": 1,
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


def test_create_session_defaults_thread_to_one(tmp_path):
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
                lambda c: c.execute("SELECT thread FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["thread"] == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_create_session_stores_an_explicit_thread(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l",
                    result_path="/r",
                    thread=2,
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT thread FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["thread"] == 2
        finally:
            await database.close()

    asyncio.run(scenario())


def test_latest_escalation_thread_and_turn_count(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            assert database.read(lambda c: store.latest_escalation_thread(c, "w1")) == 0

            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l1",
                    result_path="/r1",
                    thread=1,
                )
            )
            assert database.read(lambda c: store.latest_escalation_thread(c, "w1")) == 1
            assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 1)) == 1

            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e2",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l2",
                    result_path="/r2",
                    thread=1,
                )
            )
            assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 1)) == 2

            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e3",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l3",
                    result_path="/r3",
                    thread=2,
                )
            )
            assert database.read(lambda c: store.latest_escalation_thread(c, "w1")) == 2
            assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 1)) == 2
            assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 2)) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_escalation_threads_projection(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l1",
                    result_path="/r1",
                    thread=1,
                )
            )
            await database.write(lambda c: store.session_exited(c, "e1", "done"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e2",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l2",
                    result_path="/r2",
                    thread=1,
                )
            )
            await database.write(lambda c: store.session_exited(c, "e2", "done"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="e3",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path="/l3",
                    result_path="/r3",
                    thread=2,
                )
            )
            await database.write(lambda c: store.session_exited(c, "e3", "needs_context"))

            threads = database.read(lambda c: store.escalation_threads(c, "w1"))
            assert [t["thread"] for t in threads] == [1, 2]
            assert [t["turns"] for t in threads] == [2, 1]
            assert threads[0]["session_id"] == "e2"
            assert threads[1]["session_id"] == "e3"
            assert threads[1]["status"] == "needs_context"
            assert threads[0]["ended_at"] is not None
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
                "thread": 1,
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


def test_reusable_session_matches_a_done_session_at_the_current_head(tmp_path):
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
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            got = database.read(
                lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, "sha-a")
            )
            assert got is not None and got["id"] == "s1"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_rejects_a_stale_head_sha(tmp_path):
    """The worktree moved since this session ran (a rebase, or a fix commit
    from a later cycle) -- the recorded 'done' no longer describes the code as
    it stands, so it must not be reused."""

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
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha="sha-old",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            got = database.read(
                lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, "sha-new")
            )
            assert got is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_rejects_a_null_head_sha_on_either_side(tmp_path):
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
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    # no head_sha kwarg -- stays NULL
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            # a NULL stored row never matches a real sha the caller asks about
            assert (
                database.read(
                    lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, "sha-a")
                )
                is None
            )
            # a caller with no sha of its own (worktree HEAD unreadable) never reuses either
            assert (
                database.read(
                    lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, None)
                )
                is None
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_ignores_a_non_done_status(tmp_path):
    """`failed`, `rate_limited`, `done_with_concerns` and still-`running` are
    all not a known-good result to stand in for a fresh dispatch."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            for sid, status in (
                ("s-failed", "failed"),
                ("s-rl", "rate_limited"),
                ("s-concerns", "done_with_concerns"),
            ):
                await database.write(
                    lambda c, sid=sid: store.create_session(
                        c,
                        id=sid,
                        work_item_id="w1",
                        node_id="verify",
                        hook_point="on.test.run",
                        log_path="/l",
                        result_path="/r",
                        round=0,
                        head_sha="sha-a",
                    )
                )
                await database.write(
                    lambda c, sid=sid, status=status: store.session_exited(c, sid, status)
                )
            got = database.read(
                lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, "sha-a")
            )
            assert got is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_excludes_a_session_whose_node_already_completed(tmp_path):
    """A node revisited after its own completion (a gate rejection walking
    back to it, not a crash mid-measurement) must not reuse a prior pass's
    session even if hook_point/round/head_sha all still line up -- an agent
    task that makes no further change on a second pass leaves head_sha
    exactly where the first, already-closed pass left it too."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            await database.write(lambda c: store.complete_node(c, "w1", "implementation"))
            got = database.read(
                lambda c: store.reusable_session(
                    c, "w1", "implementation", "on.implementation.start", 0, "sha-a"
                )
            )
            assert got is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_excludes_a_second_pass_after_a_second_rejection(tmp_path):
    """`complete_node` only ever writes `node_completed` once (Kraft-gbt /
    Kraft-126), so a SECOND pass over a node reached via `reject_to` closes
    out as a silent no-op -- nothing marks it done a second time. Without the
    `node_started` re-entry check, the second pass's own session would still
    be sitting there, unexcluded, ready to be handed back on a third pass."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            # pass 1: implementation runs, then the chain walks on to a gate
            # node before rejecting back.
            await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l1",
                    result_path="/r1",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            await database.write(lambda c: store.complete_node(c, "w1", "implementation"))
            await database.write(lambda c: store.enter_node(c, "w1", "human_review"))

            # pass 2: rejected back to implementation. Its own session leaves
            # head_sha unchanged, and `complete_node` no-ops this time.
            await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s2",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l2",
                    result_path="/r2",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s2", "done"))
            await database.write(lambda c: store.complete_node(c, "w1", "implementation"))
            await database.write(lambda c: store.enter_node(c, "w1", "human_review"))

            # pass 3: rejected a second time -- `measure_node` enters the node
            # (writing its own `node_started`) before it ever asks whether a
            # prior session can stand in for a fresh dispatch.
            await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
            got = database.read(
                lambda c: store.reusable_session(
                    c, "w1", "implementation", "on.implementation.start", 0, "sha-a"
                )
            )
            assert got is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_ignores_a_stuck_pending_sibling(tmp_path):
    """A multi-scope `on.test.run` mints one session per scope, all sharing
    (node, hook point, round, head_sha) -- a crash between scopes leaves an
    earlier scope `done` and the next one stuck `pending` forever. The `done`
    row must not be handed back as if the whole hook finished (code review)."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-scope-a",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l1",
                    result_path="/r1",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-scope-a", "done"))
            # scope B's row is inserted before it runs, and the crash lands
            # before it ever exits -- left stuck 'pending'.
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-scope-b",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l2",
                    result_path="/r2",
                    round=0,
                    head_sha="sha-a",
                )
            )
            got = database.read(
                lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, "sha-a")
            )
            assert got is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_ignores_a_failed_sibling(tmp_path):
    """Batch C·MR1 Task 1 (Kraft-s7c04.9/.8/.14): once C2 stops short-circuiting
    the scope loop on the first failure, a crash/resume at the same round and
    head must not hand back the *last* scope's row as if the whole
    `on.test.run` task passed -- an earlier sibling scope that genuinely
    failed makes this attempt not reusable, the same way a still-open
    (pending/running) sibling already does above."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-scope-a",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l1",
                    result_path="/r1",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-scope-a", "failed"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-scope-b",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l2",
                    result_path="/r2",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-scope-b", "done"))
            got = database.read(
                lambda c: store.reusable_session(c, "w1", "verify", "on.test.run", 0, "sha-a")
            )
            assert got is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_latest_session_per_task_drops_a_failed_earlier_attempt(tmp_path):
    """Kraft-s15p0: an earlier attempt's failed row for the same hook_point
    must not survive alongside the current attempt's own row."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-attempt-1",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="on.plan.requested",
                    log_path="/l1",
                    result_path="/r1",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-attempt-1", "failed"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-attempt-2",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="on.plan.requested",
                    log_path="/l2",
                    result_path="/r2",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-attempt-2", "done"))
            rows = database.read(
                lambda c: store.latest_session_per_task(c, "w1", "plan", ["on.plan.requested"])
            )
            assert [r["id"] for r in rows] == ["s-attempt-2"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_latest_session_per_task_ignores_a_hook_point_the_node_never_declared(tmp_path):
    """Kraft-s15p0: an `escalation` session is never one of `node['tasks']`,
    so it must never be selected regardless of when it ran."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-escalation",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="escalation",
                    log_path="/l1",
                    result_path="/r1",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-escalation", "done"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-task",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="on.plan.requested",
                    log_path="/l2",
                    result_path="/r2",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-task", "done"))
            rows = database.read(
                lambda c: store.latest_session_per_task(c, "w1", "plan", ["on.plan.requested"])
            )
            assert [r["id"] for r in rows] == ["s-task"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_latest_session_per_task_omits_a_hook_point_with_no_session_at_all(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            rows = database.read(
                lambda c: store.latest_session_per_task(c, "w1", "plan", ["on.plan.requested"])
            )
            assert rows == []
        finally:
            await database.close()

    asyncio.run(scenario())


def test_latest_session_per_task_surfaces_a_stuck_sibling_over_a_finished_scope(tmp_path):
    """A multi-scope `on.test.run` mints one session per scope sharing this
    hook_point -- a crash between scopes leaves an earlier scope `done` and
    the next one stuck `pending`. Picking the merely-latest-created row would
    return the finished scope and read the whole hook as clean; the stuck
    sibling must win instead so the caller's `all(... in _ADVANCING)` check
    still fails closed (code review)."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-scope-a",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l1",
                    result_path="/r1",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-scope-a", "done"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-scope-b",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l2",
                    result_path="/r2",
                )
            )
            rows = database.read(
                lambda c: store.latest_session_per_task(c, "w1", "verify", ["on.test.run"])
            )
            assert [r["id"] for r in rows] == ["s-scope-b"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_excludes_a_node_rejected_back_to_itself(tmp_path):
    """The `node_started` re-entry check needs a departure to another node in
    between, which a gate with no `reject_to` never produces: `plan`'s gate
    (`templates/default.yaml`) re-enters `plan` itself, and its artifact lives
    in gitignored `.engineering/`, so `head_sha` does not move either. Without
    the `gate_rejected` check, the first pass's session matches every other
    key on a second rejection, the plan agent never runs, and the human's
    second rejection note is silently dropped."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            # pass 1: plan runs and closes out; its gate is then rejected,
            # which re-enters `plan` itself -- no other node in between.
            await database.write(lambda c: store.enter_node(c, "w1", "plan"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="on.plan.requested",
                    log_path="/l1",
                    result_path="/r1",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))
            await database.write(lambda c: store.complete_node(c, "w1", "plan"))
            await database.write(
                lambda c: store.reject_gate(
                    c, "w1", "plan_approval", "section 3 is wrong", reopen=True
                )
            )
            # pass 2: the re-planned document is rejected a second time. Its
            # own `complete_node` is the silent no-op (Kraft-gbt / Kraft-126),
            # so nothing marks `plan` done again and the `node_completed`
            # check has nothing newer than s2 to exclude against.
            await database.write(lambda c: store.enter_node(c, "w1", "plan"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s2",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="on.plan.requested",
                    log_path="/l2",
                    result_path="/r2",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s2", "done"))
            await database.write(lambda c: store.complete_node(c, "w1", "plan"))
            await database.write(
                lambda c: store.reject_gate(
                    c, "w1", "plan_approval", "section 3 is still wrong", reopen=True
                )
            )
            # pass 3 enters `plan` and asks whether s2 can stand in for it.
            await database.write(lambda c: store.enter_node(c, "w1", "plan"))

            got = database.read(
                lambda c: store.reusable_session(c, "w1", "plan", "on.plan.requested", 0, "sha-a")
            )
            assert got is None, "a rejected node must re-run its agent, not reuse the last pass"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reusable_session_still_reuses_a_crash_recovery_after_an_older_rejection(tmp_path):
    """The `gate_rejected` exclusion is scoped to rejections that come *after*
    the candidate session. A rejection earlier in the item's life started the
    pass this session belongs to, so it must not block the reuse Kraft-gl9d is
    for."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.enter_node(c, "w1", "plan"))
            await database.write(
                lambda c: store.reject_gate(
                    c, "w1", "plan_approval", "try again", reopen=True, node="plan"
                )
            )
            # the pass the rejection started: its session runs, then the
            # server crashes and re-enters the same still-open node.
            await database.write(lambda c: store.enter_node(c, "w1", "plan"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="plan",
                    hook_point="on.plan.requested",
                    log_path="/l1",
                    result_path="/r1",
                    round=0,
                    head_sha="sha-a",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s1", "done"))

            got = database.read(
                lambda c: store.reusable_session(c, "w1", "plan", "on.plan.requested", 0, "sha-a")
            )
            assert got is not None and got["id"] == "s1"
        finally:
            await database.close()

    asyncio.run(scenario())


async def _paused_session_seed(database, sid="s1"):
    await mk_item(database)
    await database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id="w1",
            node_id="chain_review",
            hook_point="gate_review",
            log_path="/l",
            result_path="/r",
        )
    )


def test_a_paused_session_records_the_usage_it_was_handed(tmp_path):
    """Kraft-s7c04.18: `session_exited` returns early on a paused row -- so the
    row and the event agree a human's interruption is not a task failure -- and
    it is the only writer of cost_usd. 71 paused rows, 28.0M tokens, NULL cost,
    permanently."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await _paused_session_seed(database)
            await database.write(lambda c: store.session_running(c, "s1", 1, 1.0))
            await database.write(lambda c: store.pause_work_item(c, "w1", ["s1"]))
            await database.write(
                lambda c: store.session_exited(
                    c, "s1", "failed", None, Usage(214_775, 19, 1.37, "claude-opus-5")
                )
            )
            return database.read(
                lambda c: c.execute(
                    "SELECT status, tokens_in, tokens_out, cost_usd, model "
                    "FROM worker_sessions WHERE id = 's1'"
                ).fetchone()
            ), database.read(
                lambda c: [
                    r["type"]
                    for r in c.execute(
                        "SELECT type FROM events WHERE work_item_id = 'w1'"
                    ).fetchall()
                ]
            )
        finally:
            await database.close()

    row, types = asyncio.run(scenario())
    assert row["cost_usd"] == pytest.approx(1.37)
    assert (row["tokens_in"], row["tokens_out"]) == (214_775, 19)
    assert row["model"] == "claude-opus-5"
    # the status is NOT moved and no exit event is emitted: a pause is not a
    # task failure, and worker_session_paused already described this moment
    assert row["status"] == "paused"
    assert "worker_session_exited" not in types


def test_recording_pause_usage_is_a_no_op_on_a_session_that_moved_on(tmp_path):
    """Same reason `session_progress` guards on 'running': a row that has
    settled must not be reopened by a late writer."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await _paused_session_seed(database)
            await database.write(lambda c: store.session_running(c, "s1", 1, 1.0))
            await database.write(
                lambda c: store.session_exited(c, "s1", "done", None, Usage(10, 1, 0.01, "m"))
            )
            await database.write(
                lambda c: store.record_pause_usage(c, "s1", Usage(999, 999, 9.99, "wrong"))
            )
            return database.read(
                lambda c: c.execute(
                    "SELECT cost_usd, model FROM worker_sessions WHERE id = 's1'"
                ).fetchone()
            )
        finally:
            await database.close()

    row = asyncio.run(scenario())
    assert row["cost_usd"] == pytest.approx(0.01)
    assert row["model"] == "m"


def test_a_pause_with_no_envelope_records_nothing_rather_than_zero(tmp_path):
    """An agent SIGINT'd before its first response has no number to report, and
    usage.py deliberately has no rate table. NULL is the honest record; zero
    would be a claim."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await _paused_session_seed(database)
            await database.write(lambda c: store.session_running(c, "s1", 1, 1.0))
            await database.write(lambda c: store.pause_work_item(c, "w1", ["s1"]))
            await database.write(lambda c: store.record_pause_usage(c, "s1", None))
            return database.read(
                lambda c: c.execute(
                    "SELECT cost_usd, tokens_in FROM worker_sessions WHERE id = 's1'"
                ).fetchone()
            )
        finally:
            await database.close()

    row = asyncio.run(scenario())
    assert row["cost_usd"] is None
    assert row["tokens_in"] is None
