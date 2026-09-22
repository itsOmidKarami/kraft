"""A node override's `extra_prompt` (Kraft-a7ers): an operator's text for one
node, appended to every agent task that node dispatches and never in place of
the task's own prompt. The model/effort precedence it sits beside is pinned in
tests/executor/test_dispatch.py (`test_node_override_beats_item_override_beats_the_task`)."""

from kraft import store
from kraft.executor import dispatch


def _agent(task_id, prompt):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": prompt}


async def _dispatch_every_task(it):
    for node in it.chain.chain.nodes:
        for step in node.steps:
            for task in step.tasks:
                await dispatch.dispatch_node(
                    it.database, it.run_dirs, task, node, it.row(), it.repo
                )


async def test_extra_prompt_reaches_every_agent_task_of_its_node_only(item_on, fake_agent):
    it = await item_on(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [_agent("a", "Task A's own prompt."), _agent("b", "Task B's own prompt.")],
            },
            {"id": "other", "kind": "exec", "tasks": [_agent("c", "Task C's own prompt.")]},
        ]
    )
    await it.database.write(
        lambda c: store.set_node_overrides(
            c, it.id, {"implementation": {"extra_prompt": "Mind the migration order."}}
        )
    )

    await _dispatch_every_task(it)

    a, b, c = fake_agent.prompts()
    for sent, own in ((a, "Task A's own prompt."), (b, "Task B's own prompt.")):
        assert own in sent
        assert "Mind the migration order." in sent
        assert sent.index(own) < sent.index("Mind the migration order.")
    assert "Task C's own prompt." in c
    assert "Mind the migration order." not in c
