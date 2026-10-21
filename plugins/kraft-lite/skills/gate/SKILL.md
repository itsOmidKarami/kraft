---
name: gate
description: "Presents a Kraft Lite gate node to the human - its message and the file it is about - and records their answer with `approve` or `reject`, sending rejected work back to the node that redoes it. Use when a Kraft Lite chain's state is `blocked` at a gate node, or the human answers a gate."
---

# Gates

A gate is a decision that belongs to a human. You present, they decide. `KL` is
short for `python3 "$CLAUDE_PLUGIN_ROOT/kl.py"`; every `KL` command carries
`--chain-id <id>`, which stops an answer landing on the wrong chain.

## 1. Check, then present

Run `KL state`. Only proceed if `kind` is `gate` and `status` is `blocked`;
otherwise stop and invoke `kraft-lite:next`, because `approve` closes whatever
node is current, its tasks unrun.

Then show the human what the gate is about: `gate.message`, and the file at
`gate.artifact_path`, the newest file of kind `gate.artifact` the run produced
(when it is null, find the file and say so); at the slice gate
(`gate.slice_gate` true), the slicing proposal beside it, if there is one. A gate
answered without the artefact in front of the person has stopped meaning anything.

A good presentation names the artefact, says where it lives, and asks the gate's
one question: "`plan_approval`: the plan is `docs/plan.md` (read it, or I can
summarise). Approve it as the way to build this, or send it back with a reason?"

Never call `approve` because the answer seemed obvious. If the human has not
answered this gate, it is not answered - "looks fine" about something else, or
an earlier "just do it", is not an answer.

When `gate.artifact` is `chain_revision` and the file proposes any change, say
Kraft Lite cannot apply it: the chain froze at `start`. Offer two ways on, and
record nothing until they choose: approve without the change, so the chain goes
on as frozen; or stop, and start again on an edited copy of the chain (`KL start --chain <edited file>`).

## 2. Record the answer

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<their reason>" --chain-id <id>

At the slice gate, `state`'s `sliced` and the proposal file say which command
records the answer:

- `sliced` null and `.kraft-lite/proposals/<chain-id>.json` exists: the proposal
  is this gate's artefact too, the one section 1 presents. An approval carries it:

      python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --slices .kraft-lite/proposals/<chain-id>.json --chain-id <id>

  A plain `approve` here leaves the chain unsliced. If it refuses, tell the human
  what it said and ask whether to approve unsliced.
- `sliced` null and no proposal file: a plain `approve`.
- A rejection: also `rm .kraft-lite/proposals/<chain-id>.json`, so it cannot be
  approved later. No slices exist yet; the proposal is redrawn when the work
  comes back to this gate.

A plain `reject` lands on `gate.reject_to` (with none, the nearest exec node
before the gate) and reopens it and every node after it, so the redone work is
verified again on the way back. Check that it is the node the human wants
redone: a `reject_to` is often the build node, which redoes the code even when
only the document the gate presents was wrong. To land anywhere else, name it:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<reason>" --from-node <node> --chain-id <id>

An unknown `<node>` makes the verb list the chain's nodes.

In a sliced run (`sliced` not null), where it lands decides the slices' fate:

- The slice gate or earlier: the plan or its slicing is redone, so the rewind
  retires every slice. Archive their children. `slices` then lists none, and the
  chain is sliced again, from a new proposal, at the slice gate.
- The first build node, to redo some slices: reopen each slice to redo first,
  `KL slice-set --slice <key> --status running`, then reject. With every slice
  still done the rewind is refused, as the first build node would close at once.
  `kraft-lite:run-slices` reopens their children.
- Later: the slices stay as they are.

`--note` is required. A rejection with no reason strands whoever picks the work
up next, including you after a compaction.

## 3. Check and continue

Each verb prints the chain's new state. Check it: after `approve` the gate node
is closed and `state` shows the next node; after `reject` the node it landed on
is `open` with your `note` set. Then invoke `kraft-lite:next`.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.
