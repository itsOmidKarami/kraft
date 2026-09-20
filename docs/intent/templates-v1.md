# Intent: template schema v1

Intended behaviour for the V1 template system. This is a deliberate breaking
change from the legacy hook-and-`gate_after` format. These requirements are
unpinned until the migration supplies their implementation tests.

## REQ template-v1-is-not-backward-compatible

The system SHALL accept only the V1 template schema and SHALL NOT preserve
compatibility with legacy hook bindings or `gate_after` chains.

## REQ major-update-replaces-incompatible-template-config

When an update crosses a major version with an incompatible template schema,
the update SHALL install the new version's template configuration rather than
continue to use the incompatible configuration.

## REQ major-update-requires-explicit-acceptance

Before replacing configuration for an incompatible major update, the update
command SHALL warn about the breaking change and require explicit acceptance;
the `-y` option MAY provide that acceptance for non-interactive use.

## REQ major-update-preserves-replaced-configuration

Before an incompatible major update replaces template configuration, the system
SHALL create a recoverable backup of the configuration it replaces.

## REQ migration-helper-is-not-guaranteed

The system MAY provide a migration helper for a major template-schema change,
but the availability of such a helper SHALL NOT be required for the update.

## REQ template-library-has-shared-components-and-chain-files

The template directory SHALL contain one `library.yaml` for reusable tasks,
steps, nodes, and named steering profiles, and one selectable chain per file
under `chains/`.

## REQ registry-is-not-a-task-configuration-source

The system SHALL NOT use `registry.yaml` as a source of agent, subprocess,
forge, or built-in task configuration; those definitions SHALL live in typed
templates.

## REQ task-kinds-are-discriminated

Every task SHALL declare exactly one discriminated kind: `builtin`, `agent`,
`subprocess`, or `forge`.

## REQ agent-task-may-select-one-skill

An agent task MAY select one skill as its primary method. A task SHALL NOT
select more than one skill; composable additional guidance SHALL be expressed
through steering profiles.

## REQ agent-task-contract-precedes-skill-and-steering

The system SHALL provide an agent task's Kraft-owned output and lifecycle
contract before its selected skill and steering profiles. A selected skill or
steering profile SHALL NOT remove that contract.

## REQ selected-skill-must-be-available

When an agent task selects a skill that its execution environment cannot load,
the task SHALL stop for human action and SHALL NOT substitute a different
method.

## REQ provider-profile-and-agent-task-are-distinct

The system SHALL distinguish a code-owned agent-runtime provider, a configured
harness profile for that provider, and an agent task that selects a profile.

## REQ provider-owns-runtime-mechanics

An agent-runtime provider SHALL own invocation, context delivery, supported
runtime options, session resumption, skill loading, and normalized task
results for its runtime.

## REQ provider-declares-harness-capabilities

An agent-runtime provider SHALL declare the capabilities and runtime-option
schema it supports. Harness profiles and templates SHALL only select from that
provider-declared surface.

## REQ harness-profile-has-safe-instance-configuration

A harness profile MAY configure an enabled runtime instance, executable or
connection choice, and default runtime options. It SHALL NOT require template
authors to configure provider command syntax or result parsing.

## REQ agent-task-selects-capability-compatible-runtime-options

An agent task MAY select a harness profile and override its runtime defaults
only with options supported by the selected provider and allowed by policy.

## REQ unavailable-selected-harness-needs-human

When a selected harness profile is unavailable at runtime, the task SHALL stop
for human action and SHALL NOT silently select a different harness.

## REQ agent-roles-use-ordinary-agent-task-runtime-configuration

Ordinary work, recovery, fixing, judging, and escalation agent roles SHALL use
ordinary agent-task harness and runtime configuration. The system SHALL NOT
require special runtime fields for escalation or judging.

## REQ judge-runtime-is-independent-from-fixer-runtime

A judge's selected harness and runtime options SHALL be independent from the
fixing tasks it assesses.

## REQ resumed-escalation-preserves-original-runtime

When a manual escalation resumes an existing escalation session, it SHALL use
that session's original harness and runtime options. Changing them SHALL
require a new escalation session.

## REQ builtin-task-references-code-owned-actions

A built-in task SHALL name its action through `ref`, and the system SHALL
reject a reference to an action Kraft does not support.

