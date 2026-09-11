import asyncio
import uuid

from support.store_fixtures import open_db

from kraft import store


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
        database = await open_db(tmp_path)
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
        database = await open_db(tmp_path)
        try:
            wid = await _spend_fixture_one(database, costs=[1.0, None, None])
            item_usd, _daily_usd = database.read(lambda c: store.budget_spend(c, wid))
            assert item_usd == 1.0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_no_sessions_is_zero_not_none(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
        try:
            wid = await _spend_fixture_one(database, costs=[])
            assert database.read(lambda c: store.budget_spend(c, wid)) == (0.0, 0.0)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_daily_window_excludes_yesterday(tmp_path):
    async def scenario():
        database = await open_db(tmp_path)
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
