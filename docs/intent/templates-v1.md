# Intent: template schema v1

Intended behaviour for the V1 template system. This is a deliberate breaking
change from the legacy hook-and-`gate_after` format. These requirements are
unpinned until the migration supplies their implementation tests.

## REQ template-v1-is-not-backward-compatible

The system SHALL accept only the V1 template schema and SHALL NOT preserve
compatibility with legacy hook bindings or `gate_after` chains.
enforced-by: tests/api/test_templates_inspection.py::test_a_legacy_gate_after_chain_does_not_resolve, tests/api/test_templates_inspection.py::test_a_legacy_home_starts_degraded_and_names_the_update_command
origin: src/kraft/templates/library.py §is_pre_v1 -- Task 11a: a legacy chain does not resolve and a pre-V1 home is refused, never converted. Task 11b deleted the legacy loader itself (`registry.yaml`, `load_templates`, the registry routes and the legacy `GET /templates`); `is_pre_v1` is how an old home is still recognised.

## REQ major-update-replaces-incompatible-template-config

When an update crosses a major version with an incompatible template schema,
the update SHALL install the new version's template configuration rather than
continue to use the incompatible configuration.
enforced-by: tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[flag], tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[prompt], tests/test_update.py::test_an_update_leaves_a_v1_home_alone, tests/test_update.py::test_a_major_update_keeps_the_policy_values_v1_still_has, tests/test_update.py::test_a_major_update_reports_each_policy_key_it_drops
origin: src/kraft/cli/admin.py §replace_pre_v1_config -- "incompatible" is a pre-V1 home (`templates.library.is_pre_v1`: a `registry.yaml` and no `library.yaml`), the one incompatible schema that exists. The machine's own files (`MACHINE_CONFIG`) are carried across; `policy.yaml` starts from the V1 seed and keeps the operator's value for every key V1 still has, printing each dropped key (`policy.CarriedPolicy`, Ruling 172); the rest is the bundled V1 configuration.

## REQ major-update-requires-explicit-acceptance

Before replacing configuration for an incompatible major update, the update
command SHALL warn about the breaking change and require explicit acceptance;
the `-y` option MAY provide that acceptance for non-interactive use.
enforced-by: tests/test_update.py::test_the_major_update_warns_before_it_asks, tests/test_update.py::test_major_update_without_acceptance_changes_nothing[no], tests/test_update.py::test_major_update_without_acceptance_changes_nothing[enter], tests/test_update.py::test_major_update_without_acceptance_changes_nothing[no-terminal], tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[prompt], tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[flag]

## REQ major-update-preserves-replaced-configuration

Before an incompatible major update replaces template configuration, the system
SHALL create a recoverable backup of the configuration it replaces.
enforced-by: tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[flag], tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[prompt], tests/test_update.py::test_a_crash_between_the_swap_renames_is_finished_not_reseeded[start], tests/test_update.py::test_a_crash_between_the_swap_renames_is_finished_not_reseeded[update], tests/test_update.py::test_a_staging_dir_no_update_finished_writing_is_never_installed
origin: src/kraft/cli/admin.py §finish_interrupted_update -- Kraft-cttgx: the staged home is marked complete before the old one moves, so a crash between the two renames is finished by the next start or update, never seeded over.

## REQ migration-helper-is-not-guaranteed

The system MAY provide a migration helper for a major template-schema change,
but the availability of such a helper SHALL NOT be required for the update.
enforced-by: tests/test_update.py::test_major_update_requires_acceptance_and_makes_backup[flag]
origin: src/kraft/cli/admin.py §replace_pre_v1_config -- no helper is offered: the update completes, and a legacy chain is left in the backup rather than converted.

## REQ template-library-has-shared-components-and-chain-files

The template directory SHALL contain one `library.yaml` for reusable tasks,
steps, nodes, and named steering profiles, and one selectable chain per file
under `chains/`.
enforced-by: tests/templates/test_library.py::test_from_yaml_dir_loads_components_and_one_chain_per_file, tests/templates/test_library.py::test_each_chain_file_is_one_selectable_chain, tests/templates/test_library.py::test_two_chain_files_claiming_one_id_is_an_error_naming_both, tests/templates/test_materialization.py::test_the_seed_is_a_library_file_and_one_chain_per_file
origin: templates/library.yaml -- Task 3's four pins are the whole sentence ("one `library.yaml`" plus "one selectable chain per file"), and they are left exactly as they were. Task 5a added a fifth entry here for `test_the_seeded_harnesses_yaml_loads_and_covers_the_seeded_library` and it is **removed again**: that test verifies harness-profile coverage, which is a different sentence, and a pin claiming more than its test proves is worse than an honest gap. It is pinned where it belongs, under `harness-profile-has-safe-instance-configuration`. This requirement says nothing about `harnesses.yaml`, and should not.

## REQ registry-is-not-a-task-configuration-source

The system SHALL NOT use `registry.yaml` as a source of agent, subprocess,
forge, or built-in task configuration; those definitions SHALL live in typed
templates.
enforced-by: tests/api/test_templates_inspection.py::test_a_registry_beside_the_library_configures_no_task, tests/templates/test_materialization.py::test_task_configuration_resolves_from_the_library_alone, tests/templates/test_materialization.py::test_the_seed_ships_no_legacy_configuration
origin: src/kraft/templates/__init__.py -- Task 11b deleted the registry loader, its routes and the seed's `registry.yaml`: a daemon with a `registry.yaml` beside its library neither loads nor reports it, and a fresh home is seeded without one.

## REQ task-kinds-are-discriminated

Every task SHALL declare exactly one discriminated kind: `builtin`, `agent`,
`subprocess`, or `forge`.
enforced-by: tests/templates/test_models.py::test_task_kind_selects_the_concrete_task_model, tests/templates/test_models.py::test_task_without_a_kind_is_rejected, tests/templates/test_models.py::test_unknown_task_kind_is_rejected

## REQ agent-task-may-select-one-skill

An agent task MAY select one skill as its primary method. A task SHALL NOT
select more than one skill; composable additional guidance SHALL be expressed
through steering profiles.
enforced-by: tests/executor/test_dispatch.py::test_an_agent_task_contract_precedes_its_skill_and_steering
origin: src/kraft/templates/models.py §AgentTask -- the single-skill half is the type (`skill: StrictStr | None`, so a list is a validation error and no runtime check exists to test); the pinned test covers the other half, that composable additional guidance arrives through steering profiles rather than a second skill.

## REQ agent-task-contract-precedes-skill-and-steering

The system SHALL provide an agent task's Kraft-owned output and lifecycle
contract before its selected skill and steering profiles. A selected skill or
steering profile SHALL NOT remove that contract.
enforced-by: tests/executor/test_dispatch.py::test_an_agent_task_contract_precedes_its_skill_and_steering, tests/executor/test_dispatch.py::test_the_seeded_library_steers_from_its_own_profiles_with_no_steering_file

## REQ a-document-contract-names-the-chain-that-carries-it-on

WHEN an agent task produces a spec or a plan, the system SHALL state in its
Kraft-owned contract, before any selected skill, that the chain implements the
document and that a verification node runs the repository's test suite, whatever
skill the task selects.
enforced-by: tests/skills/test_planning.py::test_a_plan_under_any_method_is_told_verification_runs_the_suite, tests/skills/test_planning.py::test_a_spec_under_any_method_is_told_the_chain_implements_it_headless
origin: src/kraft/adapters/artifact_notes.py -- Kraft-35u4m.3, Kraft-35u4m.4: `spec_author`/`plan_author` may name any plugin's method in `skill:`, and one written for an interactive session plans a full-suite step and waits on a human; the chain context rides in the contract so swapping the method cannot lose it.

## REQ every-agent-launch-carries-kraft-safety-rules

Every agent launch -- a chain task, a gate auto-review, an escalation turn --
SHALL carry Kraft's own safety rules as part of its contract, including the rule
never to signal a process the agent did not start. The rules SHALL NOT depend
on a task selecting them, and no task, chain, steering profile or repository
configuration SHALL be able to remove them.
enforced-by: tests/executor/test_dispatch.py::test_every_seeded_agent_task_launches_with_the_never_signal_rule, tests/executor/test_dispatch.py::test_an_operator_agent_task_with_no_skill_or_steering_gets_the_never_signal_rule, tests/executor/test_gates.py::test_a_gate_auto_review_launch_carries_the_never_signal_rule, tests/test_escalate.py::test_an_escalation_launch_carries_the_never_signal_rule
origin: src/kraft/adapters/agent.py §SAFETY_RULES -- Kraft-5x93b: the legacy registry's `defaults: {agent: {steering: [never-signal-processes-you-didnt-start]}}` gave every agent the rule born of Kraft-f8u3 (a worker SIGKILLed the daemon); V1 has no registry default, so the rule became contract text appended by `build_context`, the one builder every launch path uses.

## REQ a-turn-left-with-a-running-background-job-fails

IF an agent task's turn ends with a background job still running, THEN the
system SHALL fail the session when it exits, naming each job in the session
log and in a `background_jobs_abandoned` event, and SHALL NOT wait for a
missing result file to say so. A `needs_context` stop SHALL keep its status
and question, with the jobs still named. Every agent launch SHALL be told this
rule, and that the full test suite is the verification node's to run.
enforced-by: tests/adapters/test_background_jobs.py::test_the_claude_reader_names_the_jobs_still_running_when_the_turn_ended[incident], tests/adapters/test_background_jobs.py::test_the_claude_reader_names_the_jobs_still_running_when_the_turn_ended[run-in-background], tests/adapters/test_background_jobs.py::test_the_claude_reader_names_the_jobs_still_running_when_the_turn_ended[waited-on], tests/adapters/test_background_jobs.py::test_a_turn_left_with_a_running_job_fails_naming_it[claims-done], tests/adapters/test_background_jobs.py::test_a_turn_left_with_a_running_job_fails_naming_it[no-result-file], tests/adapters/test_background_jobs.py::test_a_question_keeps_its_stop_and_still_names_the_job, tests/adapters/test_background_jobs.py::test_a_clean_turn_is_left_alone[nothing-left-running], tests/adapters/test_background_jobs.py::test_a_session_adopted_after_a_restart_is_held_to_the_same_rule, tests/adapters/test_background_jobs.py::test_every_agent_launch_is_told_the_rule_and_where_the_full_suite_runs
origin: src/kraft/adapters/subprocess.py §fail_abandoned_jobs -- Kraft-xvugd, Kraft-nxqft: a worker backgrounded a full-suite run and ended its turn, 68 minutes and $9.46, and the prose rule in agent._CTX had not held

## REQ the-shipped-implementer-runs-under-a-time-cap

The shipped `implementer` task SHALL carry a `time_cap_minutes` default of
120, so a runaway implementation run in either shipped chain stops for a
person.
enforced-by: tests/templates/test_time_caps.py::test_the_shipped_implementer_runs_under_a_default_time_cap[default], tests/templates/test_time_caps.py::test_the_shipped_implementer_runs_under_a_default_time_cap[quick-task]
origin: templates/library.yaml §implementer -- Kraft-nxqft; every successful implementer run on record finished inside 96 minutes (p99 73)

## REQ selected-skill-must-be-available

When an agent task selects a skill that its execution environment cannot load,
the task SHALL stop for human action and SHALL NOT substitute a different
method.
enforced-by: tests/executor/test_dispatch.py::test_an_unloadable_selected_skill_stops_for_a_human

## REQ provider-profile-and-agent-task-are-distinct

The system SHALL distinguish a code-owned agent-runtime provider, a configured
harness profile for that provider, and an agent task that selects a profile.
enforced-by: tests/test_harnesses.py::test_harness_profile_selects_only_provider_declared_options, tests/test_harnesses.py::test_harness_profile_provider_must_be_the_harness_it_configures, tests/templates/test_models.py::test_agent_task_selects_a_harness_profile_by_id

## REQ provider-owns-runtime-mechanics

An agent-runtime provider SHALL own invocation, context delivery, supported
runtime options, session resumption, skill loading, and normalized task
results for its runtime.
enforced-by: tests/executor/test_dispatch.py::test_each_task_kind_reaches_its_own_adapter, tests/executor/test_dispatch.py::test_an_agent_task_contract_precedes_its_skill_and_steering, tests/executor/test_dispatch.py::test_a_typed_agent_task_reports_the_providers_own_normalized_result, tests/adapters/test_agent.py::test_a_typed_agent_task_resolves_through_provider_declared_options
origin: src/kraft/harness.py §build_argv -- five of the six mechanics are pinned above (invocation, context delivery, runtime options, skill loading, normalized results), each through a typed dispatch. **Session resumption is not**, and cannot be yet: a V1 `AgentTask` has no field that asks for a resumed provider session, so no chain dispatch reaches `build_argv`'s `resume` path. Its only caller is `kraft.escalate.dispatch`, whose launch is an ordinary typed `AgentTask` since Task 7a (`escalate.ESCALATION_TASK`) but which resumes its thread by its own session id rather than through any task field; the provider-side spelling meanwhile is covered by tests/adapters/test_agent.py::test_the_provider_spells_session_resumption_for_an_agent_task, which is harness-level and deliberately not claimed as this requirement's evidence.

## REQ provider-declares-harness-capabilities

An agent-runtime provider SHALL declare the capabilities and runtime-option
schema it supports. Harness profiles and templates SHALL only select from that
provider-declared surface.
enforced-by: tests/test_harnesses.py::test_harness_profile_rejects_an_option_the_provider_does_not_declare, tests/test_harnesses.py::test_harness_profile_rejects_a_value_the_provider_rejects, tests/templates/test_environment.py::test_a_profile_default_the_provider_does_not_declare_is_refused
origin: src/kraft/templates/environment.py -- `HarnessProfileTable.from_yaml` is the file boundary, so the check now fires when `harnesses.yaml` is read rather than only when a profile is constructed in code.

## REQ harness-profile-has-safe-instance-configuration

A harness profile MAY configure an enabled runtime instance, executable or
connection choice, and default runtime options. It SHALL NOT require template
authors to configure provider command syntax or result parsing.
enforced-by: tests/test_harnesses.py::test_harness_profile_selects_only_provider_declared_options, tests/test_harnesses.py::test_harness_profile_reports_unavailable_when_disabled, tests/templates/test_environment.py::test_harness_profiles_load_against_their_provider_declarations, tests/templates/test_environment.py::test_a_profile_whose_provider_is_not_its_harness_id_is_refused, tests/templates/test_environment.py::test_the_seeded_harnesses_yaml_loads_and_covers_the_seeded_library, tests/executor/test_dispatch.py::test_the_seeded_library_dispatches_through_its_real_harness_profiles[spec], tests/executor/test_dispatch.py::test_the_seeded_library_dispatches_through_its_real_harness_profiles[plan]
origin: src/kraft/templates/environment.py -- a profile names a provider and its defaults; it carries no command syntax or result parsing, and an unknown provider is refused at load rather than becoming an arbitrary command fragment. Reached at runtime through `adapters/agent.py` §harness_profile, which every V1 agent launch (`resolve_agent_task`: chain dispatch and gate auto-review) resolves a task's `harness:` through; the dispatch test drives the seeded library's own `claude` profile against `spec` and `plan`, with its `executable:` pointed at a fake.

## REQ agent-task-selects-capability-compatible-runtime-options

An agent task MAY select a harness profile and override its runtime defaults
only with options supported by the selected provider and allowed by policy.
enforced-by: tests/test_harnesses.py::test_harness_profile_selects_only_provider_declared_options, tests/test_harnesses.py::test_harness_profile_rejects_a_value_the_provider_rejects, tests/adapters/test_agent.py::test_a_task_overrides_its_harness_profiles_defaults, tests/templates/test_policy_scopes.py::test_materialization_refuses_an_agent_task_on_a_harness_its_policy_disallows[task-scope], tests/executor/test_policy_enforcement.py::test_a_harness_its_policy_disallows_never_launches

## REQ unavailable-selected-harness-needs-human

When a selected harness profile is unavailable at runtime, the task SHALL stop
for human action and SHALL NOT silently select a different harness.
enforced-by: tests/executor/test_dispatch.py::test_an_unavailable_selected_harness_stops_for_a_human[absent], tests/executor/test_dispatch.py::test_an_unavailable_selected_harness_stops_for_a_human[disabled], tests/executor/test_dispatch.py::test_an_unavailable_selected_harness_stops_for_a_human[unknown-provider], tests/executor/test_dispatch.py::test_an_unavailable_selected_harness_stops_for_a_human[no-file], tests/executor/test_dispatch.py::test_an_unavailable_selected_harness_stops_for_a_human[unapplied-default], tests/skills/test_gate_review.py::test_a_reviewer_on_an_unavailable_profile_launches_nothing_and_claims_nothing

## REQ agent-roles-use-ordinary-agent-task-runtime-configuration

Ordinary work, recovery, fixing, judging, and escalation agent roles SHALL use
ordinary agent-task harness and runtime configuration. The system SHALL NOT
require special runtime fields for escalation or judging.
enforced-by: tests/test_escalate.py::test_dispatch_resolves_its_agent_through_its_harness_profile, tests/test_escalate.py::test_an_escalation_on_an_unavailable_profile_launches_nothing, tests/executor/test_stuck_escalation.py::test_escalation_runs_only_after_recovery_and_the_fix_loop_and_retries_the_node, tests/executor/test_seeded_failure_walk.py::test_the_judge_launches_on_its_own_runtime_not_the_fixers

## REQ judge-runtime-is-independent-from-fixer-runtime

A judge's selected harness and runtime options SHALL be independent from the
fixing tasks it assesses.
enforced-by: tests/executor/test_seeded_failure_walk.py::test_the_judge_launches_on_its_own_runtime_not_the_fixers

## REQ review-package-is-delivered-to-a-task-that-declares-it

