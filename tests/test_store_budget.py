import asyncio
import uuid

from support.store_fixtures import open_db

from kraft import store
from kraft.store._common import session_wall_ms


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


def _row(**over):
    base = {
        "wall_ms": None,
        "started_at": "2026-09-15T10:00:00+00:00",
        "created_at": "2026-09-15T09:59:00+00:00",
        "exited_at": None,
    }
    return {**base, **over}


def test_a_recorded_wall_ms_wins_over_any_derivation():
    """`session_exited` wrote it against its own clock; nothing here second-
    guesses that."""
    assert (
        session_wall_ms(_row(wall_ms=4_501_473, exited_at="2026-09-15T10:00:01+00:00")) == 4_501_473
    )


def test_a_paused_session_derives_its_span_from_its_own_stamps():
    """Kraft-s7c04.18: 71 paused rows carry NULL wall_ms and an exited_at."""
    row = _row(exited_at="2026-09-15T10:01:30+00:00")
    assert session_wall_ms(row) == 90_000


def test_a_running_session_derives_against_now_rather_than_reading_zero():
    """Kraft-s7c04.47's totals half: a live session has no exited_at, and
    `or 0` made a node that had been running 75 minutes sum to nothing."""
    assert session_wall_ms(_row(), now="2026-09-15T10:25:00+00:00") == 1_500_000


def test_created_at_backs_up_a_missing_started_at():
    """A session paused while still pending never got a started_at -- the same
    fallback `session_exited` itself uses."""
    row = _row(started_at=None, exited_at="2026-09-15T10:00:00+00:00")
    assert session_wall_ms(row) == 60_000


def test_a_row_with_no_usable_start_is_unknown_not_zero():
    """None means "no answer". Zero would be an answer, and a wrong one."""
    assert session_wall_ms(_row(started_at=None, created_at=None)) is None


def test_a_naive_timestamp_derives_to_none_rather_than_crashing():
    """A row written outside `_now()`'s own format (sqlite's `datetime('now')`,
    say, as some test fixtures do directly) is naive; `_now()`'s own default is
    aware, and subtracting the two raises TypeError rather than ValueError.
    Surfaced by Kraft-s7c04.18's read-time derivation running on every row,
    including a still-running one a raw SQL insert seeded outside store.py."""
    row = _row(started_at=None, created_at="2026-09-15 09:59:00")
    assert session_wall_ms(row, now="2026-09-15T10:25:00+00:00") is None
