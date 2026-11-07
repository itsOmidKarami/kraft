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
  group;
* the group's `on_conflict` is the library task `resolve_join_conflict` when
  the library has one (`join_conflict_handler`); without it a join conflict
  stops for a person.

Chain revision runs again after a rewind to the plan or earlier, over a chain
that already holds the group. `unapply` takes the group out first (the body
back in its place, the copies gone, the targets naming the implementing node
again), and the slicing is worked out afresh from the plan as it then stands:
the group is rebuilt, or left out with the reason in the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from kraft import progress
from kraft.policy import FROZEN
from kraft.slices import SliceRefusal, SliceSplit, parse_slices
from kraft.templates.environment import Identifier
from kraft.templates.models import (
    Chain,
    ExecNode,
    ForgeTask,
    GateNode,
    GroupNode,
    MaterializedChain,
    NodeKind,
    RecoveryPlan,
    ResolvedChain,
    ResolvedNode,
)

GROUP_ID = "slices"
#: The library task the join hands a conflict to before it asks a person.
JOIN_HANDLER = "resolve_join_conflict"
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


def group_after(chain: MaterializedChain, at: str | None) -> str | None:
    """The id of the slice group still ahead of `at` in `chain`, or None."""
    return next((n.id for n in _remaining(chain, at, set()) if isinstance(n.node, GroupNode)), None)


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
        # `with_slicing` takes a group ahead out before it gets here.
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
    the approval all see one set.

    A chain that already holds a group ahead of `at` (revision running again
    after a rewind to the plan) has it taken out first (`unslice`), and the
    slicing is worked out on the chain without it: rebuilt from the plan as it
    stands now, or left out with the reason."""
    if (group := group_after(chain, at)) is not None:
        changes = changes.model_copy(update={"unslice": group})
        try:
            chain = unsliced(chain, group)
        except ValueError:
            return changes  # `revise` takes it out the same way, and says why it cannot
    result = slicing_for(chain, slicing.plan_text, changes, max_slices=slicing.max_slices, at=at)
    if isinstance(result, SliceChange):
        return changes.model_copy(update={"slice": result})
    if result == NO_SECTION and group is None:
        return changes
    return changes.model_copy(update={"rationale": f"{changes.rationale}\n\nNot sliced: {result}."})


def split_of(plan_text: str | None, max_slices: int) -> SliceSplit | SliceRefusal | None:
    """The plan's split, for a page that shows it."""
    return None if plan_text is None else parse_slices(plan_text, max_slices=max_slices)


def join_conflict_handler(library, pinned) -> tuple[RecoveryPlan | None, ResolvedChain | None]:
    """The group's `on_conflict`, `resolve_join_conflict` resolved out of
    `library` (its own, else the built-in add-on's), and the chain it was
    resolved in, for the steering and add-ons it reads; `(None, None)` when
    the library has none. Raises `TemplateLibraryError`."""
    from pathlib import Path

    from kraft.addons import builtin
    from kraft.templates.library import Namespace

    if library is None:
        return None, None
    name = next(
        (
            n
            for n in (JOIN_HANDLER, f"{builtin.NAMESPACE}:{JOIN_HANDLER}")
            if library.component(Namespace.TASKS, n) is not None
        ),
        None,
    )
    if name is None:
        return None, None
    task = {"id": "resolve", "extends": name}
    candidate, id = library.with_chain(
        Path(f"{JOIN_HANDLER}.yaml"),
        {"id": JOIN_HANDLER, "nodes": [{"id": GROUP_ID, "kind": "exec", "tasks": [task]}]},
    )
    resolved = candidate.resolve_chain(id, strict_pins=True, pinned=pinned)
    return RecoveryPlan(tasks=resolved.nodes[0].node.tasks), resolved


def apply(nodes: list, change: SliceChange, on_conflict: RecoveryPlan | None = None) -> list:
    """`nodes` (authored nodes of the revised chain) with `change` applied: the
    body wrapped in a group with `on_conflict` as its join conflict handler, a
    copy of every non-implementing body node after it, and the targets that
    named the implementing node naming the group. Raises `ValueError` when the
    body is no longer there, whole and in order."""
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
            "on_conflict": on_conflict,
        },
        context=FROZEN,
    )
    copies = [n for n in body if n.id not in change.unforked]
    renamed = {i: change.group for i in change.unforked}
    out = [*nodes[:at], group, *copies, *nodes[at + len(body) :]]
    return [_repointed(n, renamed) for n in out]


def unapply(nodes: list, group_id: str) -> list:
    """`nodes` with the group `group_id` taken out, `apply` undone: its body
    back in its place, the copies after it gone (each body node but the
    implementing one is the copy, which is what any later change reached), and
    the targets naming the group naming the implementing node again. Raises
    `ValueError` when `nodes` holds no such group."""
    at = next(
        (i for i, n in enumerate(nodes) if isinstance(n, GroupNode) and n.id == group_id), None
    )
    if at is None:
        raise ValueError(f"the chain holds no slice group {group_id!r}")
    group = nodes[at]
    unforked = group.unforked_ids()
    copies = {n.id: n for n in nodes[at + 1 :] if n.id in {b.id for b in group.nodes}}
    body = [n if n.id in unforked else copies.get(n.id, n) for n in group.nodes]
    out = [*nodes[:at], *body, *(n for n in nodes[at + 1 :] if n.id not in copies)]
    return [_repointed(n, {group_id: unforked[0]}) for n in out]


def unsliced(chain: MaterializedChain, group_id: str) -> MaterializedChain:
    """`chain` with its group `group_id` taken out (`unapply`), validated whole
    under the frozen context as a revision is. Its pre-trim copy (`untrimmed`)
    is left as it is: `revision.revise` gives that copy the same change set.
    Raises `ValueError`."""
    authored = chain.chain.chain
    nodes = unapply(list(authored.nodes), group_id)
    checked = Chain.model_validate_json(
        authored.model_copy(update={"nodes": nodes}).model_dump_json(), context=FROZEN
    )
    return replace(
        chain,
        chain=ResolvedChain.from_chain(
            checked, steering=chain.chain.steering, addons=chain.chain.addons
        ),
    )


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
