---
title: Chain file keys
navigation:
  title: Overview
description: Every key a chain file accepts, from the chain down to each kind of task, plus read_only, extends, icons and canonical paths.
---

The keys a chain file under `config/chains/` accepts: the chain's own, then each
kind of node, step and task. For what a chain, node, task and gate are, see
[Vocabulary](/concepts/vocabulary). For the reusable components a chain extends,
see [Library and chains](/reference/configuration/library-and-chains).

## Example

A chain of three nodes: an agent implements, a command runs under a [fix loop](/concepts/vocabulary#fix-loop)
with a [judge](/concepts/vocabulary#judge), and a gate waits for approval. `implementation` and `strict_judge`
are components of the shipped library, which `extends` pulls in.

```yaml [config/chains/example.yaml]
id: example
description: Implement, check with a fix loop, then ask for approval.
nodes:
  - id: implementation
    extends: implementation          # a library node, tasks and all

  - id: checks
    kind: exec
    skippable: false
    policy:
      time_cap_minutes: 30
    steps:
      - id: lint
        tasks:
          - id: lint
            kind: subprocess
            command: sh -c 'npm run lint && npx tsc --noEmit'
    fix_loop:
      tasks:
        - id: repair
          kind: agent
          harness: claude
          profile: strong
          prompt: Fix what the checks reported.
      judge:
        id: judge
        extends: strict_judge
      max_attempts: 3

  - id: review
    kind: gate
    message: Review the change.
    reject_to: implementation
```

Kraft refuses a key that these tables do not list when it loads the chain, and
`kraft admin templates lint` reports it. A default of `required` means the key
has no default: the chain must set it, itself or through `extends`.

## Chain keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | The file name | The chain's name. |
| `description` | string | none | One line saying what the chain is for, shown in the chain list. Listed even for a chain that does not resolve. |
| `nodes` | list | required | The ordered list of nodes. At least one. |
| `policy` | mapping | none | The chain's layer of the [policy](/reference/configuration/policy). |

## Node keys

Every node takes these keys, then the keys of its kind.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | The node's name (`[a-z][a-z0-9_-]*`), unique in the chain. |
| `kind` | string | required, unless `extends` supplies it | `exec` runs work: one or more steps, each a group of tasks. `gate` waits for you. |
| `extends` | string | none | The [library](#the-library-and-extends) node this one builds on. |
| `policy` | mapping | none | This node's layer of the [policy](/reference/configuration/policy). Safety settings only tighten. Operational values stay within the `maxima` in `policy.yaml`. |
| `skippable` | boolean | `true` | `false` refuses `kraft item skip`, a chain revision's skip, and `kraft item create --skip-nodes` naming the node. |

### Exec node keys

An exec node declares `tasks` or `steps`, never both.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tasks` | list | one of `tasks` and `steps` is required, unless `extends` supplies it | Shorthand for one step named `main`. Its tasks are dispatched together, and the node is measured once all have settled. |
| `steps` | list | see `tasks` | Steps that run in order. A later step is not dispatched if an earlier one fails, and it sees what the earlier one left behind. |
| `on_failure` | mapping | none | A recovery pass: tasks or steps that run once after the node failed, before it is measured again. A task or step may carry its own `on_failure`; the nearest one to the failure wins. |
| `fix_loop` | mapping | none | Repair tasks or steps, an optional `judge` task, and an optional `max_attempts`. It re-runs until the node passes. See [Fix loop and judge](/reference/chain-nodes/fix-loop). |
| `escalation` | mapping | none | An agent task dispatched when the node is [stuck](/concepts/vocabulary#stuck) after recovery and the fix loop, before you are asked. |
| `on_base_changed` | mapping | none | What to re-run when a rebase moves the base. See [on_base_changed](#on_base_changed). |
| `read_only` | boolean | `false` | `true` makes Kraft verify that the node's steps leave the [worktree](/concepts/vocabulary#worktree) as they found it. Refused on a node with a `fix_loop`. See [read_only](#read_only). |
| `icon` | string | none | The [icon](#icons) the board draws for the node. |

A recovery that concludes no repair can help reports `failed` with a
`suggested_action`. See [suggested_action](/reference/chain-nodes/result-file#suggested_action).

### Gate node keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `message` | string | none | The text shown to a person deciding the gate. |
| `artifact` | string | none | The document the gate is decided on: a kind an earlier node `produces`. |
| `artifact_required` | boolean | `false` | `true` refuses approval while the `artifact` document is missing, with a `422` that says to retry the node that owes it. Needs an `artifact`. |
| `reject_to` | string | see Meaning | The exec node a rejection re-enters, with the reviewer's note. It must be before the gate. Without it, a rejection re-enters the nearest exec node before the gate, or re-opens the gate when there is none. |
| `timeout` | duration | none | How long the gate waits for a decision, such as `90m`. It may not exceed the `total_time_cap_minutes` around it. |
| `auto_review` | mapping | none | An agent task that may report a verdict first. It takes no `fallback`, and may `extends` a library task. See [Add a security review or a gate reviewer](/guides/customize/add-review-agents). |
| `chain_finalized` | boolean | `false` | `true` marks the final review. A `chain_finalized` gate always requires its document, whether or not it sets `artifact_required`. |

A gate takes no `icon`.

## on_base_changed

`on_base_changed` goes on an exec node whose work can move the worktree's base
branch. The shipped `default` chain sets it on `merge_request_feedback`:

```yaml [config/chains/default.yaml]
- id: merge_request_feedback
  extends: post_draft_feedback
  on_base_changed:
    restart_from: verification
```

When a rebase moves the base, Kraft re-runs the exec nodes from `restart_from`
through this node: the [span](/concepts/vocabulary#span). A gate in that range
keeps its approval after a clean rebase. It reopens, and needs approving again,
after a rebase that needed a conflict resolved.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `restart_from` | string | required | Names this node or an earlier exec node, never a gate. |
| `on_conflict` | mapping | none | Names the task or steps that resolve a conflicting rebase. |

If a retry or resume starts at a node that has its own `mr_rebase` task and an
`on_base_changed`, that task does the rebase, and a changed base still re-runs
the span.

## Step keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | The step's name, unique in the node. |
| `extends` | string | none | The library step this one builds on. Its tasks come from the library step unless this step sets `tasks`, which replace them. |
| `tasks` | list | required, unless `extends` supplies it | The step's tasks, dispatched together. At least one. |
| `on_failure` | mapping | none | A recovery pass for this step alone. |
| `policy` | mapping | none | This step's policy layer. |
| `skippable` | boolean | `true` | `false` refuses a skip of this step. |
| `read_only` | boolean | `false` | `true` verifies the step's tasks leave the worktree as they found it. See [read_only](#read_only). |
| `icon` | string | none | The [icon](#icons) the board draws for the step. |

## Task keys

Every task takes these keys, then the keys of its kind. A task takes no
`read_only`: set it on the step or node instead.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | The task's name (`[a-z][a-z0-9_-]*`), unique in the step. A library entry is named by its key instead. |
| `kind` | string | required, unless `extends` supplies it | `agent`, `subprocess`, `builtin` or `forge`. |
| `extends` | string | none | The library task this one builds on. |
| `scope` | string | `once` | `each_repository` runs the task once per selected repository of a workspace item. `once` runs it once. |
| `steering` | list | `[]` | Names of [steering profiles](/concepts/vocabulary#steering-profile) from the library's `steering` section. Only an agent task reads it. |
| `on_failure` | mapping | none | A recovery pass for this task alone. Allowed only on a task in one of an exec node's own steps. |
| `policy` | mapping | none | This task's own policy layer. See [What a task's policy does](#what-a-tasks-policy-does). |
| `skippable` | boolean | `true` | `false` refuses a skip of this task. |
| `icon` | string | none | The [icon](#icons) the board draws for the task. Refused on a fix loop's `judge`, whose icon is fixed. |

### What a task's policy does

- On a subprocess, builtin or [forge](/concepts/vocabulary#forge) task, the layer's own effect is
  `time_cap_minutes`, `total_time_cap_minutes` and `sandbox`.
- Its other caps ([harness](/concepts/vocabulary#harness), budget, tools and grants) apply to the agent that runs
  this task's `on_failure` recovery.

### Agent task keys

`kind: agent` runs a headless coding agent in the item's worktree.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `harness` | string | required | The [harness profile](/concepts/vocabulary#harness-profile) to run on, an ID from `harnesses.yaml`. A missing or disabled harness profile stops the task and waits for you unless the task declares `fallback`. |
| `prompt` | string | required | What the task is asked to do. Kraft's output contract comes before the skill and steering. |
| `skill` | string | none | One skill to launch the agent with (`kraft:code-review`, or a plugin's `plugin:skill`). The shipped `spec_author` sets `kraft:spec`, for example. A skill that cannot load stops the task and waits for you. |
| `produces` | string | none | The document kind the task writes (the shipped library uses `spec`, `plan`, `chain_revision`, `mr_meta`, `work_brief` and `review_brief`). |
| `profile` | string | none | An [agent profile](/reference/harnesses/agent-profiles) from `harnesses.yaml` (`strong`) that sets the model tier. Not allowed with `model` or `effort`. |
| `model`, `effort` | string | none | This task's runtime options, checked against what the [provider](/concepts/vocabulary#provider) of the task's harness profile accepts. Not allowed with `profile`. |
| `fallback` | list | unset | Where the launch goes when it is rate-limited or its harness is unavailable. `[]` means none. See [Fallback](/reference/harnesses/fallback-and-escalation). |
| `inputs` | list | `[]` | What Kraft hands the task: `review_package`, `carried_findings` or `previous_review`. |

### Subprocess task keys

`kind: subprocess` runs one command in the item's worktree.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `command` | string | required | The command to run in the worktree. It is split into arguments like a shell would, but runs with no shell, so `&&` and pipes need `sh -c '...'`. Exit 0 is `done`. See [Subprocess tasks](/reference/chain-nodes/subprocess-tasks). |

### Builtin task keys

`kind: builtin` is work Kraft does itself.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ref` | string | required | `kraft.verify_changed_test_scopes` runs the repo's own test scopes. `kraft.mr_rebase` rebases the worktree onto the item's [base branch](/concepts/vocabulary#base-branch). |
| `execution` | string | `sequential` | `sequential` or `parallel`. |

### Forge task keys

`kind: forge` is a merge-request action on GitHub or GitLab, resolved from the
`forge` in the repo's `repos.yaml` entry.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `target` | string | required | `mr.open_draft`, `mr.sync`, `mr.ci`, `mr.automated_review`, `mr.mark_ready`, `mr.external_approval`, `mr.merge` or `mr.post_merge_ci`. |
| `wait` | mapping | none | For a target that waits: `polling`, with an `initial_interval` and a `max_interval`. Set the timeout with the task's `policy`. |

```yaml
wait: {polling: {initial_interval: 30s, max_interval: 5m}}
policy: {total_time_cap_minutes: 90}
```

## read_only

`read_only` goes on a step or an exec node, never on a task. A task that sets it
is refused at load.

- **What is checked.** Before a read_only step's first task launches, Kraft
  records each repository of the checkout (the root and every workspace member):
  `HEAD`, `git status --porcelain=v1 -z` (ignored files stay out), and a hash of
  `git diff HEAD`. It reads them again after the last task exits. A read_only
  node does the same around all of its steps.
- **What counts as a change.** Any difference, including an untracked file or a
  commit.
- **What happens.** The item stops and waits for you, with a `read_only_violated`
  event and a stop reason naming the changed files. It is not a task failure, so
  no recovery, fix loop, or attempt is spent on it.
- **Exclusions.** The check does not run during a recovery or a fix loop. It runs
  again when a recovery retries the step's tasks. A step inside an `on_failure` or
  a `fix_loop` refuses `read_only`.
- **Sandboxed items.** In a [sandboxed](/concepts/vocabulary#sandbox) item, if the worktree gains a repository
  Kraft did not create, Kraft runs no host git there and reports that repository
  as the change.
- **Default.** Off. No shipped chain sets it. Setting it on both a node and its
  steps is allowed and redundant.

## Icons

An `icon` is the kebab-case name of a [Lucide](https://lucide.dev/icons) icon,
such as `file-text` or `circle-check`. It is optional on an exec node, a step
and a task, library components included. A name that is not kebab-case is a
schema error.

A kebab-case name that Kraft's Lucide version does not have still loads, and
the chain runs. `kraft admin templates lint` and a draft's problems report it
as `unknown icon`, at the component that sets it, and a save in Templates › Chains
or Library refuses it like any other issue. The board draws the kind's default
icon in its place.

## The library and extends

`config/library.yaml` holds reusable `tasks`, `steps`, `nodes`, and named
`steering` profiles (the named guidance a task or a repository selects). A chain component takes one with `extends: <name>`.

- A component extends one parent of its own kind. A node extends a node, never a
  task.
- Maps merge recursively. Lists replace.
- A reference that resolves to nothing is an error naming the file it came from.
  `kraft admin templates lint` reports every one.
- `kraft admin templates show ID --resolved` prints a chain with every library
  component expanded.

[Library and chains](/reference/configuration/library-and-chains) lists the
library's sections and has examples of each.

## Canonical paths

Every task, step, and node has a canonical path, for example
`spec.main.author`, `verification.review.code_review`, or
`merge_request_feedback.fix_loop.judge`. Events, sessions,
`kraft item retry --path`, and `kraft item skip --path` address work by it.

## In this section

- [Subprocess tasks](/reference/chain-nodes/subprocess-tasks): what Kraft passes a command, and how its exit code becomes a status.
- [Fix loop and judge](/reference/chain-nodes/fix-loop): when a fix loop opens, what each cycle does, and what the judge's verdicts mean.
- [Result file](/reference/chain-nodes/result-file): the JSON a task writes at `$KRAFT_RESULT_PATH`, field by field.