## REQ changed-test-scope-verification-is-a-typed-built-in-task

Verification of repository test scopes SHALL be an explicitly configured
typed built-in task and SHALL NOT depend on a node name or another implicit
template convention.

## REQ changed-test-scope-verification-selects-safely

The changed-test-scope task SHALL run every scope selected by changed paths.
When the changed paths are empty or do not match a configured scope, it SHALL
run every configured scope.

## REQ changed-test-scope-verification-is-sequential-by-default

The changed-test-scope task SHALL run selected scopes sequentially by default.
It MAY run scopes in bounded parallelism only when its configuration explicitly
requests it.

## REQ changed-test-scope-verification-aggregates-results

The changed-test-scope task SHALL await all selected scope results and report
one aggregate task result.

## REQ exec-node-orders-concurrent-task-groups

An execution node's `tasks` SHALL be one concurrent task group; its `steps`
SHALL be ordered groups whose tasks run concurrently within each group.

## REQ exec-node-requires-one-execution-shape

An execution node SHALL define exactly one of `tasks` or `steps`.

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

## REQ gate-is-an-ordered-node

A human gate SHALL be represented by an ordered node with `kind: gate`; an
execution node SHALL NOT define `gate_after`.

## REQ gate-owns-gate-behaviour

A gate node SHALL own gate-specific configuration, including its message,
timeout, reject target, auto-escalation, review artifact reference, and any
dedicated marker such as `chain_finalized`.

## REQ resolved-chain-identifiers-are-unique

After resolution, node, step, and task occurrences SHALL each have a unique
canonical identifier across the complete chain, including recovery and
fix-loop plans. An authored node, step, or task identifier SHALL match
`[a-z][a-z0-9_-]*`, so that no identifier can contain the canonical path
separator, whitespace, or any other character that would make a resolved path
ambiguous.

## REQ component-identifiers-are-qualified-by-node-instance

When a reusable component supplies step or task identifiers, the resolved
chain SHALL assign a canonical identifier containing its complete execution
path. Ordinary execution uses `node.step.task`, including the resolved `main`
step for `tasks` shorthand. A recovery or fix plan includes its container,
such as `node.on_failure.step.task` or `node.fix_loop.step.task`. A dedicated
fix-loop judge or escalation task uses its container, such as
`node.fix_loop.judge` or `node.escalation.task`. This shall preserve global
uniqueness when a component is used more than once.

## REQ component-extends-has-one-parent

A reusable task, step, node, or chain MAY extend one same-namespace parent and
the system SHALL reject multiple parents, cross-namespace parents, and cycles.

## REQ extends-merges-objects-and-replaces-arrays

During `extends` resolution, mappings SHALL merge recursively, scalar values
including `null` SHALL replace inherited values, and arrays SHALL replace their
inherited arrays as a whole.

## REQ extends-cannot-change-kind

A derived component SHALL NOT change the discriminated `kind` of its parent.

## REQ resolved-chain-is-validated-before-use

The system SHALL reject a chain with missing references, invalid overrides,
duplicate identifiers, or invalid cross-node references before it is used.

## REQ template-resolution-preserves-source-context

The template resolver SHALL retain source paths for inheritance and validation
errors so an author can identify the input that needs correction.

## REQ authored-resolved-and-materialized-chains-are-distinct

The system SHALL distinguish the authored template library, a reusable fully
resolved chain, and a per-work-item materialized chain snapshot.

## REQ materialized-chain-is-immutable-work-item-input

A materialized chain SHALL contain effective policy values and intake-specific
decisions for one work item and SHALL NOT change as that item executes.

## REQ steer-can-address-paused-agent-tasks-individually

An operator MAY provide distinct steering instructions to selected paused agent
tasks. A steer SHALL not target a non-agent task.

## REQ steer-defaults-to-all-paused-agent-tasks

When an operator resumes paused work with one unqualified steer, the system
SHALL deliver that instruction to every paused agent task; selected tasks MAY
instead receive individual instructions.

## REQ resumed-agent-task-preserves-its-session-when-possible

When resuming a paused agent task, the system SHALL resume its durable session
when available, and otherwise restart that task with its original instruction
and any supplied steer.

## REQ pause-is-a-work-item-control

