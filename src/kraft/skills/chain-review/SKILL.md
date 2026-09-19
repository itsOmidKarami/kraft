---
name: chain-review
description: Use when a work item has just had its implementation plan approved and the orchestrator asks whether the rest of the loaded chain still fits the work. Decides whether to add, lighten, or leave alone the not-yet-executed nodes, and emits a revised chain tail for human approval at the chain_finalized gate.
---

# Chain Review

You are looking at a work item whose spec and plan were just approved. The chain
it is running was chosen at intake, from a template, **before either document
existed**. Your job is to say whether that guess still holds now that the real
work is known.

You are not reviewing the plan. The plan is already approved and is not yours to
change. You are reviewing the *chain* — the sequence of nodes that will run
after this one — in light of the plan.

## The default is to change nothing

Most of the time the template was right. A chain that was chosen sensibly at
intake and a plan that came out roughly the size everyone expected need no
intervention, and emitting the chain unchanged is the correct, common answer.

Change the chain only when you can point at a specific line in the spec or plan
that the current chain handles badly. "This could plausibly benefit from more
review" is not such a line. If you cannot name the evidence, leave it alone.

## What you get

- The approved **spec** and the approved **plan**.
- `chain_definition` — the full chain, and `current_node_id`, which is the
  `chain_review` node you are running inside.
- The **allowed hook set** and, for every hook point already in the not-yet-
  executed tail, its **resolved `registry.yaml` binding** — `model`,
  `escalate_model`, `effort`, `permission_mode`, `allowed_tools`,
  `deny_tools`, `command`, `skill`, `backend`, `handler` — read-only context
  appended after your task instruction under "Resolved hook bindings for the
  current tail". `model`/`escalate_model`/`effort` are what
  `proposed_node_overrides` above lets you override, per node. The rest —
  `permission_mode`, `allowed_tools`, `deny_tools`, `command`, `skill`,
  `backend`, `handler` — is never yours to change; see "What you may flag"
  below.
- The work item's repos, when the item spans more than one.

## The hard rule: only the tail

You may touch **only nodes that have not executed yet** — the nodes after
`current_node_id`. Everything at or before it has already run. Its gates were
already approved by a human, its worker sessions already exist, and its events
are already in the log.

Emitting a "revision" that alters an executed node is not a smaller mistake than
a wrong heuristic. It is a corrupt chain.

## Output

The orchestrator parses the artifact body as JSON — the whole thing, not the
first JSON value it finds. After the front matter block, write **only** the
object below: no heading, no prose, no ```` ```json ```` fence, nothing before
or after it. Anything else you want the human at the gate to see belongs
inside `rationale`.

```
{ status: "ready_for_approval" | "error",
  revised_chain_nodes: [Node, ...],
  flags: [{ hook_point: string, field: string, current_value: any, concern: string }, ...],
  rationale: string }
```

(`flags` may be an empty list or omitted entirely — that means "nothing to
flag," not an error.)

`revised_chain_nodes` is the **complete** not-yet-executed tail, not a diff and
not a patch. The orchestrator splices it in wholesale on approval. If you are
changing one node, the other tail nodes still appear, unchanged, in your output
— omitting a node deletes it.

Every node you write is exactly:

```
{ id: string, tasks: [hook_point, ...] | steps: [[hook_point, ...], ...],
  gate_after: string|null, fix_loop: string|null,
  on_failure?: [hook_point, ...] | null,
  reject_to?: string | null,
  rebase_bounce_to?: string | null,
  auto_escalate?: boolean | null,
  auto_escalate_stuck?: boolean | null,
  auto_escalate_delay_s?: int | null,
  proposed_node_overrides?: { model?: string, escalate_model?: string, effort?: string } }
