"""`validate_retry_override`: the runtime shape of a policy-bounded retry
override (`retry-overrides-are-policy-bounded`, first half). Task 8b's retry
route is its caller; these pin what it accepts and how it refuses."""

from __future__ import annotations

import pytest

from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, MaterializedChain, ResolvedChain
from kraft.templates.retry import RetryOverrideError, validate_retry_override


def _agent(id: str, **kw) -> dict:
    return {"id": id, "kind": "agent", "harness": "codex", "prompt": "do it", **kw}


def _chain(**maxima) -> MaterializedChain:
    nodes = [
        {
            "id": "build",
            "kind": "exec",
            "policy": {"allowed_tools": ["Read", "Edit", "Bash"], "deny_tools": ["WebFetch"]},
            "steps": [
                {
                    "id": "work",
                    "tasks": [
                        _agent("implement", policy={"allowed_tools": ["Read", "Edit"]}),
                        {"id": "check", "kind": "subprocess", "command": "true"},
                    ],
                }
            ],
            "fix_loop": {"tasks": [_agent("fix")], "max_attempts": 2},
        },
        {"id": "short", "kind": "exec", "tasks": [_agent("only")]},
        {"id": "review", "kind": "gate", "auto_review": _agent("reviewer")},
    ]
    instance = InstancePolicy.from_input(InstancePolicyInput.model_validate({"maxima": maxima}))
    return ResolvedChain.from_chain(Chain.model_validate({"id": "c", "nodes": nodes})).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=instance,
    )


def test_an_empty_override_changes_nothing():
    """The effective policy is the scope's own, unchanged; a scope with none
    has none."""
    chain = _chain()
    result = validate_retry_override(chain, "build.work.implement")
    assert result.task_config == {}
    assert result.policy.allowed_tools == ["Read", "Edit"]
    assert result.chain.to_json() == chain.to_json()
    assert validate_retry_override(chain, "short.main.only").policy is None


def test_a_task_override_narrows_the_task_and_is_written_into_its_scope():
    """The effective override is the scope's whole `policy:` after the retry
    -- what it declared plus what the retry tightened -- written into the
    fork's copy of the chain, so the runtime resolves it like any other."""
    chain = _chain(max_attempts=5)
    result = validate_retry_override(
        chain,
        "build.work.implement",
        task_config={"model": "gpt-big", "effort": "high"},
        policy={"allowed_tools": ["Read"], "deny_tools": ["Bash"]},
    )
    assert result.task_config == {"model": "gpt-big", "effort": "high"}
    assert result.policy.allowed_tools == ["Read"]
    implement = result.chain.policy_at("build.work.implement")
    assert implement.allowed_tools == ("Read",)
    assert implement.deny_tools == ("WebFetch", "Bash")
    task = next(t for t in result.chain.chain.nodes[0].tasks() if t.path == "build.work.implement")
    assert (task.task.model, task.task.effort) == ("gpt-big", "high")
    assert result.chain.task_paths == chain.task_paths
    assert chain.policy_at("build.work.implement").allowed_tools == ("Read", "Edit")


def test_a_node_override_may_move_its_fix_loop_bound_within_the_maximum():
    result = validate_retry_override(_chain(max_attempts=5), "build", policy={"max_attempts": 4})
    assert result.chain.policy_at("build").max_attempts == 4


def test_a_retry_deny_list_adds_to_the_scopes_own_rather_than_replacing_it():
    result = validate_retry_override(_chain(), "build", policy={"deny_tools": ["Bash"]})
    assert result.policy.deny_tools == ["WebFetch", "Bash"]
    assert result.chain.policy_at("build.fix_loop.main.fix").deny_tools == ("WebFetch", "Bash")


