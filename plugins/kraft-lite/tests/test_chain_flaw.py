"""`kl._chain_flaw`: what a chain that says it is V1 may lack of the frozen shape
the walk reads. `start --slice-of`, `import` and every verb reading a run's
frozen chain call it; each has one wiring row of its own, and the catalog of
flaws is this table."""

from __future__ import annotations

import copy

import pytest

import kl


def edited(node_id: str, edit):
    """The default, with `edit` applied to its node `node_id`."""

    def apply(chain: dict) -> dict:
        edit(next(n for n in chain["nodes"] if n["id"] == node_id))
        return chain

    return apply


def without(key: str):
    return lambda chain: {k: v for k, v in chain.items() if k != key}


def only(*nodes: dict):
    """A chain of nothing but `nodes`, as a hand-written file might be."""
    return lambda chain: {"format": "v1", "id": "x", "source": "bundled", "nodes": list(nodes)}


@pytest.mark.parametrize(
    "make, flaw",
    [
        pytest.param(without("nodes"), "it has no list of nodes", id="chain_without_nodes"),
        pytest.param(
            only({"id": "a", "steps": []}),
            "node 'a' has no kind (exec or gate)",
            id="node_without_kind",
        ),
        pytest.param(only({"kind": "gate"}), "node 1 has no id", id="node_without_id"),
        pytest.param(
            only({"id": "a", "kind": "exec"}), "exec node 'a' has no steps", id="exec_without_steps"
        ),
        # The whole frozen shape the walk reads, not only ids, kinds and steps.
        pytest.param(
            edited("verification", lambda n: n.pop("fix_loop")),
            "exec node 'verification' has no fix_loop",
            id="exec_without_fix_loop",
        ),
        pytest.param(
            edited("merge", lambda n: n.pop("on_merge_rejected")),
            "exec node 'merge' has no on_merge_rejected",
            id="exec_without_on_merge_rejected",
        ),
        pytest.param(
            edited("merge", lambda n: n["on_merge_rejected"].pop("repair")),
            "exec node 'merge''s on_merge_rejected.repair has no steps",
            id="on_merge_rejected_without_repair",
        ),
        pytest.param(
            edited("merge", lambda n: n["on_merge_rejected"].pop("restart_from")),
            "exec node 'merge''s on_merge_rejected has no restart_from",
            id="on_merge_rejected_without_restart_from",
        ),
        pytest.param(
            edited("plan_approval", lambda n: n.pop("message")),
            "gate node 'plan_approval' has no message",
            id="gate_without_message",
        ),
        pytest.param(
            edited("implementation", lambda n: n["steps"][0]["tasks"][0].pop("path")),
            "exec node 'implementation' has a task with no path",
            id="task_without_path",
        ),
        # `start` answers with the chain's id and source, and a child is named from its id.
        pytest.param(without("id"), "it has no id", id="chain_without_id"),
        pytest.param(without("source"), "it has no source", id="chain_without_source"),
    ],
)
def test_chain_flaw_names_what_the_walk_would_lack(default_chain, make, flaw):
    assert kl._chain_flaw(make(copy.deepcopy(default_chain))) == flaw