An agent task that declares the `review_package` input SHALL be handed the
change under review -- the whole branch on its first session, then only what
changed since its previous session -- and a task that does not declare it
SHALL NOT be.
enforced-by: tests/executor/test_agent_inputs.py::test_the_review_package_reaches_only_a_task_that_declares_it[declared], tests/executor/test_agent_inputs.py::test_the_review_package_reaches_only_a_task_that_declares_it[undeclared], tests/executor/test_agent_inputs.py::test_the_seeded_code_review_reads_the_review_package_through_its_method, tests/executor/test_seeded_failure_walk.py::test_a_failing_review_walks_verifications_own_fix_loop_never_the_implementer
origin: src/kraft/templates/models.py §AgentInput -- declared on the task (`inputs: [review_package]`), not keyed on a task's name (Ruling 47). Delivered by `executor/dispatch.py` §dispatch_node through `prompts.review_package` and `adapters/agent.py`'s `$KRAFT_REVIEW_PACKAGE`; the seeded fix-loop judge is its first consumer, and the default chain's in-loop code review (`verification.review.code_review`, Ruling 87) its second.

## REQ carried-findings-are-delivered-to-a-reviewing-task

A reviewing agent task SHALL be shown the findings its previous round
reported, each with its stable identity, so that it can report a reworded
repeat as the same finding.
enforced-by: tests/executor/test_agent_inputs.py::test_carried_findings_reach_only_a_task_that_declares_them[declared], tests/executor/test_agent_inputs.py::test_carried_findings_reach_only_a_task_that_declares_them[undeclared], tests/executor/test_agent_inputs.py::test_a_first_review_is_handed_no_history, tests/executor/test_agent_inputs.py::test_the_seeded_code_review_reads_the_review_package_through_its_method
origin: src/kraft/templates/models.py §AgentInput -- Task 11b: declared as `inputs: [carried_findings]` and delivered by `executor/dispatch.py` §dispatch_node through `prompts.carried_findings_note` -- the node's last measurement, under the tags `walk` then trusts in `findings.resolve_identity`. The seeded in-loop code review declares it.

## REQ continuity-note-is-delivered-to-a-resumed-reviewer

A reviewing agent task on its second or later session SHALL be pointed at its
own previous session's result and summary.
enforced-by: tests/executor/test_agent_inputs.py::test_a_resumed_reviewer_is_pointed_at_its_own_last_session[declared], tests/executor/test_agent_inputs.py::test_a_resumed_reviewer_is_pointed_at_its_own_last_session[undeclared], tests/executor/test_agent_inputs.py::test_a_first_review_is_handed_no_history, tests/executor/test_agent_inputs.py::test_the_seeded_code_review_reads_the_review_package_through_its_method
origin: src/kraft/templates/models.py §AgentInput -- Task 11b: declared as `inputs: [previous_review]` and delivered by `executor/dispatch.py` §dispatch_node through `prompts.previous_review_note` from the task's own last completed session (`prompts.last_review_session`). The seeded in-loop code review declares it.

## REQ resumed-escalation-preserves-original-runtime

When a manual escalation resumes an existing escalation session, it SHALL use
that session's original harness and runtime options. Changing them SHALL
require a new escalation session.
enforced-by: tests/test_escalate.py::test_a_resumed_escalation_keeps_its_original_runtime[resumed]

## REQ a-resumed-escalation-turn-writes-where-it-remembers

WHEN an escalation turn resumes a thread, the system SHALL give it the same
result file and session summary paths every earlier turn of that thread had,
SHALL start it with no result file left by an earlier turn, and SHALL keep each
earlier turn's result and summary readable from that turn's own session row.
enforced-by: tests/test_escalation_thread_files.py::test_a_resumed_turn_that_writes_where_it_remembers_is_done, tests/test_escalation_thread_files.py::test_a_turn_starts_with_no_result_file_from_the_last, tests/test_escalation_thread_files.py::test_each_turns_result_and_summary_stay_readable_from_its_row
origin: src/kraft/escalate.py §thread_files -- Kraft-s7c04.54 option (b), Ruling 207: a prompt note telling a resumed turn its paths were new did not stop a model that trusted its memory (b5afe84c), so the path it remembers is made the right one.

## REQ an-escalation-turn-hands-a-skip-to-the-person

The system SHALL tell every escalation turn, manual or automatic, that it is
not allowed to skip or abandon its work item itself, and SHALL give it the
exact commands that do, for the person to run.
enforced-by: tests/test_escalate_suggestion.py::test_an_escalation_turn_hands_a_skip_to_the_person[manual], tests/test_escalate_suggestion.py::test_an_escalation_turn_hands_a_skip_to_the_person[automatic], tests/test_escalate_suggestion.py::test_an_escalation_turn_hands_a_skip_to_the_person[paused]
origin: src/kraft/escalate.py §_HANDS_OFF -- Ruling 209 (Kraft-s7c04.67): no verb is pre-approved for an escalation agent.

## REQ builtin-task-references-code-owned-actions

A built-in task SHALL name its action through `ref`, and the system SHALL
reject a reference to an action Kraft does not support.
enforced-by: tests/templates/test_models.py::test_builtin_task_accepts_a_code_owned_ref, tests/templates/test_models.py::test_builtin_task_rejects_an_action_kraft_does_not_support, tests/templates/test_models.py::test_builtin_task_requires_a_ref

## REQ changed-test-scope-verification-is-a-typed-built-in-task

Verification of repository test scopes SHALL be an explicitly configured
typed built-in task and SHALL NOT depend on a node name or another implicit
template convention.
enforced-by: tests/executor/test_dispatch.py::test_each_task_kind_reaches_its_own_adapter, tests/executor/test_scopes.py::test_changed_test_scopes_run_all_scopes_when_nothing_matches, tests/executor/test_scopes.py::test_changed_test_scopes_run_under_a_node_not_named_verify

## REQ changed-test-scope-verification-selects-safely

The changed-test-scope task SHALL run every scope selected by changed paths.
When the changed paths are empty or do not match a configured scope, it SHALL
run every configured scope.
enforced-by: tests/executor/test_scopes.py::test_changed_test_scopes_run_all_scopes_when_nothing_matches, tests/executor/test_scopes.py::test_select_scopes_on_the_first_round_uses_the_whole_branch_diff

## REQ changed-test-scope-verification-is-sequential-by-default

The changed-test-scope task SHALL run selected scopes sequentially by default.
It MAY run scopes in bounded parallelism only when its configuration explicitly
requests it.
enforced-by: tests/executor/test_scopes.py::test_changed_test_scopes_run_sequentially_unless_configured_parallel

## REQ changed-test-scope-verification-aggregates-results

The changed-test-scope task SHALL await all selected scope results and report
one aggregate task result.
enforced-by: tests/executor/test_scopes.py::test_changed_test_scopes_report_one_aggregate_result

## REQ exec-node-orders-concurrent-task-groups

An execution node's `tasks` SHALL be one concurrent task group; its `steps`
SHALL be ordered groups whose tasks run concurrently within each group.
enforced-by: tests/templates/test_models.py::test_exec_node_tasks_are_one_concurrent_group_and_steps_are_ordered

## REQ exec-node-requires-one-execution-shape

An execution node SHALL define exactly one of `tasks` or `steps`.
enforced-by: tests/templates/test_models.py::test_exec_node_requires_one_execution_shape, tests/templates/test_models.py::test_exec_node_rejects_neither_execution_shape, tests/templates/test_models.py::test_exec_node_rejects_an_empty_task_group

## REQ task-group-shorthand-resolves-to-one-step

A `tasks` group, whether on an execution node, a recovery plan, or a fix loop,
SHALL resolve to one ordered step with the reserved identifier `main`. Every
resolved task SHALL therefore belong to an addressable step and every ordinary
execution path SHALL have the same shape. A dedicated fix-loop judge or stuck
escalation task SHALL take its container as its path segment, such as
`node.fix_loop.judge` and `node.escalation.task`. The reserved identifiers
`main`, `on_failure`, `fix_loop`, `judge`, `escalation`, `on_base_changed`, and
`on_conflict` SHALL NOT be used as authored step identifiers, nor as the
identifier of a dedicated task that occupies a step position.
enforced-by: tests/templates/test_models.py::test_a_tasks_group_resolves_to_one_step_named_main, tests/templates/test_models.py::test_a_fix_loop_tasks_group_resolves_to_a_main_step_under_its_container, tests/templates/test_models.py::test_a_reserved_segment_is_rejected_as_a_step_identifier, tests/templates/test_models.py::test_a_reserved_segment_is_rejected_for_the_dedicated_escalation_task
origin: docs/templates-v1-design.md "Resolution and execution" -- a fix loop's judge is a named slot whose own name is its canonical segment, so its authored id is never a path segment and the reserved rule does not apply to it; the escalation task's id does become a segment (`node.escalation.<id>`) and is checked.

## REQ gate-is-an-ordered-node

A human gate SHALL be represented by an ordered node with `kind: gate`; an
execution node SHALL NOT define `gate_after`.
enforced-by: tests/templates/test_models.py::test_node_kind_selects_gate_model, tests/templates/test_models.py::test_exec_node_cannot_define_gate_after, tests/templates/test_library.py::test_the_design_chain_keeps_gates_as_ordered_nodes, tests/executor/test_gates.py::test_gate_node_halts_until_approved, tests/executor/test_gates.py::test_a_gate_with_arbitrary_id_works_without_a_name_table

## REQ gate-owns-gate-behaviour

A gate node SHALL own gate-specific configuration, including its message,
timeout, reject target, auto-escalation, review artifact reference, and any
dedicated marker such as `chain_finalized`.
enforced-by: tests/templates/test_models.py::test_gate_node_owns_gate_configuration, tests/templates/test_models.py::test_exec_node_cannot_define_gate_only_fields, tests/executor/test_gates.py::test_a_gate_shows_the_artifact_its_own_field_names, tests/executor/test_gates.py::test_gate_rejection_follows_its_own_reject_to

## REQ resolved-chain-identifiers-are-unique

After resolution, node, step, and task occurrences SHALL each have a unique
canonical identifier across the complete chain, including recovery and
fix-loop plans. An authored node, step, or task identifier SHALL match
`[a-z][a-z0-9_-]*`, so that no identifier can contain the canonical path
separator, whitespace, or any other character that would make a resolved path
ambiguous.
enforced-by: tests/templates/test_models.py::test_an_identifier_that_would_make_a_path_ambiguous_is_rejected, tests/templates/test_models.py::test_duplicate_task_ids_in_one_step_are_rejected, tests/templates/test_models.py::test_duplicate_step_ids_in_one_node_are_rejected, tests/templates/test_models.py::test_duplicate_node_ids_in_a_chain_are_rejected, tests/templates/test_library.py::test_the_design_chain_resolves_to_its_documented_canonical_paths

## REQ component-identifiers-are-qualified-by-node-instance

When a reusable component supplies step or task identifiers, the resolved
chain SHALL assign a canonical identifier containing its complete execution
path. Ordinary execution uses `node.step.task`, including the resolved `main`
step for `tasks` shorthand. A recovery or fix plan includes its container,
such as `node.on_failure.step.task` or `node.fix_loop.step.task`. A dedicated
fix-loop judge or escalation task uses its container, such as
`node.fix_loop.judge` or `node.escalation.task`. This shall preserve global
uniqueness when a component is used more than once.
enforced-by: tests/templates/test_models.py::test_steps_keep_their_authored_identifiers_in_the_path, tests/templates/test_models.py::test_the_same_local_task_id_in_two_steps_gets_two_distinct_paths, tests/templates/test_models.py::test_recovery_and_fix_loop_reuse_of_a_local_id_stays_distinct, tests/templates/test_models.py::test_one_reusable_node_used_twice_keeps_its_paths_distinct, tests/templates/test_library.py::test_the_design_chain_resolves_to_its_documented_canonical_paths

## REQ component-extends-has-one-parent

A reusable task, step, node, or chain MAY extend one same-namespace parent and
the system SHALL reject multiple parents, cross-namespace parents, and cycles.
enforced-by: tests/templates/test_library.py::test_extends_rejects_more_than_one_parent, tests/templates/test_library.py::test_extends_rejects_a_cycle, tests/templates/test_library.py::test_extends_rejects_a_cross_namespace_parent, tests/templates/test_library.py::test_extends_rejects_an_unknown_parent
origin: docs/templates-v1-design.md "Library components" -- implemented for the task, step and node namespaces. Chain-level `extends` is deliberately not implemented in V1 -- no shipped chain uses it -- and a chain file carrying `extends` is rejected with an explicit resolver error (tests/templates/test_library.py::test_chain_level_extends_is_rejected_explicitly).

## REQ extends-merges-objects-and-replaces-arrays

During `extends` resolution, mappings SHALL merge recursively, scalar values
including `null` SHALL replace inherited values, and arrays SHALL replace their
inherited arrays as a whole.
enforced-by: tests/templates/test_library.py::test_extends_replaces_arrays_wholesale_and_scalars_including_null, tests/templates/test_library.py::test_extends_merges_maps_recursively

## REQ extends-cannot-change-kind

A derived component SHALL NOT change the discriminated `kind` of its parent.
enforced-by: tests/templates/test_library.py::test_extends_cannot_change_the_parent_kind, tests/templates/test_library.py::test_extends_cannot_change_a_kind_inherited_further_up_the_chain

## REQ resolved-chain-is-validated-before-use

The system SHALL reject a chain with missing references, invalid overrides,
duplicate identifiers, or invalid cross-node references before it is used.
enforced-by: tests/templates/test_models.py::test_a_gate_reject_target_must_name_a_node_in_the_chain, tests/templates/test_models.py::test_a_gate_reject_target_cannot_name_a_later_node, tests/templates/test_models.py::test_a_gate_reject_target_cannot_name_a_gate, tests/templates/test_library.py::test_extends_rejects_an_unknown_parent, tests/templates/test_library.py::test_an_unknown_steering_reference_is_rejected, tests/templates/test_materialization.py::test_lint_reports_a_scope_its_chain_refuses_without_any_instance_ceiling
origin: docs/templates-v1-design.md "Resolution and execution" -- Phase 1 covers missing references and cross-node reject targets here, including the `base-change-restart-target-is-backward` backward-reference rule applied to `reject_to`; duplicate identifiers are pinned under `resolved-chain-identifiers-are-unique`. Invalid per-scope policy overrides are refused by `ResolvedChain.check_scopes`, which lint and materialization share.

## REQ template-resolution-preserves-source-context

The template resolver SHALL retain source paths for inheritance and validation
errors so an author can identify the input that needs correction.
enforced-by: tests/templates/test_library.py::test_an_inheritance_error_names_the_file_and_the_component, tests/templates/test_library.py::test_a_schema_error_names_the_library_file_the_component_came_from, tests/templates/test_library.py::test_a_duplicate_identifier_error_names_the_container_and_the_id

## REQ authored-resolved-and-materialized-chains-are-distinct

The system SHALL distinguish the authored template library, a reusable fully
resolved chain, and a per-work-item materialized chain snapshot.
enforced-by: tests/templates/test_materialization.py::test_the_resolved_chain_is_reusable_across_work_items, tests/templates/test_materialization.py::test_materialization_freezes_chain_policy_and_target

## REQ materialized-chain-is-immutable-work-item-input

A materialized chain SHALL contain effective policy values and intake-specific
decisions for one work item and SHALL NOT change as that item executes.
enforced-by: tests/templates/test_materialization.py::test_materialization_freezes_chain_policy_and_target, tests/templates/test_materialization.py::test_a_materialized_chain_cannot_be_changed_while_the_item_executes, tests/store/test_chain_gates.py::test_a_materialized_chain_round_trips_through_the_work_item_row, tests/executor/test_gates.py::test_the_typed_override_is_a_read_time_view_and_does_not_touch_the_snapshot, tests/executor/test_dispatch.py::test_editing_the_library_after_intake_does_not_change_a_running_items_steering, tests/executor/test_dispatch.py::test_a_snapshot_without_frozen_steering_stops_for_a_human, tests/templates/test_item_policy.py::test_an_item_override_binds_the_scopes_it_addresses_and_touches_nothing_stored

## REQ steer-can-address-paused-agent-tasks-individually

An operator MAY provide distinct steering instructions to selected paused agent
tasks. A steer SHALL NOT target a non-agent task.
enforced-by: tests/executor/test_steer_targets.py::test_a_task_addressed_individually_gets_its_own_steer, tests/executor/test_steer_targets.py::test_a_steer_that_cannot_land_is_refused_naming_its_task[non-agent], tests/executor/test_steer_targets.py::test_a_steer_that_cannot_land_is_refused_naming_its_task[not-paused], tests/test_pause_resume.py::test_resume_addresses_its_steer_to_the_paused_agent_tasks[individual], tests/test_pause_resume.py::test_a_steer_aimed_at_a_non_agent_task_is_refused_naming_it

## REQ steer-defaults-to-all-paused-agent-tasks

When an operator resumes paused work with one unqualified steer, the system
SHALL deliver that instruction to every paused agent task; selected tasks MAY
instead receive individual instructions.
enforced-by: tests/executor/test_steer_targets.py::test_one_steer_reaches_every_paused_agent_task, tests/executor/test_steer_targets.py::test_a_task_that_was_not_paused_gets_no_default_steer, tests/test_pause_resume.py::test_resume_addresses_its_steer_to_the_paused_agent_tasks[one-steer-for-every-paused-agent]

## REQ resumed-agent-task-preserves-its-session-when-possible

When resuming a paused agent task, the system SHALL resume its durable session
when available, and otherwise restart that task with its original instruction
and any supplied steer.
enforced-by: tests/executor/test_steer_targets.py::test_a_paused_agent_task_resumes_its_own_session, tests/executor/test_steer_targets.py::test_an_agent_task_that_cannot_resume_restarts_with_its_instruction[no-provider-id], tests/executor/test_steer_targets.py::test_an_agent_task_that_cannot_resume_restarts_with_its_instruction[not-paused], tests/executor/test_steer_targets.py::test_an_agent_task_that_cannot_resume_restarts_with_its_instruction[before-a-retry], tests/test_pause_resume.py::test_pause_then_resume_with_a_steer_relaunches_the_task[resumes], tests/test_pause_resume.py::test_pause_then_resume_with_a_steer_relaunches_the_task[restarts]

## REQ steer-reaches-only-stopped-agentic-work

