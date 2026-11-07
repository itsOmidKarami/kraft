"""Slicing an item's chain when its plan declares slices (chain revision's one
change Kraft builds itself).

`slicing_for` reads the plan's `## Slices` section and the item's remaining
chain and says whether, and how, the implementation span becomes a slice group;
`with_slicing` puts that in a change set; `apply` is what `revision.revise`
runs to wrap the span. The agent that wrote the change set never shapes the
group: a `slice` entry in its artifact is refused at `revision.parse`.

The rules, in the item's remaining chain (the nodes after the revision gate,
less what the change set skips):

* the *body* is the implementing node and the execution nodes right after it,
  stopping before the first gate, the first node holding a forge task and the
  first node whose output a gate shows;
* right after the group, a copy of every body node but the implementing one
  runs on the combined code under its own id;
* the group's `unforked` is the implementing node;
* every `reject_to` / `restart_from` naming the implementing node names the
  group.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from kraft import progress
from kraft.policy import FROZEN
from kraft.slices import SliceRefusal, SliceSplit, parse_slices
from kraft.templates.environment import Identifier
from kraft.templates.models import (
    ExecNode,
    ForgeTask,
    GateNode,
    GroupNode,
    MaterializedChain,
    NodeKind,
    ResolvedNode,
)

GROUP_ID = "slices"
#: `slicing_for`'s answer for a plan with no `## Slices` section: nothing to say.
NO_SECTION = "the plan declares no ## Slices section"


class SliceChange(BaseModel):
    """The slicing entry of a change set: which nodes become the group's body."""

    model_config = ConfigDict(strict=True, extra="forbid")

    group: Identifier = GROUP_ID
    body: list[Identifier] = Field(min_length=1)
    unforked: list[Identifier] = Field(min_length=1)
    slices: StrictInt = Field(ge=2)


@dataclass(frozen=True)
class SlicingInput:
    """What `slicing_for` needs beyond the chain: the item kind comes from the
    chain's frozen target."""

    plan_text: str | None
    max_slices: int


def _remaining(chain: MaterializedChain, at: str | None, skipped: set[str]) -> list[ResolvedNode]:
    nodes = list(chain.chain.nodes)
    ids = [n.id for n in nodes]
    start = ids.index(at) + 1 if at in ids else 0
    return [n for n in nodes[start:] if n.id not in skipped]


def _holds_forge(node: ResolvedNode) -> bool:
    return any(isinstance(t.task, ForgeTask) for t in node.tasks())


def slicing_for(
    chain: MaterializedChain,
    plan_text: str | None,
    changes,
    *,
    max_slices: int,
    at: str | None,
) -> SliceChange | str:
    """The slicing `plan_text` and `chain` call for, or the reason there is none."""
    if chain.target.kind == "workspace":
        return "a workspace item is not sliced: its pieces would share several repositories"
    if chain.target.in_place:
        return "an in-place item is not sliced: it works in the repository's own checkout"
    if plan_text is None:
        return NO_SECTION
    split = parse_slices(plan_text, max_slices=max_slices)
    if split is None:
        return NO_SECTION
    if isinstance(split, SliceRefusal):
        return f"the plan's ## Slices section was not used: {split.reason}"
    skipped = {s.node for s in changes.skip}
    remaining = _remaining(chain, at, skipped)
    if any(isinstance(n.node, GroupNode) for n in remaining):
        return "the chain already holds a slice group"
    implementing = progress.implementing_nodes(chain.chain)
    left = [n for n in remaining if n.id in implementing and isinstance(n.node, ExecNode)]
    if not left:
        if any(i in skipped for i in implementing):
            return "the change set skips the implementing node, so there is nothing to slice"
        return "the implementing node has already run, so there is nothing left to slice"
    first = left[0]
    if _holds_forge(first):
        return f"the implementing node {first.id!r} holds a forge task, which a slice cannot run"
    shown = {n.node.artifact for n in chain.chain.nodes if isinstance(n.node, GateNode)}
    body = [first]
    for node in remaining[remaining.index(first) + 1 :]:
        if not isinstance(node.node, ExecNode) or _holds_forge(node):
            break
        if (node.produces() - {None}) & shown:
            break
        body.append(node)
    for node in body:
        exec_node = node.node
        if exec_node.on_merge_rejected is not None or exec_node.on_base_changed is not None:
            return (
                f"node {node.id!r} in the span declares on_merge_rejected or on_base_changed, "
                "which a slice body cannot hold"
            )
    if GROUP_ID in {n.id for n in chain.chain.nodes}:
        return f"the chain already has a node named {GROUP_ID!r}"
    return SliceChange(
        group=GROUP_ID,
        body=[n.id for n in body],
        unforked=[first.id],
        slices=len(split.slices),
    )


def with_slicing(changes, chain: MaterializedChain, slicing: SlicingInput, *, at: str | None):
    """`changes` with Kraft's slicing entry added, or the reason a present
    `## Slices` section was not used appended to its rationale. The same call at
    every place a change set is parsed, so a gate's page, an agent's review and
    the approval all see one set."""
    result = slicing_for(chain, slicing.plan_text, changes, max_slices=slicing.max_slices, at=at)
    if isinstance(result, SliceChange):
        return changes.model_copy(update={"slice": result})
    if result == NO_SECTION:
        return changes
    return changes.model_copy(update={"rationale": f"{changes.rationale}\n\nNot sliced: {result}."})


def split_of(plan_text: str | None, max_slices: int) -> SliceSplit | SliceRefusal | None:
    """The plan's split, for a page that shows it."""
    return None if plan_text is None else parse_slices(plan_text, max_slices=max_slices)


def apply(nodes: list, change: SliceChange) -> list:
    """`nodes` (authored nodes of the revised chain) with `change` applied: the
    body wrapped in a group, a copy of every non-implementing body node after
    it, and the targets that named the implementing node naming the group.
    Raises `ValueError` when the body is no longer there, whole and in order."""
    ids = [n.id for n in nodes]
    if any(i not in ids for i in change.body):
        raise ValueError("the slice body names a node the revised chain no longer has")
    at = ids.index(change.body[0])
    if ids[at : at + len(change.body)] != change.body:
        raise ValueError("the slice body is no longer a run of adjacent nodes")
    body = nodes[at : at + len(change.body)]
    if not all(isinstance(n, ExecNode) for n in body):
        raise ValueError("a slice body holds execution nodes only")
    group = GroupNode.model_validate(
        {
            "id": change.group,
            "kind": NodeKind.GROUP,
            "nodes": body,
            "unforked": change.unforked,
        },
        context=FROZEN,
    )
    copies = [n for n in body if n.id not in change.unforked]
    renamed = {i: change.group for i in change.unforked}
    out = [*nodes[:at], group, *copies, *nodes[at + len(body) :]]
    return [_repointed(n, renamed) for n in out]


def _repointed(node, renamed: dict[str, str]):
    if isinstance(node, GateNode):
        if node.reject_to in renamed:
            return node.model_copy(update={"reject_to": renamed[node.reject_to]})
        return node
    if isinstance(node, GroupNode):
        return node
    update = {}
    for name in ("on_base_changed", "on_merge_rejected"):
        policy = getattr(node, name)
        if policy is not None and policy.restart_from in renamed:
            update[name] = policy.model_copy(update={"restart_from": renamed[policy.restart_from]})
    return node.model_copy(update=update) if update else node
