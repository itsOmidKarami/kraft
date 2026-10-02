---
title: Chain nodes
navigation:
  title: Overview
description: Node keys, task kinds, read_only, extends, and canonical paths in chain files.
---

The keys a chain file under `templates/chains/` accepts, and the keys of every
task kind. For what a chain, node, task, and gate are, see
[Concepts](/concepts/vocabulary). For the reusable components a chain extends,
see [Library and chains](/reference/configuration/library-and-chains).

## In this section

- [Subprocess tasks](/reference/chain-nodes/subprocess-tasks): what Kraft passes a command, and how its exit code becomes a status.
- [Fix loop and judge](/reference/chain-nodes/fix-loop): when a fix loop opens, what each cycle does, and what the judge's verdicts mean.
- [Result file](/reference/chain-nodes/result-file): the JSON a task writes at `$KRAFT_RESULT_PATH`, field by field.

## Chain keys

| Key | Meaning |
|---|---|
| `id` | The chain's name. Defaults to the file name. |
| `description` | One line saying what the chain is for, shown in the chain list. Listed even for a chain that does not resolve. |
| `nodes` | The ordered list of nodes. At least one. |
| `policy` | The chain's layer of the [policy](/reference/configuration/policy). |

## Node keys

Every node takes these keys, then the keys of its kind.

