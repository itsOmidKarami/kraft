import uuid

import pytest

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


async def test_budget_spend_sums_only_this_items_sessions(database):
    mine, _theirs = await _spend_fixture(database, mine=[1.5, 2.25], theirs=[99.0])
    item_usd, daily_usd = database.read(lambda c: store.budget_spend(c, mine))
    assert item_usd == 3.75
    assert daily_usd == 102.75  # daily is instance-wide, not per item


async def test_null_cost_sessions_count_as_zero(database):
    """A running session, or a subprocess/builtin task, has no cost yet.

    Under-counting in-flight spend is a direct consequence of the design: cost
    only exists once the agent's session has exited.
    """

    wid = await _spend_fixture_one(database, costs=[1.0, None, None])
    item_usd, _daily_usd = database.read(lambda c: store.budget_spend(c, wid))
    assert item_usd == 1.0


async def test_no_sessions_is_zero_not_none(database):
    wid = await _spend_fixture_one(database, costs=[])
    assert database.read(lambda c: store.budget_spend(c, wid)) == (0.0, 0.0)


async def test_daily_window_excludes_yesterday(database):
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


_STAMPS = {
    "wall_ms": None,
    "started_at": "2026-09-15T10:00:00+00:00",
    "created_at": "2026-09-15T09:59:00+00:00",
    "exited_at": None,
}
_NOW = "2026-09-15T10:25:00+00:00"


@pytest.mark.parametrize(
    ("over", "now", "expected"),
    [
        # `session_exited` wrote it against its own clock; nothing second-guesses that
        ({"wall_ms": 4_501_473, "exited_at": "2026-09-15T10:00:01+00:00"}, None, 4_501_473),
        # Kraft-s7c04.18: 71 paused rows carry NULL wall_ms and an exited_at
        ({"exited_at": "2026-09-15T10:01:30+00:00"}, None, 90_000),
        # Kraft-s7c04.47: a live session has no exited_at, and `or 0` made a node
        # that had been running 75 minutes sum to nothing
        ({}, _NOW, 1_500_000),
        # a session paused while still pending never got a started_at -- the same
        # fallback `session_exited` itself uses
        ({"started_at": None, "exited_at": "2026-09-15T10:00:00+00:00"}, None, 60_000),
        # None means "no answer"; zero would be an answer, and a wrong one
        ({"started_at": None, "created_at": None}, None, None),
        # a row written outside `_now()`'s format (sqlite's `datetime('now')`) is
        # naive, and subtracting it from an aware now raises TypeError
        ({"started_at": None, "created_at": "2026-09-15 09:59:00"}, _NOW, None),
    ],
    ids=[
        "a-recorded-wall-ms-wins",
        "a-paused-session-derives-from-its-own-stamps",
        "a-running-session-derives-against-now",
        "created-at-backs-up-a-missing-started-at",
        "no-usable-start-is-unknown-not-zero",
        "a-naive-timestamp-is-none-not-a-crash",
    ],
)
def test_session_wall_ms(over, now, expected):
    kwargs = {"now": now} if now else {}
    assert session_wall_ms(_STAMPS | over, **kwargs) == expected
