"""A reply round's reader: the event that marks one, what closes it, and the head it names."""

from __future__ import annotations

import threading

import pytest
from support.harness import v1_node, v1_task

from kraft import events, store
from kraft.executor import reply_round
from kraft.executor.reply_round import ReplyRound

_NODE = "n"
_CHAIN = [v1_node(_NODE, tasks=[v1_task("t")]), v1_node("other", tasks=[v1_task("t")])]


def _round(head="h1", round_=1, threads=("T1",), node=_NODE):
    return (
        "reply_round_started",
        {"node_id": node, "head": head, "round": round_, "threads": list(threads)},
    )


_MEASURED = ("findings_measured", {"node_id": _NODE, "cycle": 0})
_COMPLETED = ("node_completed", {"node_id": _NODE})


async def _seed(database, wid, *seeded):
    def write(c):
        for type_, payload in seeded:
            events.append(c, wid, type_, payload)

    await database.write(write)


def _never(*_):
    raise AssertionError("the head was asked for")


@pytest.mark.parametrize(
    "seeded, head, expected",
    [
        pytest.param([_round()], "h1", ("h1", 1, ("T1",)), id="open"),
        pytest.param([_round(), _MEASURED], "h1", None, id="record-written"),
        pytest.param([_round()], "h2", None, id="other-head"),
        pytest.param([_round(), _COMPLETED], "h1", None, id="earlier-run"),
        pytest.param([_round(node="other")], "h1", None, id="another-node"),
        pytest.param(
            [_round(round_=1, threads=("T1",)), _round(round_=2, threads=("T2", "T3"))],
            "h1",
            ("h1", 2, ("T2", "T3")),
            id="second-round",
        ),
        pytest.param([], _never, None, id="no-event-never-asks-for-the-head"),
    ],
)
async def test_in_a_reply_round_reads_the_event_the_record_and_the_head(
    item_on, database, seeded, head, expected
):
    it = await item_on(_CHAIN, _NODE)
    await _seed(database, it.id, *seeded)

    head_of = head if callable(head) else (lambda: head)
    got = await reply_round.current(database, it.id, _NODE, head_of)

    assert (got.head, got.round, got.threads) == expected if expected else got is None


async def test_the_head_is_read_off_the_event_loop(item_on, database):
    it = await item_on(_CHAIN, _NODE)
    await _seed(database, it.id, _round())
    loop_thread = threading.get_ident()
    ran_on = []

    def head_of():
        ran_on.append(threading.get_ident())
        return "h1"

    assert await reply_round.current(database, it.id, _NODE, head_of) is not None
    assert ran_on and ran_on != [loop_thread]


@pytest.mark.parametrize(
    "seeded, node, head, expected",
    [
        pytest.param([_round(), _round(round_=2)], _NODE, "h1", 2, id="two-on-one-head"),
        pytest.param([_round(), _round(head="h2")], _NODE, "h2", 1, id="another-head"),
        pytest.param([_round(), _COMPLETED, _round(round_=2)], _NODE, "h1", 1, id="earlier-run"),
        pytest.param([_round(), _round(node="other")], "other", "h1", 1, id="another-node"),
    ],
)
async def test_started_count_is_per_node_head_and_run(
    item_on, database, seeded, node, head, expected
):
    it = await item_on(_CHAIN, _NODE)
    await _seed(database, it.id, *seeded)

    assert database.read(lambda c: reply_round.started_count(c, it.id, node, head)) == expected


async def test_begin_writes_the_event_and_the_cursor_together(item_on, database):
    it = await item_on(_CHAIN, _NODE)
    await database.write(lambda c: store.set_current_step(c, it.id, 2))
    writes = []

    class Counting:
        async def write(self, fn):
            writes.append(fn)
            return await database.write(fn)

    await reply_round.begin(Counting(), it.id, ReplyRound(0, "h1", 1, ("T1",)), _NODE)

    assert len(writes) == 1
    assert [e["payload"] for e in it.events("reply_round_started")] == [
        {"node_id": _NODE, "head": "h1", "round": 1, "threads": ["T1"], "deadline": None}
    ]
    assert it.row()["current_step"] == 0


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-11-22T10:00:00Z", True),
        ("2026-11-22T10:00:00+00:00", True),
        ("soon", False),
        (None, False),
    ],
    ids=["trailing-z", "offset", "not-a-time", "none"],
)
def test_when_reads_a_forge_time_or_none(value, expected):
    assert (reply_round.when(value) is not None) is expected
