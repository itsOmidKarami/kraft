import pytest
from support.store_fixtures import mk_item

from kraft import db, events, store
from kraft.usage import Usage


@pytest.fixture
async def database(database):
    """Every test here starts from work item `w1` (`mk_item`)."""
    await mk_item(database)
    return database


def _session(database, sid, *, wid="w1", node_id="verify", hook_point="on.test.run", **kw):
    """One `create_session` through the real writer. Defaults: work item w1,
    node verify, hook point on.test.run, paths derived from `sid`; `kw` is any
    other `create_session` argument (round, head_sha, thread, command, ...)."""
    return database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id=wid,
            node_id=node_id,
            hook_point=hook_point,
            log_path=f"/l/{sid}",
            result_path=f"/r/{sid}",
            **kw,
        )
    )


def _exit(database, sid, status, *args, **kw):
    return database.write(lambda c: store.session_exited(c, sid, status, *args, **kw))


def _row(database, sid, columns="*"):
    return database.read(
        lambda c: c.execute(
            f"SELECT {columns} FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )


def _last_event(database):
    return database.read(lambda c: events.read_after(c, 0, "w1"))[-1]


def _reusable(database, node_id="verify", hook_point="on.test.run", sha="sha-a"):
    return database.read(lambda c: store.reusable_session(c, "w1", node_id, hook_point, 0, sha))


async def test_session_lifecycle(database):
    await _session(database, "s1", node_id="env_setup", hook_point="on.env.prepare")
    row = _row(database, "s1")
    assert row["status"] == "pending"
    assert row["pid"] is None
    # create_session announces the session (Kraft-dce)
    created = _last_event(database)
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
    row = _row(database, "s1")
    assert (row["status"], row["pid"], row["pid_start_time"]) == ("running", 4321, 111.5)
    started = _last_event(database)
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

    await _exit(database, "s1", "done")
    row = _row(database, "s1", "status, exited_at")
    assert row["status"] == "done"
    assert row["exited_at"] is not None
    assert _last_event(database)["type"] == "worker_session_exited"


@pytest.mark.parametrize(
    ("kwargs", "column", "expected"),
    [
        ({"command": "just e2e-ci"}, "command", "just e2e-ci"),
        ({}, "command", None),
        ({}, "thread", 1),
        ({"thread": 2}, "thread", 2),
        ({"head_sha": "abc123"}, "head_sha", "abc123"),
        ({}, "head_sha", None),
    ],
    ids=[
        "records-the-command",
        "command-defaults-null",
        "thread-defaults-to-one",
        "stores-an-explicit-thread",
        "stores-the-head-sha",
        "head-sha-defaults-null",
    ],
)
async def test_create_session_stores_its_column(database, kwargs, column, expected):
    """What `create_session` is given lands in its own column, and what it is
    not given has the documented default. `head_sha` is what the gate compares a
    measurement against (Kraft-lu2); `command` is the exact subprocess a session
    ran (Kraft-s7c04.35); `thread` is the escalation thread (Kraft-dkb6g)."""
    await _session(database, "s1", **kwargs)
    assert _row(database, "s1", column)[column] == expected


async def test_latest_escalation_thread_and_turn_count(database):
    assert database.read(lambda c: store.latest_escalation_thread(c, "w1")) == 0

    await _session(database, "e1", node_id="implementation", hook_point="escalation", thread=1)
    assert database.read(lambda c: store.latest_escalation_thread(c, "w1")) == 1
    assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 1)) == 1

    await _session(database, "e2", node_id="implementation", hook_point="escalation", thread=1)
    assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 1)) == 2

    await _session(database, "e3", node_id="implementation", hook_point="escalation", thread=2)
    assert database.read(lambda c: store.latest_escalation_thread(c, "w1")) == 2
    assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 1)) == 2
    assert database.read(lambda c: store.escalation_thread_turn_count(c, "w1", 2)) == 1


