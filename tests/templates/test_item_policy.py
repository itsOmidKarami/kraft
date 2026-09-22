"""A work item's own policy override (`MaterializedChain.with_item_policy`,
Kraft-ab1bh): the work-item layer, item-wide or addressed to one node, step or
task by canonical path. It is validated by the same layering every other scope
goes through, and it binds only the item it was set on."""

from __future__ import annotations

import pytest

from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError, WorkItemPolicy
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, MaterializedChain, ResolvedChain
from kraft.templates.retry import RetryOverrideError, validate_retry_override

_WAIT = {"timeout": "90m", "polling": {"initial_interval": "30s", "max_interval": "5m"}}


def _chain(**maxima) -> MaterializedChain:
    nodes = [
        {
            "id": "verification",
            "kind": "exec",
            "steps": [
                {"id": "check", "tasks": [{"id": "test", "kind": "subprocess", "command": "t"}]}
            ],
            "fix_loop": {
                "tasks": [{"id": "fix", "kind": "subprocess", "command": "f"}],
                "max_attempts": 2,
            },
        },
        {
            "id": "feedback",
            "kind": "exec",
            "policy": {"wait_timeout_minutes": 60},
            "tasks": [
                {"id": "ci", "kind": "forge", "target": "mr.ci", "wait": _WAIT},
                {
                    "id": "approval",
                    "kind": "forge",
                    "target": "mr.external_approval",
                    "policy": {"allowed_tools": ["Read"]},
                },
            ],
        },
        {"id": "review", "kind": "gate"},
    ]
    instance = InstancePolicy.from_input(
        InstancePolicyInput.model_validate(
            {"maxima": {"allowed_tools": ["Read", "Edit", "Bash"], **maxima}}
        )
    )
    return ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"), effective_policy=instance
    )


def _task(chain: MaterializedChain, path: str):
    return next(t for n in chain.chain.nodes for t in n.tasks() if t.path == path)


def test_an_item_override_binds_the_scopes_it_addresses_and_touches_nothing_stored():
    """Item-wide fields reach every scope; a path's reach that path and
    everything under it, narrowest last. An operational value wins over the
    one the chain authored -- the node's own 60-minute wait policy here
    (Ruling 188). The snapshot itself is untouched: the override is a layer
    on it, not an edit of it."""
    chain = _chain()
    item = chain.with_item_policy(
        {
            "allowed_tools": ["Read", "Bash"],
            "paths": {
                "feedback": {"wait_timeout_minutes": 120},
                "feedback.main.ci": {"wait_timeout_minutes": 180},
                "verification": {"max_attempts": 5, "timeout_minutes": 90},
            },
        }
    )

    ci, approval = _task(item, "feedback.main.ci"), _task(item, "feedback.main.approval")
    assert ci.task.wait_bounds(item.policy_for(ci)).timeout.total_seconds() == 180 * 60
    assert approval.task.wait_bounds(item.policy_for(approval)).timeout.total_seconds() == 120 * 60
    verification = item.chain.nodes[0]
    assert (
        item.policy_for(verification).max_attempts,
        item.policy_for(verification).timeout_minutes,
    ) == (5, 90)
    assert item.policy_for(_task(item, "verification.check.test")).allowed_tools == ("Read", "Bash")
    assert item.to_json() == chain.to_json()
    # Without the layer, the chain's own value: its node policy's 60 minutes.
    assert (
        _task(chain, "feedback.main.ci")
        .task.wait_bounds(chain.policy_for(_task(chain, "feedback.main.ci")))
        .timeout.total_seconds()
        == 60 * 60
    )


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"max_attempts": 9}, "policy.max_attempts"),
        (
            {"paths": {"verification": {"timeout_minutes": 999}}},
            "policy.paths.verification.timeout_minutes",
        ),
        (
            {"paths": {"feedback.main.ci": {"wait_timeout_minutes": 999}}},
            "policy.paths.feedback.main.ci.wait_timeout_minutes",
        ),
    ],
    ids=["attempts-over-maximum", "node-timeout-over-maximum", "wait-timeout-over-maximum"],
)
def test_an_override_past_its_bounds_is_refused_naming_the_field(override, field):
    """Operational values move only within the administrator maxima. The
    refusal names the one field, as data."""
    chain = _chain(max_attempts=5, timeout_minutes=120, wait_timeout_minutes=600, token_budget=1000)

    with pytest.raises(PolicyError) as refused:
        chain.with_item_policy(override)

    assert refused.value.field == field
    assert str(refused.value).startswith(f"{field}: ")


