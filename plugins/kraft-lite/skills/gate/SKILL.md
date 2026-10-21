---
name: gate
description: "Presents a Kraft Lite gate to the human and records their answer with `approve` or `reject`, sending rejected work back to the node that needs redoing. Use when a Kraft Lite chain's state is `blocked` at a gate, or the human answers a gate."
---

# Gates

A gate is a decision that belongs to a human. You present, they decide.

## 1. Check, then present

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>`; its `gate` field is the gate name. Only proceed if
the node's `status` is `blocked`; otherwise stop and invoke `kraft-lite:next`, because
`approve` closes whatever node is current, hooks unrun.

Then show the human what the gate is actually about - the spec, the plan, the diff,
the findings, the slicing proposal. A gate answered without the artefact in front of
the person is a gate that has stopped meaning anything.

A good presentation names the artefact, says where it lives, and puts the one question
the gate asks, for example: "`plan_approval`: the plan is `docs/plan.md` (read it, or I
can summarise). Approve it as the way to build this, or send it back with a reason?"

Never call `approve` because the answer seemed obvious. If the human has not
answered this gate, it is not answered - "looks fine" about something else, or
an earlier "just do it", is not an answer.

## 2. Record the answer

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<their reason>" --chain-id <id>

At `chain_finalized`, `state`'s `sliced` and the proposal file say which
command records the answer:

- `sliced` null and `.kraft-lite/proposals/<chain-id>.json` exists: the proposal
  is this gate's artefact, the one section 1 presents. An approval carries it:

      python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --slices .kraft-lite/proposals/<chain-id>.json --chain-id <id>

  A plain `approve` here leaves the chain unsliced. If it refuses, naming
  nodes or a gate the chain lacks, tell the human and ask whether to approve
  unsliced.
- `sliced` null and no proposal file: a plain `approve`.
- A rejection: also `rm .kraft-lite/proposals/<chain-id>.json`, so it cannot be
  approved later. No slices exist yet; the proposal is redrawn when the node
  reopens.

A plain reject reopens the node being gated. That is right for a gate on the node
that made the artefact - a rejected spec re-runs the spec node. It is wrong for a
gate on work an earlier node produced: rejecting `human_review_approval` would
re-present the same work rather than return to it. When the human is rejecting
something built further back, name where it goes:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<reason>" --from-node <node> --chain-id <id>

That reopens that node and every node after it, so the redone work is verified
again on the way back rather than skipped. An unknown `<node>` makes the verb list
the chain's nodes.

In a sliced run (`sliced` not null), where it lands decides the slices' fate:

- `chain_review` or earlier: the plan or its slicing is redone, so the rewind
  retires every slice. `slices` then lists none, and the chain is sliced again,
  from a new proposal, at `chain_review`.
- `implementation`, to redo some slices: reopen each first,
  `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" slice-set --slice <key> --status running --chain-id <id>`,
  then rewind. With every slice still done the rewind is refused, as
  `implementation` would close at once. `kraft-lite:run-slices` reopens their
  children.
- Later: the slices stay as they are.

`--chain-id` is what stops an answer landing on the wrong chain, and with two
unfinished chains in the directory the verb refuses to guess. `--note` is
required. A rejection with no reason strands whoever picks the work
up next, including you after a compaction.

## 3. Check and continue

Each verb prints the chain's new state. Check it: after `approve` the gated node
is `closed` and `state` shows the next node; after `reject` the node is `open`
with your `note` set.

Then invoke `kraft-lite:next`: on a reject the node reopens and its note leads
the retry.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.
