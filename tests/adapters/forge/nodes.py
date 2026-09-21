"""V1 chain-node builders shared by more than one forge test file. Plain
helpers, not fixtures -- pytest advises against importing from conftest, so
they live here instead, beside outputs.py."""

from __future__ import annotations

from support.harness import v1_resolved

#: A route value meaning "this subcommand exits 1".
FAIL = None


def forge_node(node_id: str, target: str, **task) -> dict:
    """A one-task V1 exec node running forge `target`."""
    return {
        "id": node_id,
        "kind": "exec",
        "tasks": [{"id": node_id, "kind": "forge", "target": target, **task}],
    }


def back_half(*extra: dict, **ci_task) -> object:
    """open_mr -> mr_checks -> merge (plus `extra` nodes after it), the forge
    half of a chain. `ci_task` goes on the mr_checks task (`wait=...`)."""
    return v1_resolved(
        [
            forge_node("open_mr", "mr.open_draft"),
            forge_node("mr_checks", "mr.ci", **ci_task),
            forge_node("merge", "mr.merge"),
            *extra,
        ]
    )
