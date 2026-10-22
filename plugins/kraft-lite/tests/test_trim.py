"""`trim`: the nodes an attachment makes redundant dropped, as Kraft's
`ResolvedChain.trim_for_attachments` drops them, and every pointer a kept node
holds into a dropped one cleared or moved. The chains are built under
`tmp_path`, as in `test_chains.py`, whose library and helpers they reuse."""

from __future__ import annotations

import pytest

import kl_chains

from .test_chains import DEFAULT_IDS, LIBRARY, write_library

#: A chain whose gate on the plan sends a rejection back to the spec's author.
CROSSED = """\
    nodes:
      - id: author
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: spec}
      - id: planner
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: plan}
      - id: check
        kind: gate
        artifact: plan
        reject_to: author
      - id: build
        kind: exec
        tasks:
          - {id: work, extends: worker}
    """


#: A chain whose last node restarts, on a base change, from the plan's author.
RESTARTING = """\
    nodes:
      - id: author
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: spec}
      - id: planner
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: plan}
      - id: build
        kind: exec
        tasks:
          - {id: work, extends: worker}
      - id: ship
        kind: exec
        tasks:
          - {id: work, extends: worker}
        on_base_changed: {restart_from: planner}
    """


#: A chain whose merge, dropped by the forge, restarts from the plan's author.
REJECTING = """\
    nodes:
      - id: author
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: spec}
      - id: planner
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: plan}
      - id: merge
        kind: exec
        tasks:
          - {id: merge, kind: forge, target: mr.merge}
        on_merge_rejected:
          restart_from: planner
          repair:
            steps:
              - id: fix
                tasks:
                  - {id: repair, extends: worker}
    """


def _drop(*dropped: str) -> list[str]:
    return [n for n in DEFAULT_IDS if n not in dropped]


@pytest.mark.usefixtures("home")
@pytest.mark.parametrize(
    ("chain", "kinds", "kept"),
    [
        pytest.param("default", set(), DEFAULT_IDS, id="nothing_attached"),
        pytest.param("default", {"spec"}, _drop("spec", "spec_approval"), id="spec"),
        pytest.param(
            "default",
            {"spec", "plan"},
            _drop("spec", "spec_approval", "plan", "plan_approval"),
            id="spec_and_plan",
        ),
        # A `chain_finalized` gate is never dropped, whatever its artifact says.
        pytest.param("default", {"review_brief"}, _drop("work_item_summary"), id="final_gate_kept"),
        # The gate survives; its `reject_to` named a dropped node, so the target goes.
        pytest.param(
            "crossed", {"spec"}, ["planner", "check->None", "build"], id="reject_to_cleared"
        ),
        pytest.param("crossed", {"plan"}, ["author", "build"], id="node_and_gate"),
        # A `restart_from` naming a dropped node moves to the nearest kept exec
        # node before it, else the first kept one, as a child chain's does.
        pytest.param(
            "restarting",
            {"plan"},
            ["author", "build", "ship~author"],
            id="restart_from_redirected_to_an_earlier_node",
        ),
        pytest.param(
            "restarting",
            {"spec", "plan"},
            ["build", "ship~build"],
            id="restart_from_redirected_to_the_first_node",
        ),
        pytest.param("restarting", {"spec"}, ["planner", "build", "ship~planner"], id="kept"),
        # An `on_merge_rejected` target moves only earlier, as Kraft's
        # `without_nodes`: with no kept exec node before it, the policy goes.
        pytest.param(
            "rejecting",
            {"plan"},
            ["author", "merge~author"],
            id="merge_rejected_redirected_to_an_earlier_node",
        ),
        pytest.param(
            "rejecting", {"spec", "plan"}, ["merge~None"], id="merge_rejected_dropped_with_none"
        ),
        pytest.param("rejecting", {"spec"}, ["planner", "merge~planner"], id="merge_rejected_kept"),
    ],
)
def test_trim(tmp_path, chain, kinds, kept):
    chains = {"crossed": CROSSED, "restarting": RESTARTING, "rejecting": REJECTING}
    lib = write_library(tmp_path / "lib", LIBRARY, chains)
    env = {} if chain == "default" else {"KRAFT_CONFIG_DIR": str(lib)}
    resolved = kl_chains.resolve(tmp_path, env, chain)

    trimmed = kl_chains.trim(resolved, kinds)

    def show(n: dict) -> str:
        if n["kind"] == "gate" and chain == "crossed":
            return f"{n['id']}->{n['reject_to']}"
        if chain == "restarting" and n["on_base_changed"]:
            return f"{n['id']}~{n['on_base_changed']['restart_from']}"
        if chain == "rejecting" and n["id"] == "merge":
            rejected = n["on_merge_rejected"]
            return f"{n['id']}~{rejected['restart_from'] if rejected else None}"
        return n["id"]

    shown = [show(n) for n in trimmed["nodes"]]
    assert shown == kept
    if chain == "rejecting":
        # A repair written as steps, each task at its canonical path.
        [step] = resolved["nodes"][-1]["on_merge_rejected"]["repair"]["steps"]
        assert [t["path"] for t in step["tasks"]] == ["merge.on_merge_rejected.repair.fix.repair"]
    assert dict(trimmed, nodes=None) == dict(resolved, nodes=None)


def test_trim_refuses_to_empty_a_chain():
    gate = {"id": "g", "kind": "gate", "artifact": "spec", "reject_to": None}
    chain = {"format": "v1", "id": "c", "source": "s", "nodes": [dict(gate, chain_finalized=False)]}

    with pytest.raises(kl_chains.ChainError, match="would leave chain 'c' with no nodes"):
        kl_chains.trim(chain, {"spec"})