The system SHALL refuse a steer on a running work item, and on a paused work
item whose pause stopped no agent task, naming why. A steer SHALL NOT be
handed to whichever agent runs next in place of the work the pause stopped.
enforced-by: tests/test_pause_resume.py::test_steer_and_resume_are_refused_while_the_item_is_running, tests/test_pause_resume.py::test_a_pause_that_stopped_no_agent_task_takes_no_steer, tests/api/test_lifecycle.py::test_a_rebase_conflict_does_not_escalate_when_disarmed[resume]
origin: src/kraft/api/routes/lifecycle.py §paused_steer_refusal -- Ruling 183 (Kraft-5d3sy): steer works only on an item that is not running, and only when the current work is agentic; the paused agent session is resumed when its harness allows it, the steer injected, and the work continues. An item filed paused and never started stopped nothing, so its steer is a note to its first agent.

## REQ pause-is-a-work-item-control

An operator MAY pause a work item. The system SHALL NOT expose pause as a
task, step, or node control.
enforced-by: tests/test_operator_surface.py::test_pause_is_a_work_item_control_only, tests/test_pause_resume.py::test_pause_then_resume_with_a_steer_relaunches_the_task[restarts]

## REQ resume-preserves-completed-work

Resuming paused work SHALL continue from its saved execution point and SHALL
not rerun work that completed before the pause.
enforced-by: tests/executor/test_entry_paths.py::test_a_walk_given_no_position_starts_at_the_items_cursor[plain], tests/executor/test_entry_paths.py::test_a_walk_given_no_position_starts_at_the_items_cursor[fix-loop], tests/executor/test_entry_paths.py::test_crash_resume_keeps_the_steps_that_completed[plain], tests/executor/test_entry_paths.py::test_crash_resume_keeps_the_steps_that_completed[fix-loop], tests/test_pause_resume.py::test_resume_leaves_the_position_to_the_walk, tests/test_rate_limit_retry.py::test_a_rate_limit_relaunch_leaves_the_position_to_the_walk, tests/test_waits.py::test_a_reentry_resumes_at_the_waiting_step, tests/executor/test_entry_paths.py::test_crash_resume_dispatches_a_sibling_the_crash_never_started

## REQ resume-does-not-consume-a-retry-attempt

Resuming a paused work item SHALL NOT spend a retry attempt: no fix-loop or
gate reject-loop counter SHALL change because of the resume itself.
enforced-by: tests/api/test_lifecycle.py::test_resume_does_not_consume_a_retry_attempt
origin: src/kraft/store/work_items.py §resume_work_item -- carried from the retired legacy gate spec (Task 11b fix round 1, Kraft-bqlld): a human-initiated interruption is not a failure, so resuming leaves `retry_counters` alone.

## REQ only-a-person-resets-a-cap-counter

WHEN a work item is retried, the system SHALL reset its fix-loop, gate
reject-loop, CI-infra, stuck-escalation and base-change counters only if a
person asked for the retry (not a Kraft worker, an MCP assistant, an
escalation turn or the rate-limit relaunch), and SHALL record the counters it
reset, with their counts, in a `cap_counters_reset` event.
enforced-by: tests/api/test_lifecycle.py::test_only_a_persons_retry_resets_a_cap_counter[person], tests/api/test_lifecycle.py::test_only_a_persons_retry_resets_a_cap_counter[worker], tests/api/test_lifecycle.py::test_only_a_persons_retry_resets_a_cap_counter[mcp-assistant], tests/executor/test_escalation.py::test_an_escalations_own_retry_resets_no_cap_counter, tests/test_rate_limit_retry.py::test_a_relaunch_resets_no_cap_counter, tests/store/test_forks.py::test_a_retry_no_person_asked_for_clears_no_counter, tests/store/test_counters.py::test_a_retry_no_person_asked_for_clears_no_counter, tests/skills/test_gate_review.py::test_fixed_verdicts_across_an_agent_retry_still_breach_the_reject_loop[agent], tests/skills/test_gate_review.py::test_fixed_verdicts_across_an_agent_retry_still_breach_the_reject_loop[person]
origin: src/kraft/executor/retry.py -- Kraft-s7c04.22: an agent-initiated retry deleted the gate's reject-loop counter, so a cap meant to bound agents was one they could reset.

## REQ task-retry-reruns-that-task-and-later-work

Retrying a task SHALL rerun that task, preserve completed sibling tasks in its
concurrent step, and rerun subsequent steps and nodes.
enforced-by: tests/executor/test_retry_scopes.py::test_task_retry_reruns_only_task_then_later_work, tests/templates/test_forks.py::test_a_task_retry_preserves_its_step_siblings_and_nothing_wider

## REQ step-retry-reruns-that-step-and-later-work

Retrying a step SHALL rerun every task in that step and rerun subsequent steps
and nodes.
enforced-by: tests/executor/test_retry_scopes.py::test_step_retry_reruns_all_step_tasks_then_later_work, tests/executor/test_retry_scopes.py::test_a_later_step_retry_keeps_the_steps_before_it

## REQ node-retry-reruns-that-node-and-later-work

Retrying an execution node SHALL rerun that node and every subsequent node.
enforced-by: tests/executor/test_retry_scopes.py::test_node_retry_reruns_that_node_and_later_work

## REQ work-item-restart-reruns-the-complete-chain

Restarting a work item SHALL rerun its chain from the first node.
enforced-by: tests/executor/test_retry_scopes.py::test_work_item_restart_reruns_the_complete_chain, tests/api/test_retry_paths.py::test_a_retry_hands_its_target_to_the_fork[restart]

## REQ retry-can-target-completed-work

An operator MAY retry a task, step, or execution node that completed earlier
in the work item's run.
enforced-by: tests/executor/test_retry_scopes.py::test_retry_can_target_completed_work, tests/api/test_retry_paths.py::test_a_retry_hands_its_target_to_the_fork[completed-node], tests/store/test_forks.py::test_a_rerun_node_completes_again_in_its_fork

## REQ retry-creates-an-immutable-run-fork

A retry SHALL preserve prior run data and create a new immutable run fork for
the retried work and its invalidated downstream work.
enforced-by: tests/store/test_forks.py::test_retry_creates_a_fork_and_preserves_prior_run_data, tests/store/test_forks.py::test_a_run_fork_is_immutable[update], tests/store/test_forks.py::test_a_run_fork_is_immutable[delete], tests/test_db_migrations.py::test_a_fresh_schema_and_a_fully_migrated_one_agree, tests/executor/test_retry_scopes.py::test_every_retry_is_its_own_fork, tests/store/test_forks.py::test_the_item_runs_its_forks_copy_of_the_chain

## REQ retry-reopens-invalidated-gates

A retry SHALL reopen every gate in its invalidated downstream scope. Gate
decisions before the retry target SHALL remain in effect.
enforced-by: tests/store/test_forks.py::test_retry_reopens_downstream_gates_only, tests/executor/test_retry_scopes.py::test_node_retry_reruns_that_node_and_later_work, tests/executor/test_retry_scopes.py::test_a_retry_after_a_gate_keeps_its_decision

## REQ retry-overrides-are-policy-bounded

An operator MAY change task configuration or policy for a retry when the
change is valid for that task and within the applicable policy bounds. A retry
SHALL NOT change the chain's structure, identifiers, order, or task kinds.
enforced-by: tests/templates/test_retry_override.py::test_a_task_override_narrows_the_task_and_is_written_into_its_scope, tests/templates/test_retry_override.py::test_an_override_the_task_or_its_policy_bounds_refuse_names_its_field[widens-the-inherited-allowlist], tests/templates/test_retry_override.py::test_an_override_the_task_or_its_policy_bounds_refuse_names_its_field[renames-the-task], tests/templates/test_retry_override.py::test_an_override_the_task_or_its_policy_bounds_refuse_names_its_field[changes-the-task-kind], tests/templates/test_retry_override.py::test_an_override_the_task_or_its_policy_bounds_refuse_names_its_field[adds-structure], tests/templates/test_retry_override.py::test_a_harness_change_is_held_to_the_paths_allowed_harnesses, tests/api/test_retry_paths.py::test_an_override_within_bounds_is_applied_to_the_fork, tests/api/test_retry_paths.py::test_an_override_out_of_bounds_forks_nothing, tests/api/test_retry_paths.py::test_a_retry_the_route_cannot_honour_is_refused_naming_the_field[override-changes-the-kind], tests/api/test_retry_paths.py::test_a_retry_the_route_cannot_honour_is_refused_naming_the_field[override-policy-of-the-wrong-type], tests/templates/test_forks.py::test_a_validated_override_is_the_forks_copy_and_its_record, tests/api/test_retry_paths.py::test_a_deferred_self_retry_carries_the_validated_override, tests/api/test_retry_paths.py::test_a_deferred_self_retry_refuses_an_override_out_of_bounds, tests/executor/test_escalation.py::test_a_self_retry_applies_the_override_it_carried, tests/templates/test_item_policy.py::test_a_retry_is_bounded_by_the_items_own_layer
origin: src/kraft/templates/retry.py §validate_retry_override -- the validation half (Task 8a). The retry route validates with it before the claim and forks nothing on a refusal, and the fork stores the validator's copy of the chain as its own materialization (Task 8b: `api/routes/lifecycle.py` §_retry_override, `templates/forks.py` §RunFork.from_retry).

## REQ task-step-and-node-are-skippable-by-default

An operator MAY skip a task, step, or node unless that component explicitly
disallows skipping.
enforced-by: tests/templates/test_forks.py::test_every_component_is_skippable_unless_it_says_otherwise[build.compile.lint-True], tests/templates/test_forks.py::test_every_component_is_skippable_unless_it_says_otherwise[build.compile.cc-False], tests/api/test_skip.py::test_skipping_a_task_or_step_records_it_and_walks_on_from_the_cursor[task], tests/api/test_skip.py::test_a_node_that_disallows_skipping_is_not_skipped[current-node], tests/api/test_skip.py::test_a_task_that_disallows_skipping_is_not_skipped, tests/api/test_skip.py::test_a_pending_gate_that_disallows_skipping_is_not_skipped

## REQ skip-stops-only-the-selected-scope

Before applying a skip, the system SHALL stop active work only within the
selected task, step, or node. Skipping a task SHALL NOT skip its sibling
tasks; an operator MAY skip the step when they intend to skip the group.
enforced-by: tests/executor/test_skip_scopes.py::test_skip_task_does_not_skip_sibling, tests/api/test_skip.py::test_skipping_a_task_stops_only_its_own_session, tests/store/test_forks.py::test_the_sessions_under_a_path_stop_at_its_separator

## REQ manual-completion-is-an-explicit-work-item-terminal-action

An operator MAY explicitly mark a work item complete. The action SHALL require
a reason, stop active work, record an audit event, and prevent further chain
execution.
enforced-by: tests/api/test_terminal_actions.py::test_a_terminal_action_requires_a_reason[missing-complete], tests/api/test_terminal_actions.py::test_a_terminal_action_ends_the_item_and_records_why[complete], tests/api/test_terminal_actions.py::test_a_terminal_action_stops_active_work[complete], tests/test_operator_surface.py::test_a_terminal_action_is_a_work_item_action_that_needs_a_reason[complete], tests/api/test_terminal_actions.py::test_manual_completion_closes_beads_only_when_asked[by-default], tests/api/test_terminal_actions.py::test_manual_completion_closes_beads_only_when_asked[opted-in], tests/api/test_terminal_actions.py::test_no_door_runs_an_ended_items_chain_again[approve-complete], tests/api/test_terminal_actions.py::test_no_door_runs_an_ended_items_chain_again[reject-complete], tests/api/test_terminal_actions.py::test_ending_an_item_closes_its_pending_gate[complete], tests/executor/test_entry_paths.py::test_no_entry_into_the_walk_runs_an_ended_item[walk-complete-completed], tests/executor/test_entry_paths.py::test_no_entry_into_the_walk_runs_an_ended_item[crash-resume-complete-completed], tests/test_waits.py::test_an_item_ended_as_its_wait_came_due_stays_ended[complete-completed]

## REQ manual-cancellation-is-an-explicit-work-item-terminal-action

An operator MAY explicitly cancel a work item. The action SHALL require a
reason, stop active work, record an audit event, and prevent further chain
execution.
enforced-by: tests/api/test_terminal_actions.py::test_a_terminal_action_requires_a_reason[missing-cancel], tests/api/test_terminal_actions.py::test_a_terminal_action_ends_the_item_and_records_why[cancel], tests/api/test_terminal_actions.py::test_a_terminal_action_stops_active_work[cancel], tests/test_operator_surface.py::test_a_terminal_action_is_a_work_item_action_that_needs_a_reason[cancel], tests/api/test_terminal_actions.py::test_no_door_runs_an_ended_items_chain_again[approve-cancel], tests/api/test_terminal_actions.py::test_no_door_runs_an_ended_items_chain_again[reject-cancel], tests/api/test_terminal_actions.py::test_ending_an_item_closes_its_pending_gate[cancel], tests/executor/test_entry_paths.py::test_no_entry_into_the_walk_runs_an_ended_item[walk-cancel-abandoned], tests/executor/test_entry_paths.py::test_no_entry_into_the_walk_runs_an_ended_item[crash-resume-cancel-abandoned], tests/test_waits.py::test_an_item_ended_as_its_wait_came_due_stays_ended[cancel-abandoned]

## REQ manual-escalation-reuses-context-by-default

Manual escalation SHALL resume its previous escalation session by default so
the escalation agent retains the work item's prior context.
enforced-by: tests/test_escalate.py::test_dispatch_resumes_an_existing_thread, tests/test_escalate.py::test_a_resumed_escalation_keeps_its_original_runtime[resumed]

## REQ manual-escalation-may-start-fresh

An operator MAY request that a manual escalation start a new session instead
of resuming its prior one.
enforced-by: tests/test_escalate.py::test_dispatch_new_thread_starts_fresh_and_bumps_thread_number, tests/test_escalate.py::test_a_resumed_escalation_keeps_its_original_runtime[fresh]

## REQ exec-node-runs-then-advances

The executor SHALL run an execution node's task group or ordered steps and,
when they complete successfully, advance to the following ordered node.
enforced-by: tests/executor/test_walk.py::test_steps_are_ordered_while_tasks_inside_a_step_are_concurrent, tests/executor/test_walk.py::test_the_worktree_and_setup_command_are_prepared_without_an_env_node, tests/executor/test_walk.py::test_an_exec_node_that_completes_advances_to_the_next_exec_node

## REQ gate-node-opens-and-halts-execution

When the executor reaches a gate node, it SHALL open that gate and SHALL NOT
start the following node until the gate is approved.
enforced-by: tests/executor/test_gates.py::test_gate_node_halts_until_approved, tests/test_gates.py::test_walk_stops_at_first_gate

## REQ gate-approval-advances-to-next-node

When a gate is approved, the executor SHALL advance to the node after that
gate in the materialized chain.
enforced-by: tests/executor/test_gates.py::test_gate_approval_advances_to_the_node_after_the_gate, tests/executor/test_gates.py::test_a_walk_re_entered_at_an_approved_gate_passes_over_it, tests/executor/test_gates.py::test_a_resume_at_an_approved_gate_continues_past_it, tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve

## REQ gate-rejection-follows-gate-reject-target

When a gate is rejected, the executor SHALL apply that gate node's own
`reject_to` behaviour.
enforced-by: tests/executor/test_gates.py::test_gate_rejection_follows_its_own_reject_to, tests/executor/test_gates.py::test_a_rejection_with_no_reject_to_re_enters_the_execution_node_before_the_gate, tests/executor/test_gates.py::test_a_rejection_cannot_be_aimed_forward_past_the_gate, tests/executor/test_gates.py::test_a_fixed_verdict_re_enters_the_execution_node_before_the_gate, tests/api/test_gates.py::test_rejecting_the_final_gate_re_enters_at_implementation, tests/skills/test_gate_review.py::test_verdict_reenters_the_walk_at_the_right_node[reject-0], tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve
origin: src/kraft/executor/gates.py §reject_target -- a gate with no `reject_to` re-enters at the nearest preceding *execution* node, not at the gate itself. A V1 gate has no execution shape, so the old fallback dispatched nothing and re-requested the same gate, a ping-pong bounded only by the reject loop's cap; re-running the node that produced what the gate is about is what makes the Kraft-rv6i "measure the repair rather than trust it" rule hold at a gate. A gate with nothing before it falls back to itself, which is the one case where there is no work to re-measure.

## REQ gate-decision-is-recorded-with-its-note

When a gate is approved or rejected, the system SHALL record a `gate_approved`
or `gate_rejected` event naming the gate. A rejection SHALL carry the
reviewer's note: one without a note SHALL be refused and SHALL leave the gate
open, and the note SHALL reach the node the rejection re-enters.
enforced-by: tests/api/test_gates.py::test_a_gate_approval_is_recorded_naming_its_gate, tests/api/test_gates.py::test_gate_reject_requires_note_and_re_runs_the_producer, tests/test_gates.py::test_reject_records_the_note_and_reopen_flips_the_row, tests/test_planning_chain.py::test_spec_gate_offers_the_document_then_reject_and_approve, tests/test_planning_chain.py::test_a_rejected_plan_rerun_is_framed_as_a_revision, tests/skills/test_gate_review.py::test_verdict_reenters_the_walk_at_the_right_node[reject-0]
origin: src/kraft/api/routes/gates.py §reject_gate -- carried from the retired legacy gate spec (`docs/intent/gates.md`, deleted in Task 11b; Ruling 139a): its approve, reject-note and note-as-steer requirements describe behaviour V1 kept, in legacy vocabulary.

## REQ gate-rejection-is-bounded-by-its-reject-loop

Each rejection of a gate SHALL count against that gate's own reject loop. At
the loop's cap the system SHALL stop the work item for a human, naming the
loop, rather than re-run the rejected work.
enforced-by: tests/api/test_gates.py::test_gate_reject_is_bounded_by_its_reject_loop, tests/skills/test_gate_review.py::test_repeated_fixed_verdicts_breach_the_reject_loop
origin: src/kraft/executor/gates.py §reject_loop_key -- carried from the retired legacy gate spec's per-gate-name reject-loop requirements (Task 11b): V1 keys the loop by the gate node's own id, for every gate.