async def test_escalation_threads_projection(database):
    for sid, thread, status in (("e1", 1, "done"), ("e2", 1, "done"), ("e3", 2, "needs_context")):
        await _session(
            database, sid, node_id="implementation", hook_point="escalation", thread=thread
        )
        await _exit(database, sid, status)

    threads = database.read(lambda c: store.escalation_threads(c, "w1"))
    assert [t["thread"] for t in threads] == [1, 2]
    assert [t["turns"] for t in threads] == [2, 1]
    assert threads[0]["session_id"] == "e2"
    assert threads[1]["session_id"] == "e3"
    assert threads[1]["status"] == "needs_context"
    assert threads[0]["ended_at"] is not None


async def test_session_unknown_sets_status_and_event(database):
    await _session(database, "s1")
    await database.write(lambda c: store.session_unknown(c, "s1"))
    row = _row(database, "s1", "status, exited_at")
    assert row["status"] == "unknown"
    assert row["exited_at"] is not None
    ev = _last_event(database)
    assert ev["type"] == "session_unknown"
    assert ev["payload"] == {"session_id": "s1"}


async def test_session_reattached_emits_event_without_row_change(database):
    await _session(database, "s1")
    await database.write(lambda c: store.session_running(c, "s1", 4321, 111.5))
    await database.write(lambda c: store.session_reattached(c, "s1"))
    assert _row(database, "s1")["status"] == "running"  # unchanged
    ev = _last_event(database)
    assert ev["type"] == "session_reattached"
    assert ev["payload"] == {"session_id": "s1", "pid": 4321}


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


async def test_create_session_numbers_attempts_per_hook_point(database):
    """Re-running one node's hook point — a resumed pause, a retry, each on.ci.poll
    pass — has to count up. Every row used to read attempt 1 (Kraft-kq8m)."""
    for sid in ("s1", "s2", "s3"):
        await _session(database, sid)
    got = database.read(
        lambda c: [
            (r["id"], r["attempt"])
            for r in c.execute("SELECT id, attempt FROM worker_sessions ORDER BY id")
        ]
    )
    assert got == [("s1", 1), ("s2", 2), ("s3", 3)]


async def test_create_session_attempts_are_scoped_to_node_and_hook_point(database):
    """The count is per (work_item_id, node_id, hook_point) — the scope
    CurrentNodePanel's per-row 'attempt N' already assumes. A sibling hook point on
    the same node, and the same hook point on another work item, both start at 1."""
    await mk_item(database, "w2")
    for sid in ("s1", "s2", "s3"):
        await _session(database, sid)
    await _session(database, "sibling", hook_point="on.implementation.start")
    await _session(database, "other", wid="w2")
    got = database.read(
        lambda c: {
            r["id"]: r["attempt"] for r in c.execute("SELECT id, attempt FROM worker_sessions")
        }
    )
    assert got == {"s1": 1, "s2": 2, "s3": 3, "sibling": 1, "other": 1}


async def test_worker_session_events_carry_the_attempt(database):
    """Both session events carry the number, or the live board shows blankSession's
    hardcoded 1 until the next hydrate."""
    await _session(database, "s1")
    await _session(database, "s2")
    created = _last_event(database)
    assert created["type"] == "worker_session_created"
    assert created["payload"]["session_id"] == "s2"
    assert created["payload"]["attempt"] == 2

    await database.write(lambda c: store.session_running(c, "s2", 4321, 111.5))
    started = _last_event(database)
    assert started["type"] == "worker_session_started"
    assert started["payload"]["attempt"] == 2


async def test_session_exited_carries_concerns_and_question_on_the_event(database):
    """The result file's free text is the only channel these two fields have —
    there is no `concerns` column (Task 1's migration deliberately adds none) —
    so `session_exited` must stamp them onto `worker_session_exited` itself,
    where `GET /work-items/{wid}` can read them back without touching disk."""
    await _session(database, "s-concerns")
    await _exit(database, "s-concerns", "done_with_concerns", concerns="the retry path is untested")
    ev = _last_event(database)
    assert ev["type"] == "worker_session_exited"
    assert ev["payload"]["concerns"] == "the retry path is untested"
    assert "question" not in ev["payload"]

    await _session(database, "s-question")
    await _exit(database, "s-question", "needs_context", question="which branch is the target?")
    ev2 = _last_event(database)
    assert ev2["payload"]["question"] == "which branch is the target?"
    assert "concerns" not in ev2["payload"]


