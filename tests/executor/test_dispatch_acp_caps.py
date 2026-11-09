"""An instance dollar cap refuses an ACP launch whose entry cannot be counted against it, on
every path that launches an agent: the walk (`dispatch._launch_agent`), and the three that are
not the walk's -- an escalation turn, a gate review and a review reply -- which get the instance
Budget from `LaunchContext.budget`. The item's own dollar cap is folded in by `_run_agent_task`
itself, so no caller can miss it."""

from pathlib import Path

import pytest
from skills.test_gate_review import _chain2, _seed_at_gate
from support.harness import v1_node, v1_task
from test_review_reply import GATE, _published_unanswered_thread
from test_review_reply import _item as _reply_item

from kraft import escalate, executor, gate_review, harness, review_reply, store
from kraft import policy as _policy
from kraft.executor import dispatch

#: An entry that reports nothing: no dollar cap can be counted against it.
NOTHING = {
    "id": "fake",
    "kind": "acp",
    "command": ["fake-acp-agent"],  # no `reports`
    "options": {"model": {"config": "model"}, "effort": {"config": "effort"}},
}
WORK = [v1_node("implementation", tasks=[v1_task("implement", kind="agent", harness="fake")])]


@pytest.fixture
def acp_reports_nothing(monkeypatch, fake_agent):
    """Profile `fake` is an ACP entry that reports nothing; `fake_agent` records any launch."""
    real = harness.load(None)
    acp = harness.Harness.from_mapping(NOTHING, where="t")
    monkeypatch.setattr(
        harness,
        "load",
        lambda *_: harness.HarnessSet(valid={**real.valid, "fake": acp}, invalid=real.invalid),
    )
    return fake_agent


def _launch(**kw):
    return executor.LaunchContext(repo_entry=None, skills_dir=None, **kw)


async def _own_cap(database, wid, usd):
    """The item's own dollar cap, which no caller passes: `_run_agent_task` reads it."""
    if usd is not None:
        await database.write(lambda c: store.set_budget(c, wid, usd))


async def _walk(item_on, database, run_dirs, launch, own):
    it = await item_on(WORK)
    await _own_cap(database, it.id, own)
    node = it.chain.chain.nodes[0]
    await dispatch.dispatch_node(
        database, run_dirs, node.steps[0].tasks[0], node, it.row(), it.repo, budget=launch.budget
    )


async def _escalation(item_on, database, run_dirs, launch, own):
    it = await item_on(
        WORK, "implementation", status="needs_human", policy_override={"escalation_harness": "fake"}
    )
    await _own_cap(database, it.id, own)
    await escalate.dispatch(database, run_dirs, work_item_id=it.id, message="help", launch=launch)


async def _gate_review(item_on, database, run_dirs, launch, own):
    chain = _chain2(run_dirs, harness="fake")
    await _seed_at_gate(database, run_dirs, "w1", chain, auto_gate=True)
    await _own_cap(database, "w1", own)
    await gate_review.review(
        database,
        run_dirs,
        work_item_id="w1",
        gate="human_review_approval",
        node=chain.chain.nodes[1],
        launch=launch,
    )


async def _review_reply(item_on, database, run_dirs, launch, own):
    it = await _reply_item(item_on, run_dirs)
    await _own_cap(database, it.id, own)
    await _published_unanswered_thread(it)
    await review_reply.run(
        database, run_dirs, work_item_id=it.id, gate=GATE, nodes=it.chain.chain.nodes, launch=launch
    )


def _logs(database) -> str:
    rows = database.read(lambda c: c.execute("SELECT log_path FROM worker_sessions").fetchall())
    return "".join(Path(r["log_path"]).read_text() for r in rows if Path(r["log_path"]).exists())


@pytest.mark.parametrize(
    ("budget", "own"),
    [(_policy.Budget(work_item_usd=5), None), (_policy.NO_BUDGET, 5)],
    ids=["instance-cap", "the-items-own-cap"],
)
@pytest.mark.parametrize(
    "driver", [_walk, _escalation, _gate_review, _review_reply], ids=lambda d: d.__name__[1:]
)
async def test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing(
    item_on, database, run_dirs, acp_reports_nothing, driver, budget, own, caplog
):
    """Dropping `budget=` from the caller's `run_agent_task` call (the default `NO_BUDGET` sets
    no dollar cap, so no other test notices) makes that launch start. Where the refusal shows
    differs: a session's log, or (a review reply, which has no session of its own) the server's."""
    await driver(item_on, database, run_dirs, _launch(budget=budget), own)

    assert "dollar cap" in _logs(database) + caplog.text
    assert acp_reports_nothing.argv() == []
