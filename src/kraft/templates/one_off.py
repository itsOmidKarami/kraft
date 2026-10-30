"""One-off chains: a chain filed with a single work item, never in the library.

A one-off chain's *custom content* is anything beyond what a chain revision may
add (`revision.AddedNode`, `revision.Override`, a removal that is not a gate or
the merge request's life). Custom content never runs until a person has
approved the exact chain: `custom_parts` says what it is, `digest` names the
chain an approval covers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from kraft.templates import revision
from kraft.templates.models import PATH_SEPARATOR, ExecNode, GateNode, ResolvedChain

#: What `work_items.chain_template` holds for a one-off item; no chain id can.
ONE_OFF_TEMPLATE = "@one-off"
#: The id a one-off chain takes when it names none.
DEFAULT_ID = "one-off"
MAX_TEXT = 256 * 1024

_EDIT_KEYS = ("id", "description", "extends", "remove", "add", "overrides")


@dataclass(frozen=True)
class CustomPart:
    #: Where the part sits in the authored mapping: "", "policy", "remove[1]",
    #: "add[0].node", "overrides.<path>".
    path: str
    #: One sentence for the reviewer.
    what: str
    #: The full authored value.
    value: object

    def view(self) -> dict:
        return {"path": self.path, "what": self.what, "value": self.value}


def digest(authored: Mapping) -> str:
    return hashlib.sha256(
        json.dumps(authored, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepare(mapping: Mapping[str, object]) -> tuple[dict, str]:
    """The chain with its `id` filled in, and its digest: the only way one is made."""
    authored = {**mapping, "id": mapping.get("id") or DEFAULT_ID}
    return authored, digest(authored)


def _flat(values: object) -> object:
    if not isinstance(values, Mapping):
        return values
    policy = values.get("policy")
    rest = {k: v for k, v in values.items() if k != "policy"}
    return {**rest, **policy} if isinstance(policy, Mapping) else values


def custom_parts(
    authored: Mapping, parent: ResolvedChain | None, resolved: ResolvedChain
) -> list[CustomPart]:
    """The custom content of `authored`, empty when it is library-only.
    `parent` is the resolved `extends` chain, `resolved` the one-off chain."""
    if parent is None:
        return [CustomPart("", "the whole chain: it extends no library chain", dict(authored))]
    parts = [
        CustomPart(key, f"the chain's own {key!r} setting", value)
        for key, value in authored.items()
        if key not in _EDIT_KEYS
    ]
    before = {n.id: n for n in parent.nodes}
    for i, node_id in enumerate(authored.get("remove") or []):
        node = before.get(node_id)
        if node is None:
            continue  # the resolver reports an id the parent lacks
        if isinstance(node.node, GateNode) or not node.node.skippable or revision._lands(node):
            parts.append(
                CustomPart(
                    f"remove[{i}]",
                    f"removes {node_id!r}: a gate, an unskippable node or part of landing the MR",
                    node_id,
                )
            )
    ids = [n.id for n in resolved.nodes]
    final = next(
        (
            i
            for i, n in enumerate(resolved.nodes)
            if isinstance(n.node, GateNode) and n.node.chain_finalized
        ),
        None,
    )
    for i, added in enumerate(authored.get("add") or []):
        node = added.get("node") if isinstance(added, Mapping) else None
        if not isinstance(node, Mapping):
            continue
        what = _added_problem(node, resolved, ids, final)
        if what:
            parts.append(CustomPart(f"add[{i}].node", what, dict(node)))
    for path, values in (authored.get("overrides") or {}).items():
        what = _override_problem(path, values, before)
        if what:
            parts.append(CustomPart(f"overrides.{path}", what, values))
    return parts


def _added_problem(
    node: Mapping, resolved: ResolvedChain, ids: list[str], final: int | None
) -> str | None:
    if node.get("kind", "exec") != "exec":
        return f"adds {node.get('id')!r}, a node that is not an execution node"
    try:
        revision.AddedNode.model_validate({k: v for k, v in node.items() if k != "kind"})
    except ValidationError:
        return f"adds {node.get('id')!r}, a node with its own content rather than library parts"
    if node.get("id") not in ids or not isinstance(
        resolved.nodes[ids.index(node["id"])].node, ExecNode
    ):
        return f"adds {node.get('id')!r}, which is not an execution node"
    if final is not None and ids.index(node["id"]) > final:
        return f"adds {node['id']!r}, which runs after the final approval gate"
    return None


def _override_problem(path: str, values: object, before: Mapping) -> str | None:
    node = before.get(path.split(PATH_SEPARATOR)[0])
    if node is not None and isinstance(node.node, GateNode):
        return f"overrides {path!r}, a gate"
    try:
        revision.Override.model_validate({**_flat(values), "evidence": "one-off"})
    except (ValidationError, TypeError):
        if isinstance(values, Mapping):
            for key in ("prompt", "command"):
                if key in values:
                    return f"sets its {key} at {path!r}"
        return f"overrides {path!r} beyond model, effort and the operational limits"
    return None