async def test_create_session_reuses_a_waiting_row_for_the_same_wait_episode(database):
    first = await _session(database, "s1", node_id="mr_checks", hook_point="on.ci.poll")
    await _exit(database, "s1", "waiting")

    second = await _session(
        database, "s2", node_id="mr_checks", hook_point="on.ci.poll", reuse_if_waiting=True
    )

    assert second == first == ("s1", "/l/s1", "/r/s1")
    count = database.read(
        lambda c: c.execute(
            "SELECT count(*) FROM worker_sessions WHERE work_item_id='w1'"
        ).fetchone()[0]
    )
    assert count == 1


async def test_create_session_inserts_fresh_when_reuse_if_waiting_finds_nothing(database):
    got = await _session(
        database, "s1", node_id="mr_checks", hook_point="on.ci.poll", reuse_if_waiting=True
    )
    assert got == ("s1", "/l/s1", "/r/s1")


async def test_create_session_does_not_reuse_a_settled_session(database):
    """A 'done'/'failed' prior session must not be mistaken for the same wait
    episode -- only 'waiting' is reusable."""
    await _session(database, "s1", node_id="mr_checks", hook_point="on.ci.poll")
    await _exit(database, "s1", "done")

    new_id, _, _ = await _session(
        database, "s2", node_id="mr_checks", hook_point="on.ci.poll", reuse_if_waiting=True
    )
    assert new_id == "s2"


async def test_running_sessions_for_node_includes_an_escalation_on_another_node(database):
    """`escalate.escalation_running` is not node-scoped, so a live escalation
    row whose node_id has drifted from current_node_id (a stale row after a
    restart) is live to the caller's check and must not be missed by the kill
    (code review finding)."""
    await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
    await _session(database, "s_stale", hook_point="escalation")
    await _session(database, "s_other", hook_point="on.implementation.start")
    found = database.read(lambda c: store.running_sessions_for_node(c, "w1"))
    assert [r["id"] for r in found] == ["s_stale"]


async def test_reusable_session_matches_a_done_session_at_the_current_head(database):
    await _session(database, "s1", head_sha="sha-a")
    await _exit(database, "s1", "done")
    got = _reusable(database)
    assert got is not None and got["id"] == "s1"


async def test_reusable_session_rejects_a_stale_head_sha(database):
    """The worktree moved since this session ran (a rebase, or a fix commit
    from a later cycle) -- the recorded 'done' no longer describes the code as
    it stands, so it must not be reused."""
    await _session(database, "s1", head_sha="sha-old")
    await _exit(database, "s1", "done")
    assert _reusable(database, sha="sha-new") is None


async def test_reusable_session_rejects_a_null_head_sha_on_either_side(database):
    await _session(database, "s1")  # no head_sha kwarg -- stays NULL
    await _exit(database, "s1", "done")
    # a NULL stored row never matches a real sha the caller asks about
    assert _reusable(database, sha="sha-a") is None
    # a caller with no sha of its own (worktree HEAD unreadable) never reuses either
    assert _reusable(database, sha=None) is None


@pytest.mark.parametrize("status", ["failed", "rate_limited", "done_with_concerns", "running"])
async def test_reusable_session_ignores_a_non_done_status(database, status):
    """`failed`, `rate_limited`, `done_with_concerns` and still-`running` are
    all not a known-good result to stand in for a fresh dispatch. One session
    per case, so no sibling clause can be what refuses it."""
    await _session(database, "s1", head_sha="sha-a")
    if status == "running":
        await database.write(lambda c: store.session_running(c, "s1", 4321, 111.5))
    else:
        await _exit(database, "s1", status)
    assert _reusable(database) is None


