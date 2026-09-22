"""Per-scope spend caps (Ruling 195, `caps.budget_breach`): a `token_budget` or
`budget_usd` on a scope caps what the launches inside that scope have spent --
not the whole item's -- and a launch is refused when its own scope or any
scope around it has reached its cap. A launch whose cost was never reported is
unknown spend, never free. The launch door itself is
tests/executor/test_policy_enforcement.py."""

from __future__ import annotations

import pytest

from kraft import events
from kraft import policy as _policy
from kraft.executor import stops
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, ResolvedChain

TASK = "build.run.impl"


def _agent(task_id: str, **fields) -> dict:
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _chain(level: str | None = None, cap: dict | None = None):
    """`build` -> step `run` -> tasks `impl` and `other`, and a second node
    `ship`; `level` (task, step, node or chain) sets `cap`."""

    def own(at):
        return {"policy": cap} if level == at else {}

    nodes = [
        {
            "id": "build",
            "kind": "exec",
            **own("node"),
            "steps": [
                {
                    "id": "run",
                    **own("step"),
                    "tasks": [_agent("impl", **own("task")), _agent("other")],
                }
            ],
        },
        {"id": "ship", "kind": "exec", "tasks": [_agent("go")]},
    ]
    return ResolvedChain.from_chain(
        Chain.model_validate({"id": "c", **own("chain"), "nodes": nodes})
    ).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )


async def _spent(it, path: str, *, tokens: int = 0, usd: float | None = None, status="done"):
    sid = f"s-{path}-{tokens}-{usd}-{status}"
    await it.session(sid, path, status if status != "running" else None)
    await it.database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET tokens_in = ?, tokens_out = 0, cost_usd = ?, "
            "status = ? WHERE id = ?",
            (tokens, usd, status, sid),
        )
    )


def _breach(it, path: str = TASK):
    return stops.budget_breach(it.database, it.id, _policy.NO_BUDGET, row=it.row(), path=path)


async def _item(item_on, level: str, cap: dict):
    if level == "item":
        return await item_on(_chain(), policy_override=cap)
    return await item_on(_chain(level, cap))


#: For each level, a launch inside its scope other than `impl`'s own, and one
#: outside it: only the first counts against the cap.
_INSIDE = {
    "task": (TASK, "build.run.other"),
    "step": ("build.run.other", "ship.main.go"),
    "node": ("build.run.other", "ship.main.go"),
    "chain": ("ship.main.go", None),
    "item": ("ship.main.go", None),
}
_NAMED = {"task": TASK, "step": "build.run", "node": "build", "chain": "", "item": ""}


@pytest.mark.parametrize("level", list(_INSIDE))
async def test_a_token_budget_caps_its_own_scopes_spend(item_on, level):
    inside, outside = _INSIDE[level]
    it = await _item(item_on, level, {"token_budget": 100})
    if outside is not None:
        await _spent(it, outside, tokens=500)
        assert _breach(it) is None, "another scope's spend counted against this one's cap"

    await _spent(it, inside, tokens=100)

    breach = _breach(it)
    assert breach == {
        "scope": "tokens",
        "path": _NAMED[level],
        "spent_tokens": 100,
        "cap_tokens": 100,
    }
    where = f"`{_NAMED[level]}`" if _NAMED[level] else "the work item"
    assert f"100 tokens spent in {where}, cap 100 tokens" in stops.budget_reason(breach)


@pytest.mark.parametrize("level", list(_INSIDE))
async def test_a_budget_usd_caps_its_own_scopes_spend(item_on, level):
    inside, outside = _INSIDE[level]
    it = await _item(item_on, level, {"budget_usd": 2})
    if outside is not None:
        await _spent(it, outside, tokens=10, usd=9.0)
        assert _breach(it) is None
    await _spent(it, inside, tokens=10, usd=1.5)
    assert _breach(it) is None

    await _spent(it, inside, tokens=10, usd=0.5)

    breach = _breach(it)
    assert (breach["scope"], breach["path"], breach["spent_usd"]) == ("usd", _NAMED[level], 2.0)
    assert "budget_usd reached: $2.00 spent" in stops.budget_reason(breach)


async def test_an_enclosing_scopes_cap_refuses_a_launch_its_own_would_allow(item_on):
    it = await item_on(_chain("node", {"token_budget": 100}))
    await _spent(it, "build.run.other", tokens=100)

    assert _breach(it)["path"] == "build"


async def test_unknown_spend_is_never_counted_as_free(item_on):
    """A finished launch that spent tokens and reported no cost: the scope
    cannot be shown to be under its dollar cap, so it stops for a human and
    says why. One still running has not reported yet, and is not unknown."""
    it = await item_on(_chain("node", {"budget_usd": 5}))
    await _spent(it, "build.run.other", tokens=10, usd=None, status="running")
    assert _breach(it) is None

    await _spent(it, "build.run.other", tokens=10, usd=None)

    breach = _breach(it)
    assert breach["unknown_launches"] == 1
    assert "unknown spend is never counted as free" in stops.budget_reason(breach)


async def test_the_instance_budget_stays_the_outer_ceiling(item_on):
    it = await item_on(_chain("node", {"budget_usd": 50}))
    await _spent(it, "ship.main.go", tokens=10, usd=12.0)

    breach = stops.budget_breach(
        it.database,
        it.id,
        _policy.Budget(work_item_usd=10),
        row=it.row(),
        path=TASK,
    )

    assert breach["scope"] == "work_item"


async def test_a_scope_budget_stop_names_its_scope(item_on):
    it = await item_on(_chain("step", {"token_budget": 10}), "build")
    await _spent(it, "build.run.other", tokens=10)
    breach = _breach(it)
    await it.database.write(
        lambda c: events.append(c, it.id, "scope_budget_reached", {**breach, "task": TASK})
    )

    await stops.stop_for_budget(it.database, it.id, it.chain.chain.nodes[0], _policy.NO_BUDGET)

    (stopped,) = it.events("work_item_needs_human")
    assert "10 tokens spent in `build.run`" in stopped["payload"]["reason"]


async def test_an_escalation_turns_spend_counts_toward_its_nodes_budget(item_on):
    """Kraft-h8n21: an escalation session is written under `escalation`, not
    a task path; its spend is its node's."""
    it = await item_on(_chain("node", {"token_budget": 100}), "build")
    await _spent(it, "escalation", tokens=100)

    assert _breach(it)["path"] == "build"


async def test_a_default_budget_binds_a_tasks_own_spend_not_an_enclosing_scopes(item_on):
    """Ruling 198: a `defaults:` budget binds each task's own launches where
    nothing more specific is set; it is not the whole item's ceiling."""
    from kraft.templates.models import Chain, ResolvedChain

    nodes = [
        {"id": "build", "kind": "exec", "tasks": [_agent("impl"), _agent("other")]},
    ]
    chain = ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(
            InstancePolicyInput.model_validate({"defaults": {"token_budget": 100}})
        ),
    )
    it = await item_on(chain)
    await _spent(it, "build.main.other", tokens=500)
    assert _breach(it, "build.main.impl") is None

    await _spent(it, "build.main.impl", tokens=100)

    assert _breach(it, "build.main.impl")["path"] == "build.main.impl"