@pytest.mark.parametrize(
    ("override", "path", "expected"),
    [
        ({"allowed_tools": ["Read", "WebFetch"]}, "verification.check.test", ("Read",)),
        ({"allowed_tools": ["Read", "Edit"]}, "feedback.main.approval", ("Read",)),
        ({"paths": {"feedback": {"token_budget": 10**9}}}, "feedback.main.ci", 1000),
        ({"paths": {"feedback": {"token_budget": 10}}}, "feedback.main.ci", 10),
    ],
    ids=[
        "allowlist-wider-than-the-ceiling-intersects",
        "allowlist-wider-than-a-narrower-task-intersects",
        "budget-above-the-ceiling-takes-the-minimum",
        "budget-below-it-binds",
    ],
)
def test_an_items_safety_value_only_tightens_whatever_it_lands_on(override, path, expected):
    """Ruling 188: an item's safety values combine in no order -- an allowlist
    intersects, a budget takes the minimum -- so one is never refused because
    a ceiling or a narrower chain scope already narrowed the field. It can
    only ever tighten."""
    chain = _chain(token_budget=1000).with_item_policy(override)

    resolved = chain.policy_for(_task(chain, path))

    field = "token_budget" if isinstance(expected, int) else "allowed_tools"
    assert getattr(resolved, field) == expected


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"paths": {"verification": {"kind": "gate"}}}, "policy.paths.verification.kind"),
        ({"paths": {"verification": {"steps": []}}}, "policy.paths.verification.steps"),
        ({"fix_loop": {"max_attempts": 1}}, "policy.fix_loop"),
        (
            {"paths": {"verification.check.nope": {"max_attempts": 1}}},
            "policy.paths.verification.check.nope",
        ),
        (
            {"paths": {"verification.fix_loop": {"max_attempts": 1}}},
            "policy.paths.verification.fix_loop",
        ),
        (
            {"paths": {"verification.check.test": {"max_attempts": 3}}},
            "policy.paths.verification.check.test.max_attempts",
        ),
        ({"paths": {"review": {"timeout_minutes": 3}}}, "policy.paths.review.timeout_minutes"),
    ],
    ids=[
        "changes-a-node-kind",
        "adds-steps",
        "names-a-structural-key-item-wide",
        "names-no-path-of-the-chain",
        "addresses-a-fix-loop-not-its-node",
        "loop-bound-on-a-task",
        "loop-bound-on-a-gate",
    ],
)
def test_an_override_cannot_change_structure(override, field):
    """A policy override sets policy and nothing else: no key reshapes the
    chain, and a path must name one of its own nodes, steps or tasks. A fix
    loop's bounds belong to an execution node, as at every other layer."""
    with pytest.raises(PolicyError) as refused:
        _chain().with_item_policy(override)

    assert refused.value.field == field


def test_a_retry_is_bounded_by_the_items_own_layer():
    """The item layer is part of the bounds a retry override is held to: a
    retry cannot hand a task back a tool the item took away."""
    item = _chain().with_item_policy({"allowed_tools": ["Read"]})

    with pytest.raises(RetryOverrideError) as refused:
        validate_retry_override(item, "verification.check.test", policy={"allowed_tools": ["Bash"]})

    assert refused.value.field == "policy.allowed_tools"
    assert validate_retry_override(item, "verification").chain.item_policy == WorkItemPolicy(
        allowed_tools=["Read"]
    )


def test_a_retry_may_narrow_a_task_below_the_items_allowlist():
    """Ruling 188: the item's allowlist meets whatever the task resolves to,
    so a retry that narrows the task further is a tightening like any other,
    and the fork's task runs under the narrower list."""
    item = _chain().with_item_policy({"allowed_tools": ["Read", "Edit"]})

    fork = validate_retry_override(
        item, "verification.check.test", policy={"allowed_tools": ["Read"]}
    ).chain

    assert fork.policy_for(_task(fork, "verification.check.test")).allowed_tools == ("Read",)


def test_a_retry_that_would_unlock_a_sandbox_the_item_set_below_it_is_refused():
    """A sandbox locks at every layer. The item's is set on one task, so a
    retry of the enclosing node that names another sandbox passes the node's
    own bounds -- and is refused on the task under it, not forked into a
    task that cannot resolve a policy when it launches."""
    item = _chain().with_item_policy(
        {"paths": {"verification.check.test": {"sandbox": {"kind": "docker", "image": "a:1"}}}}
    )

    with pytest.raises(RetryOverrideError) as refused:
        validate_retry_override(
            item, "verification", policy={"sandbox": {"kind": "docker", "image": "b:1"}}
        )

    assert refused.value.field == "policy.sandbox"