async def test_reusable_session_excludes_a_session_whose_node_already_completed(database):
    """A node revisited after its own completion (a gate rejection walking
    back to it, not a crash mid-measurement) must not reuse a prior pass's
    session even if hook_point/round/head_sha all still line up -- an agent
    task that makes no further change on a second pass leaves head_sha
    exactly where the first, already-closed pass left it too."""
    impl = {"node_id": "implementation", "hook_point": "on.implementation.start"}
    await _session(database, "s1", head_sha="sha-a", **impl)
    await _exit(database, "s1", "done")
    await database.write(lambda c: store.complete_node(c, "w1", "implementation"))
    assert _reusable(database, *impl.values()) is None


async def test_reusable_session_excludes_a_second_pass_after_a_second_rejection(database):
    """`complete_node` only ever writes `node_completed` once (Kraft-gbt /
    Kraft-126), so a SECOND pass over a node reached via `reject_to` closes
    out as a silent no-op -- nothing marks it done a second time. Without the
    `node_started` re-entry check, the second pass's own session would still
    be sitting there, unexcluded, ready to be handed back on a third pass."""
    impl = {"node_id": "implementation", "hook_point": "on.implementation.start"}
    # pass 1 walks on to a gate node before rejecting back; pass 2 is rejected
    # back to implementation, its session leaves head_sha unchanged, and
    # `complete_node` no-ops this time.
    for sid in ("s1", "s2"):
        await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
        await _session(database, sid, head_sha="sha-a", **impl)
        await _exit(database, sid, "done")
        await database.write(lambda c: store.complete_node(c, "w1", "implementation"))
        await database.write(lambda c: store.enter_node(c, "w1", "human_review"))

    # pass 3: rejected a second time -- `measure_node` enters the node
    # (writing its own `node_started`) before it ever asks whether a
    # prior session can stand in for a fresh dispatch.
    await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
    assert _reusable(database, *impl.values()) is None


async def test_reusable_session_ignores_a_stuck_pending_sibling(database):
    """A multi-scope `on.test.run` mints one session per scope, all sharing
    (node, hook point, round, head_sha) -- a crash between scopes leaves an
    earlier scope `done` and the next one stuck `pending` forever. The `done`
    row must not be handed back as if the whole hook finished (code review)."""
    await _session(database, "s-scope-a", head_sha="sha-a")
    await _exit(database, "s-scope-a", "done")
    # scope B's row is inserted before it runs, and the crash lands
    # before it ever exits -- left stuck 'pending'.
    await _session(database, "s-scope-b", head_sha="sha-a")
    assert _reusable(database) is None


async def test_reusable_session_ignores_a_failed_sibling(database):
    """Batch C·MR1 Task 1 (Kraft-s7c04.9/.8/.14): once C2 stops short-circuiting
    the scope loop on the first failure, a crash/resume at the same round and
    head must not hand back the *last* scope's row as if the whole
    `on.test.run` task passed -- an earlier sibling scope that genuinely
    failed makes this attempt not reusable, the same way a still-open
    (pending/running) sibling already does above."""
    await _session(database, "s-scope-a", head_sha="sha-a")
    await _exit(database, "s-scope-a", "failed")
    await _session(database, "s-scope-b", head_sha="sha-a")
    await _exit(database, "s-scope-b", "done")
    assert _reusable(database) is None


def _latest_per_task(database, node_id, hook_point):
    return [
        r["id"]
        for r in database.read(
            lambda c: store.latest_session_per_task(c, "w1", node_id, [hook_point])
        )
    ]


async def test_latest_session_per_task_drops_a_failed_earlier_attempt(database):
    """Kraft-s15p0: an earlier attempt's failed row for the same hook_point
    must not survive alongside the current attempt's own row."""
    plan = {"node_id": "plan", "hook_point": "on.plan.requested"}
    await _session(database, "s-attempt-1", **plan)
    await _exit(database, "s-attempt-1", "failed")
    await _session(database, "s-attempt-2", **plan)
    await _exit(database, "s-attempt-2", "done")
    assert _latest_per_task(database, *plan.values()) == ["s-attempt-2"]


