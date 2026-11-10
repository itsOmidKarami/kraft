---
name: gate
description: "Presents a Kraft Lite gate node to the user and records their answer. Use when a Kraft Lite chain's state is `blocked` at a gate node, or the user answers a gate."
---

# Gates

If `$CLAUDE_PLUGIN_ROOT` is unset, `kl.py` is two directories above this file;
it needs `python3` 3.10 or newer, and without one, tell the user and stop.

A gate is a decision that belongs to the user. You present, they decide. `KL` is
short for `python3 "$CLAUDE_PLUGIN_ROOT/kl.py"`; every `KL` command except
`chains` carries `--chain-id <id>`, which stops an answer landing on the wrong
chain. Take `<id>` from the conversation; with none, run `KL state` without it,
and if it lists several chains, show them and ask which.

When a verb refuses, tell the user what it said. Never edit `.kraft-lite/`
records to get past it.

## 1. Check, then present

If the `KL gate` that brought you here printed `passed_unchanged`, the gate
never blocked: its chain revision proposed no change, and it passed as Kraft
passes one. Tell the user it passed, with that `rationale`, then invoke
`kraft-lite:next`.

Run `KL state`. Only proceed if `kind` is `gate` and `status` is `blocked`;
otherwise stop and invoke `kraft-lite:next`, because `approve` answers a gate
whether or not the user has seen it.

If `gate.reviewer` is set and `gate.review` is null, the gate's reviewer checks
first (`auto_review_mode: checks_first`): run it before the user sees the gate,
as section 1a says. With `gate.review` set, this round's review is done: read
that report and lead with it below, as "Reviewer: ready for you", or "Reviewer:
could not decide" when its verdict was ignored. A report file that has gone
missing is a review to run again.

Then show the user what the gate is about: `gate.message`, and the file at
`gate.artifact_path`, the newest file of kind `gate.artifact` the run produced.
With no `gate.artifact`, present the worktree and its diff; with `gate.artifact`
set but `gate.artifact_path` null, find the file and say so. At the final gate
(`gate.chain_finalized`), or one with `gate.artifact_required`, `approve`
refuses with no file on record. A plain `reject` would land on `gate.reject_to`,
so offer `reject --from-node <node>` instead, naming the node whose task
`produces` `gate.artifact` in `.kraft-lite/chains/<chain-id>.json` (section 2).
At the slice gate
(`gate.slice_gate` true), show the slicing proposal beside it; with no
proposal file, say no slicing is proposed, so an approval builds the plan in
one session. A gate answered without the artifact in front of the user has
stopped meaning anything.

A good presentation names the artifact, says where it lives, and asks the gate's
one question: "`plan_approval`: the plan is `docs/plan.md` (read it, or I can
summarise). Approve it as the way to build this, or send it back with a reason?"

Never call `approve` because the answer seemed obvious. If the user hasn't
answered this gate, it isn't answered: "looks fine" about something else, or
an earlier "just do it", isn't an answer. An approval with a condition ("approve,
but rename X") isn't one either: ask whether to reject with the condition as the
note, or approve and make the change yourself. A slicing proposal the user
hasn't seen in this conversation is shown before `approve --slices`.

### A chain revision that proposes a change

When `gate.artifact` is `chain_revision` and the file proposes any change, say
Kraft Lite cannot apply it: the chain froze at `start`. Offer two ways on, and
record nothing until they choose: approve without the change, so the chain goes
on as frozen; or stop and start again on an edited copy. For that:

1. Find the chain's source file from the frozen `.kraft-lite/chains/<chain-id>.json`:
   its `source` when that is a file path; otherwise the `path` that
   `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains --available` lists for its `id`.
2. Copy it to a new `.yaml` file and make the change there. For a `repo:` chain,
   put the copy in `.kraft/chains/` with a new `id`, so its `extends` still
   resolve, and pass `--chain repo:<new id>`.
3. Invoke `kraft-lite:start` with `--chain <that file>` (or `repo:<new id>`), and
   `--spec`/`--plan` set to the spec and plan the earlier gates approved, so the
   new chain starts at the chain revision rather than writing and approving
   them again.
4. The old chain then stays unfinished in `chains`, and the user decides what
   to do with it.

