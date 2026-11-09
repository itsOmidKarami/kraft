"""The walk hands the instance Budget to an ACP launch's cap check (`dispatch._launch_agent`)."""

from pathlib import Path

from support.harness import v1_node, v1_task

from kraft import harness
from kraft import policy as _policy
from kraft.executor import dispatch


async def test_an_instance_dollar_cap_refuses_an_acp_launch_whose_entry_reports_nothing(
    item_on, fake_agent, monkeypatch
):
    """Removing `budget=budget` from `_launch_agent`'s `run_agent_task` call (or from its call in
    `dispatch_node`) makes this launch start: the default `NO_BUDGET` sets no dollar cap, so no
    other test notices."""
    real = harness.load(None)
    acp = harness.Harness.from_mapping(
        {
            "id": "fake",
            "kind": "acp",
            "command": ["fake-acp-agent"],  # no `reports`
            "options": {"model": {"config": "model"}, "effort": {"config": "effort"}},
        },
        where="t",
    )
    monkeypatch.setattr(
        dispatch._harness,
        "load",
        lambda *_: harness.HarnessSet(valid={**real.valid, "fake": acp}, invalid=real.invalid),
    )
    it = await item_on(
        [v1_node("implementation", tasks=[v1_task("implement", kind="agent", harness="fake")])]
    )
    node = it.chain.chain.nodes[0]

    await dispatch.dispatch_node(
        it.database,
        it.run_dirs,
        node.steps[0].tasks[0],
        node,
        it.row(),
        it.repo,
        budget=_policy.Budget(work_item_usd=5),
    )

    [session] = it.sessions()
    assert session["status"] == "config_error"  # `LaunchRefused` is caught in `_launch_agent`
    assert "dollar cap" in Path(session["log_path"]).read_text()
    assert fake_agent.argv() == []