async def test_latest_session_per_task_ignores_a_hook_point_the_node_never_declared(database):
    """Kraft-s15p0: an `escalation` session is never one of `node['tasks']`,
    so it must never be selected regardless of when it ran -- here it runs
    last, so recency alone would pick it."""
    await _session(database, "s-task", node_id="plan", hook_point="on.plan.requested")
    await _exit(database, "s-task", "done")
    await _session(database, "s-escalation", node_id="plan", hook_point="escalation")
    await _exit(database, "s-escalation", "done")
    assert _latest_per_task(database, "plan", "on.plan.requested") == ["s-task"]


async def test_latest_session_per_task_omits_a_hook_point_with_no_session_at_all(database):
    assert _latest_per_task(database, "plan", "on.plan.requested") == []


async def test_latest_session_per_task_surfaces_a_stuck_sibling_over_a_finished_scope(database):
    """A multi-scope `on.test.run` mints one session per scope sharing this
    hook_point -- a crash between scopes leaves an earlier scope `done` and
    the next one stuck `pending`. Picking the merely-latest-created row would
    return the finished scope and read the whole hook as clean; the stuck
    sibling must win instead so the caller's `all(... in _ADVANCING)` check
    still fails closed (code review). Here the stuck scope is the *older* row,
    so recency alone would pick the finished one."""
    await _session(database, "s-scope-a")
    await _session(database, "s-scope-b")
    await _exit(database, "s-scope-b", "done")
    assert _latest_per_task(database, "verify", "on.test.run") == ["s-scope-a"]


async def test_reusable_session_excludes_a_node_rejected_back_to_itself(database):
    """The `node_started` re-entry check needs a departure to another node in
    between, which a gate with no `reject_to` never produces: `plan`'s gate
    (`templates/default.yaml`) re-enters `plan` itself, and its artifact lives
    in gitignored `.engineering/`, so `head_sha` does not move either. Without
    the `gate_rejected` check, the first pass's session matches every other
    key on a second rejection, the plan agent never runs, and the human's
    second rejection note is silently dropped."""
    plan = {"node_id": "plan", "hook_point": "on.plan.requested"}
    # pass 1: plan runs and closes out; its gate is then rejected, which
    # re-enters `plan` itself -- no other node in between. pass 2: the
    # re-planned document is rejected a second time. Its own `complete_node`
    # is the silent no-op (Kraft-gbt / Kraft-126), so nothing marks `plan`
    # done again and the `node_completed` check has nothing newer than s2 to
    # exclude against.
    for sid, note in (("s1", "section 3 is wrong"), ("s2", "section 3 is still wrong")):
        await database.write(lambda c: store.enter_node(c, "w1", "plan"))
        await _session(database, sid, head_sha="sha-a", **plan)
        await _exit(database, sid, "done")
        await database.write(lambda c: store.complete_node(c, "w1", "plan"))
        await database.write(
            lambda c, note=note: store.reject_gate(c, "w1", "plan_approval", note, reopen=True)
        )
    # pass 3 enters `plan` and asks whether s2 can stand in for it.
    await database.write(lambda c: store.enter_node(c, "w1", "plan"))

    got = _reusable(database, *plan.values())
    assert got is None, "a rejected node must re-run its agent, not reuse the last pass"


async def test_reusable_session_still_reuses_a_crash_recovery_after_an_older_rejection(database):
    """The `gate_rejected` exclusion is scoped to rejections that come *after*
    the candidate session. A rejection earlier in the item's life started the
    pass this session belongs to, so it must not block the reuse Kraft-gl9d is
    for."""
    plan = {"node_id": "plan", "hook_point": "on.plan.requested"}
    await database.write(lambda c: store.enter_node(c, "w1", "plan"))
    await database.write(
        lambda c: store.reject_gate(c, "w1", "plan_approval", "try again", reopen=True, node="plan")
    )
    # the pass the rejection started: its session runs, then the
    # server crashes and re-enters the same still-open node.
    await database.write(lambda c: store.enter_node(c, "w1", "plan"))
    await _session(database, "s1", head_sha="sha-a", **plan)
    await _exit(database, "s1", "done")

    got = _reusable(database, *plan.values())
    assert got is not None and got["id"] == "s1"


