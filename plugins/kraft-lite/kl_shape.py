"""Where a frozen chain can be sliced, read from its declarations alone.

Stdlib only, as the rest of the plugin. No rule here names a node: a chain is
sliceable by its shape, so a repo's own chain with a plan gate and a build node
slices exactly as the bundled `default` does, under whatever names it gives
them. A chain without the shape is still walked, unsliced.
"""

from __future__ import annotations

from dataclasses import dataclass

from kl_chains import ChainError

#: What a node means to a sliced controller (`state.sliced.action`).
ACTIONS = ("run_slices", "scoped_verify", "run", "skip", "await_user_merges")
#: The forge targets the shape is read from: opening the merge request a
#: feature lands through, and merging it.
OPEN_MR = "mr.open_draft"
MERGE = "mr.merge"


@dataclass(frozen=True)
class SliceShape:
    #: The last gate before the first build node: the plan is settled there.
    slice_gate: str
    #: The first exec node none of whose tasks produces anything: the build.
    first_build: str
    #: The first node with a forge task opening the merge request.
    mr_node: str
    #: What a child walks: see `slice_shape`.
    child_nodes: tuple[str, ...]


def _tasks(node: dict) -> list[dict]:
    """An exec node's own tasks, every step's; its `on_failure`, fix loop and
    judge are recovery, not what the node is for. A gate has none."""
    return [task for step in node.get("steps") or () for task in step["tasks"]]


def _forge(node: dict, target: str) -> bool:
    return any(t["kind"] == "forge" and t.get("target") == target for t in _tasks(node))


def _produces(node: dict) -> set[str]:
    return {t["produces"] for t in _tasks(node) if t.get("produces")}


def slice_shape(chain: dict) -> SliceShape:
    """The chain's slice gate, first build node, MR node and child chain, or a
    `ChainError` naming the piece it lacks.

    The child chain is the nodes after the slice gate and before the first gate
    after the MR node, less every gate and every exec node producing a kind
    some gate presents: a child has no human to answer a gate, and what it
    would produce for one is the controller's to present."""
    nodes = chain["nodes"]

    def refuse(why: str) -> ChainError:
        return ChainError(f"chain {chain['id']!r} cannot be sliced: {why}")

    build = next((i for i, n in enumerate(nodes) if n["kind"] == "exec" and not _produces(n)), None)
    if build is None:
        raise refuse("no build node (an exec node whose tasks produce nothing)")
    gates = [i for i in range(build) if nodes[i]["kind"] == "gate"]
    if not gates:
        raise refuse("no gate before the first build node")
    gate = gates[-1]
    if nodes[gate]["chain_finalized"]:
        raise refuse("the gate before the first build node is the chain_finalized gate")
    mr = next((i for i, n in enumerate(nodes) if _forge(n, OPEN_MR)), None)
    if mr is None:
        raise refuse(f"no node opens a merge request (forge {OPEN_MR})")
    if mr < build:
        raise refuse(
            f"{nodes[mr]['id']} opens a merge request (forge {OPEN_MR}) before the first "
            f"build node {nodes[build]['id']}"
        )
    end = next((i for i in range(mr + 1, len(nodes)) if nodes[i]["kind"] == "gate"), len(nodes))
    presented = {n["artifact"] for n in nodes if n["kind"] == "gate" and n["artifact"]}
    child = tuple(
        n["id"]
        for n in nodes[gate + 1 : end]
        if n["kind"] == "exec" and not (_produces(n) & presented)
    )
    return SliceShape(nodes[gate]["id"], nodes[build]["id"], nodes[mr]["id"], child)


def sliced_action(chain: dict, shape: SliceShape, node_id: str, mode: str) -> str:
    """What `node_id` means to the controller of a run sliced in `mode`.

    Up to the first build node, the chain runs as it would unsliced; the first
    build node runs the slices. After it, in `feature` mode the slices merge
    into a feature branch that still goes through the chain's own review and
    merge request: an exec node before the MR node that produces nothing is a
    check, run scoped to the feature branch's diff (`scoped_verify`); everything
    else runs. In `main` mode each slice merged on its own: the
    `chain_finalized` gate and every node merging a merge request wait for the
    user's merges, and the rest has nothing left to do."""
    ids = [n["id"] for n in chain["nodes"]]
    at, build = ids.index(node_id), ids.index(shape.first_build)
    if at < build:
        return "run"
    if at == build:
        return "run_slices"
    node = chain["nodes"][at]
    if mode == "main":
        finalized = node["kind"] == "gate" and node["chain_finalized"]
        return "await_user_merges" if finalized or _forge(node, MERGE) else "skip"
    if node["kind"] == "exec" and at < ids.index(shape.mr_node) and not _produces(node):
        return "scoped_verify"
    return "run"


def child_chain(chain: dict) -> dict:
    """The chain a child walks for one slice: `chain`'s child nodes, frozen
    as the controller froze them. A kept node's `on_base_changed.restart_from`
    naming a node the child lacks is redirected to the nearest kept exec node
    before that one, else the child's first node."""
    shape = slice_shape(chain)
    ids = [n["id"] for n in chain["nodes"]]
    kept = [n for n in chain["nodes"] if n["id"] in shape.child_nodes]

    def redirected(target: str) -> str:
        if target in shape.child_nodes:
            return target
        before = [n["id"] for n in kept if ids.index(n["id"]) < ids.index(target)]
        return before[-1] if before else shape.child_nodes[0]

    nodes = [
        dict(node, on_base_changed={"restart_from": redirected(base["restart_from"])})
        if (base := node.get("on_base_changed"))
        else node
        for node in kept
    ]
    return dict(chain, id=f"{chain['id']}/slice", nodes=nodes)
