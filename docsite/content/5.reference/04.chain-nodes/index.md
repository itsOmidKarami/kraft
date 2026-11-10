---
title: Chain file keys
navigation:
  title: Overview
description: Every key a chain file accepts, from the chain down to each kind of task, plus extends, icons and canonical paths, with links to the pages on the features the keys switch on.
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
        extends: kraft-default:strict_judge
      max_attempts: 3

  - id: review
    kind: gate
    message: Review the change.
    reject_to: implementation
```

Kraft refuses a key that these tables don't list when it loads the chain, and
`kraft admin templates lint` reports it. **Required** says whether the chain
must set the key, itself or through `extends`. **Default** is the value Kraft
uses when the key is absent, or `unset` when an absent key does nothing.

## Chain keys

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `id` | string | no | The file name | The chain's name. |
| `description` | string | no | unset | One line saying what the chain is for, shown in the chain list. Listed even for a chain that doesn't resolve. |
| `nodes` | list of nodes | yes, unless `extends` is given | — | The ordered list of nodes. At least one. A chain with `extends` holds none. |
| `policy` | [mapping](/reference/configuration/policy#policy-fields) | no | unset | The chain's layer of the [policy](/reference/configuration/policy). In a chain with `extends`, it may only tighten the parent's. |
| `extends` | string | no | unset | The chain this one builds on: one of yours, or an add-on's `ns:id`. See [Extending a chain](/reference/configuration/library-and-chains#extending-a-chain). _Added in 2.2_ |
| `remove` | list of node IDs | no | unset | With `extends`: the parent's nodes to drop, by ID. The final gate can't be one. _Added in 2.2_ |
| `add` | list of `{after, node}` | no | unset | With `extends`: each entry an `after`, a parent node's ID, and a `node`, a complete node to put directly after it. _Added in 2.2_ |
| `overrides` | mapping of canonical path to keys | no | unset | With `extends`: canonical path to the keys to merge onto that component, such as `implementation.main.implement: { profile: deep }`. _Added in 2.2_ |
| `runner` | string | no | unset | Where it runs: a machine or a [group](/concepts/how-workers-and-machines-behave#groups) from [`workers.yaml`](/reference/configuration/workers), `any`, or `local`. See [Placing a step on a machine](/reference/configuration/library-and-chains#placing-a-step-on-a-machine). _Added in 2.4_ |

## Node keys

Every node takes these keys, then the keys of its kind.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `id` | string | yes | — | The node's name (`[a-z][a-z0-9_-]*`), unique in the chain. |
| `kind` | `exec` or `gate` | yes, unless `extends` supplies it | — | `exec` runs work: one or more steps, each a group of tasks. `gate` waits for you. |
| `extends` | string | no | unset | The [library](#the-library-and-extends) node this one builds on. |
| `policy` | [mapping](/reference/configuration/policy#policy-fields) | no | unset | This node's layer of the [policy](/reference/configuration/policy). Safety settings only tighten. Operational values stay within the `maxima` in `policy.yaml`. |
| `skippable` | boolean | no | `true` | `false` refuses `kraft item skip`, a chain revision's skip, and `kraft item create --skip-nodes` naming the node. |

### Exec node keys

An exec node declares `tasks` or `steps`, never both.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `tasks` | list of tasks | one of `tasks` and `steps`, unless `extends` supplies it | — | Shorthand for one step named `main`. Its tasks are dispatched together, and the node is measured once all have settled. |
| `steps` | list of steps | one of `tasks` and `steps`, unless `extends` supplies it | — | Steps that run in order. A later step isn't dispatched if an earlier one fails, and it sees what the earlier one left behind. |
| `on_failure` | mapping: `tasks` or `steps`, and `read_only` | no | unset | A recovery pass that runs once after the node failed, before it is measured again. The nearest `on_failure` to the failure wins, on the node, a step or a task. `read_only: true` makes the whole plan read-only; see [read_only](/reference/chain-nodes/read-only). |
| `fix_loop` | [mapping](/reference/chain-nodes/fix-loop): `tasks` or `steps`, `judge`, `max_attempts` | no | unset | Repair tasks or steps, an optional `judge` task, and an optional `max_attempts`. It re-runs until the node passes. See [Fix loop and judge](/reference/chain-nodes/fix-loop). |
| `escalation` | agent task | no | unset | An agent task dispatched when the node is [stuck](/concepts/vocabulary#stuck) after recovery and the fix loop, before you are asked. On a node with `on_merge_rejected`, see [on_merge_rejected](#on_merge_rejected). |
| `on_base_changed` | [mapping](#on_base_changed) | no | unset | What to re-run when a rebase moves the base. See [on_base_changed](#on_base_changed). |
| `on_merge_rejected` | [mapping](#on_merge_rejected) | no | unset | What to repair and re-run when a merge train or merge queue drops the merge request. Only on a node with an `mr.merge` task. See [on_merge_rejected](#on_merge_rejected). _Added in 2.3_ |
| `read_only` | boolean | no | `false` | `true` makes Kraft verify that the node's steps leave the [worktree](/concepts/vocabulary#worktree) as they found it. Refused on a node with a `fix_loop`. See [read_only](/reference/chain-nodes/read-only). |
| `icon` | string | no | unset | The [icon](#icons) the board draws for the node. |
| `runner` | string | no | unset | Where it runs, as in the chain's `runner`. _Added in 2.4_ |

The steps of a fix loop or a recovery plan may not set `runner`: they, and a fix loop's judge, run where the node runs.

A recovery that concludes no repair can help reports `failed` with a
`suggested_action`. See [suggested_action](/reference/chain-nodes/result-file#suggested_action).

### Gate node keys

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `message` | string | no | unset | The text shown to a person deciding the gate. |
| `artifact` | string | no | unset | The document the gate is decided on: a kind an earlier node `produces`. |
| `artifact_required` | boolean | no | `false` | `true` refuses approval while the `artifact` document is missing, with a `422` that says to retry the node that owes it. Needs an `artifact`. |
| `reject_to` | string | no | unset | The exec node a rejection re-enters, with the reviewer's note. It must be before the gate. Without it, a rejection re-enters the nearest exec node before the gate, or re-opens the gate when there is none. |
| `timeout` | duration | no | unset | How long the gate waits for a decision, such as `90m`. It may not exceed the `total_time_cap_minutes` around it. |
| `auto_review` | agent task | no | unset | An agent task that may report a verdict first. It takes no `fallback`, and may `extends` a library task. See [Add a security review or a gate reviewer](/guides/customize/add-review-agents). |
| `auto_review_mode` | `decides` or `checks_first` | no | `decides` | What the `auto_review` verdict may do: `decides` (approve, reject or fix) or `checks_first` (reject, or hand the gate to you; never approve). Does nothing on a gate with no `auto_review`. See [What the reviewer can decide](/guides/customize/add-review-agents#what-the-reviewer-can-decide). _Added in 2.5_ |
| `runner` | string | no | unset | Where the gate's automated review runs, as in the chain's `runner`. _Added in 2.4_ |
| `chain_finalized` | boolean | no | `false` | `true` marks the final review. A `chain_finalized` gate always requires its document, whether or not it sets `artifact_required`. Its approval decides what a later `mr.merge` may merge: see [What the final review approves](/reference/chain-nodes/final-review-approval). |

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
after a rebase that needed a conflict resolved. A `chain_finalized` gate in the
range reopens after a clean rebase too, since a fix loop in the span can commit
before the walk passes it again.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `restart_from` | string | yes | — | Names this node or an earlier exec node, never a gate. On a node with an `mr.merge` task after a `chain_finalized` gate, it must name a node before that gate. |
| `on_conflict` | mapping: `tasks` or `steps`, and `read_only` | no | unset | Names the task or steps that resolve a conflicting rebase. |

If a retry or resume starts at a node that has its own `mr_rebase` task and an
`on_base_changed`, that task does the rebase, and a changed base still re-runs
the span.

## on_merge_rejected

_Added in 2.3_

`on_merge_rejected` goes on the exec node with an `mr.merge` task in its own steps. When the forge's merge train or merge queue drops the merge request, Kraft repairs the branch and re-runs the exec nodes from `restart_from` through this node. What a drop is, the causes, the restart, the cap and the escalation are on [Merge queue and train drops](/reference/chain-nodes/merge-queue-drops); the shipped `default` chain's setting is there too.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `restart_from` | string | yes | — | Names an exec node strictly before this one, never a gate or this node itself. When a `chain_finalized` gate comes before this node, it must name a node before that gate, so every rewind passes it. |
| `repair` | mapping: `tasks` or `steps`, and `read_only` | yes | — | The tasks or steps that fix the branch, written like an `on_failure` plan. The shipped `repair_merge_queue_failure` runs the `kraft:merge-queue-repair` skill on the `strong` [agent profile](/reference/harnesses/agent-profiles). |

Kraft refuses at load:

- A `fix_loop` on the same node.
- An `on_failure` on the node, or on one of its steps or tasks, that isn't [read_only](/reference/chain-nodes/read-only), including one a library component brings in. A `tasks` plan marks the plan itself; a `steps` plan marks the plan or every step.
- An `on_base_changed` on this node, or on any node with an `mr.merge` task after the `chain_finalized` gate, that doesn't restart before that gate.

## Step keys

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `id` | string | yes | — | The step's name, unique in the node. |
| `extends` | string | no | unset | The library step this one builds on. Its tasks come from the library step unless this step sets `tasks`, which replace them. |
| `tasks` | list of tasks | yes, unless `extends` supplies it | — | The step's tasks, dispatched together. At least one. |
| `on_failure` | mapping: `tasks` or `steps`, and `read_only` | no | unset | A recovery pass for this step alone. |
| `policy` | [mapping](/reference/configuration/policy#policy-fields) | no | unset | This step's policy layer. |
| `skippable` | boolean | no | `true` | `false` refuses a skip of this step. |
| `read_only` | boolean | no | `false` | `true` verifies the step's tasks leave the worktree as they found it. See [read_only](/reference/chain-nodes/read-only). |
| `icon` | string | no | unset | The [icon](#icons) the board draws for the step. |
| `runner` | string | no | unset | Where it runs, as in the chain's `runner`. _Added in 2.4_ |

## Task keys

Every task takes these keys, then the keys of its kind. A task takes no
`read_only`: set it on the step or node instead.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `id` | string | yes | — | The task's name (`[a-z][a-z0-9_-]*`), unique in the step. A library entry is named by its key instead. |
| `kind` | `agent`, `subprocess`, `builtin` or `forge` | yes, unless `extends` supplies it | — | `agent` runs a coding agent, `subprocess` a command, `builtin` is work Kraft does itself, and `forge` acts on GitHub or GitLab. |
| `extends` | string | no | unset | The library task this one builds on. |
| `scope` | `once` or `each_repository` | no | `once` | `each_repository` runs the task once per selected repository of a workspace item. `once` runs it once. |
| `steering` | list of steering profile names | no | `[]` | Names of [steering profiles](/concepts/vocabulary#steering-profile) from the library's `steering` section. Only an agent task reads it. |
| `on_failure` | mapping: `tasks` or `steps`, and `read_only` | no | unset | A recovery pass for this task alone. Allowed only on a task in one of an exec node's own steps. |
| `policy` | [mapping](/reference/configuration/policy#policy-fields) | no | unset | This task's own policy layer. See [What a task's policy does](#what-a-tasks-policy-does). |
| `skippable` | boolean | no | `true` | `false` refuses a skip of this task. |
| `icon` | string | no | unset | The [icon](#icons) the board draws for the task. Refused on a fix loop's `judge`, whose icon is fixed. |
| `outputs` | mapping: `files`, `variables` | no | unset | Only an agent or subprocess task in an exec node's own steps. `files` and `variables` name what the task leaves for later tasks. See [Step outputs](/reference/chain-nodes/step-outputs). _Added in next_ |
| `reads` | list of `step.task` or `node.step.task` | no | `[]` | Only an agent, subprocess or `ci.pipeline` task. The earlier tasks whose outputs this one is given, each `step.task` or `node.step.task`. See [Step outputs](/reference/chain-nodes/step-outputs). _Added in next_ |

### What a task's policy does

- On a subprocess, builtin or [forge](/concepts/vocabulary#forge) task, the layer's own effect is
  `time_cap_minutes`, `total_time_cap_minutes` and `sandbox`.
- Its other caps ([harness](/concepts/vocabulary#harness), budget, tools and grants) apply to the agent that runs
  this task's `on_failure` recovery.

### Agent task keys

`kind: agent` runs a headless coding agent in the item's worktree.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `harness` | string | yes | — | The [harness profile](/concepts/vocabulary#harness-profile) to run on, an ID from `harnesses.yaml`. A missing or disabled harness profile stops the task and waits for you unless the task declares `fallback`. |
| `prompt` | string | yes | — | What the task is asked to do. Kraft's output contract comes before the skill and steering. |
| `skill` | string | no | unset | One skill to launch the agent with (`kraft:code-review`, an [add-on](/reference/configuration/addons#names)'s `release:release-notes`, or another tool's `plugin:skill`). The shipped `spec_author` sets `kraft:spec`, for example. A skill that can't load stops the task and waits for you. |
| `produces` | string | no | unset | The document kind the task writes (the shipped library uses `spec`, `plan`, `chain_revision`, `mr_meta`, `work_brief` and `review_brief`), to `run/items/<id>/docs/<kind>.md`. A re-run first keeps the current one as `history/<kind>.<n>.md`. |
| `profile` | string | no | unset | An [agent profile](/reference/harnesses/agent-profiles) from `harnesses.yaml` (`strong`) that sets the model tier. Not allowed with `model` or `effort`. |
| `model`, `effort` | string | no | unset | This task's runtime options, checked against what the [provider](/concepts/vocabulary#provider) of the task's harness profile accepts. Not allowed with `profile`. |
| `fallback` | [list of fallback entries](/reference/harnesses/fallback-and-escalation) | no | unset | Where the launch goes when it is rate-limited or its harness is unavailable. `[]` means none. See [Fallback](/reference/harnesses/fallback-and-escalation). |
| `inputs` | list of input names | no | `[]` | What Kraft hands the task: `review_package`, `carried_findings`, `previous_review`, `bot_feedback` (what the review bots said once they settled; with nothing said the task is skipped), `triage_verdict` (where that triage wrote its verdict), or `pr_rules` ([the repo's pull-request rules](/reference/configuration/repos#pull-request-rules)). _Added in 2.4_ |

### Subprocess task keys

`kind: subprocess` runs one command in the item's worktree.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `command` | string | yes | — | The command to run in the worktree. It is split into arguments like a shell would, but runs with no shell, so `&&` and pipes need `sh -c '...'`. Exit 0 is `done`. See [Subprocess tasks](/reference/chain-nodes/subprocess-tasks). |

### Builtin task keys

`kind: builtin` is work Kraft does itself.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `ref` | `kraft.verify_changed_test_scopes` or `kraft.mr_rebase` | yes | — | `kraft.verify_changed_test_scopes` runs the repo's own test scopes. `kraft.mr_rebase` rebases the worktree onto the item's [base branch](/concepts/vocabulary#base-branch). |
| `execution` | `sequential` or `parallel` | no | `sequential` | For `kraft.verify_changed_test_scopes`: `sequential` runs the changed test scopes one after another, `parallel` starts all of them at once. |
| `suite` | `targeted` or `full` | no | `targeted` | Only on `kraft.verify_changed_test_scopes`: `targeted` runs each scope's [`targeted_command`](/reference/configuration/repos#targeted_command) where it has one, and `full` always runs its `command`. _Added in 2.4_ |

### Forge task keys

`kind: forge` is an action on GitHub or GitLab, resolved from the `forge` in the
repo's `repos.yaml` entry: a merge-request step, or `ci.pipeline`, a wait for one
CI pipeline by ID.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `target` | string | yes | — | `mr.open_draft`, `mr.sync`, `mr.ci`, `mr.automated_review`, `mr.mark_ready`, `mr.external_approval`, `mr.merge` or `mr.post_merge_ci`. `ci.pipeline` waits for one pipeline by ID. _Added in next_ |
| `wait` | mapping: `polling`, `grace_period` | no | unset | For a target that waits: `polling`, with an `initial_interval` and a `max_interval`. Set the timeout with the task's `policy`. |
| `wait.grace_period` | duration | no | `60s` | Only on `mr.automated_review`: how long a review bot must stay quiet after its last activity on the head to count as finished. Refused on any other target. _Added in 2.4_ |
| `pipeline` | string | yes, on `ci.pipeline` | — | Only on `ci.pipeline`: the pipeline's ID as digits, or `$NAME` (or `${NAME}`) for a variable a task in `reads` left. See [Waiting for a pipeline by ID](/reference/chain-nodes/wait-for-a-pipeline). _Added in next_ |
| `reviewers` | list of `{bot: LOGIN}` or `{check: NAME}` | no | unset | Only on `mr.automated_review`: the reviewers it waits for, each `{bot: LOGIN}` or `{check: NAME}`. The item's `reviewers` override and the repo's `automated_review` come first. `[]` names none. _Added in 2.4_ |

`mr.post_merge_ci` watches the CI of the commit the item merged, however many merges land on the base branch after it. A run cancelled for a newer push moves the watch onto the base's tip, which holds that merge, and records `post_merge_ci_moved`.

```yaml
wait: {polling: {initial_interval: 30s, max_interval: 5m}}
policy: {total_time_cap_minutes: 90}
```

A review wait that names its bots and waits for them to settle:

```yaml
- id: await_review
  kind: forge
  target: mr.automated_review
  reviewers: [{bot: coderabbitai}, {check: security-scan}]
  wait: {grace_period: 90s}
