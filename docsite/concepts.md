# Concepts

Kraft's whole vocabulary is five words: work item, chain, node, task, gate —
plus the bounds that stop a chain rather than let it loop: caps and waits.

## Work item

A **work item** is one unit of work and produces one merge request. You create
one from the UI, the CLI (`kraft item create`), or an MCP tool
(`create_work_item`). It always lands **paused** — see
[Agent integration](agent-integration.md) for why an agent can never skip that.

Its work starts from the repository's default branch, and its merge request
targets it, unless it names a **base branch** (`--base-branch`, the MCP tool's
`base_branch`): then that branch is the one its worktree is cut from, every
rebase replays onto, a base change is detected on, the merge request targets
and post-merge CI watches. The branch must already exist on the repository's
origin, or intake refuses the item. It is frozen at intake. On a workspace item
it names the root's branch; each member keeps its own default branch, because
one branch name means nothing across repositories that need not share it.

## Chain

A work item enters as a **chain**: an ordered list of **nodes**, resolved from
one file under `templates/chains/` and frozen onto the item at intake — editing
the chain afterwards changes nothing for an item already filed. Which chain it
uses is either named explicitly with `--chain`
(`kraft item create "..." --chain quick-task`) or comes from the repo's
`default_chain_template` in `repos.yaml`.

The simplest shipped chain, `quick-task`, is two nodes with no gates at all:

```yaml
# chains/quick-task.yaml
id: quick-task
nodes:
  - id: implementation
    kind: exec
    tasks:
      - { id: implement, extends: implementer }
  - id: verify
    kind: exec
    tasks:
      - { id: test_changed_scopes, extends: verify_changed_scopes }
```

`verify_changed_scopes` runs the repo's own `test_scopes` or `test_command`
from `repos.yaml`. On a repo that declares neither, `quick-task` (and
`default`'s `verification`) stops for a human there with a config error, not
a guessed command.

The shipped `default` chain is the real one: a spec and a plan, each with its
own approval gate; implementation; verification (the changed test scopes, then
a code review) inside a fix loop; a work brief and a `local_review` gate before
a draft merge request exists; CI and automated review on the draft, with its own
fix loop; a summary and the final `chain_review` gate; then ready, external
approval, merge, and the post-merge pipeline. `kraft admin templates show
default --resolved` prints it with every library component expanded.

## Node

A node is `kind: exec` (it runs work) or `kind: gate` (it waits for a human).

An **exec** node's work is one or more **steps**, each a group of tasks.
`tasks: [a, b]` is shorthand for one step named `main`: `a` and `b` are
dispatched together and the node is measured once both have settled.
`steps: [{id: tests, tasks: [a]}, {id: review, tasks: [b]}]` runs its steps in
order — `b` is not dispatched at all if `a` fails, and it sees whatever `a` left
behind. A node declares one key or the other, never both. Its other keys:

| Field | Means |
|---|---|
| `on_failure` | A recovery pass — tasks or steps that run once after the node failed, before it is measured again. A task or a step may carry its own `on_failure`; the nearest one to the failure wins. |
| `fix_loop` | Repair tasks and an optional `judge`, re-run until the node passes, up to `max_attempts` and the wall clock `policy.yaml` gives the loop (`<node>.fix_loop`). |
| `escalation` | An agent task dispatched when the node is stuck after recovery and the fix loop, before a human is asked. |
| `on_base_changed` | What to re-run when this node's rebase moves the base: `restart_from` names an earlier node, and `on_conflict` the task that resolves a conflicting rebase. |
| `policy` | This node's layer of the [policy](configuration.md#policyyaml-caps-budget-archiving) — safety only tightens, operational values stay within the administrator's maxima. |
| `skippable` | `false` to refuse an operator's skip. |

A **gate** node names its `message`, the `artifact` it asks a person to decide
on, and `reject_to` — the node a rejection re-enters with the reviewer's note.
`auto_review` names an agent task that may report a verdict first, and
`chain_finalized: true` marks the final review, which cannot be approved without
its document.

Every task, step and node has a **canonical path** — `spec.main.author`,
`verification.review.code_review`, `merge_request_feedback.fix_loop.judge` —
which is what events, sessions, `kraft item retry --path` and
`kraft item skip --path` address.

## Task

A **task** is one unit of execution, of one of four kinds:

- **`agent`** (`src/kraft/adapters/agent.py`) — runs a headless coding agent in
  the item's worktree on a [harness profile](harnesses.md) (`harness:
  codex_default`), with its own `prompt`, and optionally one `skill`, the
  `steering` profiles it reads, the document it `produces`, and the `inputs`
  Kraft hands it.
- **`subprocess`** (`src/kraft/adapters/subprocess.py`) — runs a literal
  `command`.
- **`builtin`** (`src/kraft/builtins.py`) — work Kraft does itself, named by a
  `ref` such as `kraft.verify_changed_test_scopes`.
- **`forge`** (`src/kraft/adapters/forge/`) — a merge-request action on GitHub
  or GitLab, named by its `target` (`mr.open_draft`, `mr.ci`, `mr.merge`, …),
  resolved per repo from the `forge` recorded in that repo's `repos.yaml`
  entry. A task that waits on the forge declares its own polling in `wait:`
  and its timeout as its `policy: total_time_cap_minutes`.

## The library and `extends`

A chain does not have to restate everything. `templates/library.yaml` holds
reusable `tasks`, `steps`, `nodes` and named `steering` profiles, and a chain
component takes one with `extends: <name>`:

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

A component extends one parent of its own kind (a node extends a node, never a
task). Maps merge recursively, lists replace, and a reference that resolves to
nothing is an error naming the file it came from — `kraft admin templates lint`
reports every one.

## Gate

A **gate** is where a human decision belongs. When the chain reaches a gate
node it stops; the work item's status becomes `needs_human` and the board shows
it under *Needs you*. From there:

- **Approve** — the chain continues to the node after the gate.
- **Reject**, with a note — the chain re-enters at the gate's `reject_to` node,
  carrying the note as a steer.
- **Steer**, without rejecting — for a paused-mid-flight item, not a gate.

An item filed with an attached spec or plan starts without the nodes that
document covers: the gate that decides it and the node that would have written
it.

## Cap and wait

Every retry loop is bounded. A fix loop stops at its own `max_attempts`, and at
the wall clock `policy.yaml`'s `loops:` map gives its key (or `default:`). An
external wait — CI, an automated review, an approval, a merge landing — is
bounded by its task's own `total_time_cap_minutes` instead, and running out
stops for a person. Hitting either bound stops the chain and escalates with the
full trace, rather than looping forever on a defect the agent can't actually
fix.

Time is capped per scope too. `time_cap_minutes` bounds a scope's running time
and `total_time_cap_minutes` its wall clock, waits and gates included, on the
work item, a node, a step or a task; each caps its own scope, a child's cannot
exceed its parent's, and running out stops for a person, naming the scope.

## Where this is enforced

`docs/intent/` states what the system is supposed to do as numbered
requirements, each with an `enforced-by:` line naming the test that pins it —
see [ARCHITECTURE.md](https://github.com/itsOmidKarami/kraft/blob/main/ARCHITECTURE.md#intended-behaviour-written-down-separately).
