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
explicitly (`kraft item create "..." ` picks from the templates you have) or
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
loop on test failures, a rebase right before the merge request opens, CI
watched with its own fix loop, a human-review gate, then merge:

```yaml
id: default
nodes:
  - { id: spec,             tasks: [on.spec.requested],          gate_after: spec_approval }
  - { id: plan,              tasks: [on.plan.requested],          gate_after: plan_approval }
  - { id: chain_review,      tasks: [on.chain.review_ready],      gate_after: chain_finalized, auto_escalate: true }
  - { id: env_setup,         tasks: [on.env.prepare],             gate_after: null }
  - { id: implementation,    tasks: [on.implementation.start],    gate_after: null }
  - { id: repos_scan,        tasks: [on.repos.scan],              gate_after: null }
  - { id: verify,            tasks: [on.test.run, on.review.local.run], fix_loop: verify_fix_loop, gate_after: null }
  - { id: pre_mr_rebase,     tasks: [on.mr.rebase],                gate_after: null, rebase_bounce_to: verify }
  - { id: mr_meta,           tasks: [on.mr.describe],              gate_after: null }
  - { id: open_mr,           tasks: [on.mr.open],                  gate_after: null }
  - { id: mr_checks,         tasks: [on.ci.poll, on.review.mr.run], fix_loop: ci_fix_loop, gate_after: null, rebase_bounce_to: verify, on_failure: [on.mr_checks.repair] }
  - { id: human_review,      tasks: [on.human_review.requested],   gate_after: human_review_approval, reject_to: implementation }
  - { id: mr_sync,           tasks: [on.mr.sync],                  gate_after: null }
  - { id: merge,             tasks: [on.merge],                    gate_after: null, rebase_bounce_to: verify }
  - { id: post_merge_watch,  tasks: [on.merge.watch],              gate_after: null }
```

A node's optional fields change how the chain behaves around it:

| Field | Means |
|---|---|
| `gate_after` | Names a gate id. After this node runs, the chain halts and the work item becomes `needs_human` until someone approves, rejects, or steers. |
| `fix_loop` | Names a loop in `policy.yaml`'s `loops:` map. A failure here re-runs the node instead of escalating, up to that loop's `attempts`/`wall_clock_s` cap. |
| `on_failure` | Extra hook points dispatched before the next fix-loop attempt — a repair pass, not a retry. |
| `rebase_bounce_to` | If this node's own git operation actually moves the branch, the chain jumps back to the named node (almost always `verify`) instead of continuing over a diff nothing has re-tested. |
| `reject_to` | Where a gate's "reject with a note" re-enters the chain — `human_review`'s rejection walks back to `implementation` with the reviewer's note as the steer. |
| `auto_escalate` | Notifies a person immediately when this node's gate opens, instead of waiting quietly on the board for someone to notice. |

## Hook point and adapter

Each node names one or more **hook points** — `on.test.run`, `on.mr.open`,
`on.review.local.run` — and each hook point is bound to an **adapter** by
`templates/registry.yaml`. Four kinds of adapter exist, in `src/kraft/adapters/`:

- **`agent`** — runs a headless coding agent (`claude`, and see
  [Agent integration](agent-integration.md)) in a git worktree, optionally with
  a named skill and an artifact it's expected to produce (a spec, a plan, a
  review brief).
- **`subprocess`** — runs a literal command, like `on.test.run`'s
  `[uv, run, pytest, -q]`.
- **`builtin`** — work Kraft does itself in Python: preparing a worktree,
  scanning for touched submodules, rebasing before the merge request opens.
- **`forge`** — talks to GitHub or GitLab (`backend: auto` resolves per repo
  from the `forge` recorded in that repo's `repos.yaml` entry): opening the
  merge request, polling CI, syncing, merging, watching the post-merge pipeline.

Rebinding a hook point to a different adapter, or a different skill, is a
`registry.yaml` edit — see [Configuration](configuration.md).

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
`ci_wait`, `rebase_bounce`, and a `default` fallback for anything else. Hitting
either bound stops the chain and escalates to a person with the full trace,
rather than looping forever on a defect the agent can't actually fix.

## Where this is enforced

`docs/intent/` states what the system is supposed to do as numbered
requirements, each with an `enforced-by:` line naming the test that pins it —
see [ARCHITECTURE.md](https://github.com/itsOmidKarami/kraft/blob/main/ARCHITECTURE.md#intended-behaviour-written-down-separately).