An operator MAY pause a work item. The system SHALL NOT expose pause as a
task, step, or node control.

## REQ resume-preserves-completed-work

Resuming paused work SHALL continue from its saved execution point and SHALL
not rerun work that completed before the pause.

## REQ task-retry-reruns-that-task-and-later-work

Retrying a task SHALL rerun that task, preserve completed sibling tasks in its
concurrent step, and rerun subsequent steps and nodes.

## REQ step-retry-reruns-that-step-and-later-work

Retrying a step SHALL rerun every task in that step and rerun subsequent steps
and nodes.

## REQ node-retry-reruns-that-node-and-later-work

Retrying an execution node SHALL rerun that node and every subsequent node.

## REQ work-item-restart-reruns-the-complete-chain

Restarting a work item SHALL rerun its chain from the first node.

## REQ retry-can-target-completed-work

An operator MAY retry a task, step, or execution node that completed earlier
in the work item's run.

## REQ retry-creates-an-immutable-run-fork

A retry SHALL preserve prior run data and create a new immutable run fork for
the retried work and its invalidated downstream work.

## REQ retry-reopens-invalidated-gates

A retry SHALL reopen every gate in its invalidated downstream scope. Gate
decisions before the retry target SHALL remain in effect.

## REQ retry-overrides-are-policy-bounded

An operator MAY change task configuration or policy for a retry when the
change is valid for that task and within the applicable policy bounds. A retry
SHALL NOT change the chain's structure, identifiers, order, or task kinds.

## REQ task-step-and-node-are-skippable-by-default

An operator MAY skip a task, step, or node unless that component explicitly
disallows skipping.

## REQ skip-stops-only-the-selected-scope

Before applying a skip, the system SHALL stop active work only within the
selected task, step, or node. Skipping a task SHALL NOT skip its sibling
tasks; an operator MAY skip the step when they intend to skip the group.

## REQ manual-completion-is-an-explicit-work-item-terminal-action

An operator MAY explicitly mark a work item complete. The action SHALL require
a reason, stop active work, record an audit event, and prevent further chain
execution.

## REQ manual-cancellation-is-an-explicit-work-item-terminal-action

An operator MAY explicitly cancel a work item. The action SHALL require a
reason, stop active work, record an audit event, and prevent further chain
execution.

## REQ manual-escalation-reuses-context-by-default

Manual escalation SHALL resume its previous escalation session by default so
the escalation agent retains the work item's prior context.

## REQ manual-escalation-may-start-fresh

An operator MAY request that a manual escalation start a new session instead
of resuming its prior one.

## REQ exec-node-runs-then-advances

The executor SHALL run an execution node's task group or ordered steps and,
when they complete successfully, advance to the following ordered node.

## REQ gate-node-opens-and-halts-execution

When the executor reaches a gate node, it SHALL open that gate and SHALL NOT
start the following node until the gate is approved.

## REQ gate-approval-advances-to-next-node

When a gate is approved, the executor SHALL advance to the node after that
gate in the materialized chain.

## REQ gate-rejection-follows-gate-reject-target

When a gate is rejected, the executor SHALL apply that gate node's own
`reject_to` behaviour.

## REQ gate-control-does-not-generate-review-work

A gate node SHALL be a decision control point and SHALL NOT generate its own
review artifact. A preceding execution node SHALL generate any artifact a gate
uses.

## REQ chain-finalized-remains-a-dedicated-marker

A gate with the dedicated `chain_finalized` marker SHALL retain Kraft's
chain-review behaviour; other gate nodes SHALL have ordinary pause and
approval behaviour.

## REQ gate-auto-review-is-explicit-and-bounded

A gate node MAY declare an automated review task. The system SHALL run it only
with work-item opt-in and within its effective delay and attempt limits.

## REQ attachment-behaviour-is-explicit-gate-configuration

Spec and plan attachment behaviour SHALL be configured explicitly on their
gate nodes and SHALL NOT be inferred from a preceding execution node.

## REQ task-recovery-retries-only-the-task

When a task-level recovery succeeds, the system SHALL retry only the failed
task.

## REQ step-recovery-retries-the-entire-step

When a step-level recovery succeeds, the system SHALL retry every task in the
failed concurrent step and SHALL NOT rerun preceding successful steps.

## REQ node-recovery-retries-the-entire-node

