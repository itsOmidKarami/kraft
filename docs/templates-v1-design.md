# Technical design: template schema V1

This document gives concrete shapes for the behaviours in
[`templates-v1.md`](../intent/templates-v1.md). It is deliberately a design
draft, not an implementation plan.

## Configuration layout

```text
templates/
  policy.yaml       # instance defaults and administrator maxima
  harnesses.yaml    # installed agent-runtime profiles
  repos.yaml        # repositories, workspaces, and monorepo areas
  library.yaml      # reusable steering, tasks, steps, and nodes
  chains/
    default.yaml    # one selectable chain per file
    quick-change.yaml
```

`registry.yaml`, hook names, and `gate_after` do not exist in V1.

On a major schema-version upgrade, Kraft warns before replacing the installed
template configuration, requires explicit acceptance (or the CLI's affirmative
flag), and makes a recoverable backup first.  A migration helper may be
offered, but V1 does not depend on one.

## Policy

```yaml
# policy.yaml
defaults:
  timeout_minutes: 60
  max_attempts: 3
  allowed_harnesses: [codex_default, claude_review]

maxima:
  token_budget: 2_000_000
  allowed_tools: [git, shell, pytest]
  allowed_harnesses: [codex_default, claude_review]
```

Safety restrictions only become tighter as policy is resolved:

```text
administrator maxima
  → repository restrictions
  → work-item operational override
  → chain → node → step → task
```

The work-item layer may change normal operational ceilings within the
administrator maxima. It cannot relax repository or administrator safety
restrictions.

## Harness profiles

A provider is a code or plugin adapter for an agent runtime. A harness profile
is one configured instance of a provider. Profiles belong to the installation,
not to a chain.

```yaml
# harnesses.yaml
harnesses:
  codex_default:
    provider: codex
    enabled: true
    executable: codex
    defaults:
      model: gpt-5.6-terra
      effort: medium

  claude_review:
    provider: claude
    enabled: true
    executable: claude
    defaults:
      model: sonnet
```

The provider package declares its typed capability and runtime-option schema.
It owns invocation, context injection, session resumption, skill loading,
usage parsing, and validation of those capabilities. A profile selects only
from that provider-declared surface; it does not contain arbitrary command
fragments or result-parser definitions.

## Repositories, workspaces, and areas

`repositories` names real Git repositories. Only a repository can own a forge
merge request. `workspaces` describe a virtual monorepo made from a root
repository and mounted child repositories. `areas` are path-scoped execution
contexts inside one real repository; they are never forge targets.

```yaml
# repos.yaml
repositories:
  product_root:
    path: /work/product
    enabled: true
    default_chain: default
    forge: { kind: github, project: acme/product }

  api:
    path: /work/product/services/api
    enabled: true
    forge: { kind: github, project: acme/api }
    worktree:
      setup: just setup
      local_files: [.env.test]
      environment:
        set: { CI: "1" }
        pass_through: [NPM_TOKEN]
    verification:
      test_scopes:
        - paths: [src/**, tests/**]
          command: just test
    steering: [project-standards]
    policy:
      allowed_harnesses: [codex_default, claude_review]

  platform:
    path: /work/platform
    enabled: true
    forge: { kind: github, project: acme/platform }
    areas:
      python_api:
        paths: [services/api/**]
        setup: uv sync
        verification:
          test_scopes:
            - paths: [services/api/**, tests/api/**]
              command: just test-api
      java_worker:
        paths: [services/worker/**]
        setup: ./gradlew classes
        verification:
          test_scopes:
            - paths: [services/worker/**, tests/worker/**]
              command: ./gradlew test

workspaces:
  product:
    root: product_root
    root_pointer_default: ignore
    members:
      api: { repository: api, path: services/api }
```

Repository and area test scopes are one table after resolution. A selected
area's setup is applied before its scope runs. A changed path that selects an
area omitted at intake still activates that area's setup and tests.

## Library components

Library names are reusable component names, not runtime identifiers. A task or
step written inside a reusable node has a local identifier; the resolved chain
assigns it a canonical complete execution path. `tasks` is authoring shorthand
for one resolved step named `main`, whether it appears on an exec node,
recovery plan, or fix loop.