@pytest.mark.parametrize(
    ("path", "task_config", "policy", "field"),
    [
        ("build.nope", None, None, "path"),
        (
            "build.work.implement",
            None,
            {"allowed_tools": ["Read", "Edit", "Bash"]},
            "policy.allowed_tools",
        ),
        ("build", None, {"max_attempts": 9}, "policy.max_attempts"),
        ("build.work.implement", None, {"max_attempts": 2}, "policy.max_attempts"),
        ("build.work", None, {"timeout_minutes": 2}, "policy.timeout_minutes"),
        ("build", None, {"allowed_tools": ["Bash"]}, "policy.allowed_tools"),
        ("build.work.implement", None, {"not_a_field": 1}, "policy.not_a_field"),
        ("build.work.implement", {"id": "renamed"}, None, "task_config.id"),
        ("build.work.implement", {"kind": "subprocess"}, None, "task_config.kind"),
        (
            "build.work.implement",
            {"on_failure": {"tasks": [_agent("x")]}},
            None,
            "task_config.on_failure",
        ),
        ("build.work.implement", {"scope": "each_repository"}, None, "task_config.scope"),
        ("build.work.implement", {"policy": {}}, None, "task_config.policy"),
        ("build.work.implement", {"prompt": ""}, None, "task_config.prompt"),
        ("build.work.check", {"model": "x"}, None, "task_config.model"),
        ("build", {"model": "x"}, None, "task_config"),
        ("short.main", None, {"deny_tools": ["Bash"]}, "path"),
    ],
    ids=[
        "unknown-path",
        "widens-the-inherited-allowlist",
        "past-the-attempts-maximum",
        "a-loop-bound-on-a-task",
        "a-loop-bound-on-a-step",
        "a-node-narrowing-that-its-own-task-then-widens",
        "an-unknown-policy-field",
        "renames-the-task",
        "changes-the-task-kind",
        "adds-structure",
        "fans-the-task-out",
        "policy-through-the-config-door",
        "an-invalid-value",
        "a-field-the-task-kind-lacks",
        "task-config-on-a-node",
        "the-shorthand-step-has-no-scope",
    ],
)
def test_an_override_the_task_or_its_policy_bounds_refuse_names_its_field(
    path, task_config, policy, field
):
    """`policy-override-rules-are-field-specific`: every refusal names the one
    field it is about, and the chain is left untouched."""
    chain = _chain(max_attempts=5)
    before = chain.to_json()
    with pytest.raises(RetryOverrideError) as refused:
        validate_retry_override(chain, path, task_config=task_config, policy=policy)
    assert refused.value.field == field, refused.value
    assert chain.to_json() == before


def test_a_harness_change_is_held_to_the_paths_allowed_harnesses():
    chain = _chain(allowed_harnesses=["codex"])
    with pytest.raises(RetryOverrideError) as refused:
        validate_retry_override(chain, "build.work.implement", task_config={"harness": "claude"})
    assert refused.value.field == "task_config.harness"


def test_a_workspace_items_fork_keeps_each_repositorys_policy():
    """Kraft-jc39p: a retry fork re-materializes nothing, so a workspace
    item's per-repository policies travel with it unchanged."""
    from kraft.templates.environment import Workspace

    chain = _chain()
    workspace = Workspace.model_validate(
        {"id": "ws", "root": "ws", "members": {"a": {"repository": "a", "path": "libs/a"}}}
    )
    member = chain.policy.apply_template_override({"deny_tools": ["Bash"]})
    workspace_chain = chain.chain.materialize(
        target=WorkItemTarget.from_selection(workspace, members=["a"]),
        effective_policy=member,
        repository_policies={"a": member},
    )

    fork = validate_retry_override(workspace_chain, "build.work.implement").chain

    assert fork.repository_policies == workspace_chain.repository_policies
    assert fork.to_json() == workspace_chain.to_json()


def test_a_retry_fork_keeps_the_items_base_branch_and_its_own_policy():
    """Kraft-ielvs, Kraft-wehx7: a fork is the item's, base branch
    (Kraft-v9gbi) and item policy layer (Kraft-ab1bh) both -- a retry never
    retargets the merge request nor drops the caps the item was given."""
    chain = _chain()
    on_release = chain.chain.materialize(
        target=WorkItemTarget.for_repository("target", base_branch="release"),
        effective_policy=chain.policy,
    ).with_item_policy({"paths": {"build": {"max_attempts": 2}}})

    fork = validate_retry_override(on_release, "build.work.implement").chain

    assert fork.target.base_branch == "release"
    assert fork.item_policy == on_release.item_policy
    assert fork.policy_for(fork.chain.nodes[0]).max_attempts == 2


def test_a_retry_can_drop_a_grant_but_never_add_one():
    """Grants widen what a task may do, so a retry holds to the item layer's
    rule (Kraft-4in7z): what a task is granted is authored, not filed."""
    none = validate_retry_override(_chain(), "short", policy={"grants": ["git-push"]})
    assert none.chain.policy_at("short.main.only").grants == ()

    node = {
        "id": "n",
        "kind": "exec",
        "policy": {"grants": ["git-push", "git-rebase"]},
        "tasks": [_agent("t")],
    }
    granted = ResolvedChain.from_chain(
        Chain.model_validate({"id": "c", "nodes": [node]})
    ).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )
    dropped = validate_retry_override(granted, "n", policy={"grants": ["git-push", "git-commit"]})
    assert dropped.chain.policy_at("n.main.t").grants == ("git-push",)
