"""Item chain drafts (B15): a person's unapplied edits to one work item's own
chain (`store.materialized_chain_of`: the run fork's copy once a retry made
one), applied all at once or not at all.

Four ops, each bounded the way the door it stands in for is:

* `override {path, task_config?, policy?}` -- `validate_retry_override`, so a
  draft sets exactly what a retry override may.
* `add_node {after, node}` and `remove_node {node}` -- `revision.revise`, the
  chain-revision gate's own code: library components only, and never a gate,
  an unskippable node or one that lands the merge request removed.
* `skip {path}` -- a step or task path, recorded at apply as `scope_skipped`.

An op is *passed* once the item has moved past where it acts (`passed`); a
passed op is never applied, and applying a draft that holds one is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from kraft.templates import revision
from kraft.templates.forks import ChainPath, ControlScope, PathError
from kraft.templates.library import TemplateLibrary
from kraft.templates.models import PATH_SEPARATOR, MaterializedChain, ResolvedChain
from kraft.templates.retry import RetryOverrideError, validate_retry_override

#: The evidence and rationale of the change set a draft becomes.
EVIDENCE = "item draft"


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverrideOp(_Op):
    op: Literal["override"]
    path: str
    task_config: dict[str, object] | None = None
    policy: dict[str, object] | None = None


class AddNodeOp(_Op):
    op: Literal["add_node"]
    after: str
    node: revision.AddedNode


class RemoveNodeOp(_Op):
    op: Literal["remove_node"]
    node: str


class SkipOp(_Op):
    op: Literal["skip"]
    path: str


Op = Annotated[OverrideOp | AddNodeOp | RemoveNodeOp | SkipOp, Field(discriminator="op")]
#: The op list a `PUT` sends, read strictly; a malformed one is the client's 422.
OPS = TypeAdapter(list[Op])


def _node(op: Op) -> str:
    """The node `op` acts on; for `add_node`, the node it follows."""
    if isinstance(op, AddNodeOp):
        return op.after
    if isinstance(op, RemoveNodeOp):
        return op.node
    return op.path.split(PATH_SEPARATOR)[0]


def passed(op: Op, ids: list[str], current: str | None) -> bool:
    """Whether the item has moved past where `op` acts: its node is at or
    before `current` (an `add_node`: its `after` is before it). Never before
    the item starts (`current` None); always once its chain has run to the
    end (`current` no longer one of `ids`). A node an earlier op adds is never
    passed: it follows one that is not."""
    if current is None:
        return False
    if current not in ids:
        return True
    node, here = _node(op), ids.index(current)
    if node not in ids:
        return False
    return ids.index(node) < here if isinstance(op, AddNodeOp) else ids.index(node) <= here


def _override(chain: MaterializedChain, op: OverrideOp) -> MaterializedChain:
    def one(c: MaterializedChain) -> ResolvedChain:
        return validate_retry_override(
            c, op.path, task_config=op.task_config, policy=op.policy
        ).chain.chain

    revised = replace(chain, chain=one(chain))
    if chain.untrimmed is not None:
        # The copy before the attachment trim gets the override too, as
        # `revise` keeps it for a skip or an add.
        whole = replace(
            chain,
            chain=ResolvedChain.from_chain(
                chain.untrimmed, steering=chain.chain.steering, plugins=chain.chain.plugins
            ),
            untrimmed=None,
        )
        revised = replace(revised, untrimmed=one(whole).chain)
    return revised


def _skip(chain: MaterializedChain, op: SkipOp) -> None:
    target = ChainPath.parse(chain, op.path)
    if target.scope is ControlScope.NODE:
        raise PathError(f"{op.path!r} is a node; skip names a step or a task (remove_node a node)")
    if not target.skippable:
        raise PathError(f"{op.path!r} does not allow skipping")


@dataclass(frozen=True)
class Evaluated:
    #: Each op as stored, with `passed`.
    ops: list[dict]
    #: `[{op: index, message}]`, one per op that cannot apply.
    problems: list[dict]
    #: The chain with every op applied that is neither passed nor a problem.
    chain: MaterializedChain
    #: The paths of the `skip` ops applied.
    skips: list[str]

    @property
    def passed(self) -> list[int]:
        return [i for i, op in enumerate(self.ops) if op["passed"]]


def evaluate(
    chain: MaterializedChain,
    current: str | None,
    ops: list[dict],
    library: TemplateLibrary | None,
) -> Evaluated:
    """`ops` applied in order to `chain`, the item standing on `current` (None:
    not started). Each op is its own change set, so a problem names its op;
    the next op applies to the chain as the ones before it left it."""
    parsed = OPS.validate_python(ops)
    ids = [n.id for n in chain.chain.nodes]
    at = current if current in ids else None
    marked, problems, skips = [], [], []
    for i, (raw, op) in enumerate(zip(ops, parsed, strict=True)):
        marked.append({**raw, "passed": passed(op, ids, current)})
        if marked[-1]["passed"]:
            continue
        try:
            if isinstance(op, OverrideOp):
                chain = _override(chain, op)
            elif isinstance(op, SkipOp):
                _skip(chain, op)
                skips.append(op.path)
            else:
                change = (
                    {"add": [{"after": op.after, "node": op.node, "evidence": EVIDENCE}]}
                    if isinstance(op, AddNodeOp)
                    else {"skip": [{"node": op.node, "evidence": EVIDENCE}]}
                )
                changes = revision.ChangeSet.model_validate({"rationale": EVIDENCE, **change})
                chain = revision.revise(chain, changes, at=at, library=library)
        except (revision.RevisionError, RetryOverrideError, PathError) as exc:
            problems.append({"op": i, "message": str(exc)})
    return Evaluated(ops=marked, problems=problems, chain=chain, skips=skips)
