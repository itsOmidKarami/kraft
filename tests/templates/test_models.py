"""Typed V1 template schema: task kinds, execution shapes, gates, identifiers,
and the canonical execution paths a resolved chain assigns."""

import pytest
from pydantic import ValidationError

from kraft import harness as harness_mod
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates import models as tm
from kraft.templates.environment import (
    HarnessProfile,
    HarnessProfileInput,
    Repository,
    WorkItemTarget,
)


def agent(id: str = "author", **kw) -> dict:
    return {"id": id, "kind": "agent", "harness": "codex_default", "prompt": "do it", **kw}


def exec_node(id: str = "spec", **kw) -> dict:
    return {"id": id, "kind": "exec", **kw}


# ── task kinds are discriminated (task-kinds-are-discriminated) ──


def test_task_kind_selects_the_concrete_task_model():
    step = tm.Step.model_validate(
        {
            "id": "mixed",
            "tasks": [
                agent("a", produces="spec", skill="kraft:spec"),
                {"id": "b", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"},
                {"id": "c", "kind": "subprocess", "command": "just test"},
                {"id": "d", "kind": "forge", "target": "mr.open_draft"},
            ],
        }
    )
    assert [type(t) for t in step.tasks] == [
        tm.AgentTask,
        tm.BuiltinTask,
        tm.SubprocessTask,
        tm.ForgeTask,
    ]
    assert step.tasks[0].kind is tm.TaskKind.AGENT
    assert step.tasks[1].ref is tm.BuiltinAction.VERIFY_CHANGED_TEST_SCOPES
    assert step.tasks[3].target is tm.ForgeAction.MR_OPEN_DRAFT


def test_task_without_a_kind_is_rejected():
    with pytest.raises(ValidationError, match="discriminator 'kind'"):
        tm.Step.model_validate({"id": "s", "tasks": [{"id": "a", "prompt": "do it"}]})


def test_unknown_task_kind_is_rejected():
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        tm.Step.model_validate({"id": "s", "tasks": [{"id": "a", "kind": "wizard"}]})


def test_an_agent_task_field_does_not_leak_onto_another_kind():
    with pytest.raises(ValidationError, match="prompt"):
        tm.Step.model_validate(
            {
                "id": "s",
                "tasks": [{"id": "a", "kind": "forge", "target": "mr.merge", "prompt": "x"}],
            }
        )


# ── built-in refs name code-owned actions
# (builtin-task-references-code-owned-actions) ──


def test_builtin_task_accepts_a_code_owned_ref():
    task = tm.BuiltinTask.model_validate(
        {
            "id": "test_changed_scopes",
            "kind": "builtin",
            "ref": "kraft.verify_changed_test_scopes",
            "scope": "each_repository",
            "execution": "sequential",
        }
    )
    assert task.ref is tm.BuiltinAction.VERIFY_CHANGED_TEST_SCOPES
    assert task.scope is tm.TaskScope.EACH_REPOSITORY
    assert task.execution is tm.ExecutionMode.SEQUENTIAL


def test_builtin_task_rejects_an_action_kraft_does_not_support():
    with pytest.raises(ValidationError, match="ref"):
        tm.BuiltinTask.model_validate(
            {"id": "x", "kind": "builtin", "ref": "kraft.delete_the_repository"}
        )


def test_builtin_task_requires_a_ref():
    with pytest.raises(ValidationError, match="ref"):
        tm.BuiltinTask.model_validate({"id": "x", "kind": "builtin"})


def test_builtin_task_runs_scopes_sequentially_unless_asked():
    task = tm.BuiltinTask.model_validate(
        {"id": "x", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}
    )
    assert task.execution is tm.ExecutionMode.SEQUENTIAL
    assert task.scope is tm.TaskScope.ONCE


# ── durations are positive and a polling window is coherent
# (external-waits-use-a-shared-due-scheduler, external-wait-timeout-needs-human) ──


def test_duration_rejects_zero():
    with pytest.raises(ValidationError, match="positive duration, not zero"):
        tm.WaitPolicy.model_validate({"timeout": "0s"})


def test_duration_accepts_a_positive_amount():
    assert tm.WaitPolicy.model_validate({"timeout": "30s"}).timeout.total_seconds() == 30


def test_polling_policy_rejects_initial_interval_above_max():
    with pytest.raises(ValidationError, match="must not exceed"):
        tm.PollingPolicy.model_validate({"initial_interval": "5m", "max_interval": "30s"})


def test_polling_policy_allows_initial_interval_at_or_below_max():
    policy = tm.PollingPolicy.model_validate({"initial_interval": "30s", "max_interval": "5m"})
    assert policy.initial_interval.total_seconds() == 30
    equal = tm.PollingPolicy.model_validate({"initial_interval": "5m", "max_interval": "5m"})
    assert equal.initial_interval == equal.max_interval


# ── execution shapes (exec-node-requires-one-execution-shape,
# exec-node-orders-concurrent-task-groups) ──


def test_exec_node_requires_one_execution_shape():
    with pytest.raises(ValidationError, match="exactly one"):
        tm.ExecNode(id="build", kind="exec", tasks=[], steps=[])


def test_exec_node_rejects_neither_execution_shape():
    with pytest.raises(ValidationError, match="exactly one"):
        tm.ExecNode.model_validate(exec_node("build"))


def test_exec_node_rejects_an_empty_task_group():
    with pytest.raises(ValidationError, match="must not be empty"):
        tm.ExecNode.model_validate(exec_node("build", tasks=[]))


def test_exec_node_tasks_are_one_concurrent_group_and_steps_are_ordered():
    group = tm.ExecNode.model_validate(exec_node("spec", tasks=[agent("author")]))
    assert group.steps is None
    assert [t.id for t in group.tasks] == ["author"]

    ordered = tm.ExecNode.model_validate(
        exec_node(
            "implementation",
            steps=[
                {"id": "implementation", "tasks": [agent("implement")]},
                {"id": "verification", "tasks": [agent("test")]},
            ],
        )
    )
    assert ordered.tasks is None
    assert [s.id for s in ordered.steps] == ["implementation", "verification"]


def test_recovery_plan_and_fix_loop_also_require_one_execution_shape():
    with pytest.raises(ValidationError, match="exactly one"):
        tm.RecoveryPlan.model_validate({"tasks": [agent("repair")], "steps": []})
    with pytest.raises(ValidationError, match="exactly one"):
        tm.FixLoop.model_validate({})


def test_step_requires_at_least_one_task():
    with pytest.raises(ValidationError, match="at least 1 item"):
        tm.Step.model_validate({"id": "empty", "tasks": []})


# ── gates are ordered nodes (gate-is-an-ordered-node, gate-owns-gate-behaviour) ──


def test_node_kind_selects_gate_model():
    assert isinstance(
        tm.Chain.model_validate({"nodes": [{"id": "review", "kind": "gate"}]}).nodes[0], tm.GateNode
    )


def test_gate_node_owns_gate_configuration():
    """`auto_escalate: true` became `auto_review: <task>` (Task 4b): a bare
    boolean could only mean "Kraft's own default reviewer", which is the name
    indirection V1 exists to delete. The gate declares the task itself."""
    gate = tm.GateNode.model_validate(
        {
            "id": "chain_review",
            "kind": "gate",
            "chain_finalized": True,
            "message": "Review the complete work item.",
            "artifact": "review_brief",
            "reject_to": "implementation",
            "auto_review": agent("reviewer", skill="kraft:gate-review"),
            "timeout": "2d",
        }
    )
    assert gate.chain_finalized is True
    assert gate.artifact == "review_brief"
    assert gate.timeout.total_seconds() == 2 * 24 * 3600
    assert isinstance(gate.auto_review, tm.AgentTask)
    assert gate.auto_review.skill == "kraft:gate-review"


def test_a_gate_cannot_be_armed_with_a_bare_boolean():
    """The field is a task or nothing. `auto_escalate` survives only as the
    per-item *override* key (`store.effective_nodes`), never as a chain field."""
    with pytest.raises(ValidationError, match="auto_escalate"):
        tm.GateNode.model_validate({"id": "g", "kind": "gate", "auto_escalate": True})


def test_a_gates_reviewing_task_resolves_to_its_own_canonical_path():
    resolved = tm.ResolvedChain.from_chain(
        tm.Chain.model_validate(
            {
                "nodes": [
                    {"id": "g", "kind": "gate", "auto_review": agent("reviewer")},
                ]
            }
        )
    )
    assert resolved.nodes[0].auto_review.path == "g.auto_review"
    assert resolved.task_paths == ("g.auto_review",)


# -- attachment trimming (attachment-behaviour-is-explicit-gate-configuration,
# chain-finalized-remains-a-dedicated-marker) --


def _attachment_chain(**gate_extra) -> tm.ResolvedChain:
    """spec author + its gate, plan author + its gate, then a final-review gate
    whose own artifact kind is `spec` -- the string-match edge Ruling 35 names."""
    return tm.ResolvedChain.from_chain(
        tm.Chain.model_validate(
            {
                "id": "t",
                "nodes": [
                    exec_node("spec", tasks=[agent("author", produces="spec")]),
                    {
                        "id": "spec_approval",
                        "kind": "gate",
                        "artifact": "spec",
                        "reject_to": "spec",
                    },
                    exec_node("plan", tasks=[agent("author", produces="plan")]),
                    {
                        "id": "plan_approval",
                        "kind": "gate",
                        "artifact": "plan",
                        "reject_to": "plan",
                    },
                    exec_node("build", tasks=[agent("code")]),
                    # `reject_to: spec` on a surviving gate is the orphan case:
                    # a `spec` attachment drops `spec` and this gate stays.
                    {
                        "id": "done",
                        "kind": "gate",
                        "chain_finalized": True,
                        "artifact": "spec",
                        "reject_to": "spec",
                        **gate_extra,
                    },
                ],
            }
        )
    )


def _ids(chain: tm.ResolvedChain) -> list[str]:
    return [n.id for n in chain.nodes]


def test_an_attachment_drops_the_gate_that_decides_it_and_its_producing_node():
    trimmed = _attachment_chain().trim_for_attachments(frozenset({"spec"}))
    assert "spec_approval" not in _ids(trimmed)
    # The producing node too: legacy got this for free because the author and
    # its gate were one node. In V1 they are two, so without it the attached
    # spec is handed straight back to a spec author to write again.
    assert "spec" not in _ids(trimmed)


def test_an_attachment_does_not_drop_a_gate_no_attachment_kind_names():
    trimmed = _attachment_chain().trim_for_attachments(frozenset({"spec"}))
    assert ["plan", "plan_approval", "build", "done"] == _ids(trimmed)


def test_an_attachment_never_trims_the_chain_finalized_gate():
    """The rule is a string match, and `done` here names `artifact: spec`. A
    chain that lost its only `chain_finalized` marker to an attachment would
    violate `chain-finalized-remains-a-dedicated-marker`."""
    trimmed = _attachment_chain().trim_for_attachments(frozenset({"spec"}))
    done = next(n for n in trimmed.nodes if n.id == "done")
    assert done.node.chain_finalized is True


def test_a_trim_that_orphans_a_reject_target_clears_it_rather_than_dangling():
    """A dangling `reject_to` would make the stored snapshot fail to re-validate
    as a `Chain` when it is read back; `gates.reject_target` then falls back the
    same way it does for a gate that declared no target at all."""
    trimmed = _attachment_chain().trim_for_attachments(frozenset({"spec"}))
    done = next(n for n in trimmed.nodes if n.id == "done")
    assert done.node.reject_to is None
    # And the stored snapshot still re-validates as a `Chain` on read-back,
    # which a dangling name would fail.
    assert tm.MaterializedChain.from_json(
        trimmed.materialize(
            WorkItemTarget.for_repository(Repository(id="r", path="/r")),
            InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
        ).to_json()
    )


def test_a_trim_leaving_no_nodes_is_refused():
    chain = tm.ResolvedChain.from_chain(
        tm.Chain.model_validate(
            {"id": "t", "nodes": [exec_node("spec", tasks=[agent("a", produces="spec")])]}
        )
    )
    with pytest.raises(ValueError, match="no nodes"):
        chain.trim_for_attachments(frozenset({"spec"}))


def test_exec_node_cannot_define_gate_after():
    with pytest.raises(ValidationError, match="gate_after"):
        tm.ExecNode.model_validate(exec_node("build", tasks=[agent()], gate_after="approval"))


def test_exec_node_cannot_define_gate_only_fields():
    with pytest.raises(ValidationError, match="chain_finalized"):
        tm.ExecNode.model_validate(exec_node("build", tasks=[agent()], chain_finalized=True))


def test_gate_node_cannot_define_execution_fields():
    with pytest.raises(ValidationError, match="tasks"):
        tm.GateNode.model_validate({"id": "review", "kind": "gate", "tasks": [agent()]})


# ── authored identifiers (resolved-chain-identifiers-are-unique,
# task-group-shorthand-resolves-to-one-step) ──


def test_an_identifier_that_would_make_a_path_ambiguous_is_rejected():
    # Not parametrized: an intent pin names one node id, and every case here
    # enforces the same requirement.
    for bad in ("spec.author", "spec author", "Spec", "1spec", "", "spec/author"):
        with pytest.raises(ValidationError, match="string_pattern_mismatch|at least 1"):
            tm.Step.model_validate({"id": bad, "tasks": [agent()]})
        with pytest.raises(ValidationError, match="string_pattern_mismatch|at least 1"):
            tm.Step.model_validate({"id": "ok", "tasks": [agent(bad)]})


def test_a_reserved_segment_is_rejected_as_a_step_identifier():
    for reserved in sorted(tm.RESERVED_SEGMENTS):
        with pytest.raises(ValidationError, match="reserved"):
            tm.Step.model_validate({"id": reserved, "tasks": [agent()]})


def test_a_reserved_segment_is_rejected_for_the_dedicated_escalation_task():
    # `node.escalation.<id>`: the escalation task's authored id becomes a path
    # segment of its own, so it obeys the reserved-segment rule.
    for reserved in sorted(tm.RESERVED_SEGMENTS):
        with pytest.raises(ValidationError, match="reserved"):
            tm.ExecNode.model_validate(
                exec_node("build", tasks=[agent()], escalation=agent(reserved))
            )


def test_the_dedicated_judge_is_a_slot_so_any_id_is_addressable():
    # A judge occupies the named `judge` slot, whose own name is its canonical
    # segment, so its authored id is never a path segment and cannot collide --
    # not even with a reserved word.
    for id in ("judge", "main", "strict"):
        loop = tm.FixLoop.model_validate({"tasks": [agent("repair")], "judge": agent(id)})
        assert loop.judge.id == id
    resolved_judge = (
        tm.ResolvedChain.from_chain(
            tm.Chain.model_validate(
                {
                    "id": "c",
                    "nodes": [
                        exec_node(
                            "build",
                            tasks=[agent()],
                            fix_loop={"tasks": [agent("r")], "judge": agent("main")},
                        )
                    ],
                }
            )
        )
        .nodes[0]
        .judge
    )
    assert resolved_judge.path == "build.fix_loop.judge"


def test_an_ordinary_task_may_reuse_a_reserved_word_as_its_own_identifier():
    # Only step position is reserved: a task sits under a step segment, so
    # `build.main.main` is unambiguous.
    node = tm.ExecNode.model_validate(exec_node("build", tasks=[agent("main")]))
    assert node.tasks[0].id == "main"


# ── per-container sibling uniqueness ──


def test_duplicate_task_ids_in_one_step_are_rejected():
    with pytest.raises(ValidationError, match="duplicate task id 'author'"):
        tm.Step.model_validate({"id": "s", "tasks": [agent("author"), agent("author")]})


def test_duplicate_step_ids_in_one_node_are_rejected():
    with pytest.raises(ValidationError, match="duplicate step id 'repair'"):
        tm.ExecNode.model_validate(
            exec_node(
                "build",
                steps=[
                    {"id": "repair", "tasks": [agent("a")]},
                    {"id": "repair", "tasks": [agent("b")]},
                ],
            )
        )


def test_duplicate_node_ids_in_a_chain_are_rejected():
    with pytest.raises(ValidationError, match="duplicate node id 'spec'"):
        tm.Chain.model_validate(
            {
                "id": "default",
                "nodes": [
                    exec_node("spec", tasks=[agent()]),
                    exec_node("spec", tasks=[agent()]),
                ],
            }
        )


# ── canonical execution paths
# (component-identifiers-are-qualified-by-node-instance,
# task-group-shorthand-resolves-to-one-step) ──


def resolved(*nodes) -> tm.ResolvedChain:
    return tm.ResolvedChain.from_chain(tm.Chain.model_validate({"id": "c", "nodes": list(nodes)}))


def test_a_tasks_group_resolves_to_one_step_named_main():
    chain = resolved(exec_node("spec", tasks=[agent("author")]))
    assert chain.task_paths == ("spec.main.author",)
    assert [s.id for s in chain.nodes[0].steps] == ["main"]
    assert chain.nodes[0].steps[0].path == "spec.main"


def test_steps_keep_their_authored_identifiers_in_the_path():
    chain = resolved(
        exec_node(
            "implementation",
            steps=[
                {"id": "implementation", "tasks": [agent("implement")]},
                {"id": "verification", "tasks": [agent("test_changed_scopes")]},
            ],
        )
    )
    assert chain.task_paths == (
        "implementation.implementation.implement",
        "implementation.verification.test_changed_scopes",
    )


def test_the_same_local_task_id_in_two_steps_gets_two_distinct_paths():
    chain = resolved(
        exec_node(
            "post_draft",
            steps=[
                {"id": "repair", "tasks": [agent("run")]},
                {"id": "sync", "tasks": [agent("run")]},
            ],
        )
    )
    assert chain.task_paths == ("post_draft.repair.run", "post_draft.sync.run")
    assert len(set(chain.task_paths)) == 2


def test_recovery_and_fix_loop_reuse_of_a_local_id_stays_distinct():
    chain = resolved(
        exec_node(
            "merge_request_feedback",
            steps=[{"id": "ci", "tasks": [agent("await_ci")]}],
            on_failure={"steps": [{"id": "repair", "tasks": [agent("repair")]}]},
            fix_loop={
                "steps": [{"id": "repair", "tasks": [agent("repair")]}],
                "judge": agent("judge"),
                "max_attempts": 3,
            },
        )
    )
    assert chain.task_paths == (
        "merge_request_feedback.ci.await_ci",
        "merge_request_feedback.on_failure.repair.repair",
        "merge_request_feedback.fix_loop.repair.repair",
        "merge_request_feedback.fix_loop.judge",
    )
    assert len(set(chain.task_paths)) == 4


def test_a_fix_loop_tasks_group_resolves_to_a_main_step_under_its_container():
    chain = resolved(
        exec_node(
            "implementation",
            tasks=[agent("implement")],
            fix_loop={"tasks": [agent("repair")], "judge": agent("judge")},
            escalation=agent("task"),
        )
    )
    assert chain.task_paths == (
        "implementation.main.implement",
        "implementation.fix_loop.main.repair",
        "implementation.fix_loop.judge",
        "implementation.escalation.task",
    )


def test_one_reusable_node_used_twice_keeps_its_paths_distinct():
    body = {"kind": "exec", "steps": [{"id": "run", "tasks": [agent("go")]}]}
    chain = resolved({"id": "first", **body}, {"id": "second", **body})
    assert chain.task_paths == ("first.run.go", "second.run.go")


def test_a_gate_node_contributes_no_task_paths():
    chain = resolved(
        exec_node("spec", tasks=[agent("author")]),
        {"id": "spec_approval", "kind": "gate", "message": "Approve.", "reject_to": "spec"},
    )
    assert chain.task_paths == ("spec.main.author",)
    assert chain.nodes[1].steps == ()


# ── a resolved chain is validated before use (resolved-chain-is-validated-before-use) ──


def test_a_gate_reject_target_must_name_a_node_in_the_chain():
    with pytest.raises(ValidationError, match="reject_to 'nowhere'"):
        tm.Chain.model_validate(
            {
                "id": "default",
                "nodes": [
                    exec_node("spec", tasks=[agent()]),
                    {"id": "gate", "kind": "gate", "reject_to": "nowhere"},
                ],
            }
        )


def test_a_gate_reject_target_cannot_name_a_later_node():
    """`gate-rejection-follows-gate-reject-target` sends execution to
    `reject_to` on rejection, so it must be work that can change the
    artifact -- an earlier execution node -- never a later gate
    (`base-change-restart-target-is-backward` applies the same rule to
    `restart_from`)."""
    with pytest.raises(ValidationError, match="reject_to 'chain_review' must name an earlier"):
        tm.Chain.model_validate(
            {
                "id": "default",
                "nodes": [
                    exec_node("spec", tasks=[agent()]),
                    {
                        "id": "spec_approval",
                        "kind": "gate",
                        "reject_to": "chain_review",
                    },
                    {"id": "chain_review", "kind": "gate"},
                ],
            }
        )


def test_a_gate_reject_target_cannot_name_a_gate():
    with pytest.raises(ValidationError, match="must name an earlier"):
        tm.Chain.model_validate(
            {
                "id": "default",
                "nodes": [
                    {"id": "earlier_gate", "kind": "gate"},
                    {"id": "gate", "kind": "gate", "reject_to": "earlier_gate"},
                ],
            }
        )


def test_a_chain_needs_at_least_one_node():
    with pytest.raises(ValidationError, match="at least 1 item"):
        tm.Chain.model_validate({"id": "default", "nodes": []})


# ── materialization (authored-resolved-and-materialized-chains-are-distinct) ──


def policy(**maxima) -> InstancePolicy:
    return InstancePolicy.from_input(
        InstancePolicyInput.model_validate({"defaults": {"timeout_minutes": 60}, "maxima": maxima})
    )


def test_materialize_binds_the_target_and_effective_policy():
    chain = resolved(exec_node("spec", tasks=[agent("author")]))
    target = WorkItemTarget.for_repository(Repository(id="api", path="/work/api"))
    materialized = chain.materialize(target, policy())

    assert materialized.chain is chain
    assert materialized.target == target
    assert materialized.policy.timeout_minutes == 60
    assert materialized.task_paths == ("spec.main.author",)


def test_materialize_applies_the_chains_own_policy_override():
    chain = tm.ResolvedChain.from_chain(
        tm.Chain.model_validate(
            {
                "id": "c",
                "nodes": [exec_node("spec", tasks=[agent()])],
                "policy": {"timeout_minutes": 15},
            }
        )
    )
    materialized = chain.materialize(
        WorkItemTarget.for_repository(Repository(id="api", path="/work/api")), policy()
    )
    assert materialized.policy.timeout_minutes == 15


def test_materialize_cannot_exceed_an_administrator_maximum():
    chain = tm.ResolvedChain.from_chain(
        tm.Chain.model_validate(
            {
                "id": "c",
                "nodes": [exec_node("spec", tasks=[agent()])],
                "policy": {"timeout_minutes": 600},
            }
        )
    )
    with pytest.raises(PolicyError, match="administrator maximum"):
        chain.materialize(
            WorkItemTarget.for_repository(Repository(id="api", path="/work/api")),
            policy(timeout_minutes=120),
        )


def test_agent_task_selects_a_harness_profile_by_id():
    """Three distinct things (`provider-profile-and-agent-task-are-distinct`):
    the code-owned provider (`kraft.harness.Harness`), one configured profile
    of that provider, and an agent task that selects the profile by id. The
    task names the profile only -- it has no field for the provider, and none
    for the runtime mechanics the provider owns."""
    claude = harness_mod.load(None).valid["claude"]
    profile = HarnessProfile.from_input(
        "claude_review",
        HarnessProfileInput(provider="claude", defaults={"effort": "low"}),
        harness=claude,
    )
    assert profile.provider == claude.id
    task = tm.AgentTask.model_validate(agent("review", harness=profile.id))
    assert task.harness == profile.id == "claude_review"
    assert "provider" not in tm.AgentTask.model_fields