async def _running_review(database):
    """A `chain_review` session, running: the shape a pause lands on."""
    await _session(database, "s1", node_id="chain_review", hook_point="gate_review")
    await database.write(lambda c: store.session_running(c, "s1", 1, 1.0))


async def test_a_paused_session_records_the_usage_it_was_handed(database):
    """Kraft-s7c04.18: `session_exited` returns early on a paused row -- so the
    row and the event agree a human's interruption is not a task failure -- and
    it is the only writer of cost_usd. 71 paused rows, 28.0M tokens, NULL cost,
    permanently."""
    await _running_review(database)
    await database.write(lambda c: store.pause_work_item(c, "w1", ["s1"]))
    await _exit(database, "s1", "failed", None, Usage(214_775, 19, 1.37, "claude-opus-5"))

    row = _row(database, "s1", "status, tokens_in, tokens_out, cost_usd, model")
    types = database.read(
        lambda c: [
            r["type"]
            for r in c.execute("SELECT type FROM events WHERE work_item_id = 'w1'").fetchall()
        ]
    )
    assert row["cost_usd"] == pytest.approx(1.37)
    assert (row["tokens_in"], row["tokens_out"]) == (214_775, 19)
    assert row["model"] == "claude-opus-5"
    # the status is NOT moved and no exit event is emitted: a pause is not a
    # task failure, and worker_session_paused already described this moment
    assert row["status"] == "paused"
    assert "worker_session_exited" not in types


async def test_recording_pause_usage_is_a_no_op_on_a_session_that_moved_on(database):
    """Same reason `session_progress` guards on 'running': a row that has
    settled must not be reopened by a late writer."""
    await _running_review(database)
    await _exit(database, "s1", "done", None, Usage(10, 1, 0.01, "m"))
    await database.write(
        lambda c: store.record_pause_usage(c, "s1", Usage(999, 999, 9.99, "wrong"))
    )
    row = _row(database, "s1", "cost_usd, model")
    assert row["cost_usd"] == pytest.approx(0.01)
    assert row["model"] == "m"


async def test_a_pause_with_no_envelope_records_nothing_rather_than_zero(database):
    """An agent SIGINT'd before its first response has no number to report, and
    usage.py deliberately has no rate table. NULL is the honest record; zero
    would be a claim."""
    await _running_review(database)
    await database.write(lambda c: store.pause_work_item(c, "w1", ["s1"]))
    await database.write(lambda c: store.record_pause_usage(c, "s1", None))
    row = _row(database, "s1", "cost_usd, tokens_in")
    assert row["cost_usd"] is None
    assert row["tokens_in"] is None


async def test_a_session_has_a_start_time_from_birth(database):
    """Only the subprocess adapter calls session_running, so a forge or builtin
    session went pending -> done with started_at NULL and its duration was
    invisible."""
    await _session(database, "s1", node_id="merge", hook_point="on.merge")
    assert _row(database, "s1", "started_at")["started_at"] is not None


@pytest.mark.parametrize("writer", ["progress", "pause", "exit"])
async def test_every_usage_writer_stores_the_cache_kinds_apart(database, writer):
    """Ruling 211: the live count, a paused session's usage and the final one
    each store uncached input, cache writes and cache reads in their own
    columns, and the exit event carries them for the board to patch in."""
    split = Usage(7, 3, 0.5, "m", tokens_cache_write=40, tokens_cache_read=900)
    await _running_review(database)
    if writer == "progress":
        await database.write(lambda c: store.session_progress(c, "s1", split))
    elif writer == "pause":
        await database.write(lambda c: store.pause_work_item(c, "w1", ["s1"]))
        await database.write(lambda c: store.record_pause_usage(c, "s1", split))
    else:
        await _exit(database, "s1", "done", None, split)
    kinds = "tokens_in, tokens_cache_write, tokens_cache_read, tokens_out"
    assert tuple(_row(database, "s1", kinds)) == (7, 40, 900, 3)
    if writer == "exit":
        payload = _last_event(database)["payload"]
        assert [payload[k] for k in kinds.split(", ")] == [7, 40, 900, 3]