| Key | Meaning |
|---|---|
| `id` | The node's name (`[a-z][a-z0-9_-]*`), unique in the chain. |
| `kind` | `exec` runs work: one or more steps, each a group of tasks. `gate` waits for a person. |
| `extends` | The [library](#the-library-and-extends) node this one builds on. |
| `policy` | This node's layer of the [policy](/reference/configuration/policy). Safety settings only tighten. Operational values stay within the administrator's maxima. |
| `skippable` | `false` refuses `kraft item skip`, a chain revision's skip, and `kraft item create --skip-nodes` naming the node. Default `true`. |

### Exec node keys

An exec node declares `tasks` or `steps`, never both.

| Key | Meaning |
|---|---|
| `tasks` | Shorthand for one step named `main`. Its tasks are dispatched together, and the node is measured once all have settled. |
| `steps` | Steps that run in order. A later step is not dispatched if an earlier one fails, and it sees what the earlier one left behind. |
| `on_failure` | A recovery pass: tasks or steps that run once after the node failed, before it is measured again. A task or step may carry its own `on_failure`; the nearest one to the failure wins. |
| `fix_loop` | Repair tasks or steps, an optional `judge` task, and an optional `max_attempts`. It re-runs until the node passes, up to `max_attempts` and the wall clock that `policy.yaml` gives the loop (`<node>.fix_loop`). See [Fix loop and judge](/reference/chain-nodes/fix-loop). |
| `escalation` | An agent task dispatched when the node is stuck after recovery and the fix loop, before a person is asked. |
| `on_base_changed` | What to re-run when a rebase moves the base. `restart_from` names an earlier node. `on_conflict` names the task or steps that resolve a conflicting rebase. |
| `read_only` | `true` makes Kraft verify that the node's steps leave the worktree as they found it. Refused on a node with a `fix_loop`. See [read_only](#read_only). |
| `icon` | The [icon](#icons) the board draws for the node. |

A recovery that concludes no repair can help reports `failed` with a
`suggested_action` (`skip`, `retry` or `abandon`, with a reason) in its
[result file](/reference/chain-nodes/result-file). `kraft view show` prints the one command that takes it. An infra stop
suggests `retry`.

### Step keys

| Key | Meaning |
|---|---|
| `id` | The step's name, unique in the node. |
| `tasks` | The step's tasks, dispatched together. At least one. |
| `on_failure` | A recovery pass for this step alone. |
| `policy` | This step's policy layer. |
| `skippable` | `false` refuses an operator's skip. |
| `read_only` | `true` verifies the step's tasks leave the worktree as they found it. See [read_only](#read_only). |
| `icon` | The [icon](#icons) the board draws for the step. |

### Gate node keys

| Key | Meaning |
|---|---|
| `message` | The text shown to the reviewer. |
| `artifact` | The document the reviewer decides on: a kind an earlier node `produces`. |
| `artifact_required` | `true` refuses approval while the `artifact` document is missing, with a `422` that says to retry the node that owes it. Needs an `artifact`. Default `false`. |
| `reject_to` | The node a rejection re-enters, with the reviewer's note. It must be an exec node before the gate. Without it, a rejection re-enters the nearest exec node before the gate, or re-opens the gate when there is none. |
| `timeout` | How long the gate waits for a decision. It may not exceed the `total_time_cap_minutes` around it. |
| `auto_review` | An agent task that may report a verdict first. It takes no `fallback`, and may `extends` a library task. |
| `chain_finalized` | `true` marks the final review. |

A `chain_finalized` gate cannot be approved without its document either,
whether or not it sets `artifact_required`. Dropping that rule would make every
existing final gate, the shipped `default` chain's included, approvable with
nothing to read. A gate takes no `icon`.

## Task keys

Every task takes these keys, then the keys of its kind.

| Key | Type | Meaning |
|---|---|---|
| `id` | string | The task's name (`[a-z][a-z0-9_-]*`), unique in the step. A library entry is named by its key instead. |
| `kind` | string | `agent`, `subprocess`, `builtin` or `forge`. |
| `extends` | string | The library task this one builds on. |
| `scope` | string | `each_repository` runs the task once per selected repository of a workspace item. `once` (default) runs it once. |
| `steering` | list | Names from the library's `steering` section. Only an agent task reads it. |
| `on_failure` | mapping | A recovery pass for this task alone. Allowed only on a task in one of an exec node's own steps. |
| `policy` | mapping | This task's own policy layer. A subprocess, builtin or forge task reads only `time_cap_minutes`, `total_time_cap_minutes` and `sandbox` itself. Its harness, budget, tool and grant caps bound the agent of its `on_failure` recovery, which runs under this layer. |
| `skippable` | boolean | `false` refuses an operator's skip. Default `true`. |
| `icon` | string | The [icon](#icons) the board draws for the task. Refused on a fix loop's `judge`, whose icon is fixed. |

A task takes no `read_only`. Set it on the step or node instead.

### Agent task keys

`kind: agent` runs a headless coding agent in the item's worktree.

| Key | Type | Meaning |
|---|---|---|
| `harness` | string | The [harness profile](/reference/harnesses) to run on, an ID from `harnesses.yaml`. A missing or disabled profile stops the task for a human unless the task declares `fallback`. |
| `prompt` | string | What the task is asked to do. Required. Kraft's output contract comes before the skill and steering. |
| `skill` | string | One skill to launch the agent with (`kraft:code-review`, or a plugin's `plugin:skill`). `spec_author` and `plan_author` default to `kraft:spec` and `kraft:plan`. A skill that cannot load stops the task for a human. |
| `produces` | string | The document kind the task writes (`spec`, `plan`, `work_brief`, `review_brief`). |
| `profile` | string | An [agent profile](/reference/harnesses/agent-profiles) from `harnesses.yaml` (`strong`) that sets the model tier. Not allowed with `model` or `effort`. |
| `model`, `effort` | string | This task's runtime options, checked against what the profile's provider accepts. Not allowed with `profile`. |
| `fallback` | list | Where the launch goes when it is rate-limited or its harness is unavailable. `[]` means none. See [Fallback](/reference/harnesses/fallback-and-escalation). |
| `inputs` | list | What Kraft hands the task: `review_package`, `carried_findings` or `previous_review`. |

### Other task kinds

| Kind | Key | Meaning |
|---|---|---|
| `subprocess` | `command` | The command to run in the worktree. It is split into arguments like a shell would, but runs with no shell, so `&&` and pipes need `sh -c '...'`. Exit 0 is `done`. See [Subprocess tasks](/reference/chain-nodes/subprocess-tasks). |
| `builtin` | `ref` | The work Kraft does itself: `kraft.verify_changed_test_scopes` (the repo's own test scopes) or `kraft.mr_rebase` (rebase the worktree onto the item's base branch). |
| `builtin` | `execution` | `sequential` (default) or `parallel`. |
| `forge` | `target` | A merge-request action on GitHub or GitLab, resolved from the `forge` in the repo's `repos.yaml` entry: `mr.open_draft`, `mr.sync`, `mr.ci`, `mr.automated_review`, `mr.mark_ready`, `mr.external_approval`, `mr.merge` or `mr.post_merge_ci`. |
| `forge` | `wait` | For a wait, `{polling: {initial_interval: 30s, max_interval: 5m}}`. Set the timeout with the task's `policy: {total_time_cap_minutes: 90}`. |

In Templates › Chains and Library, `ref`, `target` and `inputs` list the
values above as you type, each with a line on what it does. So do `steering`,
and a policy's `grants` and `allowed_harnesses`. A value that is not on the
list is marked where you typed it and is not saved.
An agent task's `fallback` is edited there as its list of entries, a harness
and a profile picked for each; an entry's `model` and `effort` are set in YAML.

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
- **What happens.** The item stops for a person with a `read_only_violated`
  event and a stop reason naming the changed files. It is not a task failure, so
  no recovery, fix loop, or attempt is spent on it.
- **Exclusions.** Recovery and fix loops run outside the check. A recovery's
  retry of the step's tasks is checked again. A step inside an `on_failure` or a
  `fix_loop` refuses `read_only`.
- **Sandboxed items.** Kraft runs no host git in a worktree that gained a
  repository it did not create. That repository is the reported change.
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

`templates/library.yaml` holds reusable `tasks`, `steps`, `nodes`, and named
`steering` profiles. A chain component takes one with `extends: <name>`.

- A component extends one parent of its own kind. A node extends a node, never a
  task.
- Maps merge recursively. Lists replace.
- A reference that resolves to nothing is an error naming the file it came from.
  `kraft admin templates lint` reports every one.
- `kraft admin templates show ID --resolved` prints a chain with every library
  component expanded.

```yaml
# chains/quick-task-with-review.yaml
id: quick-task-with-review
nodes:
  - id: implementation
    kind: exec
    tasks:
      - { id: implement, extends: implementer }
  - id: verification
    extends: verification        # the library node, fix loop and all
```

## Canonical paths

Every task, step, and node has a canonical path, for example
`spec.main.author`, `verification.review.code_review`, or
`merge_request_feedback.fix_loop.judge`. Events, sessions,
`kraft item retry --path`, and `kraft item skip --path` address work by it.