```yaml
# library.yaml
steering:
  project-standards:
    instructions: |
      Keep changes focused. Run the relevant checks before finishing.

tasks:
  spec_author:
    kind: agent
    harness: codex_default
    prompt: Produce the work item's specification.
    produces: spec
    skill: kraft:spec
    steering: [project-standards]

  plan_author:
    kind: agent
    harness: codex_default
    prompt: Produce an executable implementation plan from the approved spec.
    produces: plan
    skill: kraft:plan

  implementer:
    kind: agent
    harness: codex_default
    model: gpt-5.6-terra
    effort: high
    prompt: Implement the approved plan.

  verify_changed_scopes:
    kind: builtin
    ref: kraft.verify_changed_test_scopes
    scope: each_repository
    execution: sequential

  repair_mr_feedback:
    kind: agent
    harness: codex_default
    model: gpt-5.6-terra
    effort: high
    prompt: Resolve the current CI failures and actionable merge-request feedback.

  strict_judge:
    kind: agent
    harness: claude_review
    effort: high
    prompt: Decide whether another repair attempt is justified.

  open_draft_mr:
    kind: forge
    target: mr.open_draft
    scope: each_repository

  sync_draft_mr:
    kind: forge
    target: mr.sync
    scope: each_repository

  await_mr_ci:
    kind: forge
    target: mr.ci
    scope: each_repository
    wait:
      timeout: 90m
      polling:
        initial_interval: 30s
        max_interval: 5m

  await_automated_review:
    kind: forge
    target: mr.automated_review
    scope: each_repository
    wait:
      timeout: 30m
      polling:
        initial_interval: 30s
        max_interval: 5m

  write_summary:
    kind: agent
    harness: claude_review
    prompt: Write the work-item summary and review brief.
    produces: review_brief
    skill: kraft:review-brief

  mark_mr_ready:
    kind: forge
    target: mr.mark_ready
    scope: each_repository

  await_external_approval:
    kind: forge
    target: mr.external_approval
    scope: each_repository
    wait:
      timeout: 7d
      polling:
        initial_interval: 5m
        max_interval: 1h

  merge_mr:
    kind: forge
    target: mr.merge
    scope: each_repository

  await_post_merge_ci:
    kind: forge
    target: mr.post_merge_ci
    scope: each_repository
    wait:
      timeout: 90m
      polling:
        initial_interval: 30s
        max_interval: 5m

nodes:
  implementation:
    kind: exec
    steps:
      - id: implementation
        tasks:
          - id: implement
            extends: implementer
      - id: verification
        tasks:
          - id: test_changed_scopes
            extends: verify_changed_scopes
    fix_loop:
      tasks:
        - id: repair
          extends: repair_mr_feedback
      judge:
        id: judge
        extends: strict_judge
      max_attempts: 2

  post_draft_feedback:
    kind: exec
    steps:
      - id: ci
        tasks:
          - id: await_ci
            extends: await_mr_ci
      - id: automated_review
        tasks:
          - id: await_review
            extends: await_automated_review
    on_failure:
      steps:
        - id: repair
          tasks:
            - id: repair_feedback
              extends: repair_mr_feedback
        - id: sync
          tasks:
            - id: sync_mr
              extends: sync_draft_mr
    fix_loop:
      steps:
        - id: repair
          tasks:
            - id: repair
              extends: repair_mr_feedback
        - id: sync
          tasks:
            - id: sync
              extends: sync_draft_mr
      judge:
        id: judge
        extends: strict_judge
      max_attempts: 3
```

The `produces` contract is Kraft-owned. An agent task may select one skill as a
method, but the contract is supplied before the skill and steering and cannot
be removed by either.

The provider or recipe behind `automated_review` owns webhooks, forge API
calls, CLI probes, and other transport details. The task reports only ordinary
pending, clean, actionable, or error results.

## Chain example

```yaml
# chains/default.yaml
id: default

nodes:
  - id: spec
    kind: exec
    tasks:
      - id: author
        extends: spec_author

  - id: spec_approval
    kind: gate
    message: Review and approve the specification.
    artifact: spec
    reject_to: spec

  - id: plan
    kind: exec
    tasks:
      - id: author
        extends: plan_author

  - id: plan_approval
    kind: gate
    message: Review and approve the implementation plan.
    artifact: plan
    reject_to: plan

  - id: implementation
    extends: implementation

  - id: local_review
    kind: gate
    message: Approve creating a draft merge request.
    reject_to: implementation

  - id: draft_merge_request
    kind: exec
    tasks:
      - id: open
        extends: open_draft_mr

  - id: merge_request_feedback
    extends: post_draft_feedback

  - id: work_item_summary
    kind: exec
    tasks:
      - id: author
        extends: write_summary

  - id: chain_review
    kind: gate
    chain_finalized: true
    message: Review the complete work item and merge-request summary.
    artifact: review_brief
    reject_to: implementation

  - id: mark_ready
    kind: exec
    tasks:
      - id: ready
        extends: mark_mr_ready

  - id: external_approval
    kind: exec
    tasks:
      - id: await
        extends: await_external_approval

  - id: merge
    kind: exec
    tasks:
      - id: merge
        extends: merge_mr

  - id: post_merge_ci
    kind: exec
    tasks:
      - id: await
        extends: await_post_merge_ci
```

