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
- The **allowed hook set**: every hook point registered and enabled for this
  repo. It is injected with your prompt.
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
  rationale: string }
```

`revised_chain_nodes` is the **complete** not-yet-executed tail, not a diff and
not a patch. The orchestrator splices it in wholesale on approval. If you are
changing one node, the other tail nodes still appear, unchanged, in your output
— omitting a node deletes it.

Every node you write is exactly:

```
{ id: string, tasks: [hook_point, ...], gate_after: string|null, fix_loop: string|null }
```

A real node also carries `on_failure`, `reject_to`, `rebase_bounce_to`, and
`auto_escalate` — fields you never set. For any node id that already existed in
the tail, the orchestrator carries those fields forward from the node you are
replacing, so an "unchanged" node keeps its repair hooks and reject targets
without you naming them. A node id you invented (one you are adding) gets
`null` for all four — you cannot give a new node a repair task or a reject
target this way.

- `tasks` — hook points, and **only names from the allowed hook set**. A name you
  invented is not a task the orchestrator can run; it is a chain that fails
  validation, or worse, a node that silently does nothing.
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
