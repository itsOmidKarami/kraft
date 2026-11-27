"""What `dispatch` hands the review wait and the CI look while their node is in a reply round (each
case seeds the round's event and calls `dispatch_node` directly)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kraft import events
from kraft.adapters.forge.replies import owed
from kraft.executor import dispatch, reply_round

from .test_dispatch import NO_SETUP

_REVIEW = {
    "id": "review",
    "kind": "forge",
    "target": "mr.automated_review",
    "wait": {"polling": {"initial_interval": "30s", "max_interval": "2m"}},
    "policy": {"total_time_cap_minutes": 10},
}
_CI = {"id": "await_ci", "kind": "forge", "target": "mr.ci"}
_READY = {"id": "ready", "kind": "forge", "target": "mr.mark_ready"}


def _chain(*, review=None, node_policy=None):
    node = {
        "id": "feedback",
        "kind": "exec",
        "steps": [
            {"id": "settle", "tasks": [{**_REVIEW, **(review or {})}]},
            {"id": "ci", "tasks": [_CI]},
            {"id": "ready", "tasks": [_READY]},
        ],
    }
    if node_policy:
        node["policy"] = node_policy
    return [node]


async def _handed(it, monkeypatch, task=(0, 0), *seeded):
    """The keyword arguments the forge adapter was run with, for the task at `task`
    (step, task index), after `seeded` events."""
    seen: dict = {}

    async def run_task(_db, _rd, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr(dispatch._forge, "run_task", run_task)
    await it.database.write(
        lambda c: [events.append(c, it.id, type_, payload) for type_, payload in seeded]
    )
    node = it.chain.chain.nodes[0]
    step, index = task
    assert (
        await dispatch.dispatch_node(
            it.database,
            it.run_dirs,
            node.steps[step].tasks[index],
            node,
            it.row(),
            it.repo,
            launch=NO_SETUP,
        )
        == "done"
    )
    return seen


_DEADLINE = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)


def _started(head, node="feedback", threads=("T1", "T2"), deadline=None):
    return (
        "reply_round_started",
        {
            "node_id": node,
            "head": head,
            "round": 1,
            "threads": list(threads),
            "deadline": deadline.isoformat() if deadline else None,
        },
    )


_FIVE = timedelta(minutes=5)


@pytest.mark.parametrize(
    "case, task, expected",
    [
        pytest.param("open", (0, 0), owed.ReplyWait(("T1", "T2"), _FIVE), id="open-round"),
        pytest.param("none", (0, 0), None, id="no-round"),
        pytest.param("other-node", (0, 0), None, id="another-node-s-round"),
        pytest.param("recorded", (0, 0), None, id="a-record-written-after-it"),
        pytest.param("other-head", (0, 0), None, id="another-head"),
        pytest.param(
            "open-with-a-deadline",
            (1, 0),
            owed.ReplyWait(("T1", "T2"), _FIVE, _DEADLINE),
            id="ci-task-with-an-open-round",
        ),
        pytest.param("other-head", (1, 0), None, id="ci-task-with-another-heads-round"),
    ],
)
async def test_the_review_wait_is_handed_the_open_round(item_on, monkeypatch, case, task, expected):
    """The review wait and the CI look of a node in a reply round are handed it, the CI look
    with the round's deadline (`a-reply-round-never-stops-the-item-on-pending-ci`)."""
    it = await item_on(_chain(), "feedback")
    head = reply_round.item_head(it.row(), it.repo)
    seeded = {
        "open": [_started(head)],
        "open-with-a-deadline": [_started(head, deadline=_DEADLINE)],
        "none": [],
        "other-node": [_started(head, node="other")],
        "recorded": [_started(head), ("findings_measured", {"node_id": "feedback", "cycle": 0})],
        "other-head": [_started("0" * 40)],
    }[case]

    seen = await _handed(it, monkeypatch, task, *seeded)

    assert seen["reply"] == expected


@pytest.mark.parametrize(
    "chain, minutes",
    [
        pytest.param(_chain(), 5, id="node-default"),
        pytest.param(_chain(node_policy={"reply_timeout_minutes": 3}), 3, id="node-level"),
        pytest.param(
            _chain(
                review={"policy": {**_REVIEW["policy"], "reply_timeout_minutes": 9}},
                node_policy={"reply_timeout_minutes": 3},
            ),
            9,
            id="task-override",
        ),
    ],
)
async def test_the_reply_timeout_is_the_review_tasks(item_on, monkeypatch, chain, minutes):
    it = await item_on(chain, "feedback")
    head = reply_round.item_head(it.row(), it.repo)

    seen = await _handed(it, monkeypatch, (0, 0), _started(head))

    assert seen["reply"].timeout == timedelta(minutes=minutes)


async def test_a_task_that_is_not_the_review_wait_gets_no_reply(item_on, monkeypatch):
    it = await item_on(_chain(), "feedback")
    head = reply_round.item_head(it.row(), it.repo)

    seen = await _handed(it, monkeypatch, (2, 0), _started(head))

    assert seen["reply"] is None
