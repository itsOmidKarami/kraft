"""The bookkeeping behind a fix-loop repair's CI re-run (Kraft-zje6v)."""

import pytest
from support.harness import v1_node, v1_resolved
from support.store_fixtures import mk_item

from kraft import ci_reruns, events, store
from kraft.vocab import ChainEvent, ForgeEvent, GateEvent, WorkItemEvent

STARTED = ChainEvent.FIX_CYCLE_STARTED
FINISHED = ChainEvent.FIX_CYCLE_FINISHED
REFUNDED = ChainEvent.FIX_CYCLE_REFUNDED


async def _append(database, *specs):
    """Append `(type, node_id)` events for w1; returns their seqs."""

    def write(c):
        return [events.append(c, "w1", t, {"node_id": n, "cycle": 1}) for t, n in specs]

    return await database.write(write)


@pytest.mark.parametrize(
    ("specs", "open_index"),
    [
        pytest.param([(STARTED, "n1")], 0, id="started-only"),
        pytest.param([(STARTED, "n1"), (FINISHED, "n1")], None, id="started-then-finished"),
        pytest.param([(STARTED, "n1"), (REFUNDED, "n1")], None, id="started-then-refunded"),
        pytest.param(
            [(STARTED, "n1"), (REFUNDED, "n1"), (STARTED, "n1")],
            2,
            id="refunded-then-started-again",
        ),
        pytest.param([(STARTED, "n2")], None, id="another-nodes-cycle"),
    ],
)
async def test_open_fix_cycle(database, specs, open_index):
    await mk_item(database)
    seqs = await _append(database, *specs)

    found = database.read(lambda c: ci_reruns.open_fix_cycle(c, "w1", "n1"))

    assert (found["seq"] if found else None) == (None if open_index is None else seqs[open_index])


async def test_reruns_counts_only_this_node_after_the_seq(database):
    await mk_item(database)
    early, marker, late, other = await _append(
        database,
        (ForgeEvent.CI_RERUN_REQUESTED, "n1"),
        (STARTED, "n1"),
        (ForgeEvent.CI_RERUN_REQUESTED, "n1"),
        (ForgeEvent.CI_RERUN_REQUESTED, "n2"),
    )

    found = database.read(lambda c: ci_reruns.reruns(c, "w1", "n1", after_seq=marker))

    assert [e["seq"] for e in found] == [late]
    assert early < marker < late < other


@pytest.mark.parametrize(
    ("specs", "start_index"),
    [
        pytest.param([], None, id="none"),
        # One fires per measurement round, so it says nothing about the run.
        pytest.param([(ChainEvent.NODE_STARTED, "n1")], None, id="node-started-is-not-a-run"),
        pytest.param([(WorkItemEvent.RETRIED, None)], 0, id="a-retry"),
        # A rewind to an earlier node abandons the run, though this node never completed.
        pytest.param([(ChainEvent.BASE_CHANGE_RESTART, None)], 0, id="a-base-change-restart"),
        pytest.param(
            [(ChainEvent.MERGE_REJECTION_RESTART, None)], 0, id="a-merge-rejection-restart"
        ),
        pytest.param([(ChainEvent.RUN_FORKED, None)], 0, id="a-fork"),
        pytest.param([(GateEvent.REJECTED, "g")], 0, id="a-rejection"),
        pytest.param([(ChainEvent.NODE_COMPLETED, "n1")], 0, id="the-node-completing"),
        pytest.param([(ChainEvent.NODE_COMPLETED, "n2")], None, id="another-node-completing"),
        pytest.param(
            [(WorkItemEvent.RETRIED, None), (ChainEvent.NODE_COMPLETED, "n1")], 1, id="the-latest"
        ),
    ],
)
async def test_node_run_start_is_the_latest_retry_decision_or_completion(
    database, specs, start_index
):
    await mk_item(database)
    seqs = await _append(database, *specs)

    found = database.read(lambda c: ci_reruns.node_run_start(c, "w1", "n1"))

    assert found == (0 if start_index is None else seqs[start_index])


def _forge_node(target):
    task = {"id": "ci", "kind": "forge", "target": target}
    if target == "ci.pipeline":
        task["pipeline"] = "7"
    return v1_node("feedback", tasks=[task])


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("mr.ci", True, id="mr-ci"),
        pytest.param("ci.pipeline", False, id="ci-pipeline"),
    ],
)
def test_waits_on_mr_ci(target, expected):
    node = v1_resolved([_forge_node(target)]).nodes[0]

    assert ci_reruns.waits_on_mr_ci(node) is expected


def test_waits_on_mr_ci_is_false_for_a_node_that_is_not_there():
    assert ci_reruns.waits_on_mr_ci(None) is False


@pytest.mark.parametrize(
    ("node_id", "expected"),
    [
        pytest.param("build", True, id="the-mr-ci-node"),
        pytest.param("elsewhere", None, id="an-unknown-id"),
        pytest.param(None, None, id="no-id"),
    ],
)
async def test_node_of_finds_the_node_in_an_items_materialized_chain(item_on, node_id, expected):
    """The route's path: the item's stored chain, then `node_of`, then `waits_on_mr_ci`."""
    task = {"id": "ci", "kind": "forge", "target": "mr.ci"}
    it = await item_on([v1_node("build", tasks=[task])])

    found = ci_reruns.node_of(store.materialized_chain_of(it.row()), node_id)

    assert (found is not None) is bool(expected)
    assert ci_reruns.waits_on_mr_ci(found) is bool(expected)


def test_node_of_is_none_without_a_chain():
    assert ci_reruns.node_of(None, "build") is None
