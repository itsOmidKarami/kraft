"""Task 5: every agent dispatch carries the item's unanswered review threads
(review threads anywhere §1). Split out of test_dispatch.py to keep it under
the line budget."""

from __future__ import annotations

import pytest
from support.harness import entry_of, v1_chain, v1_walk

from kraft import store
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext


async def _publish(database, wid="w1"):
    tid = await database.write(
        lambda c: store.create_thread(
            c, wid=wid, gate=None, anchor_sha="h", body="evict LRU", label="must_fix"
        )
    )
    await database.write(
        lambda c: store.submit_review(
            c, wid=wid, gate=None, outcome="comment", summary=None, head_sha="h", base_sha="h"
        )
    )
    return tid


def _walk(tmp_path, repo, chain, **kwargs):
    """File `chain` (node list or `ResolvedChain`) on `repo` and walk it once."""
    return v1_walk(tmp_path, v1_chain(chain, repo=repo), repo=repo, **kwargs)


async def test_the_implementer_prompt_carries_a_mid_run_thread(tmp_path, repo, fake_agent):
    """review threads anywhere §1: every agent dispatch carries the item's
    unanswered review threads, not only a rejection's own note. Seeds a
    published thread against the item's id right after it is filed, before
    the walk's implementer dispatch reads it."""
    seeded = {}

    async def seed(database):
        seeded["tid"] = await _publish(database)

    status, *_ = await _walk(tmp_path, repo, fake_agent.quick_task, after_item=seed)

    assert status == "completed"
    [prompt] = fake_agent.prompts()
    assert f"[{seeded['tid']}]" in prompt


@pytest.mark.parametrize(
    ("network", "reply"),
    [({"runtime": {"allow": ["x.io"]}}, True), (None, False)],
    ids=["network", "no-network"],
)
async def test_a_sandboxed_worker_is_told_to_run_kraft_only_with_a_route_to_it(
    item_on, fake_agent, monkeypatch, network, reply
):
    """The dispatch hands the item's sandbox to the notes: without `network:`
    the worker has no `kraft` to reply or report progress with."""
    seen = {}

    async def fake_run_agent_task(db_, run_dirs_, **kw):
        seen["instruction"] = kw["task_instruction"]
        return "done"

    monkeypatch.setattr(dispatch._agent, "run_agent_task", fake_run_agent_task)
    monkeypatch.setattr(dispatch.prompts._progress, "tasks_for", lambda row, wt: ["a", "b"])
    task = {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "do it"}
    it = await item_on([{"id": "implementation", "kind": "exec", "tasks": [task]}])
    await _publish(it.database, it.id)
    sandbox = {"kind": "docker", "image": "x", **({"network": network} if network else {})}
    node = it.chain.chain.nodes[0]
    await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        it.repo,
        launch=LaunchContext(repo_entry=entry_of({"sandbox": sandbox})),
    )

    assert "evict LRU" in seen["instruction"]
    assert ("kraft item reply" in seen["instruction"]) is reply
    assert ("kraft item progress" in seen["instruction"]) is reply
