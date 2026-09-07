---
name: gate
description: Use when a Kraft Lite chain is blocked at a gate - presents what the human must decide on, then records their approval or rejection.
---

# Gates

A gate is a decision that belongs to a human. You present, they decide.

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>` for the gate name, then show them
what the gate is actually about - the spec, the plan, the diff, the findings. A
gate answered without the artefact in front of the person is a gate that has
stopped meaning anything.

Then, on their answer:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" approve --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<their reason>" --chain-id <id>

A plain reject reopens the node being gated. That is right for a gate on the node
that made the artefact - a rejected spec re-runs the spec node. It is wrong for a
gate on work an earlier node produced: rejecting `human_review_approval` would
re-present the same work rather than return to it. When the human is rejecting
something built further back, name where it goes:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" reject --note "<reason>" --from-node <node> --chain-id <id>

That reopens that node and every node after it, so the redone work is verified
again on the way back rather than skipped.

`--chain-id` is what stops an answer landing on the wrong chain, and with two
unfinished chains in the directory the verb refuses to guess. `--note` is
required. A rejection with no reason strands whoever picks the work
up next, including you after a compaction.

After an approve, invoke the `next` skill. After a reject, invoke `next` too: the
node reopens and its note leads the retry.

Never call `approve` because the answer seemed obvious. If the human has not
answered, the gate is not answered.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
