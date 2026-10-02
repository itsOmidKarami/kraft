"""`dispatch.dispatch_node` and a stored override: a model 1.4 stored as any
text is held to the model-id rule at launch. A sibling of test_dispatch.py."""

import json
from pathlib import Path

import pytest
from support.harness import entry_of

from kraft import executor, store
from kraft.executor import dispatch

#: A real item id is 32 hex characters, which a stop reason must still carry whole.
WID = "b45e5f7996" + "0" * 22


def _exec(node_id, *tasks):
    return {"id": node_id, "kind": "exec", "tasks": list(tasks)}


def _agent():
    return {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "Do it."}


async def _dispatch_each_node(it):
    for node in it.chain.chain.nodes:
        await dispatch.dispatch_node(
            it.database, it.run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo
        )


@pytest.mark.parametrize(
    ("item", "node", "named", "clear"),
    [
        ({"model": "sonnet 4"}, {}, "the item's stored model", "set-overrides w1 --clear"),
        (
            {"escalate_model": "opus"},
            {"escalate_model": "opus; rm"},
            "node implementation's stored escalate_model",
            "set-node-override w1 --node implementation --clear",
        ),
    ],
    ids=["item-wide", "node"],
)
async def test_a_model_stored_before_model_ids_were_checked_stops_the_task(
    item_on, fake_agent, item, node, named, clear
):
    """1.4 stored any text as a model override. The write doors now refuse
    text that is no model id; one already stored is held to the same rule at
    launch, so it stops the task with how to clear it and never reaches the
    agent's command line."""
    it = await item_on([_exec("implementation", _agent())])
    await it.database.write(lambda c: store.set_agent_overrides(c, it.id, json.dumps(item)))
    await it.database.write(lambda c: store.set_node_overrides(c, it.id, {"implementation": node}))

    await _dispatch_each_node(it)

    [session] = it.sessions()
    assert session["status"] == "config_error"
    log = Path(session["log_path"]).read_text()
    assert named in log and "is not a model id" in log and f"kraft item {clear}" in log
    assert fake_agent.argv() == []


@pytest.mark.parametrize(
    ("item", "node", "clear"),
    [
        ({"model": "sonnet 4"}, {}, f"kraft item set-overrides {WID} --clear"),
        (
            {"escalate_model": "opus " + "x" * 300},
            {},
            f"kraft item set-overrides {WID} --clear",
        ),
        (
            {},
            {"model": "claude opus " + "y" * 300},
            f"kraft item set-node-override {WID} --node implementation --clear",
        ),
    ],
    ids=["item-model", "item-escalate_model", "node-model"],
)
async def test_the_stop_reason_names_the_whole_command_that_clears_the_override(
    item_on, fake_agent, item, node, clear
):
    """The board cuts a stop reason at a few hundred characters. With a real
    32-character id, and a long stored text, the reason must still carry
    the whole command that clears the override."""
    it = await item_on([_exec("implementation", _agent())], wid=WID)
    await it.database.write(lambda c: store.set_agent_overrides(c, it.id, json.dumps(item)))
    await it.database.write(lambda c: store.set_node_overrides(c, it.id, {"implementation": node}))

    await executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=entry_of({"setup_command": ""})),
    )

    assert it.status() == "needs_human"
    reason = it.events("work_item_needs_human")[-1]["payload"]["reason"]
    assert f"`{clear}`" in reason, reason
    assert fake_agent.argv() == []
