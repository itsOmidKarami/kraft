"""`waits.observe`'s seam for a wait that decides for itself when to look again
and what to remember (the review wait's collect path): the caller's own
instant, its own next observation, and a memory that comes back. How the
ordinary waits back off is tests/test_waits.py."""

from __future__ import annotations

from datetime import datetime, timedelta

from adapters.forge.nodes import forge_node
from support.harness import v1_chain, v1_item

from kraft import events, waits
from kraft.templates.models import WaitBounds


def _look(conn, **seam):
    """One pending look at a wait that times out in 5 minutes."""
    return waits.observe(
        conn,
        "w1",
        node_id="review",
        task="review.main.review",
        kind="mr.automated_review",
        bounds=WaitBounds.from_seconds(timeout=300, initial=30, maximum=120),
        condition="review",
        state="pending",
        result="pending",
        **seam,
    )


async def _item(database, repo):
    await v1_item(
        database, v1_chain([forge_node("review", "mr.automated_review")], repo=repo), repo=repo
    )


def _next_observation(database) -> str:
    seen = [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, "w1"))
        if e["type"] == "external_wait_observed"
    ]
    return seen[-1]["next_observation_at"]


async def test_a_next_observation_before_the_backoff_is_honoured(database, repo, wait_clock):
    await _item(database, repo)

    await database.write(lambda c: _look(c, next_at=wait_clock.now + timedelta(seconds=7)))

    assert _next_observation(database) == wait_clock.at(7)


async def test_a_next_observation_past_the_deadline_is_capped_at_it(database, repo, wait_clock):
    await _item(database, repo)

    await database.write(lambda c: _look(c, next_at=wait_clock.now + timedelta(hours=1)))

    assert _next_observation(database) == wait_clock.at(300)


async def test_what_the_caller_remembers_comes_back_on_the_open_wait(database, repo, wait_clock):
    await _item(database, repo)
    memory = {"bot:x": {"activity_at": None, "threads": ["a"]}}

    await database.write(lambda c: _look(c, extra={"memory": memory}))

    last = database.read(lambda c: waits.open_wait(c, "w1", "review.main.review")).last
    assert last["memory"] == memory


async def test_the_instant_the_caller_decided_at_is_the_one_the_deadline_is_tested_at(
    database, repo, wait_clock
):
    """The clock stays put; the caller says it is a second past the deadline."""
    await _item(database, repo)
    await database.write(lambda c: _look(c))
    past: datetime = wait_clock.now + timedelta(seconds=301)

    assert await database.write(lambda c: _look(c, now=past)) == "timed_out"
