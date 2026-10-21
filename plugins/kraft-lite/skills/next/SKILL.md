---
name: next
description: "Runs the next node of a Kraft Lite chain from its recorded state: an exec node's steps and tasks with their failure handling and fix loop, or a gate node opened for the human. Reports the finished run's time and attempts per node. Use when continuing a chain: right after `kraft-lite:start`, after a gate answer, or in a new or cleared session. A chain blocked at a gate belongs to `kraft-lite:gate`; a read-only progress question belongs to `kraft-lite:status`."
---

# Running the next node

This skill holds no state in the conversation: everything comes from `state`,
so it survives a `/clear`. Below, `KL` is short for
`python3 "$CLAUDE_PLUGIN_ROOT/kl.py"`, and every `KL` command carries
`--chain-id <id>`. Omit it only when you know this directory holds one chain. If
a verb exits saying there is more than one unfinished chain, show the human the
list it printed and ask which - do not pick. A verb that refuses with
`Run: python3 kl.py migrate` means a run or registry from an older Kraft Lite:
run `migrate` as `kraft-lite:start` says, then go on with the new id it prints.

## 1. Read the state

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>

You get `node`, `kind` (`exec` or `gate`), `status`, `note`, `attempt`, `cap`,
`steps`, `on_failure`, `on_failure_ran`, `fix_loop`, `on_base_changed`, `gate`
and `sliced`.

- `status: unstarted` - there is no chain here. Invoke `kraft-lite:start`, or say
  so if you were not asked to start one. Do not report this as finished.
- `status: done` - the chain is finished. Report what it cost (section 5) and stop.
- `status: blocked` - a gate is waiting. Invoke `kraft-lite:gate`. Do not proceed.
- `note` non-empty - the node was sent back. That note leads this round.
- `sliced` not null, `status` neither `done` nor `blocked` - `sliced.action` says
  what this node is:
  - `run_slices` - invoke `kraft-lite:run-slices`, which closes this node when every
    slice is done. Stop.
  - `scoped_verify` - a quick check of the seams between slices before the
    feature PR. In the feature worktree (below), first set it up as the repo says:
    a fresh one has nothing installed. Then run only the node's tasks with
    `task_kind` `builtin` and `ref` `kraft.verify_changed_test_scopes`, on the files
    `git diff --name-only <base branch>...<feature branch>` lists: a `subprocess`
    `command` gets their test paths appended, and one that takes no paths is a
    reason to stop and say so, never to run the whole suite. Run none of its other
    tasks: every slice was reviewed, and the feature PR's review covers how they
    fit. Then `KL close`, or section 3 on a failure.
  - `skip` - `KL close` with nothing run (`KL approve` on a gate node: `close`
    refuses one), then this skill again.
  - `await_user_merges` - check the PR of each slice that `slices` does not show as
    `merged`. For each the user merged: `KL slice-set --slice <key> --status merged`,
    tell each slice cut from its branch or from the target to rebase onto the
    target, and archive its child, as `kraft-lite:run-slices` says after a merge,
    then export state, as `kraft-lite:run-slices` describes under State branch.
    Record a slice merged only once every slice it comes after is: `slice-set`
    refuses it before. Name the slices still waiting and stop. Once all are
    merged, `KL close` (`KL approve` on a gate node), then this skill again. Never
    open this node's gate or ask anyone to approve it: the user's merges are the answer.
  - `run` - as in an unsliced chain, below.
- Otherwise `kind: exec` is section 2 and `kind: gate` is section 4.

In a sliced `feature` run, every task from the first build node on runs in a
worktree of the feature branch, never in the state branch's checkout: the rebase
and the merge request are made from the branch they run in. Make the worktree
once, from origin's feature branch, where the slices merged:

    git fetch origin
    git worktree add -B <feature branch> <path> origin/<feature branch>

The local feature branch is as old as the run's start: a worktree of it tests
none of the slices, and a push from it drops them. If `git worktree list` shows
the feature branch checked out already, use that checkout, after
`git -C <it> merge --ff-only origin/<feature branch>`. Only the tasks run there:
every `KL` command runs from the controller's checkout, the one whose
`.kraft-lite/` holds the chain, not the worktree's own, empty one.

## 2. Run an exec node

Run `steps` in order. The tasks inside one step may run in any order, or in
parallel as subagents (set each one's model explicitly, from the task's `hints`
where they name one, not the strongest by default). Run each task by its `kind`:

- `skill` - invoke `skill`, with `prompt` as the instruction.
- `prompt` - follow `prompt`. A `source` ending `(skill missing)` means the bound
  skill is not installed here: say you fell back.
