# Concepts

Kraft's whole vocabulary is five words: work item, chain, node, hook point,
adapter — plus two ways a chain stops: gate and cap.

## Work item

A **work item** is one unit of work and produces one merge request. You create
one from the UI, the CLI (`kraft item create`), or an MCP tool
(`create_work_item`). It always lands **paused** — see
[Agent integration](agent-integration.md) for why an agent can never skip that.

## Chain

A work item enters as a **chain**: an ordered list of **nodes**, materialized
from a YAML template in `templates/`. Which template it uses is either named
explicitly with `--chain` (`kraft item create "..." --chain quick-task`) or
comes from the repo's `default_chain_template` in `repos.yaml`.

The simplest shipped template, `quick-task`, is three nodes with no gates at all:

```yaml
id: quick-task
nodes:
  - { id: env_setup,      tasks: [on.env.prepare],         gate_after: null }
  - { id: implementation, tasks: [on.implementation.start], gate_after: null }
  - { id: verify,         tasks: [on.test.run],            gate_after: null }
```

The shipped `default` template is the real one — spec and plan gates, a fix
loop on test failures, a rebase at the start of every node that writes or measures code, CI
watched with its own fix loop, a human-review gate, then merge:

```yaml
id: default
nodes:
  - { id: spec,             steps: [[on.mr.rebase], [on.spec.requested]], gate_after: spec_approval }
  - { id: plan,             steps: [[on.mr.rebase], [on.plan.requested]], gate_after: plan_approval }
  - { id: chain_review,     tasks: [on.chain.review_ready],      gate_after: chain_finalized, auto_escalate: true }
  - { id: implementation,   steps: [[on.mr.rebase], [on.env.prepare], [on.implementation.start], [on.repos.scan]], gate_after: null }
  - { id: verify,           steps: [[on.mr.rebase], [on.env.prepare], [on.test.run, on.review.local.run]], fix_loop: verify_fix_loop, gate_after: null }
  - { id: open_mr,          steps: [[on.mr.rebase], [on.mr.describe], [on.mr.open]], gate_after: null, rebase_bounce_to: verify }
  - { id: mr_checks,        steps: [[on.ci.poll], [on.review.mr.run]], fix_loop: ci_fix_loop, gate_after: null, rebase_bounce_to: verify }
  - { id: human_review,      tasks: [on.human_review.requested],   gate_after: human_review_approval, reject_to: implementation }
  - { id: mr_sync,           tasks: [on.mr.sync],                  gate_after: null }
  - { id: merge,             tasks: [on.merge],                    gate_after: null, rebase_bounce_to: verify }
  - { id: post_merge_watch,  tasks: [on.merge.watch],              gate_after: null }
```

A node's work is one or more **groups** of tasks. `tasks: [a, b]` is a single
group: `a` and `b` are dispatched together and the node is measured once both
have settled. `steps: [[a], [b]]` is two groups, run in order — `b` is not
dispatched at all if `a` fails, and it sees whatever `a` left behind. A node
declares one key or the other, never both.

Reach for `steps` when the second task needs the first task's result: the
default chain's `implementation` node runs the implementing agent and then
scans submodules, so the scan reads a worktree the agent has actually
touched rather than one it has not started on.

Every node that authors or measures code rebases onto the fetched tip of the
target branch as its first step. A work item can run for hours while other work
merges, and a spec written against stale code propagates into the plan and the
implementation, where a later rebase does not undo it. The rebase is cheap and
does nothing when the branch is already current. `implementation` and `verify`
then re-run `on.env.prepare`, because a rebase can land a new lockfile.

`open_mr` is the one node that stops on it: if its rebase moves the branch, the
merge request is not opened and the chain returns to `verify`, because the tests
that passed measured a base that no longer exists.

A node's optional fields change how the chain behaves around it:

