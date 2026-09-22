"""Publication in the chain's own order: nothing readies or merges before the
final gate, the seeded default chain's post-draft order, and a gate before
the draft keeping the work local. Runtime publication across a workspace's
repositories is test_workspace_publication.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft.adapters import forge
from kraft.executor.context import LaunchContext

NO_SETUP = {"setup_command": ""}


def _chain(*nodes):
    from kraft.templates.models import Chain

    return Chain.model_validate({"id": "c", "nodes": list(nodes)})


def _forge(id, target):
    return {"id": id, "kind": "exec", "tasks": [{"id": id, "kind": "forge", "target": target}]}


_FINAL = {"id": "final", "kind": "gate", "message": "m", "chain_finalized": True}


_WORK = {"id": "w", "kind": "subprocess", "command": "true"}


def _placed(where: str, task: dict) -> dict:
    """An execution node running `task` from position `where`: every task
    position the executor can run in an execution node (a gate's
    `auto_review` is an agent task by type, so it cannot publish)."""
    node = {"id": "n", "kind": "exec", "tasks": [_WORK]}
    recovery = {"tasks": [task]}
    match where:
        case "node-task":
            node["tasks"] = [task]
        case "step-task":
            del node["tasks"]
            node["steps"] = [{"id": "s", "tasks": [task]}]
        case "task-on-failure":
            node["tasks"] = [{**_WORK, "on_failure": recovery}]
        case "step-on-failure":
            del node["tasks"]
            node["steps"] = [{"id": "s", "tasks": [_WORK], "on_failure": recovery}]
        case "node-on-failure":
            node["on_failure"] = recovery
        case "fix-loop":
            node["fix_loop"] = recovery
        case "fix-loop-judge":
            node["fix_loop"] = {"tasks": [_WORK], "judge": task}
        case "escalation":
            node["escalation"] = task
        case "on-conflict":
            node["on_base_changed"] = {"restart_from": "n", "on_conflict": recovery}
    return node


_POSITIONS = [
    "node-task",
    "step-task",
    "task-on-failure",
    "step-on-failure",
    "node-on-failure",
    "fix-loop",
    "fix-loop-judge",
    "escalation",
    "on-conflict",
]


@pytest.mark.parametrize("target", ["mr.mark_ready", "mr.merge"])
@pytest.mark.parametrize("where", _POSITIONS)
def test_nothing_readies_or_merges_before_the_final_gate(target, where):
    """`draft-merge-request-enables-external-checks`: a draft may be opened
    before final-gate approval, but a chain that could mark it ready or merge
    it before that approval -- from any task position the executor can run in
    a node ahead of the gate -- is refused when it is loaded, not discovered
    once it has published (Kraft-nwonj)."""
    node = _placed(where, {"id": "t", "kind": "forge", "target": target})
    with pytest.raises(ValueError, match=f"{target}.*before.*'final'"):
        _chain(_forge("draft", "mr.open_draft"), node, _FINAL)
    # After the gate, the same node is the ordinary publication.
    assert _chain(_forge("draft", "mr.open_draft"), _FINAL, node)


def test_the_seeded_default_chain_publishes_in_the_required_order():
    """`default-post-draft-flow-is-ordered` and `final-gate-governs-merge-
    request-readiness`: draft, CI then automated review, the summary, the
    final gate; only then ready, external approval, merge."""
    from kraft.templates.library import TemplateLibrary

    chain = TemplateLibrary.from_yaml_dir(Path(__file__).parents[2] / "templates").resolve_chain(
        "default"
    )
    order = []
    for n in chain.nodes:
        if n.node.kind == "gate":
            order += ["final gate"] if n.node.chain_finalized else []
        else:
            order += [t.task.target for t in n.tasks() if t.task.kind == "forge"]
    assert [str(o) for o in order if o != "mr.sync"] == [
        "mr.open_draft",
        "mr.ci",
        "mr.automated_review",
        "final gate",
        "mr.mark_ready",
        "mr.external_approval",
        "mr.merge",
        "mr.post_merge_ci",
    ]
    ids = [n.id for n in chain.nodes]
    assert ids.index("work_item_summary") < ids.index("chain_review") < ids.index("mark_ready")


async def test_a_pre_draft_gate_keeps_the_work_local(item_on, database, run_dirs, monkeypatch):
    """`optional-pre-draft-gate-keeps-work-local`: until a gate before the
    draft is approved, the walk stops there and nothing reaches the forge."""
    from kraft import executor

    it = await item_on(
        [
            {"id": "local_review", "kind": "gate", "message": "Approve the draft."},
            _forge("draft", "mr.open_draft"),
        ]
    )
    fake = forge.FakeForge()
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    await executor.run(
        database,
        run_dirs,
        work_item_id=it.id,
        policy=None,
        launch=LaunchContext(repo_entry={**NO_SETUP, "forge": "github"}, steering_dir=None),
    )

    assert it.row()["current_node_id"] == "local_review"
    assert fake.opened == {} and fake.pushed == []
