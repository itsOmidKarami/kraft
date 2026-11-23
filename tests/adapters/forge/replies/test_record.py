"""The reply record: each intent's state folded from its events, over every event of
the work item."""

from __future__ import annotations

import pytest
from support.harness import v1_node, v1_task

from kraft import store
from kraft.adapters.forge.models import ForgeError, Posted
from kraft.adapters.forge.replies import record

_PAYLOAD = {
    "op": "reply",
    "node_id": "n",
    "repo": "/repo",
    "thread": "T1",
    "kind": "declined",
    "body_sha256": "0" * 64,
    "head": "abc123",
    "round": 0,
    "session": "s1",
}
_NODE = [v1_node("n", tasks=[v1_task("t")])]


def _apply(c, item, intent, step):
    kind, *rest = step
    if kind == "failed":
        record.send_failed(c, item, intent, rest[0], ForgeError("x", status=None))
    elif kind == "began":
        record.send_began(c, item, intent)
    elif kind == "posted":
        record.posted(c, item, intent, Posted(("1", "N1"), "t", "b"), via=rest[0])
    elif kind == "not_found":
        record.not_found(c, item, intent)
    elif kind == "ended":
        record.ended(c, item, intent, rest[0], "why")


@pytest.mark.parametrize(
    "after, state, sends, ever_unclear",
    [
        ([], "pending", 0, False),
        ([("failed", "unclear")], "unclear", 1, True),
        ([("failed", "unclear"), ("not_found",)], "pending", 1, True),
        ([("failed", "rate_limited")], "pending", 1, False),
        ([("failed", "deferred")], "pending", 0, False),
        ([("failed", "unclear"), ("posted", "reconcile")], "posted", 1, True),
        ([("posted", "send")], "posted", 0, False),
        ([("ended", "refused")], "refused", 0, False),
        ([("failed", "unclear"), ("not_found",), ("ended", "superseded")], "superseded", 1, True),
        ([("failed", "unclear")] * 3 + [("ended", "given_up")], "given_up", 3, True),
        ([("began",)], "unclear", 0, False),
        ([("began",), ("posted", "send")], "posted", 0, False),
        ([("began",), ("not_found",)], "pending", 0, True),
    ],
    ids=[
        "intended",
        "unclear",
        "not-found-is-pending-again",
        "rate-limited-counts",
        "deferred-does-not-count",
        "reconciled",
        "posted",
        "refused",
        "superseded-after-a-reconcile",
        "given-up",
        "send-began-is-unclear",
        "began-then-posted",
        "a-crash-found-absent",
    ],
)
async def test_an_intent_s_state_is_folded_from_its_events(
    item_on, database, after, state, sends, ever_unclear
):
    it = await item_on(_NODE)
    intent = record.new_id(it.id)
    await database.write(lambda c: record.intend(c, it.id, intent, _PAYLOAD))
    for step in after:
        await database.write(lambda c, s=step: _apply(c, it.id, intent, s))

    got = database.read(lambda c: record.read(c, it.id))[intent]

    assert (got.state, got.sends, got.ever_unclear) == (state, sends, ever_unclear)
    # An ended intent keeps the reason it ended with (`_apply` writes "why").
    assert got.why == ("why" if state in ("refused", "superseded", "given_up") else None)


async def test_the_record_spans_every_fork(item_on, database):
    it = await item_on(_NODE)
    intent = record.new_id(it.id)
    await database.write(lambda c: record.intend(c, it.id, intent, _PAYLOAD))
    await database.write(lambda c: store.fork_run(c, it.id, None, by_person=True))
    await database.write(lambda c: _apply(c, it.id, intent, ("failed", "unclear")))

    got = database.read(lambda c: record.read(c, it.id))

    assert list(got) == [intent]
    assert (got[intent].sends, got[intent].state) == (1, "unclear")


@pytest.mark.parametrize(
    "payload",
    [{**_PAYLOAD, "op": "delete"}, {k: v for k, v in _PAYLOAD.items() if k != "body_sha256"}],
    ids=["unknown-op", "no-body-hash"],
)
async def test_intend_refuses_an_unknown_op_or_a_missing_hash(item_on, database, payload):
    it = await item_on(_NODE)

    with pytest.raises(ValueError):
        await database.write(lambda c: record.intend(c, it.id, "w-0123456789ab", payload))


async def test_intend_accepts_a_resolve_op(item_on, database):
    it = await item_on(_NODE)
    intent = record.new_id(it.id)

    await database.write(
        lambda c: record.intend(c, it.id, intent, {**_PAYLOAD, "op": "resolve", "kind": "resolve"})
    )

    assert database.read(lambda c: record.read(c, it.id))[intent].payload["op"] == "resolve"


async def test_posted_hashes_map_every_id_to_the_returned_hash(item_on, database):
    it = await item_on(_NODE)
    for ids, body in ((("1", "N1"), "first"), (("2", "N2"), "second")):
        intent = record.new_id(it.id)
        await database.write(lambda c, i=intent: record.intend(c, it.id, i, _PAYLOAD))
        await database.write(
            lambda c, i=intent, ids=ids, body=body: record.posted(
                c, it.id, i, Posted(ids, "t", body), via="send"
            )
        )

    got = record.posted_hashes(database.read(lambda c: record.read(c, it.id)))

    assert sorted(got) == ["1", "2", "N1", "N2"]
    assert got["1"] == got["N1"] != got["2"]


@pytest.mark.parametrize(
    "intents, expected",
    [
        pytest.param([("reply", "T1", "posted")], {"T1"}, id="posted"),
        pytest.param([("reply", "T1", "unclear")], {"T1"}, id="unclear"),
        pytest.param([("reply", "T1", "refused")], set(), id="refused-excluded"),
        pytest.param(
            [
                ("reply", "T1", "posted", "fixed"),
                ("reply", "T1", "posted"),
                ("reply", "T2", "posted"),
            ],
            {"T1", "T2"},
            id="fixed-and-triage-both",
        ),
        # Not a thread's reply even if its payload named a thread: only op "reply" counts.
        pytest.param([("summary", "T9", "posted")], set(), id="summary-excluded"),
    ],
)
def test_thread_ids(intents, expected):
    """The threads the record holds a reply in: posted or unclear ones, never a refused
    reply or the summary comment (which is in no thread)."""
    held = {
        str(n): record.Intent(str(n), {"op": op, "thread": thread, "kind": kind}, state)
        for n, (op, thread, state, *kind) in enumerate(intents)
        for kind in [kind[0] if kind else "declined"]
    }

    assert record.thread_ids(held) == frozenset(expected)