```

The shipped chain follows it with a triage step. See [How review and CI waits settle](/concepts/how-review-and-ci-waits-settle).

## Icons

An `icon` is the kebab-case name of a [Lucide](https://lucide.dev/icons) icon,
such as `file-text` or `circle-check`. It is optional on an exec node, a step
and a task, library components included. A name that isn't kebab-case is a
schema error.

A kebab-case name that Kraft's Lucide version doesn't have still loads, and
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

## Group node

_Added in next_

A `group` node holds the execution nodes that a plan's slices share, its body. Only chain
revision makes one. A chain file that declares a group is refused, and so is an extending
chain's `add`. You see one in an item's revised chain and in the chain editor, which never
creates one.

| Key | Type | Meaning |
|---|---|---|
| `nodes` | list of exec nodes | The body, run by each child item in its own worktree. It holds no gate, no forge task, no `on_merge_rejected` and no `on_base_changed`. |
| `unforked` | list of node IDs | The body nodes the item runs itself when it doesn't split, and when a rejection comes back to the group. Every body node when empty. |
| `on_conflict` | recovery plan | What the join hands a conflict to before it asks you, at `<group>.on_conflict`. Chain revision sets it to the library task `resolve_join_conflict`, your own or else `kraft-default`'s; without one, a conflict stops for you. |

A rejection or restart may name a group as its target. It may not name a node inside one. A
`reads` entry may not cross a group's boundary in either direction.

## Canonical paths

Every task, step and node has a canonical path, for example
`spec.main.author`, `verification.review.code_review`, or
`merge_request_feedback.fix_loop.judge`. Events, sessions,
`kraft item retry --path`, and `kraft item skip --path` address work by it. A node in a
[group](#group-node) has the group's ID first: `slices.verification.review.code_review`.

## In this section

- [The shipped chains](/reference/chain-nodes/shipped-chains): the nodes of the default and quick-task chains in order, with the gates marked.
- [Subprocess tasks](/reference/chain-nodes/subprocess-tasks): what Kraft passes a command, and how its exit code becomes a status.
- [Fix loop and judge](/reference/chain-nodes/fix-loop): when a fix loop opens, what each cycle does, and what the judge's verdicts mean.
- [Result file](/reference/chain-nodes/result-file): the JSON a task writes at `$KRAFT_RESULT_PATH`, field by field.
- [Merge queue and train drops](/reference/chain-nodes/merge-queue-drops): how Kraft detects a drop, what each cause does, and when the item stops.
- [What the final review approves](/reference/chain-nodes/final-review-approval): the head an approval pins and what `mr.merge` may merge.
- [Waiting for a pipeline by ID](/reference/chain-nodes/wait-for-a-pipeline): how a `ci.pipeline` task waits for one pipeline, and what each state does.
- [read_only](/reference/chain-nodes/read-only): how Kraft verifies that a step or node leaves the checkout unchanged.
- [Step outputs](/reference/chain-nodes/step-outputs): how a task leaves files and variables for a later task.
