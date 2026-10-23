"""The machine a remote step ran on is kept with its session."""

from support.store_fixtures import mk_item

from kraft import store


async def test_a_remote_sessions_machine_is_recorded_and_a_local_one_has_none(database):
    await mk_item(database)
    for sid, kw in (("r1", {"runner": "gpu"}), ("l1", {})):
        await database.write(
            lambda c, sid=sid, kw=kw: store.create_session(
                c,
                id=sid,
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                log_path=f"/l/{sid}",
                result_path=f"/r/{sid}",
                **kw,
            )
        )
    rows = database.read(lambda c: c.execute("SELECT id, runner FROM worker_sessions").fetchall())
    assert {r["id"]: r["runner"] for r in rows} == {"r1": "gpu", "l1": None}
