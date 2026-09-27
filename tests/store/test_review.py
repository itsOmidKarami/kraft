from __future__ import annotations

from support.store_fixtures import mk_item

from kraft import store


async def test_start_run_numbers_attempts_and_skips_reentry(database):
    await mk_item(database)
    first = await database.write(
        lambda c: store.start_run(c, "w1", "impl", start_sha="a", base_sha="b")
    )
    again = await database.write(
        lambda c: store.start_run(c, "w1", "impl", start_sha="a2", base_sha="b")
    )
    assert (first, again) == (1, None)  # unfinished run: a resume, not a new attempt
    await database.write(lambda c: store.finish_run(c, "w1", "impl", end_sha="c", dirty=False))
    second = await database.write(
        lambda c: store.start_run(c, "w1", "impl", start_sha="c", base_sha="b")
    )
    assert second == 2


async def test_finish_run_without_a_started_run_is_a_noop(database):
    await mk_item(database)
    got = await database.write(
        lambda c: store.finish_run(c, "w1", "impl", end_sha="c", dirty=False)
    )
    assert got is None
    assert database.read(lambda c: store.node_run_rows(c, "w1")) == []


async def test_pin_gate_dedupes_a_rerequest_at_the_same_head(database):
    """Review Focus 1: a restart re-requesting a pending gate is not attempt 2."""
    await mk_item(database)
    one = await database.write(
        lambda c: store.pin_gate(c, "w1", "review", sha="h1", base_sha="b", dirty=False)
    )
    same = await database.write(
        lambda c: store.pin_gate(c, "w1", "review", sha="h1", base_sha="b", dirty=False)
    )
    two = await database.write(
        lambda c: store.pin_gate(c, "w1", "review", sha="h2", base_sha="b", dirty=False)
    )
    assert (one, same, two) == (1, None, 2)
    attempts = database.read(lambda c: store.gate_attempts(c, "w1", "review"))
    assert [(a["n"], a["sha"]) for a in attempts] == [(1, "h1"), (2, "h2")]
