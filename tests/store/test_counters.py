import pytest
from support.store_fixtures import mk_item

from kraft import events, store
from kraft.policy import Cap


@pytest.fixture
async def database(database):
    """Every test here starts from work item `w1` (`mk_item`)."""
    await mk_item(database)
    return database


def _session(database, sid, node_id="verify", hook_point="on.test.run"):
    return database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id="w1",
            node_id=node_id,
            hook_point=hook_point,
            log_path="/l",
            result_path="/r",
        )
    )


def _statuses(database):
    rows = database.read(lambda c: c.execute("SELECT id, status FROM worker_sessions").fetchall())
    return {r["id"]: r["status"] for r in rows}


def _bump(database, key, attempts=9):
    cap = Cap(attempts=attempts, wall_clock_s=3600)
    return database.write(lambda c: store.bump_counter(c, "w1", key, cap))


def _counter(database, key):
    return database.read(lambda c: store.read_counter(c, "w1", key))


def _item(database, columns):
    return database.read(
        lambda c: c.execute(f"SELECT {columns} FROM work_items WHERE id = 'w1'").fetchone()
    )


async def test_mark_sessions_capped_out_scoped_to_measuring_hook_points(database):
    """Only the node's measuring sessions are capped_out, not the fix task's."""
    await _session(database, "measure")
    await _session(database, "fix", hook_point="on.implementation.start")
    await database.write(
        lambda c: store.mark_sessions_capped_out(c, "w1", "verify", ["on.test.run"])
    )
    assert _statuses(database) == {"measure": "capped_out", "fix": "pending"}

    # The SPA only learns session status from worker_session_* events, so
    # the cap must emit one for each session it flipped (and only those).
    evts = database.read(lambda c: events.read_after(c, 0, "w1"))
    capped_evts = [
        e
        for e in evts
        if e["type"] == "worker_session_exited" and e["payload"]["status"] == "capped_out"
    ]
    assert [e["payload"]["session_id"] for e in capped_evts] == ["measure"]


async def test_mark_sessions_capped_out_leaves_a_waiting_session_alone(database):
    """A ci_fix_loop cap breach must not steal a live on.ci.poll wait episode
    out from under the separate ci_wait cap that already governs it."""
    await _session(database, "poll", node_id="mr_checks", hook_point="on.ci.poll")
    await database.write(lambda c: store.session_exited(c, "poll", "waiting"))
    await database.write(
        lambda c: store.mark_sessions_capped_out(c, "w1", "mr_checks", ["on.ci.poll"])
    )
    assert _statuses(database) == {"poll": "waiting"}


async def test_done_with_concerns_is_not_capped_out_by_a_sibling_breach(database):
    """A `done_with_concerns` session on a capping node keeps its status and text;
    only the still-pending sibling that actually breached becomes capped_out."""
    await _session(database, "concerns")
    await _session(database, "pending")
    await database.write(lambda c: store.session_exited(c, "concerns", "done_with_concerns"))
    await database.write(
        lambda c: store.mark_sessions_capped_out(c, "w1", "verify", ["on.test.run"])
    )
    assert _statuses(database) == {"concerns": "done_with_concerns", "pending": "capped_out"}


async def test_clear_loop_counters_deletes_key_and_ci_counters_leaves_gate(database):
    """.25: `clear_loop_counters` is the piece `retry_after_cap` extracts --
    it must clear `key` and `ci_infra:<node_id>`, and leave any gate-reject
    counter alone (that is `.22`'s question, not this one's -- spec §2)."""
    keys = ("verify.fix_loop", "ci_infra:verify", "spec_approval_reject_loop")
    for key in keys:
        await _bump(database, key)
    await database.write(lambda c: store.clear_loop_counters(c, "w1", "verify", "verify.fix_loop"))
    assert [_counter(database, key) for key in keys[:2]] == [None] * 2
    assert _counter(database, "spec_approval_reject_loop") is not None, (
        "clear_loop_counters must not touch a gate-reject counter"
    )


async def test_clear_loop_counters_with_no_key_still_clears_ci_counters(database):
    """A node with no fix_loop (`key=None`) still has a ci_infra row that a
    restart must clear."""
    await _bump(database, "ci_infra:mr_checks")
    await database.write(lambda c: store.clear_loop_counters(c, "w1", "mr_checks", None))
    assert _counter(database, "ci_infra:mr_checks") is None


async def test_retry_after_cap_clears_the_gate_reject_counter_too(database):
    """Kraft-ko7j §A4: without this the cap becomes a dead end one step out —
    the counter is spent, the gate re-opens after every retry, and every
    rejection after that is refused forever."""
    await _bump(database, "spec_approval_reject_loop", attempts=1)
    await _bump(database, "verify_fix_loop", attempts=1)
    await database.write(
        lambda c: store.retry_after_cap(
            c, "w1", "spec", "verify_fix_loop", None, gate_key="spec_approval_reject_loop"
        )
    )
    assert _counter(database, "verify_fix_loop") is None
    assert _counter(database, "spec_approval_reject_loop") is None


async def test_retry_after_cap_clears_ci_pipeline_ref(database):
    await database.write(lambda c: store.set_ci_pipeline_ref(c, "w1", "abc123:456"))
    await database.write(lambda c: store.retry_after_cap(c, "w1", "mr_checks", None, None))
    assert _item(database, "ci_pipeline_ref")[0] is None


async def test_retry_after_cap_clears_retry_at(database):
    await database.write(
        lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
    )
    await database.write(lambda c: store.claim_for_run(c, "w1", from_statuses=["rate_limited"]))
    await database.write(lambda c: store.retry_after_cap(c, "w1", "implementation", None, "go"))
    assert tuple(_item(database, "status, retry_at")) == ("active", None)


@pytest.mark.parametrize(
    ("kwargs", "seeded"),
    [({"seeded": True}, True), ({}, False)],
    ids=["tags-a-seeded-steer", "defaults-to-false"],
)
async def test_retry_after_cap_marks_seeded_on_the_event(database, kwargs, seeded):
    steer = "- [important] a.py:1 — missing null check (reviewer)"
    await database.write(
        lambda c: store.retry_after_cap(c, "w1", "verify", "verify_fix_loop", steer, **kwargs)
    )
    ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
    assert ev["type"] == "work_item_retried"
    assert ev["payload"]["seeded"] is seeded
