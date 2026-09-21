"""`AgentTask.inputs`: what Kraft hands an agent task beyond its prompt, only
when the task declares it -- today the review package (Ruling 47), whose first
consumer is the seeded fix-loop judge."""

import pytest
from support.harness import v1_named_chain

from kraft import executor
from kraft.templates.models import AgentInput

NO_SETUP = {"setup_command": ""}


def _node(**agent_fields):
    agent = {"id": "judge", "kind": "agent", "harness": "fake", "prompt": "Judge it."}
    return [{"id": "review", "kind": "exec", "tasks": [{**agent, **agent_fields}]}]


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
async def test_the_review_package_reaches_only_a_task_that_declares_it(
    item_on, fake_agent, run_dirs, declared
):
    """`review-package-is-delivered-to-a-task-that-declares-it`: the package
    is written for the session and named in the launch's context, and a task
    that does not ask for it gets neither."""
    it = await item_on(_node(**({"inputs": ["review_package"]} if declared else {})))

    assert (
        await executor.run_once(
            it.database,
            it.run_dirs,
            work_item_id=it.id,
            registry=None,
            launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
        )
        == "completed"
    )
    [argv] = fake_agent.argv()
    assert ("$KRAFT_REVIEW_PACKAGE" in " ".join(argv)) is declared
    [session] = it.sessions("review")
    assert (run_dirs.results / f"{session['id']}.review.md").is_file() is declared


def test_the_seeded_fix_loop_judge_declares_the_package_and_its_method(fake_agent):
    """Ruling 47 and Ruling 73: the seeded `strict_judge` is the package's
    first consumer, and states its three-decision contract through
    `kraft:fix-loop-judge` rather than a one-line prompt."""
    chain = v1_named_chain(fake_agent.templates, "default")
    nodes = {n.id: n for n in chain.nodes}
    for node_id in ("implementation", "merge_request_feedback"):
        judge = nodes[node_id].judge.task
        assert judge.inputs == [AgentInput.REVIEW_PACKAGE]
        assert judge.skill == "kraft:fix-loop-judge"
    [repair] = nodes["merge_request_feedback"].on_failure[0].tasks
    assert repair.task.skill == "kraft:mr-checks-repair"
    # The fix loops' own repairs fix code too, so the metadata-only method is
    # not theirs (see the task-7a report).
    for node_id in ("implementation", "merge_request_feedback"):
        agents = [
            t.task for s in nodes[node_id].fix_loop for t in s.tasks if t.task.kind == "agent"
        ]
        assert agents and all(a.skill is None for a in agents)