## 1a. A reviewer that checks first

Run `gate.reviewer` by its `kind`, as `kraft-lite:next` runs a task, in a
subagent with the model its `hints` name, so it reads the work fresh. Its
instruction: the gate's id, `gate.message`, the document it is about (the file
section 1 finds; with no `gate.artifact`, the worktree and the diff), and that it
checks before the user and cannot approve. It must not change, write or commit
any file. It reports one verdict with its `concerns`: `pass` hands the gate to
the user, the default whenever it is unsure; `reject` only for a concrete defect,
its `concerns` written as an instruction for whoever redoes the work.

Before it runs, note `git status --porcelain`, `git rev-parse HEAD` and a
checksum of the gate's document, and compare them after. Then:

- It changed something: its verdict is ignored. Tell the user what it changed,
  and leave the changes for them.
- `reject` with `concerns`: `KL reject --note "Reviewer: <concerns>"`, with no
  question to the user: the chain asked for this. Tell the user the reviewer sent
  the work back, and why, then section 3. Three cases make it a `pass` instead,
  each said to the user: `KL summary` already shows 3 `rejections` on the node it
  would land on (`gate.reject_to`, else the nearest exec node before the gate),
  so the reviewer keeps sending it back; `sliced` is not null, where which slices
  to redo is the user's call; and the slice gate, where the slicing is theirs.
- Anything else is a `pass`: a `reject` with no `concerns`, an `approve`, or a
  reviewer bound to `skip` or `subprocess`, whose `why` or output is its note.
  The reviewer never clears a gate.

After a `pass` or an ignored verdict, write the verdict and `concerns` (or what
it changed) to `.kraft-lite/reviews/<chain-id>-<gate node>.md` and record it, so
a cleared session does not review again: `KL mark --reviewed <that file>`. Then
present the gate as section 1 says, with the reviewer's note first. The user
decides it as any other gate.

## 2. Record the answer

### Approve

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --chain-id <id>

At the slice gate, `state`'s `sliced` and the proposal file say which command
records the answer:

- `sliced` null and `.kraft-lite/proposals/<chain-id>.json` exists: the proposal
  is this gate's artifact too, the one section 1 presents. An approval carries it:

      python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --slices .kraft-lite/proposals/<chain-id>.json --chain-id <id>

  A plain `approve` here leaves the chain unsliced. If it refuses, tell the user
  what it said and ask whether to approve unsliced.
- `sliced` null and no proposal file: a plain `approve`.

### Reject

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<their reason>" --chain-id <id>

At the slice gate, also `rm .kraft-lite/proposals/<chain-id>.json`, so it cannot
be approved later. No slices exist yet; the proposal is redrawn when the work
comes back to this gate.

A plain `reject` lands on `gate.reject_to` (with none, the nearest exec node
before the gate) and reopens it and every node after it, so the redone work is
verified again. Check that is the node the user wants redone: a `reject_to` is
often the first build node, which redoes the code even when only the document
the gate presents was wrong. If their reason doesn't say, ask: the document (name
the node that wrote it) or the work behind it (`gate.reject_to`). To land
anywhere else, name it (an unknown `<node>` makes the verb list the chain's nodes):

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<reason>" --from-node <node> --chain-id <id>

In a sliced run (`sliced` not null), where it lands decides the slices' fate.
`KL slices` names the slice gate and the first build node in its `shape`:

- The slice gate or earlier: the plan or its slicing is redone, so the rewind
  retires every slice. Archive their children as `kraft-lite:run-slices` says.
  `slices` then lists none, and the chain is sliced again, from a new proposal,
  at the slice gate.
- The first build node, or a node between the slice gate and it, to redo some
  slices: reopen each slice to redo first, `KL slice-set --slice <key> --status
  running`, then reject. With every slice still done the rewind is refused, as
  the first build node would close at once. `kraft-lite:run-slices` reopens their children.
- Later: the slices stay as they are.

`--note` is required: a rejection with no reason strands whoever picks the work up next.

## 3. Check and continue

Each verb prints the chain's new state. Check it: after `approve` the gate node
is closed and `state` shows the next node; after `reject` the node it landed on
is `open` with your `note` set. Then invoke `kraft-lite:next`.