```

The field above is node-level only; a task-level repair lives on that task's
registry binding instead (see below).

A node carries `tasks` **or** `steps`, never both. `tasks` is one group: every
task in it is dispatched at once and the node is measured when they have all
settled. `steps` is a list of those groups, run in order, and the node stops at
the first group that fails — so `steps: [[on.test.run], [on.review.local.run]]`
means a red suite stops the node before the review is dispatched at all, while
`tasks: [on.test.run, on.review.local.run]` means the reviewer is dispatched
while the suite is still running.

The prompt shows you the tail's current shape. A node listed there with `steps`
has ordering that somebody chose on purpose: re-emit it as `steps`. Re-emitting
it as a flat `tasks` list is a proposal to make those tasks concurrent, and
will be read as one.

`on_failure`, `reject_to`, and `rebase_bounce_to` are yours to set directly —
you are not limited to carrying them forward blind. Set one only when the spec
or plan gives you a reason to (a reject target that should route somewhere
other than where it already does).

`on_failure` on a **node** is the outer repair: it runs once, after every task
in the node has settled and the node still failed, and the whole node is then
re-measured. It is the right place only for a repair that is about this node's
combination of tasks.

A repair that is really about **one task** — reading a red pipeline, triaging a
failing test suite — does not belong here at all. It lives on that task's
registry binding, where it travels with the task into every chain that runs it,
and where it costs one task's re-dispatch instead of the whole node's. You
cannot set that from here; say so in your rationale and name the task, and a
human will put it in `registry.yaml`.

A node may write `steps:` instead of `tasks:` — a list of groups, run in order,
concurrent within a group:

```
{ id: string, steps: [[hook_point, ...], ...], gate_after: string|null, ... }
```

`tasks` is the one-group shorthand and stays perfectly valid; emit it whenever
a node's tasks have no ordering between them. Use `steps` only when the plan
gives you a reason one task must finish before another starts — a rebase before
the thing that reads the rebased tree, a scan after the agent that changes what
it scans. A node may declare one or the other, never both. A node whose id
already existed keeps its `steps` only if you re-emit it with the same `tasks`
list; change the `tasks` and the ordering is rebuilt as one concurrent group, so
write `steps:` yourself when you change a stepped node.

Omit a field on a node whose id already existed in the tail
and the orchestrator carries its old value forward unchanged, exactly as it
always has; a node id you invented (one you are adding) gets `null` for
whichever of these you omit — you cannot give a brand-new node a reject
target this way.

`reject_to` and `rebase_bounce_to`, when you do set them, must name a real
node id at or before the node declaring them — either a node still in your
own tail, or one from earlier in the chain that "Nodes already run" below
lists. A forward reference, or a name that resolves to
nothing, fails validation and the whole approval is rejected — nothing is
spliced.

`proposed_node_overrides` is the cost/capability dial, per node
(`model`/`escalate_model`/`effort` — the same three fields a work item's
own item-wide override already carries, see "Resolved hook bindings"
below). Propose one only when the plan gives you a reason: a node whose
task just got heavier (add `effort: high`), or lighter (drop to a cheaper
`model`). Applied atomically with the rest of your revision on approval, as
a per-node override — it does not touch the registry binding itself.

The three escalation fields are yours too, and only `auto_escalate` is about
this node's gate: it has an agent review the gate before a human is asked,
which is worth setting on a gate whose artifact an agent can judge and wrong on
one that is a human's decision. `auto_escalate_stuck` covers a stop that is not
a gate at all -- a fix loop out of attempts -- and `auto_escalate_delay_s`
holds either back that many seconds, so a human already on their way to the
board is not preempted. Leave all three unset unless the spec or plan gives you
a reason.

- `tasks` — hook points, and **only names from the allowed hook set**. A name you
  invented is not a task the orchestrator can run; it is a chain that fails
  validation, or worse, a node that silently does nothing.
- `steps` — the same hook points, in ordered groups, for a node whose sequence
  matters. Adding a task to a node that has `steps` means choosing which group
  it joins, or giving it one of its own: a security review added to `verify`
  belongs after the suite, not beside it.
- `gate_after` — one of `spec_approval`, `plan_approval`, `chain_finalized`,
  `human_review_approval`, or `null`. These four are the entire set. You cannot
  create a new gate.
- `fix_loop` — a loop-counter name, or `null`. A node with a `fix_loop` must have
  at least one task; a loop with nothing to measure never terminates.

`rationale` is one short paragraph, written for the human standing at the
`chain_finalized` gate. State what you changed and the line of the spec or plan
that made you change it — or state that the chain fits and why. Someone approves
this diff; a diff with no stated reason makes their gate decorative.

Use `status: "error"` when you genuinely cannot decide — a chain whose tail
references hooks not in the allowed set, a plan that contradicts the spec. Say so
in `rationale` rather than guessing.

## When to add

| Evidence in the spec or plan | Reasonable change |
|---|---|
| Touches authentication, sessions, tokens, secrets, or permission checks | Add a security-review task to `verify` |
| Changes a database schema or a migration | Add a migration/verification task before `open_mr` |
| Changes a public API shape or a wire format other code depends on | Add a contract/compat task to `verify` |
| Spans several repos with an ordering requirement | Confirm `open_mr` / `mr_checks` / `merge` are all present; a multi-repo item that skips `mr_checks` merges unverified |
| Plan's own risk section names a failure mode the chain never checks for | Add the task that checks for it |

Each of these presumes the task exists in the allowed hook set. **If the right
hook is not available, do not approximate it with a different one.** Leave the
chain as it is and say so in `rationale` — a human reading "this needs a security
review and no such hook is registered" can act on it. A human reading a chain
that quietly ran the wrong task cannot.

## When to lighten

Lightening is riskier than adding — you are removing a check someone thought was
worth running — so demand stronger evidence.

| Evidence | Reasonable change |
|---|---|
| Plan touches only docs, comments, or non-executing content | Drop test/review tasks that have nothing to run against |
| Plan is a single-file change with no branching logic and its own test | Drop the redundant tier, not the whole `verify` node |
| A node's tasks are all inapplicable to the repos involved | Drop that node |

Never drop:

- **`human_review`** or any node carrying `gate_after`. Gates are the human's
  control over the run. Deciding a human need not look is not your decision.
- **`merge`**, `open_mr`, or anything that moves the work toward landing.
- The last remaining verification in a chain that changes executable code.

### What you may flag, never propose

`permission_mode`, `allowed_tools`, `deny_tools`, `command`, `skill`,
`backend`, and `handler` are shown to you for context only. You cannot
propose a new value for any of them — there is no field in your schema for
it, and one you invent is dropped. If one of these looks wrong for what the
plan is about to do (a node about to touch secrets running with a permission
mode wider than it needs, a review hook missing a `deny_tools` entry the
plan's own risk section calls for), name it in a top-level `flags` list instead:

```
flags: [{ hook_point: string, field: string, current_value: any, concern: string }, ...]
```

Each entry is one sentence in `concern`, naming what you saw and why it
matters. This is not a rejection of the chain — a non-empty `flags` list
still ships with `status: "ready_for_approval"`. It puts the concern in
front of the human at the `chain_finalized` gate so they can go edit
`registry.yaml` themselves; you never edit it, propose a value for it, or
withhold approval over it.

## When to leave it alone

- The chain differs from what you would have picked, but nothing is wrong with it.
- You want to reorder nodes for tidiness.
- The plan is large — size alone is not a reason; a big plan running the standard
  chain is the normal case.
- You are unsure. Unsure means no change.

## Worked examples

**Adds.** Plan implements a login endpoint and a session cookie. `verify` runs
`on.test.run` and `on.review.local.run`. Auth is in scope and no task looks at it
— add the security-review hook to `verify`, keep everything else, and say in
`rationale` that the plan introduces session handling.

**Lightens.** Plan rewrites three paragraphs in a design document. No code
changes. `verify`'s test task has nothing to run — drop it, keep the review task,
keep every gate. Rationale names the plan as docs-only.

**No change, and says something.** Plan changes the wire format of an event the
UI consumes. A contract check would fit, but the allowed hook set has no such
hook. Emit the chain unchanged with `rationale`: the change is wire-breaking, a
contract task would be the right addition, and none is registered — so the human
can decide whether to hold the item.

## Before you emit

- Does the tail start at the node right after `current_node_id`?
- Is every node in the tail present, including the ones you did not change?
- Is every task in the allowed hook set?
- Is every `gate_after` one of the four names, or `null`?
- Does every node with a `fix_loop` have tasks?
- Does `rationale` name real evidence, or is it a description of the diff?
- If nothing changed, did you emit the tail unchanged rather than an empty list?
- Does every `reject_to`/`rebase_bounce_to` you set name a real node at or
  before it? Does every `proposed_node_overrides` you set use only
  `model`/`escalate_model`/`effort`?