- `subprocess` - run `command` as an argument list, without a shell; show its output.
- `skip` - run nothing; say the task was skipped, and its `why`.

After a task with `produces`, record the file it wrote before anything else:
`KL produced --kind <produces> --path <file>`. A gate later presents it.

A step fails when a task in it fails or reports findings of critical or
important severity; no later step runs then: section 3. A step that moved the
branch's base (a rebase or sync that did work) has not failed. If
`on_base_changed` is set, stop the node there and `KL restart`: it reopens the
walk from `on_base_changed.restart_from` through this node. Then this skill
again. It refuses past 3 restarts: stop and ask the human, as for `over_cap`.

Every step passed: `KL close`, then this skill again. In a sliced run (`sliced`
not null), export state after every `close`, as `kraft-lite:run-slices`
describes under State branch.

Resuming replays a node from its first step: keep tasks idempotent, and stop and
ask rather than run twice one that is not. The chain froze at `start` and cannot
be reordered, but `.kraft-lite/bindings.yaml` is read again at every `state`, so
rebinding a task there is how a live chain is corrected.

## 3. When a step fails, in Kraft's order

1. The node failed, `on_failure` is set and `on_failure_ran` is false: run
   `on_failure.steps` as in section 2, `KL mark --on-failure-ran`, then the
   whole node again from its first step.
2. Every failure is a task with `task_kind` `builtin` or `forge` that ended in
   an error no repair can change - credentials, a tool missing, no merge request:
   stop and tell the human, and spend no attempt. Failing tests or failing checks
   such a task reports are findings, for the fix loop below.
3. `fix_loop` null: stop and report. A node with no fix loop has no retry budget.
4. `KL attempt`. When it prints `attempt` 2 or more and `fix_loop.judge` is set,
   run the judge first, with every round's findings and the attempts used of
   `cap`. It rules: stop for the human (stop, with its reasoning); finish with
   findings, when no task failed outright (`KL close`, report the findings left);
   or continue.
5. `over_cap: true` - stop the chain and escalate: show the human every attempt's
   output, not a summary. A cap that is hit is a failure they need the traces for.
6. Run `fix_loop.steps` as in section 2, with what failed and the findings as
   their input, then the whole node again from its first step - not the chain.

## 4. A gate node

- `gate.slice_gate` true and `sliced` null: invoke `kraft-lite:run-slices`. It
  decides whether to slice, opens the gate itself and hands it to
  `kraft-lite:gate`. Stop.
- Otherwise `KL gate`, then invoke `kraft-lite:gate`. Stop. Never `approve` or
  `reject` it here, whatever the human said before it opened: the gate skill
  presents it and records their answer.

## 5. When the chain is done, report what the run cost

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" summary --chain-id <id>

Render it as a short table - one row per node with its time, how much of that time
was `blocked_seconds` waiting on a human, attempts against the cap where there is
one, its gate, and any rejection note - then a line of totals. The shape to
follow, with the field names from `summary`:

    node           seconds  blocked_seconds  attempts/cap  gate           rejected
    spec_approval      312              300  -             spec_approval  -
    verification       120                -  1/2           -              -
    total   nodes 2, duration_seconds 432, blocked_seconds 300, attempts 1, gates 1, rejections 0

`blocked_seconds` is normally part of the node's `seconds`, not extra to it. Say
which of the two the run actually went on - in a gated chain the waiting is usually
the larger, and reporting the total alone bills the agent for the human's hours.
After a rewind the two stop nesting: waiting is banked across every round, while
`seconds` is re-timed from the redo. Report them as the node's whole history
against its last run. Likewise `attempts` is every attempt the node ever cost, so
after a rewind it can exceed `cap`, the budget it has now: those are the retries
the run paid for.

Times are stamped a second apart at the coarsest: a fast node's second is the
clock's floor, not a measurement - say so. There is no token count, cost or turn
count: Lite runs inside your session and never sees them. Do not estimate them.

## A wait never blocks

A task that reports CI or an approval (`mr.ci`, `mr.external_approval`,
`mr.post_merge_ci`) reports once. Never sit in a synchronous loop waiting for
it. Do not idle - the human is sitting here, and their attention is the resource
this whole mode is spending. If your harness can wait in the background and wake
you when the wait finishes, use it: start the check in the background, say so,
and report once when the pipeline settles.

If your starting instructions name a controller, wait for CI as
`kraft-lite:build-slice` says. With no controller and no background primitive,
run the check once, report, and tell the human to invoke this skill again when it
finishes. That is the fallback, not the design.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.