When an execution-node recovery succeeds, the system SHALL retry that node
from its first step.

## REQ parallel-step-settles-before-recovery

When one task in a concurrent step fails, the system SHALL allow already
started sibling tasks to settle and SHALL NOT start a later step before
recovery begins.

## REQ recovery-plan-supports-task-groups-or-steps

A recovery plan SHALL define exactly one of a concurrent task group or ordered
steps and SHALL NOT contain gates, nested recovery handlers, or a fix loop.

## REQ nearest-recovery-handler-wins

For a task failure, the system SHALL select at most one recovery handler, in
task, step, then execution-node precedence order.

## REQ recovery-tasks-run-after-a-concurrent-step-settles

When several failed tasks in one concurrent step have task-level recovery
handlers, the system SHALL run those recovery handlers only after that step has
settled and SHALL run them sequentially.

## REQ failed-recovery-enters-node-fix-loop-or-needs-human

When a selected recovery handler does not restore its scope, the system SHALL
enter the declaring execution node's fix loop when one exists, or otherwise
stop for a human.

## REQ fix-loop-is-an-exec-node-control

A fix loop SHALL be configured only on an execution node and SHALL name its
fixing tasks explicitly.

## REQ fix-loop-supports-one-ordered-repair-shape

A fix loop SHALL define exactly one concurrent task group or ordered steps.

## REQ fix-loop-remeasures-the-whole-node

After each successful fix-loop attempt, the system SHALL rerun the execution
node from its first step before deciding whether another attempt is needed.

## REQ fix-loop-is-bounded-and-detects-stall

A fix loop SHALL enforce its effective attempt and duration limits and SHALL
stop for a human when the loop is exhausted or detects that it is not making
progress.

## REQ fix-loop-judge-is-optional

A fix loop MAY declare a judge. Without a judge, the system SHALL repeat
measurement and fixing until the node is clean or the loop reaches another
stopping condition.

## REQ fix-loop-judge-runs-after-the-first-attempt

When configured, a fix-loop judge SHALL assess a measured result only after at
least one fixing attempt has run.

## REQ fix-loop-judge-has-three-decisions

A fix-loop judge SHALL decide whether to continue fixing, accept a clean
execution result, or stop for a human.

## REQ fix-loop-judge-cannot-override-limits

A fix-loop judge SHALL NOT cause the system to exceed the loop's effective
attempt or duration limits.

## REQ invalid-judge-result-does-not-block-the-loop

When a fix-loop judge cannot provide a valid decision, the system SHALL
continue under the loop's ordinary limits and stall detection.

## REQ stuck-escalation-is-an-exec-node-control

An execution node MAY declare a bounded escalation task that runs only after
its recovery and fix-loop controls cannot advance the node.

## REQ successful-stuck-escalation-retries-the-node

When a stuck escalation succeeds, the system SHALL retry that execution node
from its first step.

## REQ failed-or-questioning-stuck-escalation-needs-human

When a stuck escalation fails or asks a question, the system SHALL leave the
work item for a human.

## REQ base-change-restarts-a-declared-chain-span

An execution node MAY declare `on_base_changed.restart_from` to restart the
chain at an earlier execution node when its work changes the worktree base.

## REQ base-change-is-not-an-execution-failure

When `on_base_changed` applies, the system SHALL restart the declared chain
span without spending a recovery attempt or fix-loop attempt on that base
change.

## REQ base-change-restart-target-is-backward

The system SHALL reject an `on_base_changed.restart_from` target that is
missing, is not an execution node, or is later than the declaring node.

## REQ rebase-conflict-requires-explicit-handler

The system SHALL attempt automatic rebase-conflict resolution only when the
relevant execution node's `on_base_changed` configuration declares an explicit
`on_conflict` handler.

## REQ resolved-conflict-restarts-from-base-change-target

When an explicit conflict handler resolves a conflict and changes the worktree
base, the system SHALL apply that node's `on_base_changed` restart behaviour.

## REQ policy-is-layered-by-execution-scope

The effective task policy SHALL resolve from instance policy through repository,
work-item, chain, node, step, and task policy overrides, from broadest scope
to narrowest scope.

## REQ policy-has-defaults-and-administrator-maxima

The instance policy SHALL distinguish inheritable operational defaults from
non-overridable administrator safety maxima.

