import asyncio

from support.store_fixtures import mk_item, open_db

from kraft import events, store
from kraft.policy import Cap


def test_mark_sessions_capped_out_scoped_to_measuring_hook_points(tmp_path):
    """Only the node's measuring sessions are capped_out, not the fix task's."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_mark_sessions_capped_out_leaves_a_waiting_session_alone(tmp_path):
    """A ci_fix_loop cap breach must not steal a live on.ci.poll wait episode
    out from under the separate ci_wait cap that already governs it."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="poll",
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "poll", "waiting"))

            await database.write(
                lambda c: store.mark_sessions_capped_out(c, "w1", "mr_checks", ["on.ci.poll"])
            )

            row = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions WHERE id = 'poll'"
                ).fetchone()
            )
            assert row["status"] == "waiting"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_done_with_concerns_is_not_capped_out_by_a_sibling_breach(tmp_path):
    """A `done_with_concerns` session on a capping node keeps its status and text;
    only the still-pending sibling that actually breached becomes capped_out."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_clear_loop_counters_deletes_key_and_ci_counters_leaves_gate(tmp_path):
    """.25: `clear_loop_counters` is the piece `retry_after_cap` extracts --
    it must clear `key`, `ci_wait:<node_id>` and `ci_infra:<node_id>`, and
    leave any gate-reject counter alone (that is `.22`'s question, not this
    one's -- spec §2)."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            cap = Cap(attempts=9, wall_clock_s=3600)
            for key in (
                "verify_fix_loop",
                "ci_wait:verify",
                "ci_infra:verify",
                "spec_approval_reject_loop",
            ):
                await database.write(lambda c, key=key: store.bump_counter(c, "w1", key, cap))
            await database.write(
                lambda c: store.clear_loop_counters(c, "w1", "verify", "verify_fix_loop")
            )
            assert database.read(lambda c: store.read_counter(c, "w1", "verify_fix_loop")) is None
            assert database.read(lambda c: store.read_counter(c, "w1", "ci_wait:verify")) is None
            assert database.read(lambda c: store.read_counter(c, "w1", "ci_infra:verify")) is None
            assert (
                database.read(lambda c: store.read_counter(c, "w1", "spec_approval_reject_loop"))
                is not None
            ), "clear_loop_counters must not touch a gate-reject counter"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_clear_loop_counters_with_no_key_still_clears_ci_counters(tmp_path):
    """A node with no fix_loop (`key=None`) still has ci_wait/ci_infra rows
    that a bounce must clear."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            cap = Cap(attempts=9, wall_clock_s=3600)
            await database.write(lambda c: store.bump_counter(c, "w1", "ci_wait:mr_checks", cap))
            await database.write(lambda c: store.clear_loop_counters(c, "w1", "mr_checks", None))
            assert database.read(lambda c: store.read_counter(c, "w1", "ci_wait:mr_checks")) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_retry_after_cap_clears_the_gate_reject_counter_too(tmp_path):
    """Kraft-ko7j §A4: without this the cap becomes a dead end one step out —
    the counter is spent, the gate re-opens after every retry, and every
    rejection after that is refused forever."""

    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
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


def test_retry_after_cap_clears_ci_pipeline_ref(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(lambda c: store.set_ci_pipeline_ref(c, "w1", "abc123:456"))
            await database.write(lambda c: store.retry_after_cap(c, "w1", "mr_checks", None, None))
            row = database.read(
                lambda c: c.execute(
                    "SELECT ci_pipeline_ref FROM work_items WHERE id = 'w1'"
                ).fetchone()
            )
            assert row["ci_pipeline_ref"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_retry_after_cap_clears_retry_at(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["rate_limited"])
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


def test_retry_after_cap_tags_a_seeded_steer_on_the_event(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.retry_after_cap(
                    c,
                    "w1",
                    "verify",
                    "verify_fix_loop",
                    "- [important] a.py:1 — missing null check (reviewer)",
                    seeded=True,
                )
            )
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "work_item_retried"
            assert ev["payload"]["seeded"] is True
        finally:
            await database.close()

    asyncio.run(scenario())


def test_retry_after_cap_defaults_seeded_to_false(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            await mk_item(database)
            await database.write(
                lambda c: store.retry_after_cap(c, "w1", "verify", "verify_fix_loop", "go")
            )
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["payload"]["seeded"] is False
        finally:
            await database.close()

    asyncio.run(scenario())