## REQ gate-control-does-not-generate-review-work

A gate node SHALL be a decision control point and SHALL NOT generate its own
review artifact. A preceding execution node SHALL generate any artifact a gate
uses.
enforced-by: tests/executor/test_gates.py::test_gate_node_halts_until_approved, tests/executor/test_gates.py::test_a_gate_shows_the_artifact_its_own_field_names

## REQ chain-finalized-remains-a-dedicated-marker

A gate with the dedicated `chain_finalized` marker SHALL retain Kraft's
chain-review behaviour; other gate nodes SHALL have ordinary pause and
approval behaviour.
enforced-by: tests/executor/test_gates.py::test_the_chain_finalized_marker_not_the_gate_name_selects_chain_review, tests/executor/test_gates.py::test_an_ordinary_gate_has_ordinary_pause_and_approval_behaviour, tests/templates/test_models.py::test_an_attachment_never_trims_the_chain_finalized_gate
origin: src/kraft/api/routes/gates.py §apply_approval -- the marker selects the final-review path, and what that path still does in V1 is refuse an approval whose review document was never written (every other gate is answerable with nothing to read). Splicing a reviewer's revised nodes back in is **not** part of V1: the parked legacy splice was deleted in Task 11b, and revising a *materialized* chain in place has no V1 requirement or schema yet (Kraft-x2hdr).

## REQ gate-auto-review-is-explicit-and-bounded

A gate node MAY declare an automated review task that reports a verdict on the
gate's decision. The system SHALL run it only with work-item opt-in and within
its effective delay and attempt limits. Such a task SHALL report a verdict for
the system to apply and SHALL NOT itself approve or reject the gate.

This is distinct from two neighbouring behaviours it is easy to conflate.
`gate-control-does-not-generate-review-work` forbids a gate *producing the
artifact* it shows, which a verdict-reporting reviewer does not do — it reads an
artifact an earlier execution node produced.
`automated-review-is-an-explicit-optional-task` is a chain-level task that waits
on the *forge's* merge-request review, which is a different subject entirely.
enforced-by: tests/executor/test_gates.py::test_auto_review_runs_only_with_work_item_opt_in, tests/executor/test_gates.py::test_auto_review_waits_out_its_effective_delay, tests/executor/test_gates.py::test_auto_review_stops_at_its_effective_attempt_limit, tests/executor/test_gates.py::test_auto_review_reports_a_verdict_and_cannot_clear_its_own_gate, tests/executor/test_gates.py::test_a_node_override_permits_or_suppresses_the_declared_auto_review, tests/executor/test_gates.py::test_an_override_cannot_switch_on_a_gate_that_declares_no_auto_review, tests/executor/test_gates.py::test_a_gate_cannot_declare_a_reviewer_that_cannot_report_a_verdict, tests/executor/test_gates.py::test_an_unpaired_skip_counts_as_its_own_attempt, tests/executor/test_gates.py::test_an_override_that_cannot_do_anything_is_refused_at_the_door
origin: src/kraft/templates/models.py §GateNode -- "declare" is one field, `auto_review: AnyTask | None`, not a boolean plus a name: a bare `auto_escalate: true` could only mean "Kraft's own default reviewer", which is the name indirection V1 deletes. "Effective" is policy vocabulary and stays policy-owned -- the delay is `policy.auto_escalate_delay_s` folded through `store.effective_auto_escalate_delay_s`, the attempt bound is `policy.auto_review_attempts` -- so neither is duplicated onto the node. The per-item override keeps its persisted, publicly exposed key name `auto_escalate` (`db.py`'s `work_items.node_overrides`, `set_node_overrides`) and can only *suppress*: it names no task, so it cannot arm a gate that declares none. "SHALL NOT itself approve or reject" is enforced by dispatching the reviewer as a worker (`run_agent_task`'s `identify_as_worker` default, which sets `KRAFT_WORK_ITEM_ID` and therefore `client.context._forbid_self_action`); the pinned test asserts nothing overrides that default rather than re-testing the client guard.

## REQ attachment-behaviour-is-explicit-gate-configuration

Spec and plan attachment behaviour SHALL be configured explicitly on their
gate nodes and SHALL NOT be inferred from a preceding execution node.
enforced-by: tests/templates/test_models.py::test_an_attachment_drops_the_gate_that_decides_it_and_its_producing_node, tests/templates/test_models.py::test_an_attachment_does_not_drop_a_gate_no_attachment_kind_names, tests/templates/test_library.py::test_lint_refuses_a_node_mixing_tasks_with_and_without_produces, tests/templates/test_library.py::test_lint_refuses_a_node_whose_tasks_produce_two_different_kinds
origin: src/kraft/templates/models.py §trim_for_attachments -- both ends are declared: the gate's own `artifact:` and the producing node's tasks' `produces:`. The kind-to-gate-*name* table this replaces (`templates.ATTACHMENT_GATES`) is deleted. The producing node is dropped as well as the gate, which legacy got for free by having them be one node. Intake reaches it through `ResolvedChain.materialize(attachment_kinds=...)` (`api/routes/work_items.py`, `executor/entry.py`), which is the path the model pins exercise; `trim_for_attachments` is a wrapper with no `src/` caller.

## REQ task-recovery-retries-only-the-task

When a task-level recovery succeeds, the system SHALL retry only the failed
task.
enforced-by: tests/executor/test_recovery.py::test_a_task_recovery_retries_only_the_failed_task, tests/executor/test_recovery.py::test_a_task_recovery_is_told_the_failure_it_repairs

## REQ step-recovery-retries-the-entire-step

When a step-level recovery succeeds, the system SHALL retry every task in the
failed concurrent step and SHALL NOT rerun preceding successful steps.
enforced-by: tests/executor/test_recovery.py::test_a_step_recovery_reruns_every_task_in_the_step_and_no_earlier_step, tests/executor/test_recovery.py::test_a_step_handler_reruns_a_task_its_own_handler_already_recovered

## REQ node-recovery-retries-the-entire-node

When an execution-node recovery succeeds, the system SHALL retry that node
from its first step.
enforced-by: tests/executor/test_recovery.py::test_a_node_recovery_reruns_the_node_from_its_first_step

## REQ parallel-step-settles-before-recovery

