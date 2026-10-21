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
| `nodes` | list | required, unless `extends` is given | The ordered list of nodes. At least one. A chain with `extends` holds none. |
| `policy` | mapping | none | The chain's layer of the [policy](/reference/configuration/policy). In a chain with `extends`, it may only tighten the parent's. |
| `extends` | string | none | The chain this one builds on: one of yours, or an add-on's `ns:id`. See [Extending a chain](/reference/configuration/library-and-chains#extending-a-chain). _Added in 2.2_ |
| `remove` | list of strings | none | With `extends`: the parent's nodes to drop, by id. The final gate cannot be one. _Added in 2.2_ |
| `add` | list | none | With `extends`: each entry an `after`, a parent node's id, and a `node`, a complete node to put directly after it. _Added in 2.2_ |
| `overrides` | mapping | none | With `extends`: canonical path to the keys to merge onto that component, such as `implementation.main.implement: { profile: deep }`. _Added in 2.2_ |

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
| `on_merge_rejected` | mapping | none | What to repair and re-run when a merge train or merge queue drops the merge request. Only on a node with an `mr.merge` task. See [on_merge_rejected](#on_merge_rejected). _Added in next_ |
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

## on_merge_rejected

_Added in next_

`on_merge_rejected` goes on the exec node that asks the forge to merge: a node
with an `mr.merge` task in its own steps. The shipped `default` chain sets it on
`merge`:

```yaml [config/chains/default.yaml]
- id: merge
  kind: exec
  tasks:
    - id: merge
      extends: merge_mr
  on_merge_rejected:
    restart_from: merge_request_feedback
    repair:
      tasks:
        - id: repair
          extends: repair_merge_queue_failure
```

When the forge's merge train or merge queue drops the merge request after Kraft
asked for the merge, Kraft repairs the branch, then re-runs the exec nodes from
`restart_from` through this node: the [span](/concepts/vocabulary#span). Every
approved gate in that range reopens, and needs approving again. In the shipped
chain that is `final_review`, so you see the repaired code before it goes back
onto the train.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `restart_from` | string | required | Names an exec node strictly before this one, never a gate or this node itself. When a `chain_finalized` gate comes before this node, it must name a node before that gate, so every rewind passes it. |
| `repair` | mapping | required | The tasks or steps that fix the branch, written like an `on_failure` plan. The shipped `repair_merge_queue_failure` runs the `kraft:merge-queue-repair` skill on the `strong` [agent profile](/reference/harnesses/agent-profiles). |

A node with `on_merge_rejected` cannot also declare a `fix_loop` or a
node-level `on_failure`: only the repair answers a drop. A task's own
`on_failure` is allowed.

Without `on_merge_rejected`, a drop stops the item at once, with the forge's
reason in the stop, even when the drop comes on the re-measure after the
node's `on_failure`. No `on_failure` or fix loop on the node runs for it, and
no escalation agent is sent to the stop: Kraft never re-queues a merge request
on its own.

### Merge trains and merge queues

A **GitLab merge train** or **GitHub merge queue** does not merge a merge
request when asked. It queues it, then runs a further pipeline on the merge
request combined with the target branch and everything ahead of it. That
pipeline can fail after the merge request's own pipeline went green, because the
target moved or because it runs tests the merge request pipeline does not.

**Detection.** Kraft reads from the forge, each time the `merge` node runs,
whether the target branch merges through a train or queue. There is no setting
to turn on. A repository without one waits for the merge exactly as before.

- GitLab: the project must have both `merge_trains_enabled` and
  `merge_pipelines_enabled`. A project that does not report them, or a read the
  forge refuses (403 or 404), counts as having no train.
- GitLab, queueing: on a train project Kraft adds the merge request through the
  merge-train API with the reviewed commit's sha, so it never merges a head
  nobody reviewed. Only when the project read says there is no train does Kraft
  merge with `glab mr merge`.
- GitLab, a failed project read: a read that fails any other way, such as a 5xx,
  a timeout or an answer that is not the project, fails the merge as a failed
  `glab mr merge` would. On GitLab before 19.1 `glab mr merge` can merge
  without going through the train.
- GitHub: the target branch has a merge queue. Kraft blames only a failed
  merge-group run created after the pull request's latest enqueue, so a failure
  from an earlier drop is never held against the current head. When there is
  no such run, for example because the checks do not run on GitHub Actions, a
  removal reason that names failed or timed-out checks or CI counts as `code`,
  and that reason is what the repair is given.

A read of the train or queue that fails says nothing about the merge, so Kraft
keeps waiting and notes it in the log. A drop is noticed on the next poll of the
merge wait. When the forge has not yet written why (no GitLab system note, no
GitHub removal event for this entry), Kraft keeps waiting and reads it again on
the next poll, up to the merge wait's own timeout.

**The drop.** Each drop is recorded as a `merge_queue_dropped` event with its
cause, the forge's reason, the failed jobs and the head. See the
[events](/reference/events). The cause is one of five:

| Cause | What it means | What Kraft does |
|---|---|---|
| `code` | The train or queue jobs failed. | Rebases the branch onto the target as it is now, hands the failed jobs and their log tails to the `repair`, then restarts. |
| `conflict` | The merge request no longer merges cleanly with the target. | Rebases the branch. A clean rebase restarts with no agent. A rebase that conflicts goes to the `repair` to resolve, then Kraft restarts. |
| `not_added` | GitLab only: the merge request never reached the train, because its own checks failed while it waited to be added. | Treated as `code`. |
| `infra` | The train or queue's own runners failed. | Asks the forge to queue the same head again, with no agent and no restart, up to 2 times. Then it stops for you. |
| `other` | A person took it off the train or queue, or the forge's reason cannot be read. | Stops for you. Kraft never re-queues what a person removed, and no repair, recovery or escalation agent runs for it. |

**The restart.** After the repair commits, or after a clean conflict rebase,
Kraft reopens the approved gates in the span, gives the span's fix loops a
fresh count and re-runs from `restart_from`. The head goes through the merge
request's pipeline and your final review again, and is then queued again.

The next agent is told why in a note. The review brief opens with a "Merge
train/queue rejection" section giving the cause, the reason, the failed jobs and
what the repair changed.

**What stops the item.** The repair must commit: a head the train already
rejected would only be rejected again, so a repair that commits nothing, fails,
or asks a question stops for you.

When a repair had already committed before it
stopped, whether it asked, failed, was paused or ran out of budget, the item is
held at `restart_from` with the span's gates reopened. A commit counts on the
item's branch in its own checkout or in any workspace member still to merge,
not only in the one whose merge request was dropped.

A repair that stopped before committing leaves the item on the merge node.
Whatever brings it back there, a resume, the rate-limit retry or your
`kraft item retry`, runs the repair again, with your steer, and never queues
the head the train rejected. Only a retry from the last gate before the merge
node, or from a node before it, goes through that gate and then queues the
head again.

The same goes for a Kraft restart while the repair works or the restart is
under way: if a repair had started or the branch moved, the resume holds the
item there and stops for you. A retry then walks back through `final_review`,
and the commit is never merged unseen.

**The cap.** Rewinds are counted under `<node>.on_merge_rejected`: 3 attempts
over 7 days unless `policy.yaml` names that key under `loops:`. See
[Loop keys](/reference/configuration/policy#loop-keys). Kraft checks it before
the repair agent runs, and at the cap the item stops with the kind `cap`,
without running an agent. A retry from a person resets it.

**Skipping a node.** When `kraft item create --skip-nodes` skips the node that
`restart_from` names, it moves to the nearest earlier exec node that is kept. If
none is left, the policy is dropped.

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
| `skill` | string | none | One skill to launch the agent with (`kraft:code-review`, an [add-on](/reference/configuration/addons#names)'s `release:release-notes`, or another tool's `plugin:skill`). The shipped `spec_author` sets `kraft:spec`, for example. A skill that cannot load stops the task and waits for you. |
| `produces` | string | none | The document kind the task writes (the shipped library uses `spec`, `plan`, `chain_revision`, `mr_meta`, `work_brief` and `review_brief`), to `run/items/<id>/docs/<kind>.md`. A re-run first keeps the current one as `history/<kind>.<n>.md`. |
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