The optional `local_review` gate is omitted by chains that should create a
draft immediately after local verification. This example also includes an
automated-review task; a repository without an expected automated reviewer
selects a chain variant that omits that task.

## Resolution and execution

Resolution expands `extends`, recursively merges objects, replaces arrays, and
validates references and component kinds. `extends` resolves only to a parent
of the same component kind: an inline chain node may therefore extend a
library node, but never a task, step, or chain. Resolved identifiers preserve
the authored local names but use complete canonical execution paths for
addressing:

- `implementation.verification.test_changed_scopes`
- `spec.main.author`
- `merge_request_feedback.on_failure.repair.repair_feedback`
- `merge_request_feedback.fix_loop.repair.repair`
- `implementation.fix_loop.main.repair`
- `merge_request_feedback.fix_loop.judge`

This makes every ordinary, recovery, and fix-loop step or task unambiguous in
events, sessions, diagnostics, steering, and operator controls. `main`,
`on_failure`, `fix_loop`, `judge`, `escalation`, `on_base_changed`, and
`on_conflict` are reserved step identifiers.

Because a canonical path is its container's path plus one local identifier,
global uniqueness is not a global property to enforce -- it follows from
checking that siblings within each immediate container have distinct local
identifiers. Resolution validates per container; it does not need a registry
of every path in the chain. Two further local rules make that sufficient:

- an authored identifier matches `[a-z][a-z0-9_-]*`, so it cannot contain the
  `.` separator and forge a path it does not occupy;
- an authored step identifier, or the identifier of a dedicated task in step
  position, is not one of the reserved segments above.

A resolver still builds a path-to-source map, but for diagnostics rather than
enforcement: it is what lets an error name the file a component's definition
came from when it was inherited. A duplicate is reported as its container's
path plus the colliding local identifier -- that is where the check happens and
it is sufficient to locate the collision, so no global path registry is built
to name "both paths".

Materialization binds a resolved chain to a work item, applying effective
policy, intake attachments, and an immutable target:

```yaml
target:
  kind: workspace
  workspace: product
  members: [api]
  include_root: false
  root_pointer_policy: bump
```

For a workspace item, tasks use an assembled checkout containing selected child
repositories. `scope: each_repository` fans a task out once for every selected
real repository; the default scope runs once in the ordinary assembled
context.

External waits persist their condition and next observation time, then release
the worker. A shared due scheduler performs later observations with bounded
backoff. A wait timeout requires human action rather than becoming a code
failure.

## Publication order

Draft merge requests permit CI and automated review before the final gate.
After final-gate approval, Kraft marks draft merge requests ready, waits for
external approval, and merges.

For workspace members, changed child merge requests land before a root pointer
update. A pointer-only root update is ignored by default; a requested bump is
pushed directly when permitted and otherwise becomes a root merge request.

When both root source and child repositories change, Kraft may create a draft
root merge request for CI. It must not mark that root merge request ready until
the child merge requests have landed and its pointers name their final
revisions.

## Operator controls and run forks

Pause is work-item-wide. Resume preserves completed work and resumes an agent
session when its provider supports it. A single steer applies to all paused
agent tasks unless the operator targets tasks individually.

Retrying creates a new immutable run fork. A task retry reruns that task and
later work; a step retry reruns the step and later work; a node retry reruns
the node and later work. Invalidated downstream gates reopen. A retry may carry
policy-bounded runtime changes but cannot change chain structure.

Skip is precise to a task, step, or node unless that component disallows it.
Manual completion and cancellation stop active work, require a reason, and
record an audit event. Manual escalation resumes its prior escalation session
by default; a new session is required to change that escalation's harness or
runtime options.

## Validation surface

`GET /templates/lint` validates the installed library and chains. `GET
/templates/{id}/resolved` returns a chain after component resolution, before
work-item materialization. `POST /templates/resolve` accepts either an unsaved
candidate against the installed library or a complete unsaved library in
isolation. The CLI exposes the corresponding lint and resolved-template views.
