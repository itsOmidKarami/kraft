---
name: review
description: "Reads the spec, plan or diff a Kraft work item's pending gate is about and recommends approving or rejecting it, with reasons, for the person to confirm. Use when an item is waiting at a gate and someone asks for a review or help deciding; kraft:gates owns the approve, reject and submit calls themselves."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Reviewing before a gate is answered

The gate is the person's decision. Your job is to arrive at it with the answer
read, so they spend a minute confirming instead of an hour reading.

## 1. Read what the gate is about

```bash
kraft view show ID                 # the item's description (the brief), the pending gate, concerns a task flagged, the head
kraft view artifact ID             # the document the pending gate asks about
kraft view docs ID                 # lists the spec and plan; read one with --attachment spec
kraft view diff ID --stat          # then the full diff for what the next line names
kraft view threads ID --open       # review threads still waiting on an answer
kraft view compare ID --from last_review --to latest --stat   # what changed since the last review
kraft view mr ID                   # final review: the merge request
kraft view pipelines ID            # final review: CI
```

Read the full diff for any file the plan doesn't name, any deletion, and any
change to config, CI or dependencies.

For the final review, note `review_head` (in `view show`, and in
`get_work_item()`) now: it is the head you read, and the one to quote in your
recommendation and pass on.

The document's kind sets the question. A spec: does it solve the problem in
the brief? A plan: do its steps produce the spec's design, with test steps?
A chain revision: do the nodes it adds, drops or changes serve the approved
plan? Before a merge request exists (`local_review`): does what was built match
what was approved, with tests, and does its size match the plan's? The final
review: did CI and the automated review settle everything they raised? A
checkpoint with no document: is the diff ready for the step that follows? Where
the repo's contributing or test docs set a convention for tests, check the
plan's or the diff's tests follow it. On a second round, check each open thread
was met in what `compare` shows: an unresolved `must_fix` blocks
`submit_review(outcome="approve")`.

## 2. Recommend, with reasons

Lead with the recommendation: approve, or reject. Then the specific reasons,
each tied to a file, a spec line or a plan task. Approve only for a reason you
can state, not because nothing looked wrong. When unsure, say it is a
judgement call and what you could not verify.

A rejection needs a note, and the note is an instruction for whoever redoes the
work. Draft it as one. When the reasons are about specific lines, draft them as
review threads instead (`add_review_comment`, one per finding, labelled
`must_fix`, `question` or `nit`; nothing is sent yet) and, once the person
agrees (step 3), send them with `submit_review(outcome="request_changes")`: each
thread is answered and resolved on its own, and a later review can check which
ones held up. See `kraft:gates`.

## 3. Ask, then act

Before `approve_gate`, `reject_gate`, `skip_work_item` or `submit_review`, the
person has to have answered this gate in this conversation. Your recommendation
isn't their answer, and neither is a general "go ahead" that predates the gate.
An instruction that names this gate does count, even a conditional one
("approve it if it looks fine"): review first, act only if your own review meets
the condition, and otherwise leave the gate pending and report what you found. A
gate answered on your say-so has stopped meaning anything. Act through those MCP
tools, not the `kraft item` verbs from a shell (`kraft:gates` says why). A chain
revision gate also needs the `digest` `get_gate_artifact()` returned when you
read the revision (`kraft view artifact ID --json` carries it), and the final
review the `review_head` you noted in step 1: neither fetched now. See
`kraft:gates`.

## If you are a Kraft worker session

You can't approve or reject your own item. Report the recommendation.

In a sandbox `kraft view artifact`, `docs`, `diff` and `compare` answer `not
available in a sandbox`; `threads` works. Read the change with git
instead: `git diff --stat origin/<base>...HEAD` first, then `git diff
origin/<base>...HEAD` where the stat looks risky, where `<base>` is the branch
the work goes into (the repository's default unless the work item names
another). The spec and plan are files in your worktree.