## REQ template-policy-cannot-relax-safety-ceilings

Template policy overrides SHALL only tighten inherited safety ceilings,
including budgets, allowed tools, permissions, and repository access.

## REQ repository-policy-cannot-relax-instance-safety

Repository policy overrides SHALL only tighten inherited safety ceilings and
SHALL remain effective for every chain and task that runs in that repository.

## REQ repositories-workspaces-and-areas-are-distinct

The system SHALL distinguish an independent repository, a workspace that
combines repositories, and a path-scoped area within one repository. An area
SHALL NOT be treated as an independent repository or forge target.

## REQ workspace-declares-root-and-members

A workspace SHALL declare its root repository and each member repository with
the path where it is mounted in that root.

## REQ work-item-target-selection-is-immutable

A work item MAY target one repository, selected members of a workspace, or a
workspace root and its members. The selected targets and root-pointer policy
SHALL be captured when the work item is materialized.

## REQ workspace-root-pointer-update-is-explicit

A workspace SHALL declare a default policy for submodule-pointer updates. A
work item MAY choose an allowed pointer-update policy for its selected target.

## REQ workspace-root-pointer-update-defaults-to-ignore

The default workspace root-pointer policy SHALL leave the root repository
unchanged.

## REQ workspace-pointer-bump-prefers-direct-push

When a selected root-pointer policy requests a bump without root source
changes, the system SHALL commit and push the pointer update directly to the
workspace root when permitted.

## REQ workspace-pointer-bump-falls-back-to-merge-request

When the system cannot push a requested root-pointer bump directly, it SHALL
create a merge request for the pointer update instead.

## REQ workspace-root-code-change-gets-a-root-merge-request

When a workspace work item changes source in the root repository, the system
SHALL create a merge request for that repository. Pointer-only root changes
SHALL instead follow the selected root-pointer policy.

## REQ workspace-tasks-have-an-assembled-checkout

A workspace-targeted work item SHALL provide tasks an assembled checkout that
contains its selected member repositories at their declared paths.

## REQ task-may-explicitly-fan-out-by-repository

A task MAY explicitly run once for each repository selected by a work item.
Tasks that do not opt in SHALL run in the work item's ordinary execution
context.

## REQ repository-area-can-declare-setup-and-test-scopes

A repository area MAY declare its setup requirements and test scopes. Area
test scopes SHALL use the same selection and result semantics as repository
test scopes.

## REQ selected-test-scope-activates-its-area-setup

Before running a selected test scope belonging to an area, the system SHALL
apply that area's setup requirements.

## REQ unexpected-area-changes-are-tested

When changed paths select a test scope from an area not chosen at intake, the
system SHALL still apply that area's setup requirements and run the scope.

## REQ work-item-target-is-typed-and-immutable

A work item SHALL select either one repository or a workspace target. A
workspace target MAY select member repositories and its root repository. The
selected targets, mount paths, base revisions, effective repository and area
policy, and root-pointer policy SHALL remain unchanged for that work item.

## REQ selected-repositories-get-corresponding-branches

The system SHALL create a corresponding work-item branch in every selected
repository. The branches MAY share one work-item branch name because each
repository has its own branch namespace.

## REQ changed-child-repositories-get-separate-merge-requests

When publishing a workspace work item, the system SHALL create one draft merge
request for each changed child repository.

## REQ draft-merge-request-enables-external-checks

The system MAY create a draft merge request before final-gate approval so CI
and automated merge-request review can run. A draft merge request SHALL NOT be
marked ready or merged before that approval.

## REQ optional-pre-draft-gate-keeps-work-local

A chain MAY declare a gate before draft merge-request creation. Until that gate
is approved, the work item SHALL remain local and SHALL NOT create a merge
request.

## REQ final-gate-governs-merge-request-readiness

After final-gate approval, the system SHALL mark the draft merge request ready
for external approval and merge.

## REQ external-wait-has-configurable-timeout-and-polling

An external-wait task SHALL allow configuration of its timeout and polling
intervals, subject to applicable policy limits.

## REQ external-wait-does-not-hold-an-active-worker

When an external condition is pending, the task SHALL persist its wait state
and next observation time, then release its worker resources.

## REQ external-waits-use-a-shared-due-scheduler

