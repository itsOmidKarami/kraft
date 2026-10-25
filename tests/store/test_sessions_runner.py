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


async def test_running_local_sessions_counts_running_rows_with_a_pid_and_no_runner(database):
    await mk_item(database)
    for sid, runner, pid, exits in (
        ("local-live", None, 11, None),
        ("remote-live", "gpu", 12, None),
        ("forge-no-pid", None, None, None),
        ("local-done", None, 13, "done"),
    ):
        await database.write(
            lambda c, sid=sid, runner=runner: store.create_session(
                c,
                id=sid,
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                log_path=f"/l/{sid}",
                result_path=f"/r/{sid}",
                runner=runner,
            )
        )
        if pid is not None:
            await database.write(
                lambda c, sid=sid, pid=pid: store.session_running(
                    c, sid, pid, 1.0, work_item_id="w1"
                )
            )
        if exits is not None:
            await database.write(
                lambda c, sid=sid, exits=exits: store.session_exited(
                    c, sid, exits, work_item_id="w1"
                )
            )
    assert database.read(store.running_local_sessions) == 1