| Field | Means |
|---|---|
| `gate_after` | Names a gate id. After this node runs, the chain halts and the work item becomes `needs_human` until someone approves, rejects, or steers. |
| `fix_loop` | Names a loop in `policy.yaml`'s `loops:` map. A failure here re-runs the node instead of escalating, up to that loop's `attempts`/`wall_clock_s` cap. |
| `on_failure` | A repair pass for *this node* — extra hook points dispatched once, after every task in the node has settled and the node still failed, before the next fix-loop attempt. Not a retry. A repair that is really about one task belongs on that task's binding instead (see [Configuration](configuration.md#registryyaml-hook-point-bindings)) — it travels with the task into every chain and costs one task's re-dispatch rather than the whole node's. |
| `rebase_bounce_to` | If this node's own git operation actually moves the branch, the chain jumps back to the named node (almost always `verify`) instead of continuing over a diff nothing has re-tested. |
| `reject_to` | Where a gate's "reject with a note" re-enters the chain — `human_review`'s rejection walks back to `implementation` with the reviewer's note as the steer. |
| `auto_escalate` | Notifies a person immediately when this node's gate opens, instead of waiting quietly on the board for someone to notice. |
| `auto_escalate_stuck` | Whether this node stopping for a reason that is *not* a pending gate — a fix loop out of attempts — dispatches an escalation turn before a human is asked. A different mechanism and a different trigger from `auto_escalate`; defaults on. |
| `auto_escalate_delay_s` | Seconds to hold either escalation back after the event that triggered it, so a human already about to look at the board is not preempted by an agent. `0`, the default, fires immediately. |

## Hook point and adapter

Each node names one or more **hook points** — `on.test.run`, `on.mr.open`,
`on.review.local.run` — and each hook point is bound to an **adapter** by
`templates/registry.yaml`. Four kinds of adapter exist:

- **`agent`** (`src/kraft/adapters/`) — runs a headless coding agent in a git
  worktree, on whichever [harness](harnesses.md) the binding names (`claude`
  by default; `codex` and `gemini` also ship), optionally with a named skill
  and an artifact it's expected to produce (a spec, a plan, a review brief).
- **`subprocess`** (`src/kraft/adapters/subprocess.py`) — runs a literal
  command, like `on.test.run`'s `[uv, run, pytest, -q]`.
- **`builtin`** (`src/kraft/builtins.py`) — work Kraft does itself in Python:
  preparing a worktree, scanning for touched submodules, rebasing before the
  merge request opens.
- **`forge`** (`src/kraft/adapters/forge/`) — talks to GitHub or GitLab
  (`backend: auto` resolves per repo from the `forge` recorded in that repo's
  `repos.yaml` entry): opening the merge request, polling CI, syncing,
  merging, watching the post-merge pipeline.

Rebinding a hook point to a different adapter, or a different skill, is a
`registry.yaml` edit — see [Configuration](configuration.md).

## Composing a template

A custom template doesn't have to restate the shipped ones. `extends: <id>`
starts from another template's already-resolved node list, then `remove`,
`insert_before`, and `insert_after` edit it — never both `extends` and a
`nodes:` list on the same template:

```yaml
id: quick-task-with-security-review
extends: quick-task
insert_after: { verify: [{ id: security_review, tasks: [on.review.security.run] }] }
```

(`on.review.security.run` is a real hook, registered but in no shipped
chain — see [Configuration](configuration.md#registryyaml-hook-point-bindings).)

`remove` names node ids to drop (unknown ids reject at load); `insert_before`/
`insert_after` are maps of an existing node id to a list of new node dicts
spliced in beside it (an unknown anchor id, or a naming collision with an
existing node, also rejects at load). A chain resolves `extends` recursively,
so a template can extend a template that itself extends another — but not
itself, directly or through a cycle.

## Gate

A **gate** is where a human decision belongs. A node with `gate_after: <id>`
stops the chain the moment it finishes; the work item's status becomes
`needs_human` and the board shows it under *Needs you*. From there:

- **Approve** — the chain continues to the next node.
- **Reject**, with a note — the chain re-enters at the node named by
  `reject_to` (or re-runs the same producing node if none is set), carrying
  the note as a steer.
- **Steer**, without rejecting — for a paused-mid-flight item, not a gate.

## Cap

Every retry loop is bounded. `policy.yaml`'s `loops:` map names each loop's
`attempts` and `wall_clock_s` ceiling — `verify_fix_loop`, `ci_fix_loop`,
`ci_wait`, `rebase_bounce`, `rebase_conflict`. A top-level `default:` key,
sibling to `loops:` rather than inside it, covers any `fix_loop` name not
listed there. Hitting either bound stops the chain and escalates to a person
with the full trace, rather than looping forever on a defect the agent can't
actually fix.

## Where this is enforced

`docs/intent/` states what the system is supposed to do as numbered
requirements, each with an `enforced-by:` line naming the test that pins it —
see [ARCHITECTURE.md](https://github.com/itsOmidKarami/kraft/blob/main/ARCHITECTURE.md#intended-behaviour-written-down-separately).