The system SHALL use one scheduler to observe due external waits. It SHALL
increase a wait's observation interval from its configured initial interval up
to its configured maximum interval while the condition remains pending.

## REQ external-wait-timeout-needs-human

When an external-wait task reaches its configured timeout, the system SHALL
stop for human action and SHALL NOT classify the timeout as a code failure.

## REQ external-wait-covers-merge-request-lifecycle

The shared external-wait mechanism SHALL support CI completion, automated
review settlement, external approval, merge completion, and post-merge CI.

## REQ automated-review-is-an-explicit-optional-task

A chain MAY declare a task that waits for automated merge-request review. When
no such task is declared, the system SHALL not expect automated review for that
chain.

## REQ automated-review-task-uses-ordinary-task-results

An automated-review task SHALL report ordinary pending, clean, actionable, or
error results. Actionable feedback SHALL enter the declaring execution node's
recovery or fix-loop controls.

## REQ automated-review-implementation-is-not-template-configuration

Templates SHALL NOT expose transport details such as webhook event names,
provider check names, comment authors, or API and CLI mechanics for automated
review. The selected task implementation SHALL own those details.

## REQ default-post-draft-flow-is-ordered

The default chain SHALL create a draft merge request, await CI, run any
declared automated-review task, address CI failures and actionable feedback,
produce a work-item summary and review, and then request final-gate approval.
After approval, it SHALL mark the merge request ready, await external approval,
and merge.

## REQ post-draft-feedback-uses-node-recovery-controls

CI failures and actionable automated-review feedback in the post-draft flow
SHALL enter that execution node's recovery or fix-loop controls. After a
successful repair, the system SHALL resynchronize the draft merge request and
remeasure the node.

## REQ missing-external-approval-is-normal-pending-state

The absence of required external merge-request approval SHALL be a normal
pending condition, not a failure. It SHALL NOT prevent the work-item summary,
review, or final gate from occurring before the approval wait begins.

## REQ child-merge-precedes-parent-pointer-update

The system SHALL wait for a changed child repository to merge before updating
a workspace root pointer to that child's revision.

## REQ root-source-draft-merge-request-may-run-early

When a workspace work item changes root source and child repositories, the
system MAY create a draft root merge request before the child merge requests
merge so root CI can run.

## REQ root-source-merge-request-readiness-waits-for-child-merges

When a workspace work item changes root source and child repositories, the
system SHALL not mark the root merge request ready until the child merge
requests have merged and the root contains their final pointer revisions.

## REQ blocked-child-merge-leaves-parent-unchanged

When a child merge request is rejected, blocked, or fails to merge, the system
SHALL leave its workspace root pointer unchanged and require human action.

## REQ template-policy-may-replace-operational-defaults

Template policy overrides MAY replace operational defaults, including timeouts
and retry or wait timing, in either direction.

## REQ work-item-policy-may-exceed-default-ceilings-within-admin-maximum

An operator editing a work item MAY raise a normal policy ceiling for that work
item, but SHALL NOT exceed an explicitly configured administrator maximum.

## REQ policy-override-rules-are-field-specific

The system SHALL apply field-specific restriction rules to policy overrides and
SHALL NOT treat policy overrides as an unrestricted generic merge.

## REQ template-lint-reports-library-validity

`GET /templates/lint` SHALL validate the complete installed template library
and report all parse, resolution, schema, reference, and identifier errors
without writing or reloading configuration.

## REQ resolved-template-api-shows-saved-chain

`GET /templates/{id}/resolved` SHALL return the fully resolved configuration of
a saved chain, before per-work-item materialization.

## REQ resolve-api-supports-candidate-and-library-input

`POST /templates/resolve` SHALL resolve a single unsaved candidate chain
against the installed library and SHALL also resolve a complete unsaved
template library in isolation, without writing either input to disk.

## REQ resolved-template-is-deterministic

A resolved-template response SHALL represent inheritance and component
expansion only; attachment-driven gate satisfaction and other per-work-item
materialization SHALL remain separate.

## REQ template-cli-exposes-lint-and-resolved-output

`kraft admin templates lint` SHALL lint the installed template library and exit
non-zero when errors exist. `kraft admin templates show <id> --resolved` SHALL
print a selected chain's resolved configuration.
