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
