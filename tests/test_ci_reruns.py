"""The bookkeeping behind a fix-loop repair's CI re-run (Kraft-zje6v)."""

import pytest
from support.harness import v1_node, v1_resolved
from support.store_fixtures import mk_item

from kraft import ci_reruns, events
from kraft.vocab import ChainEvent, ForgeEvent

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


async def test_node_run_start_is_the_latest_node_started(database):
    await mk_item(database)
    assert database.read(lambda c: ci_reruns.node_run_start(c, "w1", "n1")) == 0
    _, second, _ = await _append(
        database,
        (ChainEvent.NODE_STARTED, "n1"),
        (ChainEvent.NODE_STARTED, "n1"),
        (ChainEvent.NODE_STARTED, "n2"),
    )

    assert database.read(lambda c: ci_reruns.node_run_start(c, "w1", "n1")) == second


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