When one task in a concurrent step fails, the system SHALL allow already
started sibling tasks to settle and SHALL NOT start a later step before
recovery begins.
enforced-by: tests/executor/test_recovery.py::test_a_concurrent_step_settles_before_recovery_begins, tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[paused-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[paused-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[paused-node], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[budget-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[budget-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[budget-node], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[rate_limited-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[rate_limited-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[rate_limited-node], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[config_error-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[config_error-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[config_error-node], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[waiting-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[waiting-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[waiting-node], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[infra_stop-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[infra_stop-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[infra_stop-node], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[base_moved-task], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[base_moved-step], tests/executor/test_recovery.py::test_no_recovery_launches_while_a_sibling_in_the_step_stopped[base_moved-node], tests/executor/test_recovery.py::test_a_measure_without_a_spent_set_runs_no_handler

## REQ recovery-plan-supports-task-groups-or-steps

A recovery plan SHALL define exactly one of a concurrent task group or ordered
steps and SHALL NOT contain gates, nested recovery handlers, or a fix loop.
enforced-by: tests/templates/test_failure_controls.py::test_a_handler_cannot_nest_inside_another_control[recovery-task], tests/templates/test_failure_controls.py::test_a_handler_cannot_nest_inside_another_control[recovery-step], tests/templates/test_failure_controls.py::test_a_handler_cannot_nest_inside_another_control[fix-loop-task], tests/templates/test_failure_controls.py::test_a_handler_cannot_nest_inside_another_control[judge], tests/templates/test_failure_controls.py::test_a_handler_cannot_nest_inside_another_control[escalation], tests/templates/test_failure_controls.py::test_a_handler_cannot_nest_inside_another_control[on-conflict], tests/templates/test_failure_controls.py::test_a_recovery_plan_is_one_shape_with_no_gate_or_fix_loop, tests/templates/test_models.py::test_recovery_plan_and_fix_loop_also_require_one_execution_shape

## REQ nearest-recovery-handler-wins

For a task failure, the system SHALL select at most one recovery handler, in
task, step, then execution-node precedence order.
enforced-by: tests/executor/test_recovery.py::test_the_nearest_handler_wins_and_only_it_runs[task-wins], tests/executor/test_recovery.py::test_the_nearest_handler_wins_and_only_it_runs[step-wins], tests/executor/test_recovery.py::test_the_nearest_handler_wins_and_only_it_runs[node-only], tests/executor/test_recovery.py::test_a_stop_or_a_question_spends_no_recovery[paused], tests/executor/test_recovery.py::test_a_stop_or_a_question_spends_no_recovery[budget], tests/executor/test_recovery.py::test_a_stop_or_a_question_spends_no_recovery[rate_limited], tests/executor/test_recovery.py::test_a_stop_or_a_question_spends_no_recovery[needs_context]

## REQ recovery-tasks-run-after-a-concurrent-step-settles

When several failed tasks in one concurrent step have task-level recovery
handlers, the system SHALL run those recovery handlers only after that step has
settled and SHALL run them sequentially.
enforced-by: tests/executor/test_recovery.py::test_sibling_task_recoveries_run_one_at_a_time_after_the_step_settles

## REQ failed-recovery-enters-node-fix-loop-or-needs-human

When a selected recovery handler does not restore its scope, the system SHALL
enter the declaring execution node's fix loop when one exists, or otherwise
stop for a human.
enforced-by: tests/executor/test_recovery.py::test_a_recovery_that_does_not_take_enters_the_fix_loop_or_stops[fix-loop], tests/executor/test_recovery.py::test_a_recovery_that_does_not_take_enters_the_fix_loop_or_stops[no-fix-loop]

## REQ a-repair-with-concerns-stops-for-a-human

When a task, step or execution-node recovery handler finishes
`done_with_concerns`, the system SHALL stop for a human with the handler's
concerns as the reason and SHALL NOT measure the recovered scope again.
enforced-by: tests/executor/test_recovery.py::test_a_repair_with_concerns_stops_for_a_human_rather_than_re_measuring[task], tests/executor/test_recovery.py::test_a_repair_with_concerns_stops_for_a_human_rather_than_re_measuring[step], tests/executor/test_recovery.py::test_a_repair_with_concerns_stops_for_a_human_rather_than_re_measuring[node], tests/executor/test_base_change.py::test_a_conflict_handler_s_concerns_do_not_stop_its_resolved_rebase
origin: src/kraft/executor/dispatch.py §run_recovery -- for an ordinary task doubts are information for the next gate (`_ADVANCING`); for a repair they are a verdict on whether repair was possible at all (Kraft-s7c04.56, b5afe84c). A conflict handler is excluded: its `done_with_concerns` means "resolved, but upstream touches this item" and rides `CONFLICT_RESOLVED` into the span's reopened gates.

## REQ fix-loop-is-an-exec-node-control

A fix loop SHALL be configured only on an execution node and SHALL name its
fixing tasks explicitly.
enforced-by: tests/templates/test_failure_controls.py::test_a_fix_loop_is_an_execution_node_control_naming_its_fixing_tasks

## REQ fix-loop-supports-one-ordered-repair-shape

A fix loop SHALL define exactly one concurrent task group or ordered steps.
enforced-by: tests/templates/test_models.py::test_recovery_plan_and_fix_loop_also_require_one_execution_shape, tests/templates/test_models.py::test_a_fix_loop_tasks_group_resolves_to_a_main_step_under_its_container, tests/executor/test_fix_loop_outcomes.py::test_a_failed_sync_step_stops_naming_it_without_spending_an_attempt

## REQ fix-loop-remeasures-the-whole-node

After each successful fix-loop attempt, the system SHALL rerun the execution
node from its first step before deciding whether another attempt is needed.
enforced-by: tests/executor/test_walk.py::test_a_fix_loop_attempt_remeasures_the_node_from_its_first_step

## REQ fix-loop-is-bounded-and-detects-stall

A fix loop SHALL enforce its effective attempt and duration limits and SHALL
stop for a human when the loop is exhausted or detects that it is not making
progress.
enforced-by: tests/test_fix_loop.py::test_fix_loop_cap_breach, tests/test_fix_loop.py::test_fix_loop_wall_clock_breach, tests/test_fix_loop_memory.py::test_a_reworded_repeat_is_recognised_as_the_same_finding, tests/skills/test_fix_loop_judge.py::test_stuck_fingerprint_found_once_it_survives_enough_fixes, tests/executor/test_seeded_failure_walk.py::test_a_failing_item_walks_recovery_then_the_fix_loop_then_escalation, tests/executor/test_fix_loop_outcomes.py::test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause[rate_limited-rate_limited-rate_limited], tests/executor/test_fix_loop_outcomes.py::test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause[waiting-waiting-waiting], tests/executor/test_fix_loop_outcomes.py::test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause[infra_stop-needs_human-needs_human], tests/executor/test_fix_loop_outcomes.py::test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause[config_error-needs_human-needs_human], tests/executor/test_fix_loop_outcomes.py::test_a_repair_that_never_ran_spends_no_attempt_and_names_its_own_cause[paused-paused-active], tests/executor/test_fix_loop_outcomes.py::test_a_repair_that_ran_and_failed_is_a_spent_attempt, tests/executor/test_fix_loop_outcomes.py::test_a_refunded_cycle_does_not_read_as_no_progress_on_re_entry

## REQ fix-loop-judge-is-optional

A fix loop MAY declare a judge. Without a judge, the system SHALL repeat
measurement and fixing until the node is clean or the loop reaches another
stopping condition.
enforced-by: tests/test_fix_loop.py::test_fix_loop_succeeds_first_cycle, tests/skills/test_fix_loop_judge.py::test_judge_verdict_fails_open_when_the_hook_is_not_registered

## REQ fix-loop-judge-runs-after-the-first-attempt

When configured, a fix-loop judge SHALL assess a measured result only after at
least one fixing attempt has run.
enforced-by: tests/skills/test_fix_loop_judge.py::test_round_one_fixes_freely_no_judge_call, tests/skills/test_fix_loop_judge.py::test_retry_fixes_freely_no_judge_call_on_the_first_post_retry_cycle

## REQ fix-loop-judge-has-three-decisions

A fix-loop judge SHALL decide whether to continue fixing, accept a clean
execution result, or stop for a human.
enforced-by: tests/skills/test_fix_loop_judge.py::test_judge_continue_behaves_like_no_judge_present, tests/skills/test_fix_loop_judge.py::test_judge_stop_needs_human_preempts_the_cap, tests/skills/test_fix_loop_judge.py::test_judge_stop_downgrade_exits_the_loop_clean, tests/skills/test_fix_loop_judge.py::test_judge_stop_downgrade_does_not_downgrade_a_failing_task, tests/executor/test_agent_inputs.py::test_the_seeded_fix_loop_judge_declares_the_package_and_its_method

## REQ fix-loop-judge-cannot-override-limits

A fix-loop judge SHALL NOT cause the system to exceed the loop's effective
attempt or duration limits.
enforced-by: tests/skills/test_fix_loop_judge.py::test_judge_continue_behaves_like_no_judge_present

## REQ invalid-judge-result-does-not-block-the-loop

When a fix-loop judge cannot provide a valid decision, the system SHALL
continue under the loop's ordinary limits and stall detection.
enforced-by: tests/skills/test_fix_loop_judge.py::test_judge_result_resolution[done-STOP EVERYTHING-continue], tests/skills/test_fix_loop_judge.py::test_judge_result_resolution[done-None-continue], tests/skills/test_fix_loop_judge.py::test_judge_result_resolution[failed-stop_needs_human-continue], tests/skills/test_fix_loop_judge.py::test_judge_result_resolution[needs_context-continue-continue], tests/skills/test_fix_loop_judge.py::test_judge_dispatch_failure_falls_open_to_continue

## REQ stuck-escalation-is-an-exec-node-control

An execution node MAY declare a bounded escalation task that runs only after
its recovery and fix-loop controls cannot advance the node.
enforced-by: tests/executor/test_stuck_escalation.py::test_escalation_runs_only_after_recovery_and_the_fix_loop_and_retries_the_node, tests/executor/test_stuck_escalation.py::test_escalation_is_bounded_per_node, tests/executor/test_stuck_escalation.py::test_a_stop_the_controls_did_not_reach_is_not_escalated[needs_context], tests/executor/test_stuck_escalation.py::test_a_stop_the_controls_did_not_reach_is_not_escalated[config_error], tests/executor/test_stuck_escalation.py::test_a_stuck_stop_is_escalated_by_exactly_one_mechanism[stall-declared], tests/executor/test_stuck_escalation.py::test_a_stuck_stop_is_escalated_by_exactly_one_mechanism[judge stop_needs_human-declared], tests/executor/test_stuck_escalation.py::test_a_stop_outside_the_stuck_set_goes_straight_to_a_human[infra stop-declared], tests/executor/test_stuck_escalation.py::test_a_stop_outside_the_stuck_set_goes_straight_to_a_human[budget-declared]

## REQ successful-stuck-escalation-retries-the-node

When a stuck escalation succeeds, the system SHALL retry that execution node
from its first step.
enforced-by: tests/executor/test_stuck_escalation.py::test_escalation_runs_only_after_recovery_and_the_fix_loop_and_retries_the_node

## REQ failed-or-questioning-stuck-escalation-needs-human

When a stuck escalation fails or asks a question, the system SHALL leave the
work item for a human.
enforced-by: tests/executor/test_stuck_escalation.py::test_a_failed_or_questioning_escalation_leaves_the_item_for_a_human[failed], tests/executor/test_stuck_escalation.py::test_a_failed_or_questioning_escalation_leaves_the_item_for_a_human[needs_context], tests/executor/test_stuck_escalation.py::test_the_generic_auto_escalation_does_not_follow_a_declared_one

## REQ base-change-restarts-a-declared-chain-span

An execution node MAY declare `on_base_changed.restart_from` to restart the
chain at an earlier execution node when its work changes the worktree base.
enforced-by: tests/executor/test_base_change.py::test_a_moved_base_restarts_the_declared_span_and_spends_no_attempt, tests/executor/test_base_change.py::test_a_base_moved_stop_skips_the_nodes_later_steps_until_the_restart[loopless], tests/executor/test_base_change.py::test_a_base_moved_stop_skips_the_nodes_later_steps_until_the_restart[fix-loop], tests/executor/test_base_change.py::test_no_movement_and_no_declaration_mean_no_restart, tests/executor/test_base_change.py::test_restarts_are_bounded_by_the_nodes_own_counter

## REQ base-change-is-not-an-execution-failure

When `on_base_changed` applies, the system SHALL restart the declared chain
span without spending a recovery attempt or fix-loop attempt on that base
change.
enforced-by: tests/executor/test_base_change.py::test_a_moved_base_restarts_the_declared_span_and_spends_no_attempt, tests/executor/test_base_change.py::test_a_base_moved_stop_skips_the_nodes_later_steps_until_the_restart[loopless], tests/executor/test_base_change.py::test_a_base_moved_stop_skips_the_nodes_later_steps_until_the_restart[fix-loop], tests/executor/test_base_change.py::test_a_base_moved_stop_on_an_undeclared_node_completes_it_and_spends_nothing[loopless], tests/executor/test_base_change.py::test_a_base_moved_stop_on_an_undeclared_node_completes_it_and_spends_nothing[fix-loop]

## REQ base-change-restart-target-is-backward

The system SHALL reject an `on_base_changed.restart_from` target that is
missing, is not an execution node, or is later than the declaring node.
enforced-by: tests/templates/test_failure_controls.py::test_a_restart_target_must_be_this_or_an_earlier_execution_node[missing], tests/templates/test_failure_controls.py::test_a_restart_target_must_be_this_or_an_earlier_execution_node[gate], tests/templates/test_failure_controls.py::test_a_restart_target_must_be_this_or_an_earlier_execution_node[later], tests/templates/test_failure_controls.py::test_a_backward_restart_target_is_accepted[earlier], tests/templates/test_failure_controls.py::test_a_backward_restart_target_is_accepted[itself]

## REQ rebase-conflict-requires-explicit-handler

The system SHALL attempt automatic rebase-conflict resolution only when the
relevant execution node's `on_base_changed` configuration declares an explicit
`on_conflict` handler.
enforced-by: tests/executor/test_base_change.py::test_a_conflict_without_an_explicit_handler_is_an_ordinary_failure, tests/executor/test_base_change.py::test_a_conflict_handler_that_rebases_restarts_the_declared_span, tests/executor/test_base_change.py::test_a_conflict_at_the_door_with_no_handler_stops_for_a_human, tests/api/test_retry_paths.py::test_a_refresh_conflict_at_the_door_is_handed_to_the_walk[retry], tests/api/test_retry_paths.py::test_a_refresh_conflict_at_the_door_is_handed_to_the_walk[resume]

## REQ resolved-conflict-restarts-from-base-change-target

When an explicit conflict handler resolves a conflict and changes the worktree
base, the system SHALL apply that node's `on_base_changed` restart behaviour.
enforced-by: tests/executor/test_base_change.py::test_a_conflict_handler_that_rebases_restarts_the_declared_span, tests/executor/test_base_change.py::test_a_conflict_handler_that_did_not_resolve_it_stops_for_a_human[did-not-rebase], tests/executor/test_base_change.py::test_a_conflict_handler_that_did_not_resolve_it_stops_for_a_human[failed], tests/executor/test_base_change.py::test_a_conflict_handler_that_did_not_resolve_it_stops_for_a_human[asked]

## REQ policy-is-layered-by-execution-scope

The effective task policy SHALL resolve from instance policy through repository,
chain, node, step, and task policy overrides, from broadest scope to narrowest
scope, and then through the work item's own override (Ruling 188). The work
item's operational values (timeouts, retry and wait timing, harness selection)
SHALL apply after the chain's scopes, so they replace what the chain authored
within the administrator maxima. Its safety values SHALL combine independently
of order: an allowlist intersects, a deny list unions, and a sandbox, once set,
cannot change. A work item's safety value SHALL therefore only tighten, and
SHALL NOT be refused because a narrower scope already narrowed the same field.
A cap -- a time cap or a budget, each capping its own scope (Rulings 194, 195)
-- SHALL tighten every looser scope under it and SHALL be refused above the cap
it lands on.
enforced-by: tests/test_policy.py::test_policy_overrides_compose_and_a_narrower_layer_cannot_widen_a_broader_one, tests/templates/test_policy_scopes.py::test_a_narrower_scope_narrows_what_it_inherits, tests/templates/test_policy_scopes.py::test_each_task_resolves_policy_from_the_scopes_it_sits_in[task], tests/templates/test_policy_scopes.py::test_each_task_resolves_policy_from_the_scopes_it_sits_in[task-recovery-sits-in-its-task], tests/templates/test_policy_scopes.py::test_each_task_resolves_policy_from_the_scopes_it_sits_in[fix-loop-sits-in-its-node], tests/templates/test_policy_scopes.py::test_each_task_resolves_policy_from_the_scopes_it_sits_in[auto-review-sits-in-its-gate], tests/api/test_repository_policy.py::test_the_repository_layer_folds_in_the_entrys_own_deny_tools_and_sandbox, tests/templates/test_item_policy.py::test_an_item_override_binds_the_scopes_it_addresses_and_touches_nothing_stored, tests/templates/test_item_policy.py::test_an_items_safety_value_only_tightens_whatever_it_lands_on[allowlist-wider-than-a-narrower-task-intersects], tests/templates/test_item_policy.py::test_an_override_past_its_bounds_is_refused_naming_the_field[budget-above-the-ceiling], tests/templates/test_item_policy.py::test_a_retry_may_narrow_a_task_below_the_items_allowlist
origin: src/kraft/templates/models.py §MaterializedChain.policy_for -- the chain's policy (instance → repository → chain, folded at materialization) with each enclosing node, step and task override applied, then the work item's own override (`policy.WorkItemPolicy.apply_to`, Kraft-ab1bh): item-wide, then each enclosing path's. Ruling 188 settles the order: the spec's instance → repository → work item → chain would let any value the chain authors override the item's, so an operator could not lengthen an authored wait or raise an authored fix-loop cap; applying the item layer last for its operational values keeps that, and meeting its safety values keeps "only tightens" without a refusal that depends on which scope narrowed first.

## REQ policy-has-defaults-and-administrator-maxima

The instance policy SHALL distinguish inheritable operational defaults from
non-overridable administrator safety maxima. A `defaults:` value SHALL NOT
exceed its `maxima:` counterpart. A `maxima:` field left unset SHALL bound
nothing: `allowed_harnesses` with no administrator maximum permits an override
naming any harness.
enforced-by: tests/test_policy.py::test_template_policy_cannot_widen_allowed_tools, tests/test_policy.py::test_work_item_policy_may_exceed_default_within_admin_maximum, tests/test_policy.py::test_default_timeout_above_its_maximum_is_rejected, tests/test_policy.py::test_default_max_attempts_above_its_maximum_is_rejected, tests/test_policy.py::test_default_harnesses_outside_its_maximum_are_rejected, tests/test_policy.py::test_unset_harness_maximum_bounds_nothing

## REQ template-policy-cannot-relax-safety-ceilings

Template policy overrides SHALL only tighten inherited safety ceilings,
including budgets, allowed tools, permissions, and repository access.

The split between a safety ceiling and an operational value is a rule, not the
membership of these examples. A **safety ceiling** may only tighten, against the
inherited value: `allowed_tools`. A **budget** (`token_budget`, `budget_usd`) is
a scope's own cap: it may not exceed its parent scope's or the administrator
maximum, but an instance or repository default is not a parent (Rulings 195,
198). An **operational value** may
move in either direction, bounded by an explicitly configured administrator
maximum rather than by the inherited value: timeouts, retry and wait timing, and
`allowed_harnesses`. A field absent from `maxima:` is unbounded.
enforced-by: tests/test_policy.py::test_template_policy_cannot_widen_allowed_tools, tests/test_policy.py::test_template_policy_can_narrow_allowed_tools, tests/test_policy.py::test_template_policy_cannot_exceed_token_budget_ceiling, tests/test_policy.py::test_token_budget_moves_within_the_maximum_not_the_inherited_value, tests/test_policy.py::test_deny_tools_only_accumulate_down_the_layers, tests/test_policy.py::test_a_sandbox_once_set_cannot_be_changed_by_a_narrower_layer, tests/templates/test_policy_scopes.py::test_materialization_refuses_a_scope_that_relaxes_what_it_inherits[node-widens-chain], tests/templates/test_policy_scopes.py::test_materialization_refuses_a_scope_that_relaxes_what_it_inherits[task-widens-step], tests/templates/test_policy_scopes.py::test_materialization_refuses_a_scope_that_relaxes_what_it_inherits[step-raises-node-budget], tests/templates/test_policy_scopes.py::test_materialization_refuses_a_scope_that_relaxes_what_it_inherits[task-swaps-step-sandbox], tests/test_permissions.py::test_permission_request_answers_from_the_tasks_resolved_policy[empty-allowlist], tests/test_permissions.py::test_permission_request_answers_from_the_tasks_resolved_policy[denied-beats-allowlisted], tests/executor/test_policy_enforcement.py::test_a_tasks_resolved_tool_policy_reaches_its_launch, tests/executor/test_policy_enforcement.py::test_token_budget_refuses_the_next_agent_launch[at]

## REQ sandbox-wraps-the-whole-work-item

A sandbox set at any scope of a work item SHALL wrap every project-controlled
launch of that item: every task, recovery, judge, escalation turn, gate review,
test scope, area setup and the repository's `setup_command`. No launch SHALL
run on the host once the item has a sandbox, and a missing sandbox runtime
SHALL stop the item for a human rather than fall back to the host. Two
scopes setting different sandboxes SHALL be refused when the chain is built,
naming both scopes; a work item's own policy override is one of those scopes.
A launch whose sandbox cannot be resolved SHALL stop as its own `config_error`
session naming the cause, and launch nothing.
enforced-by: tests/executor/test_policy_enforcement.py::test_a_task_whose_sandbox_cannot_be_resolved_stops_as_its_own_session[subprocess], tests/executor/test_policy_enforcement.py::test_a_task_whose_sandbox_cannot_be_resolved_stops_as_its_own_session[builtin], tests/executor/test_policy_enforcement.py::test_a_task_whose_sandbox_cannot_be_resolved_stops_as_its_own_session[agent], tests/executor/test_policy_enforcement.py::test_a_gate_review_whose_sandbox_cannot_be_resolved_stops_as_its_own_session, tests/executor/test_policy_enforcement.py::test_an_escalation_whose_sandbox_cannot_be_resolved_stops_as_its_own_session[manual], tests/executor/test_policy_enforcement.py::test_a_work_items_own_sandbox_wraps_the_whole_item, tests/executor/test_policy_enforcement.py::test_a_work_items_sandbox_that_conflicts_with_the_chains_is_refused, tests/executor/test_policy_enforcement.py::test_a_sandbox_on_one_task_wraps_every_launch_of_the_item[sandbox0-expected0], tests/executor/test_policy_enforcement.py::test_a_sandbox_on_one_task_wraps_every_launch_of_the_item[None-None], tests/executor/test_policy_enforcement.py::test_an_escalation_turn_runs_in_a_sandbox_another_node_set[manual], tests/executor/test_policy_enforcement.py::test_a_gate_reviewer_runs_in_a_sandbox_another_node_set, tests/executor/test_policy_enforcement.py::test_two_scopes_asking_for_different_sandboxes_are_refused_at_build[another-task], tests/executor/test_policy_enforcement.py::test_two_scopes_asking_for_different_sandboxes_are_refused_at_build[another-node], tests/executor/test_policy_enforcement.py::test_an_item_filed_with_two_sandboxes_stops_rather_than_pick_one, tests/executor/test_setup_in_sandbox.py::test_the_walk_runs_both_setups_in_the_items_sandbox[entry0-expected0], tests/executor/test_setup_in_sandbox.py::test_a_sandboxed_item_without_docker_stops_for_a_human, tests/executor/test_setup_in_sandbox.py::test_test_scopes_and_their_area_setup_launch_in_the_items_sandbox[entry0-expected0]
origin: src/kraft/executor/dispatch.py §item_sandbox -- Omid's decision (Ruling 189, Kraft-h10e5, Kraft-p8nem): a sandbox is a safety ceiling that only tightens, and once one task has run in it the worktree is the worker's to write, so every later host-side launch would run what it wrote. `item_sandbox` is the one resolution every launch reads; `ResolvedChain.check_scopes` refuses two.


## REQ host-git-never-runs-worker-planted-code

Host-side git that Kraft runs on a work item's worktree SHALL NOT run a
program or load config that a sandboxed worker could have written. It SHALL
NOT enter a repository nested in the worktree. A sandboxed item whose worktree
holds a nested repository Kraft did not create SHALL stop for a human, naming
its paths, before host git touches it. While a sandboxed session is live, no
host git SHALL read that worktree.
enforced-by: tests/worker/test_host_git_trust.py::test_assert_clean_never_runs_a_filter_planted_behind_a_gitlink, tests/worker/test_host_git_trust.py::test_no_host_call_runs_a_program_planted_in_a_nested_repository[filter-clean], tests/worker/test_host_git_trust.py::test_no_host_call_runs_a_program_planted_in_a_nested_repository[filter-process], tests/worker/test_host_git_trust.py::test_no_host_call_runs_a_program_planted_in_a_nested_repository[textconv], tests/worker/test_host_git_trust.py::test_no_host_call_runs_a_program_planted_in_a_nested_repository[diff-command], tests/worker/test_host_git_trust.py::test_no_host_call_runs_a_program_planted_in_a_nested_repository[include-path], tests/worker/test_planted_repos.py::test_the_hardened_environment_pins_every_recursion_setting_and_the_hooks, tests/worker/test_planted_repos.py::test_push_lifts_only_the_hooks_path_and_keeps_every_recursion_pin, tests/worker/test_planted_repos.py::test_the_sweep_leaves_an_undeclared_nested_repository_out, tests/worker/test_planted_repos.py::test_the_sweep_stages_a_declared_mount_and_never_enters_it_on_status, tests/worker/test_planted_repos.py::test_a_planted_repository_stops_a_sandboxed_item_naming_its_path, tests/worker/test_planted_repos.py::test_the_walk_entry_guard_delegates_a_plain_item_to_the_planted_scan, tests/worker/test_planted_repos.py::test_an_unreadable_index_stops_rather_than_reads_as_clean, tests/worker/test_planted_repos.py::test_host_git_waits_only_for_a_live_sandboxed_session[live-and-sandboxed], tests/executor/test_planted_repo_stops.py::test_a_planted_repository_stops_the_next_task_before_it_launches[sandboxed], tests/executor/test_planted_repo_stops.py::test_the_review_package_is_not_read_while_a_sandboxed_co_task_runs[sandboxed], tests/executor/test_planted_repo_stops.py::test_the_straggler_sweep_leaves_a_sandboxed_worktree_alone[sandboxed-live], tests/executor/test_planted_repo_stops.py::test_the_straggler_sweep_leaves_a_sandboxed_worktree_alone[sandboxed-planted], tests/executor/test_planted_repo_stops.py::test_the_diagnosis_bundle_reads_no_status_while_a_sandboxed_session_runs[sandboxed], tests/api/test_diff.py::test_diff_waits_for_a_live_sandboxed_session[sandboxed]
origin: src/kraft/worker/sandbox.py -- Kraft-dshto, Kraft-nx4id, Kraft-69rwp (Ruling 194 option (a)); review-g1 finding 1.
## REQ policy-tool-lists-hold-tool-names

A policy's `allowed_tools` and `deny_tools` SHALL hold only tool names the
permission gate can match exactly: a bare tool or one exact MCP tool. A scoped
rule, a glob or a whole MCP server SHALL be refused wherever policy loads,
naming the field and the name to write instead. A snapshot frozen before the
refusal SHALL still read, and its launch SHALL stop naming the field.
enforced-by: tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[instance-maxima], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[repository-policy-allowed], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[repository-policy-denied], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[repository-entry-denied], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[template-allowed], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[template-denied], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[retry-override-allowed], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[retry-override-denied], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[work-item-allowed], tests/test_policy_tool_names.py::test_every_policy_door_refuses_a_rule_naming_the_field[work-item-denied], tests/test_policy_tool_names.py::test_a_tool_list_holds_only_names_the_gate_can_match[allowed_tools-scoped-rule], tests/test_policy_tool_names.py::test_a_tool_list_holds_only_names_the_gate_can_match[allowed_tools-mcp-glob], tests/test_policy_tool_names.py::test_a_tool_list_holds_only_names_the_gate_can_match[allowed_tools-mcp-server-rule], tests/test_policy_tool_names.py::test_a_tool_list_holds_only_names_the_gate_can_match[allowed_tools-mcp-tool], tests/test_policy_tool_names.py::test_a_snapshot_frozen_with_a_rule_still_renders_on_the_board, tests/executor/test_policy_enforcement.py::test_a_rule_frozen_into_a_snapshot_stops_its_launch_naming_the_field[allowed_tools], tests/executor/test_policy_enforcement.py::test_a_rule_frozen_into_a_snapshot_stops_its_launch_naming_the_field[deny_tools]
origin: src/kraft/policy.py §_tool_names -- Kraft-9i6xy: the gate (`sessions.permission_request`) compares names exactly and the harness restricts only bare tools, so a rule bounded nothing it claimed to; the ruling was to refuse rule syntax at load rather than teach the gate the CLI's rule matching.

## REQ repository-policy-cannot-relax-instance-safety

Repository policy overrides SHALL only tighten inherited safety ceilings and
SHALL remain effective for every chain and task that runs in that repository.
enforced-by: tests/api/test_repository_policy.py::test_a_repository_policy_cannot_relax_the_instance, tests/api/test_repository_policy.py::test_a_repository_policy_the_instance_refuses_is_a_422_at_intake[/api/work-items], tests/api/test_repository_policy.py::test_a_repository_policy_the_instance_refuses_is_a_422_at_intake[/api/triggers], tests/api/test_repository_policy.py::test_every_item_filed_in_a_repository_is_bound_by_its_policy, tests/api/test_repository_policy.py::test_an_unreadable_repos_yaml_refuses_rather_than_drops_the_layer, tests/test_intake_poller.py::test_an_auto_intaken_item_is_bound_by_its_repositorys_policy, tests/test_triggers.py::test_a_triggered_item_is_bound_by_its_repositorys_policy, tests/api/test_repository_policy.py::test_a_workspace_item_binds_each_repository_by_its_own_layer_and_the_checkout_by_all, tests/api/test_repository_policy.py::test_a_member_policy_the_instance_refuses_refuses_the_workspace_item, tests/api/test_repository_policy.py::test_a_filed_workspace_item_freezes_each_repositorys_policy, tests/test_policy.py::test_the_meet_of_repository_layers_is_the_tightest_of_each_field, tests/templates/test_workspace_fanout.py::test_each_repository_binds_its_own_task_and_the_checkout_binds_all, tests/executor/test_policy_enforcement.py::test_an_escalation_turn_launches_under_its_nodes_policy[manual], tests/executor/test_policy_enforcement.py::test_an_escalation_turn_launches_under_its_nodes_policy[auto], tests/executor/test_policy_enforcement.py::test_an_escalation_turn_its_nodes_policy_refuses_never_launches[token-budget-spent]
origin: src/kraft/api/deps.py §item_policy -- the repository layer is the entry's `policy:` block with its own `deny_tools`/`sandbox` folded in (Ruling 105, `config.repository_override`), layered onto the instance policy by every intake door and frozen into the item's snapshot at materialization.

## REQ repositories-workspaces-and-areas-are-distinct

The system SHALL distinguish an independent repository, a workspace that
combines repositories, and a path-scoped area within one repository. An area
SHALL NOT be treated as an independent repository or forge target.
enforced-by: tests/templates/test_environment.py::test_area_has_no_forge_field_to_declare, tests/templates/test_materialization.py::test_the_design_documents_repos_yaml_is_what_the_daemon_reads, tests/test_config_repos.py::test_load_repos_rejects_an_entry[an-area-naming-a-forge], tests/test_config_repos.py::test_a_top_level_repositories_key_fails_loudly
origin: src/kraft/config.py -- `repos.yaml` keeps the three in separate sections (the `repos:` list, `workspaces:` beside it naming entries by `id`, `areas:` only inside an entry), read by `load_repos`/`load_workspaces` into the one repository model, `RepoEntry` (Ruling 177), so the distinction holds at the file boundary and not only in the types.

## REQ workspace-declares-root-and-members

A workspace SHALL declare its root repository and each member repository with
the path where it is mounted in that root.
enforced-by: tests/templates/test_environment.py::test_workspace_declares_root_and_members, tests/test_config_repos.py::test_a_workspace_is_read_from_repos_yaml_by_repository_id, tests/test_config_repos.py::test_a_workspace_that_cannot_assemble_is_refused_at_load[an-unknown-root], tests/test_config_repos.py::test_a_workspace_that_cannot_assemble_is_refused_at_load[an-unknown-member-repository], tests/api/test_repos.py::test_connecting_a_workspace_declares_it_with_its_submodules_as_members, tests/api/test_repos.py::test_disconnecting_a_repository_a_workspace_mounts_is_refused
origin: src/kraft/config.py §load_workspaces -- both ends of every declaration are resolved when the file is read; a root or member naming no declared repository assembles an empty checkout at run time, hours after the typo.

## REQ workspace-members-are-not-nested

A workspace SHALL NOT declare a member whose mount path is inside another
member's, or is another member's. Loading `workspaces:` and filing a work item
against such a workspace SHALL be refused, naming the nested member and the
member that encloses it, or the two members sharing the mount.
enforced-by: tests/test_config_repos.py::test_a_workspace_that_cannot_assemble_is_refused_at_load[a-member-nested-inside-another], tests/test_config_repos.py::test_a_workspace_that_cannot_assemble_is_refused_at_load[two-members-at-one-mount], tests/api/test_workspace_intake.py::test_a_workspace_nesting_one_member_inside_another_is_a_422, tests/test_config_repos.py::test_a_workspace_is_read_from_repos_yaml_by_repository_id
origin: src/kraft/templates/environment.py §Workspace -- the root's `git submodule update` cannot reach a submodule inside a submodule, so a nested member failed only when its checkout was assembled (Kraft-z0wzd, option A). Supporting nesting is Kraft-37gnq. Two members at one mount cannot both be checked out there (Kraft-rx4n5).

## REQ work-item-target-selection-is-immutable

This requirement governs **selection at intake**; the run-time immutability of
what was selected is `work-item-target-is-typed-and-immutable`.

A work item MAY target one repository, selected members of a workspace, or a
workspace root and its members. The selected targets and root-pointer policy
SHALL be captured when the work item is materialized.
enforced-by: tests/templates/test_materialization.py::test_the_target_selection_survives_serialization, tests/templates/test_materialization.py::test_materialization_freezes_chain_policy_and_target, tests/api/test_workspace_intake.py::test_a_workspace_intake_freezes_its_selected_members_and_pointer_policy[the-workspace-default], tests/api/test_workspace_intake.py::test_a_workspace_selection_that_cannot_assemble_is_a_422[a-member-it-does-not-mount], tests/api/test_workspace_intake.py::test_a_workspace_is_filed_only_against_its_own_root

## REQ workspace-root-pointer-update-is-explicit

A workspace SHALL declare a default policy for submodule-pointer updates. A
work item MAY choose an allowed pointer-update policy for its selected target.
enforced-by: tests/api/test_workspace_intake.py::test_a_workspace_intake_freezes_its_selected_members_and_pointer_policy[the-workspace-default], tests/api/test_workspace_intake.py::test_a_workspace_intake_freezes_its_selected_members_and_pointer_policy[the-item-chooses], frontend/src/components/IntakeModal.test.tsx::sends the workspace with its picked members and the workspace's root pointer policy by default: bump, frontend/src/components/IntakeModal.test.tsx::sends the workspace with its picked members and the workspace's root pointer policy by default: ignore

## REQ workspace-root-pointer-update-defaults-to-ignore

The default workspace root-pointer policy SHALL leave the root repository
unchanged.
enforced-by: tests/templates/test_workspace_publication.py::test_the_default_root_pointer_policy_leaves_the_root_unchanged[workspace], tests/templates/test_workspace_publication.py::test_the_default_root_pointer_policy_leaves_the_root_unchanged[filed-before-workspaces-skip], tests/api/test_repos.py::test_connecting_a_workspace_declares_it_with_its_submodules_as_members, tests/adapters/forge/test_run_chain.py::test_the_shape_that_broke_on_9d0ab38ff3c9439b90506df0f6966660

## REQ workspace-pointer-bump-prefers-direct-push

When a selected root-pointer policy requests a bump without root source
changes, the system SHALL commit and push the pointer update directly to the
workspace root when permitted.
enforced-by: tests/templates/test_workspace_publication.py::test_child_merge_precedes_workspace_pointer_update[workspace], tests/templates/test_workspace_publication.py::test_child_merge_precedes_workspace_pointer_update[filed-before-workspaces]

## REQ workspace-pointer-bump-falls-back-to-merge-request

When the system cannot push a requested root-pointer bump directly, it SHALL
create a merge request for the pointer update instead.
enforced-by: tests/templates/test_workspace_publication.py::test_pointer_bump_falls_back_to_merge_request_when_push_is_denied, tests/templates/test_workspace_publication.py::test_a_fallback_pointer_merge_request_is_followed_to_its_merge

## REQ workspace-root-code-change-gets-a-root-merge-request

When a workspace work item changes source in the root repository, the system
SHALL create a merge request for that repository. Pointer-only root changes
SHALL instead follow the selected root-pointer policy.
enforced-by: tests/adapters/forge/test_run_chain.py::test_run_task_opens_a_merge_request_per_repo_deepest_first[bump], tests/adapters/forge/test_run_chain.py::test_run_task_opens_a_merge_request_per_repo_deepest_first[ignore], tests/adapters/forge/test_run_chain.py::test_root_with_no_changes_of_its_own_never_opens_a_merge_request

## REQ workspace-tasks-have-an-assembled-checkout

A workspace-targeted work item SHALL provide tasks an assembled checkout that
contains its selected member repositories at their declared paths.
enforced-by: tests/test_builtins.py::test_a_workspace_item_assembles_its_selected_members_each_on_the_items_branch, tests/test_builtins.py::test_ensure_worktree_never_runs_a_blanket_submodule_init, tests/templates/test_workspace_fanout.py::test_a_task_opting_in_runs_once_per_selected_repository_and_others_once

## REQ task-may-explicitly-fan-out-by-repository

A task MAY explicitly run once for each repository selected by a work item.
Tasks that do not opt in SHALL run in the work item's ordinary execution
context.
enforced-by: tests/templates/test_workspace_fanout.py::test_a_task_opting_in_runs_once_per_selected_repository_and_others_once, tests/templates/test_workspace_fanout.py::test_a_fanned_out_task_fails_when_any_repository_fails, tests/templates/test_workspace_fanout.py::test_a_fanned_out_run_reads_its_own_repositorys_entry

## REQ repository-area-can-declare-setup-and-test-scopes

A repository area MAY declare its setup requirements and test scopes. Area
test scopes SHALL use the same selection and result semantics as repository
test scopes.
enforced-by: tests/test_config_repos.py::test_load_repos_reads_an_entry[keeps-an-area-as-written], tests/test_config_repos.py::test_load_repos_rejects_an_entry[an-area-covering-no-path], tests/templates/test_workspace_fanout.py::test_a_changed_path_in_an_area_runs_its_setup_then_its_scope

## REQ selected-test-scope-activates-its-area-setup

Before running a selected test scope belonging to an area, the system SHALL
apply that area's setup requirements.
enforced-by: tests/templates/test_workspace_fanout.py::test_a_changed_path_in_an_area_runs_its_setup_then_its_scope

## REQ unexpected-area-changes-are-tested

When changed paths select a test scope from an area not chosen at intake, the
system SHALL still apply that area's setup requirements and run the scope.
enforced-by: tests/templates/test_workspace_fanout.py::test_a_changed_path_in_an_area_runs_its_setup_then_its_scope

## REQ work-item-target-is-typed-and-immutable

A work item SHALL select either one repository or a workspace target. A
workspace target MAY select member repositories and its root repository. The
selected targets, mount paths, base revisions, effective repository and area
policy, and root-pointer policy SHALL remain unchanged for that work item.
enforced-by: tests/templates/test_materialization.py::test_the_target_selection_survives_serialization, tests/templates/test_materialization.py::test_a_materialized_chain_cannot_be_changed_while_the_item_executes, tests/templates/test_environment.py::test_workspace_target_captures_selected_members, tests/templates/test_environment.py::test_a_workspace_target_mounts_exactly_its_selected_members, tests/api/test_repository_policy.py::test_a_filed_workspace_item_freezes_each_repositorys_policy

## REQ selected-repositories-get-corresponding-branches

The system SHALL create a corresponding work-item branch in every selected
repository. The branches MAY share one work-item branch name because each
repository has its own branch namespace.
enforced-by: tests/test_builtins.py::test_a_workspace_item_assembles_its_selected_members_each_on_the_items_branch

## REQ changed-child-repositories-get-separate-merge-requests

When publishing a workspace work item, the system SHALL create one draft merge
request for each changed child repository.
enforced-by: tests/adapters/forge/test_run_chain.py::test_run_task_opens_a_merge_request_per_repo_deepest_first[bump], tests/adapters/forge/test_run_chain.py::test_root_with_no_changes_of_its_own_never_opens_a_merge_request, tests/adapters/forge/test_run_chain.py::test_a_member_changed_without_being_selected_stops_publication, tests/templates/test_workspace_publication.py::test_an_untouched_selected_member_gets_no_merge_request_and_the_item_completes

## REQ draft-merge-request-enables-external-checks

The system MAY create a draft merge request before final-gate approval so CI
and automated merge-request review can run. A draft merge request SHALL NOT be
marked ready or merged before that approval.
enforced-by: tests/templates/test_publication_chain_order.py::test_nothing_readies_or_merges_before_the_final_gate[node-task-mr.mark_ready], tests/templates/test_publication_chain_order.py::test_nothing_readies_or_merges_before_the_final_gate[node-task-mr.merge], tests/templates/test_publication_chain_order.py::test_nothing_readies_or_merges_before_the_final_gate[fix-loop-mr.merge], tests/templates/test_publication_chain_order.py::test_nothing_readies_or_merges_before_the_final_gate[task-on-failure-mr.mark_ready]

## REQ default-chain-verification-does-not-rerun-the-implementer

The default chain SHALL run its implementing agent in an execution node with no
fix loop, and SHALL repair a failing test or a failing review in the
verification node after it, whose fix loop re-runs the tests and the review
and SHALL NOT re-run the implementing agent.
enforced-by: tests/executor/test_default_chain.py::test_a_verification_failure_reruns_the_tests_and_review_not_the_implementer[tests-red], tests/executor/test_default_chain.py::test_a_verification_failure_reruns_the_tests_and_review_not_the_implementer[review-red], tests/executor/test_seeded_failure_walk.py::test_a_failing_review_walks_verifications_own_fix_loop_never_the_implementer, tests/templates/test_library.py::test_the_design_chain_implements_then_verifies_then_briefs_before_the_draft
origin: templates/library.yaml -- Ruling 87. `fix-loop-remeasures-the-whole-node` reruns a node from its first step, so on the merged `implementation` node every test failure re-ran the implementer; splitting the node makes a repair re-run only the tests and the review. The walks use the seed's own changed-test-scope `builtin`, which `walk._IN_PROCESS_KINDS` had counted as Kraft's own code: every red test stopped with "reinstall and restart" instead of opening the fix loop.

## REQ default-chain-reviews-only-green-tests

The default chain's in-loop code review SHALL run only after the
changed-test-scope verification in the same node has passed.
enforced-by: tests/executor/test_default_chain.py::test_a_verification_failure_reruns_the_tests_and_review_not_the_implementer[tests-red], tests/executor/test_seeded_failure_walk.py::test_a_failing_review_walks_verifications_own_fix_loop_never_the_implementer
origin: templates/library.yaml -- the review is the verification node's second step, and a step group stops at its first failure (`exec-node-orders-concurrent-task-groups`), so no new mechanism is needed.

## REQ pre-draft-gate-shows-a-work-brief

The default chain's pre-draft gate SHALL show a work brief written by the
execution node before it. The brief SHALL say what was asked, what changed,
what was verified, what the review found and what was done about it, what is
unresolved, and that approving opens a draft merge request and starts CI. It
SHALL NOT contain the diff.
enforced-by: tests/executor/test_default_chain.py::test_the_pre_draft_gate_shows_the_work_brief_the_node_before_it_wrote, tests/templates/test_library.py::test_the_design_chain_implements_then_verifies_then_briefs_before_the_draft, tests/skills/test_work_brief.py::test_the_skill_says_what_approving_does, tests/skills/test_work_brief.py::test_the_skill_asks_for_each_section_the_gate_needs[asked], tests/skills/test_work_brief.py::test_the_skill_asks_for_each_section_the_gate_needs[changed], tests/skills/test_work_brief.py::test_the_skill_asks_for_each_section_the_gate_needs[verified], tests/skills/test_work_brief.py::test_the_skill_asks_for_each_section_the_gate_needs[reviewed], tests/skills/test_work_brief.py::test_the_skill_asks_for_each_section_the_gate_needs[unresolved], tests/skills/test_work_brief.py::test_the_skill_leaves_out_the_diff_and_the_final_review_brief
origin: src/kraft/skills/work-brief/SKILL.md -- Ruling 87 (Omid). The artifact kind is `work_brief`, not `work_summary`, because `agent.artifact_path` pluralises naively. The brief is its own execution node, not a last step of `verification`, because a node either wholly produces one kind or declares none (`TemplateLibrary.resolve_chain`).

## REQ default-chain-describes-the-merge-request-before-opening-it

The default chain SHALL write the merge request's title, labels, reviewers and
description in an execution node before the one that opens the draft, and SHALL
open the draft with them.
enforced-by: tests/api/test_default_chain_walk.py::test_approving_every_gate_through_the_api_walks_the_default_chain_to_post_merge_ci, tests/templates/test_library.py::test_resolution_types_every_task_in_the_design_chain
origin: templates/chains/default.yaml -- Ruling 207. The V1 library had no task producing `mr_meta` (the legacy `on.mr.describe` hook went with the old registry), so every draft opened with the work item's title and no labels, and a repo whose CI requires a label failed its first pipeline. `describe_merge_request` is its own node because a node wholly produces one kind or declares none, and `open` produces nothing.

## REQ a-draft-with-no-metadata-opens-with-the-default-body

IF a work item reaches its draft merge request with no merge-request metadata
written, THEN the system SHALL still open the draft, titled after the work
item, unlabelled, with Kraft's default description.
enforced-by: tests/api/test_default_chain_walk.py::test_with_no_merge_request_metadata_the_draft_opens_with_the_default_body, tests/adapters/forge/test_run.py::test_open_mr_without_the_artifact_opens_the_default_body
origin: src/kraft/adapters/forge/mr.py §read_mr_meta -- an MR must still open when the metadata is gone; Ruling 207 kept that for an item that skips the describe node.

## REQ default-chain-retests-a-rebased-head

When the default chain's post-draft feedback moves the worktree base, the
system SHALL restart at the verification node, so the rebased head is tested
and reviewed again before the chain goes on.
enforced-by: tests/executor/test_default_chain.py::test_a_rebase_in_post_draft_feedback_retests_and_rereviews_the_rebased_head, tests/templates/test_library.py::test_the_design_chain_implements_then_verifies_then_briefs_before_the_draft
origin: templates/chains/default.yaml -- Kraft-bjw6a. `merge_request_feedback` declares `on_base_changed: {restart_from: verification}`, since its CI-conflict path force-rebases. `merge`'s own conflict rebase is not declared: without a declaration it re-checks CI on the rebased head itself, and a restart from there would cross `chain_review`.

## REQ an-undeclared-conflict-rebase-waits-for-the-rebased-heads-ci

When a forge node rebases a merge request's conflict away and declares no
`on_base_changed` restart to verify the rebased head again, the system SHALL
neither report that node's check passed nor merge until it has read a settled
green pipeline for the rebased head itself.
enforced-by: tests/adapters/forge/test_run_chain.py::test_a_ci_poll_conflict_rebase_is_never_a_green_check[undeclared-it-waits-for-the-rebased-heads-ci], tests/templates/test_workspace_publication.py::test_a_members_conflict_is_rebased_onto_its_own_origin[undeclared-it-waits-for-the-rebased-heads-ci-ci_poll], tests/templates/test_workspace_publication.py::test_a_members_conflict_is_rebased_onto_its_own_origin[undeclared-it-waits-for-the-rebased-heads-ci-merge]
origin: src/kraft/adapters/forge/run.py §_run_one -- Kraft-tx0dz. A member's rebase reports its move only to a declared restart (Kraft-puqxq), and `ci_poll` reported a rebased head green on the pipeline of the head before it. The forge's pipeline for the pushed head is the verification: `render_ci` reads a pipeline for another head as pending, and `merge` reads it again before `forge.merge`.

## REQ base-change-restart-passes-an-approved-gate

A base-change restart after a clean rebase SHALL NOT reopen a gate in its span
that was already approved; the restarted walk SHALL pass over it. A restart
after an explicit conflict handler resolved a rebase conflict SHALL reopen every
approved gate in its span, whether the conflict arose inside the walk or when
`/retry` or `/resume` refreshed the worktree.
enforced-by: tests/executor/test_default_chain.py::test_a_rebase_in_post_draft_feedback_retests_and_rereviews_the_rebased_head, tests/executor/test_base_change.py::test_a_resolved_conflict_reopens_the_approved_gates_in_its_span, tests/executor/test_base_change.py::test_a_conflict_at_the_door_goes_to_the_nodes_handler[retry], tests/executor/test_base_change.py::test_a_conflict_at_the_door_goes_to_the_nodes_handler[resume]
origin: src/kraft/executor/walk.py §_restart_for_base_change -- Omid's decision (Ruling 162): a clean rebase, the forge's forced rebase included, changes nothing a gate saw; a resolved conflict changed code no gate saw, so a human approves again. Distinct from `retry-reopens-invalidated-gates`: a restart is the walk's answer to a moved base, not an operator's retry.

## REQ optional-pre-draft-gate-keeps-work-local

A chain MAY declare a gate before draft merge-request creation. Until that gate
is approved, the work item SHALL remain local and SHALL NOT create a merge
request.
enforced-by: tests/templates/test_publication_chain_order.py::test_a_pre_draft_gate_keeps_the_work_local

## REQ final-gate-governs-merge-request-readiness

After final-gate approval, the system SHALL mark the draft merge request ready
for external approval and merge.
enforced-by: tests/templates/test_publication_chain_order.py::test_the_seeded_default_chain_publishes_in_the_required_order, tests/templates/test_publication_chain_order.py::test_nothing_readies_or_merges_before_the_final_gate[node-task-mr.mark_ready]

## REQ external-wait-has-configurable-timeout-and-polling

An external-wait task SHALL allow configuration of its timeout and polling
intervals, subject to applicable policy limits. Its timeout is its task's own
`total_time_cap_minutes` (Ruling 196).
enforced-by: tests/test_waits.py::test_a_wait_resolves_its_bounds_through_the_task_policy[authored], tests/test_waits.py::test_a_wait_resolves_its_bounds_through_the_task_policy[seed-default], tests/test_waits.py::test_a_wait_resolves_its_bounds_through_the_task_policy[default-clamped-to-maximum], tests/test_waits.py::test_a_wait_over_the_administrator_maximum_is_refused_when_the_item_is_filed, tests/adapters/forge/test_run_chain.py::test_the_executor_hands_the_task_s_resolved_wait_to_the_forge, tests/test_waits.py::test_an_items_policy_override_shortens_its_own_wait_and_no_other_items, tests/test_waits.py::test_a_policy_change_rebounds_an_open_wait_from_its_next_observation, tests/templates/test_time_caps.py::test_a_wait_timeout_written_before_ruling_196_reads_as_the_tasks_total_cap

## REQ external-wait-does-not-hold-an-active-worker

When an external condition is pending, the task SHALL persist its wait state
and next observation time, then release its worker resources.
enforced-by: tests/test_waits.py::test_pending_wait_releases_worker_and_reschedules_with_backoff, tests/adapters/forge/test_waits.py::test_merge_hands_a_pending_pipeline_back_to_the_scheduler

## REQ external-waits-use-a-shared-due-scheduler

The system SHALL use one scheduler to observe due external waits. It SHALL
increase a wait's observation interval from its configured initial interval up
to its configured maximum interval while the condition remains pending.
enforced-by: tests/test_waits.py::test_interval_grows_from_initial_to_max_while_pending, tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[ci], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[automated-review], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[external-approval], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[merge-completion], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[post-merge-ci], tests/test_waits.py::test_a_node_waiting_on_two_conditions_is_due_at_the_earlier_one, tests/api/test_wait_scheduler_startup.py::test_the_server_s_scheduler_observes_a_wait_parked_before_it_started, tests/test_waits.py::test_a_retry_on_a_run_fork_starts_a_fresh_wait

## REQ external-wait-timeout-needs-human

When an external-wait task reaches its configured timeout, the system SHALL
stop for human action and SHALL NOT classify the timeout as a code failure.
enforced-by: tests/test_waits.py::test_wait_timeout_stops_for_human_and_is_not_a_code_failure, tests/adapters/forge/test_merge_watch.py::test_a_post_merge_pipeline_that_never_settles_times_out_for_a_human, tests/test_waits.py::test_a_wait_timeout_and_a_loop_cap_are_reported_apart, tests/executor/test_stuck_escalation.py::test_a_stop_outside_the_stuck_set_goes_straight_to_a_human[wait timeout-declared], tests/executor/test_stuck_escalation.py::test_a_stop_outside_the_stuck_set_goes_straight_to_a_human[wait timeout-undeclared], tests/templates/test_workspace_publication.py::test_a_root_approval_that_never_comes_times_out_for_a_human

## REQ scope-time-caps-bound-their-own-scope

A time cap MAY be configured on the work item (its instance, repository, chain
or its own override), a node, a step or a task, and each configured cap SHALL
bound that scope's own elapsed time: `time_cap_minutes` its running time,
leaving out paused, external-wait, gate and rate-limited time, and
`total_time_cap_minutes` its wall clock, leaving out only a manual pause. A
child scope's cap SHALL NOT exceed its parent's among the chain, node, step and
task: one that does SHALL be refused when the chain loads and when a work item
is filed, retried or its override set, naming both scopes. An instance or
repository cap SHALL be a default, not a ceiling: any scope MAY set more, up
to the administrator maximum (Ruling 198). A work item's item-wide cap SHALL be
its own, which MAY exceed the chain's up to the maximum and SHALL meet any
scope's own cap; its cap on a path SHALL only tighten. A gate's timeout
SHALL bound the gate's wait, under the total caps around it. Reaching a cap
SHALL stop the running process and stop the work item for human action, naming
the scope, and SHALL NOT be treated as a code failure: it SHALL spend no
recovery or fix-loop attempt and SHALL NOT trigger stuck escalation.
enforced-by: tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[task-time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[step-time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[node-time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[chain-time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[item-time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[task-total_time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[node-total_time_cap_minutes], tests/executor/test_time_caps.py::test_each_levels_cap_stops_its_own_scope_and_names_it[item-total_time_cap_minutes], tests/executor/test_time_caps.py::test_a_task_cap_under_a_larger_step_cap_stops_the_task_at_its_own_value, tests/executor/test_time_caps.py::test_a_launch_under_a_spent_cap_is_refused_and_nothing_runs, tests/executor/test_time_caps.py::test_the_kill_reaches_a_sandboxs_container, tests/executor/test_time_caps.py::test_running_time_leaves_out_paused_wait_gate_and_rate_limited_time[pause], tests/executor/test_time_caps.py::test_running_time_leaves_out_paused_wait_gate_and_rate_limited_time[gate], tests/executor/test_time_caps.py::test_running_time_leaves_out_paused_wait_gate_and_rate_limited_time[wait], tests/executor/test_time_caps.py::test_running_time_leaves_out_paused_wait_gate_and_rate_limited_time[rate_limited], tests/executor/test_time_caps.py::test_the_wall_clock_leaves_out_only_a_manual_pause[pause-95], tests/executor/test_time_caps.py::test_the_wall_clock_leaves_out_only_a_manual_pause[gate-35], tests/executor/test_time_caps.py::test_a_cap_stop_spends_no_attempt_and_is_not_escalated, tests/executor/test_time_caps.py::test_a_parked_item_past_its_total_cap_is_stopped_for_a_human[waiting], tests/executor/test_time_caps.py::test_a_gate_past_its_timeout_stops_naming_the_gate_and_stays_answerable, tests/templates/test_time_caps.py::test_a_child_cap_above_its_parents_is_refused_when_the_item_is_filed[task-over-step-time_cap_minutes], tests/templates/test_time_caps.py::test_a_child_cap_above_its_parents_is_refused_when_the_item_is_filed[step-over-node-total_time_cap_minutes], tests/templates/test_time_caps.py::test_a_child_cap_above_its_parents_is_refused_when_the_item_is_filed[node-over-chain-time_cap_minutes], tests/templates/test_time_caps.py::test_a_child_cap_above_its_parents_is_refused_at_load[time_cap_minutes], tests/templates/test_time_caps.py::test_an_items_path_cap_above_the_one_it_lands_on_is_refused[path-over-its-own-total_time_cap_minutes], tests/templates/test_time_caps.py::test_a_scope_may_raise_a_cap_above_the_default_up_to_the_maximum[node-time_cap_minutes], tests/templates/test_time_caps.py::test_a_scope_past_the_maximum_is_refused_even_with_no_parent_cap[total_time_cap_minutes], tests/templates/test_time_caps.py::test_an_item_may_raise_its_own_cap_above_the_chains_up_to_the_maximum[time_cap_minutes], tests/templates/test_time_caps.py::test_a_retry_raising_a_cap_above_its_parents_is_refused_naming_both[time_cap_minutes], tests/templates/test_time_caps.py::test_a_retry_raising_a_cap_above_its_parents_is_refused_naming_both[total_time_cap_minutes], tests/executor/test_time_cap_launches.py::test_a_default_cap_leaves_a_longer_scope_its_own_and_binds_the_rest, tests/executor/test_time_cap_launches.py::test_raising_the_items_own_cap_unsticks_a_capped_item, tests/executor/test_time_cap_launches.py::test_a_gate_review_launches_under_its_gates_time_cap, tests/executor/test_time_cap_launches.py::test_only_an_automatic_escalation_turn_runs_under_its_nodes_time_cap[auto], tests/executor/test_time_cap_launches.py::test_a_session_adopted_after_a_restart_is_killed_at_its_caps_deadline, tests/executor/test_time_cap_launches.py::test_the_poller_does_not_stop_an_item_that_moved_since_it_measured[status], tests/templates/test_time_caps.py::test_a_gate_timeout_past_its_total_cap_is_refused, tests/api/test_item_policy.py::test_intake_refuses_an_override_naming_the_field[wait-cap-raised], tests/api/test_item_policy.py::test_a_patch_refuses_an_override_naming_the_field_and_keeps_the_old_one[wait-cap-raised]
origin: src/kraft/caps.py §at_launch -- Rulings 194, 195, 196 and 198 (a default is not a ceiling; an item may raise its own cap). Omid, 2026-09-22: "at any level that it's configured, it applies ... caps can[not] be configured to be more than a parent cap. So if a step has 10 mins, the task inside that step can't set it to 20 mins." The refusal is `ResolvedChain._check_caps` (src/kraft/templates/models.py), run by `check_scopes` at load, intake and PATCH; the ratchet is `InstancePolicy.apply_template_override`, and an item layer meets (`WorkItemPolicy.apply_to`). A wait's timeout became its task's total cap (Ruling 196), so `wait_timeout_minutes` is retired and still read.

## REQ a-resumed-cli-session-is-counted-once

WHEN a session resumes an earlier session's agent CLI session (a paused task
resumed, or a later turn of an escalation thread), the system SHALL record for
it only the tokens and cost it added to that CLI session's running totals,
and SHALL record its cost as unknown when the earlier session's is unknown.
enforced-by: tests/store/test_resumed_usage.py::test_a_resumed_session_records_only_what_it_spent_itself, tests/store/test_resumed_usage.py::test_a_resumed_escalation_turn_skips_a_refused_turn_between, tests/store/test_resumed_usage.py::test_an_unknown_earlier_cost_leaves_the_resumed_cost_unknown, tests/store/test_resumed_usage.py::test_a_new_cli_session_is_not_netted_against_an_earlier_one
origin: src/kraft/store/sessions.py §_own_share -- Kraft-s7c04.62: the CLI's cost and modelUsage are cumulative per session id, so a resumed row repeated what the paused row already recorded and usage_rollup counted it twice.

## REQ scope-budgets-cap-their-own-spend

A `token_budget` or `budget_usd` MAY be configured on the work item, a node, a
step or a task, and each SHALL cap the spend of the launches inside that scope
(Ruling 195), not the whole work item's. A launch SHALL be refused, and the
work item stopped for human action naming the scope, when its own scope or any
enclosing scope has reached its cap. A child scope's budget SHALL NOT exceed
its parent's, refused as a time cap is. An instance or repository budget SHALL
be a default, not a ceiling: it binds a task's own launches where nothing more
specific is set, and any scope MAY set more up to the maximum; a work item's
item-wide budget MAY exceed the chain's up to the maximum (Ruling 198). An
escalation turn's spend SHALL count as its node's. A launch whose cost was never reported
SHALL count as unknown spend, never as free, so a dollar cap over unknown
spend SHALL stop for human action. The instance `budget.work_item_usd` and
`budget.daily_usd` SHALL remain the outer ceilings.
enforced-by: tests/executor/test_budget_caps.py::test_a_token_budget_caps_its_own_scopes_spend[task], tests/executor/test_budget_caps.py::test_a_token_budget_caps_its_own_scopes_spend[step], tests/executor/test_budget_caps.py::test_a_token_budget_caps_its_own_scopes_spend[node], tests/executor/test_budget_caps.py::test_a_token_budget_caps_its_own_scopes_spend[chain], tests/executor/test_budget_caps.py::test_a_token_budget_caps_its_own_scopes_spend[item], tests/executor/test_budget_caps.py::test_a_budget_usd_caps_its_own_scopes_spend[task], tests/executor/test_budget_caps.py::test_a_budget_usd_caps_its_own_scopes_spend[node], tests/executor/test_budget_caps.py::test_a_budget_usd_caps_its_own_scopes_spend[item], tests/executor/test_budget_caps.py::test_an_enclosing_scopes_cap_refuses_a_launch_its_own_would_allow, tests/executor/test_budget_caps.py::test_unknown_spend_is_never_counted_as_free, tests/executor/test_budget_caps.py::test_the_instance_budget_stays_the_outer_ceiling, tests/executor/test_budget_caps.py::test_a_scope_budget_stop_names_its_scope, tests/executor/test_policy_enforcement.py::test_token_budget_refuses_the_next_agent_launch[at], tests/templates/test_time_caps.py::test_a_child_budget_above_its_parents_is_refused_naming_both[token_budget-20-10], tests/templates/test_time_caps.py::test_a_child_budget_above_its_parents_is_refused_naming_both[budget_usd-2.5-1], tests/templates/test_time_caps.py::test_an_item_may_raise_its_own_budget_above_the_chains_up_to_the_maximum[token_budget-20-10], tests/templates/test_time_caps.py::test_a_budget_may_be_raised_above_the_default_up_to_the_maximum[budget_usd-9], tests/executor/test_budget_caps.py::test_an_escalation_turns_spend_counts_toward_its_nodes_budget, tests/executor/test_budget_caps.py::test_a_default_budget_binds_a_tasks_own_spend_not_an_enclosing_scopes
origin: src/kraft/caps.py §budget_breach -- Ruling 195 (Omid, 2026-09-22): `token_budget` compared the whole item's spend at every scope; it now caps the scope that sets it, and `budget_usd` joins it. Unknown cost is Omid's default: "it counts as unknown, never as free, so a USD cap with unknown spend stops for a human rather than silently passing."

## REQ external-wait-covers-merge-request-lifecycle

The shared external-wait mechanism SHALL support CI completion, automated
review settlement, external approval, merge completion, and post-merge CI.
enforced-by: tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[ci], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[automated-review], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[external-approval], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[merge-completion], tests/test_waits.py::test_every_wait_kind_parks_and_is_resumed_by_the_one_scheduler[post-merge-ci], tests/api/test_default_chain_walk.py::test_approving_every_gate_through_the_api_walks_the_default_chain_to_post_merge_ci, tests/templates/test_workspace_publication.py::test_a_root_merge_request_awaits_its_own_approval_then_merges_then_the_item_completes, tests/templates/test_workspace_publication.py::test_a_fallback_pointer_merge_request_is_followed_to_its_merge, tests/templates/test_workspace_publication.py::test_a_root_merge_request_opened_for_source_since_reverted_is_still_merged, tests/adapters/forge/test_waits.py::test_a_restart_mid_merge_asks_the_forge_to_merge_only_once[work_item_retried], tests/adapters/forge/test_waits.py::test_a_restart_mid_merge_asks_the_forge_to_merge_only_once[run_forked], tests/adapters/forge/test_waits.py::test_a_restart_mid_merge_asks_the_forge_to_merge_only_once[base_change_restart], tests/adapters/forge/test_waits.py::test_a_new_head_after_a_base_change_asks_for_the_merge_again, tests/templates/test_workspace_publication.py::test_a_restart_mid_the_root_merge_asks_the_forge_to_merge_it_once[root], tests/templates/test_workspace_publication.py::test_a_restart_mid_the_root_merge_asks_the_forge_to_merge_it_once[fallback-pointer], tests/adapters/forge/test_forge_cli_contract.py::test_find_mr_reads_a_merge_the_forge_already_holds_queued[glab], tests/adapters/forge/test_forge_cli_contract.py::test_find_mr_reads_a_merge_the_forge_already_holds_queued[gh]

## REQ automated-review-is-an-explicit-optional-task

A chain MAY declare a task that waits for automated merge-request review. When
no such task is declared, the system SHALL NOT expect automated review for that
chain.
enforced-by: tests/test_waits.py::test_a_chain_without_an_automated_review_task_never_waits_for_one, tests/adapters/forge/test_automated_review.py::test_a_repository_naming_no_reviewer_settles_clean_and_says_why[gh], tests/adapters/forge/test_automated_review.py::test_a_repository_naming_no_reviewer_settles_clean_and_says_why[glab]

## REQ automated-review-task-uses-ordinary-task-results

An automated-review task SHALL report ordinary pending, clean, actionable, or
error results. Actionable feedback SHALL enter the declaring execution node's
recovery or fix-loop controls.
enforced-by: tests/adapters/forge/test_waits.py::test_automated_review_reports_ordinary_task_results[pending], tests/adapters/forge/test_waits.py::test_automated_review_reports_ordinary_task_results[clean], tests/adapters/forge/test_waits.py::test_automated_review_reports_ordinary_task_results[actionable], tests/adapters/forge/test_waits.py::test_automated_review_reports_ordinary_task_results[error], tests/test_waits.py::test_actionable_automated_review_is_repaired_resynced_and_remeasured[on_failure], tests/test_waits.py::test_actionable_automated_review_is_repaired_resynced_and_remeasured[fix_loop], tests/adapters/forge/test_automated_review.py::test_a_bot_s_unresolved_feedback_is_actionable[gh], tests/adapters/forge/test_automated_review.py::test_a_bot_s_unresolved_feedback_is_actionable[glab], tests/adapters/forge/test_automated_review.py::test_a_review_check_still_running_is_pending[gh], tests/adapters/forge/test_automated_review.py::test_a_review_check_still_running_is_pending[glab], tests/test_waits.py::test_a_reviewer_error_stops_for_a_human_and_spends_no_repair, tests/adapters/forge/test_automated_review.py::test_a_withdrawn_bot_review_does_not_count[gh-dismissed-only], tests/adapters/forge/test_automated_review.py::test_a_withdrawn_bot_review_does_not_count[gh-dismissed-over-an-earlier-review], tests/adapters/forge/test_automated_review.py::test_a_withdrawn_bot_review_does_not_count[glab-resolved-only]

## REQ automated-review-implementation-is-not-template-configuration

Templates SHALL NOT expose transport details such as webhook event names,
provider check names, comment authors, or API and CLI mechanics for automated
review. The selected task implementation SHALL own those details.
enforced-by: tests/test_waits.py::test_a_template_cannot_configure_how_automated_review_is_read[webhook_event], tests/test_waits.py::test_a_template_cannot_configure_how_automated_review_is_read[check_name], tests/test_waits.py::test_a_template_cannot_configure_how_automated_review_is_read[comment_author], tests/test_waits.py::test_a_template_cannot_configure_how_automated_review_is_read[command], tests/test_waits.py::test_the_repository_s_named_reviewer_reaches_the_review_task, tests/adapters/forge/test_automated_review.py::test_a_reviewer_is_named_exactly_one_way_or_refused_at_load[both], tests/adapters/forge/test_automated_review.py::test_a_reviewer_is_named_exactly_one_way_or_refused_at_load[neither], tests/adapters/forge/test_automated_review.py::test_a_bot_review_is_pending_until_the_bot_reviews_the_head_then_clean[gh], tests/adapters/forge/test_automated_review.py::test_a_bot_review_is_pending_until_the_bot_reviews_the_head_then_clean[glab], tests/adapters/forge/test_automated_review.py::test_a_failed_review_check_is_actionable_with_its_output[gh], tests/adapters/forge/test_automated_review.py::test_a_failed_review_check_is_actionable_with_its_output[glab]

## REQ default-post-draft-flow-is-ordered

The default chain SHALL create a draft merge request, await CI, run any
declared automated-review task, address CI failures and actionable feedback,
produce a work-item summary and review, and then request final-gate approval.
After approval, it SHALL mark the merge request ready, await external approval,
and merge.
enforced-by: tests/api/test_default_chain_walk.py::test_approving_every_gate_through_the_api_walks_the_default_chain_to_post_merge_ci, tests/templates/test_publication_chain_order.py::test_the_seeded_default_chain_publishes_in_the_required_order

## REQ post-draft-feedback-uses-node-recovery-controls

CI failures and actionable automated-review feedback in the post-draft flow
SHALL enter that execution node's recovery or fix-loop controls. After a
successful repair, the system SHALL resynchronize the draft merge request and
remeasure the node.
enforced-by: tests/test_waits.py::test_actionable_automated_review_is_repaired_resynced_and_remeasured[on_failure], tests/test_waits.py::test_actionable_automated_review_is_repaired_resynced_and_remeasured[fix_loop]

## REQ missing-external-approval-is-normal-pending-state

The absence of required external merge-request approval SHALL be a normal
pending condition, not a failure. It SHALL NOT prevent the work-item summary,
review, or final gate from occurring before the approval wait begins.
enforced-by: tests/adapters/forge/test_waits.py::test_missing_external_approval_is_pending_not_a_failure[missing-approval-waits], tests/adapters/forge/test_waits.py::test_missing_external_approval_is_pending_not_a_failure[approved], tests/adapters/forge/test_waits.py::test_merge_waits_for_a_missing_approval_instead_of_failing, tests/api/test_default_chain_walk.py::test_approving_every_gate_through_the_api_walks_the_default_chain_to_post_merge_ci

## REQ child-merge-precedes-parent-pointer-update

The system SHALL wait for a changed child repository to merge before updating
a workspace root pointer to that child's revision.
enforced-by: tests/templates/test_workspace_publication.py::test_child_merge_precedes_workspace_pointer_update[workspace], tests/templates/test_workspace_publication.py::test_child_merge_precedes_workspace_pointer_update[filed-before-workspaces], tests/templates/test_workspace_publication.py::test_root_mr_not_ready_until_child_mrs_have_merged, tests/templates/test_workspace_publication.py::test_the_pointer_bump_moves_only_merged_members_and_to_what_merged

## REQ root-source-draft-merge-request-may-run-early

When a workspace work item changes root source and child repositories, the
system MAY create a draft root merge request before the child merge requests
merge so root CI can run.
enforced-by: tests/templates/test_workspace_publication.py::test_root_mr_not_ready_until_child_mrs_have_merged

## REQ root-source-merge-request-readiness-waits-for-child-merges

When a workspace work item changes root source and child repositories, the
system SHALL NOT mark the root merge request ready until the child merge
requests have merged and the root contains their final pointer revisions.
enforced-by: tests/templates/test_workspace_publication.py::test_root_mr_not_ready_until_child_mrs_have_merged, tests/templates/test_workspace_publication.py::test_a_root_merge_request_awaits_its_own_approval_then_merges_then_the_item_completes

## REQ blocked-child-merge-leaves-parent-unchanged

When a child merge request is rejected, blocked, or fails to merge, the system
SHALL leave its workspace root pointer unchanged and require human action.
enforced-by: tests/templates/test_workspace_publication.py::test_blocked_child_merge_leaves_the_root_unchanged[root-with-source-awaiting-approval], tests/templates/test_workspace_publication.py::test_blocked_child_merge_leaves_the_root_unchanged[pointer-only-root-refused], tests/templates/test_workspace_publication.py::test_blocked_child_merge_leaves_the_root_unchanged[one-member-landed]

## REQ template-policy-may-replace-operational-defaults

Template policy overrides MAY replace operational defaults, including timeouts
and retry or wait timing, in either direction.
enforced-by: tests/test_policy.py::test_template_policy_may_replace_operational_defaults_either_direction, tests/executor/test_policy_enforcement.py::test_a_fix_loops_bounds_come_from_its_nodes_policy[v1-policy-replaces-it], tests/executor/test_policy_enforcement.py::test_the_fix_loop_counts_under_the_bounds_its_node_policy_resolves

## REQ work-item-policy-may-exceed-default-ceilings-within-admin-maximum

An operator editing a work item MAY raise a normal policy ceiling for that work
item, but SHALL NOT exceed an explicitly configured administrator maximum.
enforced-by: tests/test_policy.py::test_work_item_policy_may_exceed_default_within_admin_maximum, tests/api/test_item_policy.py::test_intake_and_a_patch_set_the_items_own_override, tests/api/test_item_policy.py::test_intake_refuses_an_override_naming_the_field[attempts-over-maximum], tests/api/test_item_policy.py::test_a_patch_refuses_an_override_naming_the_field_and_keeps_the_old_one[attempts-over-maximum], tests/templates/test_item_policy.py::test_an_override_past_its_bounds_is_refused_naming_the_field[attempts-over-maximum], tests/templates/test_item_policy.py::test_an_items_safety_value_only_tightens_whatever_it_lands_on[allowlist-wider-than-the-ceiling-intersects], tests/executor/test_policy_enforcement.py::test_an_items_own_node_cap_binds_that_nodes_fix_loop, tests/api/test_item_policy.py::test_the_older_node_override_door_is_held_to_the_same_maxima[intake-attempts], tests/api/test_item_policy.py::test_the_older_node_override_door_is_held_to_the_same_maxima[patch-wall-clock]
origin: src/kraft/templates/models.py §MaterializedChain.with_item_policy -- the check every door that sets a work item's own override makes (`POST /work-items`, `PATCH /work-items/{id}`, a chain switch), by `ResolvedChain.check_scopes`, the engine every layer resolves through. A PATCH on a running or waiting item binds from the next node entered (`walk.walk_node` re-reads the row) and the next observation of a wait (`waits.observe` rebounds an open wait).

## REQ policy-override-rules-are-field-specific

The system SHALL apply field-specific restriction rules to policy overrides and
SHALL NOT treat policy overrides as an unrestricted generic merge.
enforced-by: tests/test_policy.py::test_policy_override_rejects_unknown_fields_field_specifically, tests/test_policy.py::test_template_policy_cannot_widen_allowed_tools, tests/test_policy.py::test_template_policy_may_replace_operational_defaults_either_direction, tests/test_policy.py::test_a_policy_refusal_names_the_field_it_refused[allowed_tools], tests/test_policy.py::test_a_task_scope_override_refuses_the_loop_bounds, tests/templates/test_policy_scopes.py::test_a_scope_without_a_fix_loop_refuses_the_loop_bounds_at_load[step-max_attempts], tests/templates/test_policy_scopes.py::test_a_scope_without_a_fix_loop_refuses_the_loop_bounds_at_load[gate-timeout_minutes], tests/templates/test_item_policy.py::test_an_override_cannot_change_structure[changes-a-node-kind], tests/templates/test_item_policy.py::test_an_override_cannot_change_structure[loop-bound-on-a-task]

## REQ template-lint-reports-library-validity

`GET /templates/lint` SHALL validate the complete installed template library
and report all parse, resolution, schema, reference, and identifier errors
without writing or reloading configuration.
enforced-by: tests/api/test_templates_inspection.py::test_lint_reports_all_library_errors_without_writing, tests/api/test_templates_inspection.py::test_lint_reports_an_unreadable_library_file_as_an_issue, tests/api/test_templates_inspection.py::test_lint_of_the_installed_library_is_clean
origin: src/kraft/templates/library.py §TemplateLibrary.lint_dir -- reads the installed directory into a scratch library, never into the daemon's `st.library`. A bad `library.yaml` is one issue: nothing can be resolved against it.

## REQ resolved-template-api-shows-saved-chain

`GET /templates/chains/{id}/resolved` SHALL return the fully resolved configuration of
a saved chain, before per-work-item materialization.
enforced-by: tests/api/test_templates_inspection.py::test_resolved_shows_a_saved_chain_expanded_and_not_materialized, tests/api/test_templates_inspection.py::test_resolved_of_an_unknown_chain_is_404, tests/api/test_templates_inspection.py::test_with_no_library_loaded_the_library_reads_are_503

## REQ resolve-api-supports-candidate-and-library-input

`POST /templates/resolve` SHALL resolve a single unsaved candidate chain
against the installed library and SHALL also resolve a complete unsaved
template library in isolation, without writing either input to disk.
enforced-by: tests/api/test_templates_inspection.py::test_resolve_candidate_uses_installed_library_without_writing, tests/api/test_templates_inspection.py::test_resolve_a_complete_library_in_isolation_without_writing, tests/api/test_templates_inspection.py::test_a_candidate_that_does_not_resolve_is_reported_not_raised, tests/api/test_templates_inspection.py::test_with_no_library_loaded_the_library_reads_are_503

## REQ resolved-template-is-deterministic

A resolved-template response SHALL represent inheritance and component
expansion only; attachment-driven gate satisfaction and other per-work-item
materialization SHALL remain separate.
enforced-by: tests/templates/test_materialization.py::test_resolution_is_expansion_only_and_repeatable

## REQ template-cli-exposes-lint-and-resolved-output

`kraft admin templates lint` SHALL lint the installed template library and exit
non-zero when errors exist. `kraft admin templates show <id> --resolved` SHALL
print a selected chain's resolved configuration.
enforced-by: tests/cli/test_admin.py::test_admin_templates_lint_of_a_clean_library_exits_0, tests/cli/test_admin.py::test_admin_templates_lint_prints_each_error_and_exits_1, tests/cli/test_admin.py::test_admin_templates_show_resolved_prints_the_expanded_chain

## REQ template-library-api-lists-its-components

`GET /templates/library` SHALL list every component the loaded `library.yaml`
declares with its definition as written, the chains that use it, and the lint
issues that name it. `PUT /templates/library` SHALL refuse, writing nothing, a
library that would stop any chain that resolves now from resolving.
enforced-by: tests/api/test_templates_library.py::test_the_library_lists_every_component_with_the_chains_that_use_it, tests/api/test_templates_library.py::test_a_lint_issue_is_listed_on_the_component_it_names, tests/api/test_templates_library.py::test_a_library_save_that_breaks_a_chain_is_refused_and_writes_nothing, tests/api/test_templates_library.py::test_a_library_save_is_written_verbatim_and_live, tests/api/test_templates_inspection.py::test_with_no_library_loaded_the_library_reads_are_503
origin: src/kraft/templates/catalogue.py §components -- built from the daemon's loaded `st.library` and its own `lint`, never a second parse; `TemplateLibrary.references` records what each chain's `extends` expansion actually follows. Kraft-6xkkm.

## REQ template-cli-lists-library-components

`kraft admin templates library` SHALL print every library component with its
kind and the chains that use it, and `kraft admin templates library <id>` one
component's definition.
enforced-by: tests/cli/test_admin_templates_library.py::test_the_table_lists_each_component_its_kind_and_the_chains_using_it, tests/cli/test_admin_templates_library.py::test_one_component_prints_its_definition_and_users

## REQ chain-routes-are-their-own-namespace

A saved chain SHALL be read, saved and resolved under `/templates/chains/{id}`,
so that no chain id can shadow the library or an inspection route and none is
reserved. The pre-1.0 flat `/templates/{id}` paths SHALL NOT remain as aliases.
enforced-by: tests/api/test_settings_templates.py::test_a_chain_may_take_the_name_of_a_templates_route[library], tests/api/test_settings_templates.py::test_a_chain_may_take_the_name_of_a_templates_route[lint], tests/api/test_settings_templates.py::test_the_pre_ruling_204_chain_paths_are_gone
origin: src/kraft/api/routes/settings.py §get_template -- Ruling 204, before 1.0 so the break happens once.

## REQ harness-api-lists-and-guards-profiles

`GET /harnesses/profiles` SHALL list every `harnesses.yaml` profile with its
provider, executable, enabled flag and defaults, and the library tasks and
chains that select it; `GET /harnesses/providers` each provider package's
capability surface. `PUT /harnesses/profiles/{id}` SHALL save one profile only
through the loader's own parse, and SHALL refuse, writing nothing, a profile
that would stop an agent task of a chain that resolves now from launching.
Profiles and providers SHALL each live under their own prefix, so no profile id
is reserved, and the flat `/harnesses/{id}` paths SHALL NOT remain.
enforced-by: tests/api/test_harnesses.py::test_a_profile_may_take_the_name_of_a_harnesses_route[providers], tests/api/test_harnesses.py::test_the_flat_profile_paths_are_gone[get-/api/harnesses], tests/api/test_harnesses.py::test_every_profile_is_listed_with_the_library_tasks_and_chains_selecting_it, tests/api/test_harnesses.py::test_providers_are_each_packages_capability_surface, tests/api/test_harnesses.py::test_a_save_the_loader_refuses_is_refused_and_writes_nothing[bad-value], tests/api/test_harnesses.py::test_a_save_that_stops_a_chain_launching_is_refused[disabled], tests/api/test_harnesses.py::test_a_provider_change_the_selecting_task_cannot_run_on_is_refused, tests/api/test_harnesses.py::test_a_chain_already_unlaunchable_does_not_block_an_unrelated_save, tests/api/test_harnesses.py::test_a_profile_save_is_written_and_read_back
origin: src/kraft/api/routes/harnesses.py §put_harness -- `HarnessProfileTable.from_mapping` is `from_yaml`'s parse, and `agent.select_profile` is the rule a launch applies. Kraft-archr, Ruling 206.

## REQ harness-cli-lists-profiles

`kraft admin harnesses` SHALL print every harness profile with its provider and
the library tasks that select it, and `kraft admin harnesses <id>` one profile's
settings and users.
enforced-by: tests/cli/test_admin_harnesses.py::test_the_table_lists_each_profile_its_provider_and_the_tasks_using_it, tests/cli/test_admin_harnesses.py::test_one_profile_prints_its_settings_and_users
