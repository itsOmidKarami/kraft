---
name: review
description: "Use when a Kraft work item is waiting at a review gate and someone wants help deciding whether to approve or reject it."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
connection error.

# Reviewing before a gate is answered

The gate is the person's decision. Your job is to arrive at it with the answer
read, so they spend a minute confirming instead of an hour reading.

## 1. Read what the gate is about

```bash
kraft view artifact ID             # the document the pending gate asks about
kraft view docs ID                 # the spec and plan the work was held to
kraft view diff ID --stat          # then the full diff where the stat looks risky
```

The document's kind sets the question. A spec: does it solve the problem in
the brief? A plan: do its steps produce the spec's design, with test steps?
A finished item: does what was built match what was approved, and does its
size match the plan's? A checkpoint with no document: is the diff ready for the
step that follows?

## 2. Recommend, with reasons

Lead with the recommendation: approve, or reject. Then the specific reasons,
each tied to a file, a spec line or a plan task. Approve only for a reason you
can state, not because nothing looked wrong. When unsure, say it is a
judgement call and what you could not verify.

A rejection needs a note, and the note is an instruction for whoever redoes the
work. Draft it as one.

## 3. Ask, then act

Before `approve_gate` or `reject_gate`, the person has to have answered this
gate. Your recommendation is not their answer, and neither is a general "go
ahead" that predates the gate. An instruction that names this gate does count,
even a conditional one ("approve it if it looks fine"): review first, act only
if your own review meets the condition, and otherwise leave the gate pending and
report what you found. A gate answered on your say-so has stopped meaning
anything. A chain revision gate also
needs the `digest` from `get_gate_artifact()`: see `kraft:gates`.

## If you are a Kraft worker session

You cannot approve or reject your own item. Report the recommendation.
