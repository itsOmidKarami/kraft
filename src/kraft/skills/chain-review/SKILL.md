---
name: chain-review
description: Use when a work item's spec and plan have just been approved and Kraft asks whether the rest of its chain still fits the work. Writes a chain_revision change set - usually an empty one - that skips, adds or re-tunes only the nodes that have not run yet, for a person to approve at the revision gate.
---

# Chain Review

You are looking at a work item whose spec and plan were just approved. The chain
it runs was chosen at intake, from a template, **before either document
existed**. Your job is to say whether that guess still holds now that the real
work is known.

You are not reviewing the plan. It is approved and not yours to change. You are
reviewing the *chain*: the nodes that run after the revision gate, in light of
the plan.

## The default is to change nothing

Most of the time the template was right. A chain chosen sensibly at intake and a
plan about the size everyone expected need no intervention, and an empty change
set is the correct, common answer. It also costs nobody anything: an empty
change set passes the revision gate without a person being asked.

Change the chain only when you can point at a specific line in the spec or plan
that the current chain handles badly. "This could plausibly benefit from more
review" is not such a line. If you cannot name the evidence, leave it alone.

## What you get

- The approved **spec** and **plan**.
- After your task instruction, **the nodes you may change**, as authored JSON,
  with every task's canonical path (`node.step.task`; a `tasks:` group's step is
  `main`). Everything up to and including the revision gate has run or is
  running.
- The library components you may add from: run `kraft admin templates library`
  (and `kraft admin templates library <name>` for one component's definition).

## The hard rule: only the tail

You may change **only the nodes listed after the revision gate**. Nothing at or
before it: those nodes have run, their gates were answered by a person, and
their sessions and events already exist. A change set that names one is refused
whole.

**A gate never changes.** You cannot skip one, override one, or add one. Gates
are a person's control over the run; deciding that a person need not look is not
your decision.

## What you may propose

Three kinds of change, and nothing else. Every change carries `evidence`: the
line of the spec or plan that asks for it, quoted or cited closely enough that
the person at the gate can find it.

- **`skip`** a node that has become pointless. Demand stronger evidence than for
  adding: you are removing a check someone thought worth running. Never skip the
  merge, the draft or ready merge request, or the last verification of a change
  to executable code. A node marked `skippable: false`, a node another node
  restarts from, and a node a gate rejects back to cannot be skipped; the
  proposal is refused if you try.
- **`add`** an execution node after a named node (the revision gate itself, or
  any node after it). An added node is built **only from library components**:
  either `{"id": ..., "extends": "<library node>"}`, or
  `{"id": ..., "tasks": [{"id": ..., "extends": "<library task>"}]}` (or
  `steps` of such tasks). No prompts, commands or settings of your own. If the
  component the plan calls for is not in the library, do not approximate it with
  a different one: say so in `rationale` and propose nothing for it.
- **`overrides`** at a canonical path: an agent task's `model` or `effort`, or
  an operational policy value -- `time_cap_minutes`, `total_time_cap_minutes`,
  `token_budget` or `budget_usd` on a node or task, and `max_attempts` or
  `timeout_minutes` on an execution node's fix loop. Kraft holds each within the
  administrator maxima and under its enclosing scope's cap. Tools, sandbox,
  harness and permissions are not yours to change; a concern about one goes in
  `rationale` for the person.

| Evidence in the spec or plan | Reasonable change |
|---|---|
| Touches authentication, sessions, tokens, secrets or permission checks | Add a security-review node from the library after verification |
| A plan task is clearly heavier than the template assumed | Raise that task's `effort`, or its node's fix-loop `max_attempts` |
| The plan is docs-only, with no executable change | Skip a test node that has nothing to run against, never the review |
| A risk section names a failure mode the chain never checks, and the library has the check | Add it |

Leave it alone when the chain differs from what you would have picked but
nothing is wrong with it, when you want to reorder for tidiness, when the plan is
merely large, and when you are unsure. Unsure means no change.

## Output

Write the `chain_revision` document the task asks for. After its front matter,
the body is **exactly one fenced JSON block and nothing else**: no heading, and
no prose before or after it. Kraft parses the whole body strictly. An unknown
key, a key written twice, or anything outside the fence makes the proposal
unreadable, and a person has to send it back. Anything you want the person to
read goes in `rationale` and in each change's `evidence`; Kraft renders them,
with the diff, at the gate.

````
```json
{
  "rationale": "One short paragraph: what you changed and why, or why the chain fits.",
  "skip": [{"node": "<node id>", "evidence": "<spec or plan line>"}],
  "add": [
    {
      "after": "<node id>",
      "node": {"id": "<new id>", "extends": "<library node>"},
      "evidence": "<spec or plan line>"
    }
  ],
  "overrides": {
    "<canonical path>": {"effort": "high", "evidence": "<spec or plan line>"}
  }
}
```
````

`skip`, `add` and `overrides` may each be left out. The no-change answer is
just:

````
```json
{"rationale": "The plan is one focused change the chain already covers."}
```
````

## Before you write it

- Does every change name a node after the revision gate, and no gate?
- Does every added task `extend` a component `kraft admin templates library`
  lists?
- Does every change carry evidence from the spec or plan, not a description of
  the change itself?
- If nothing needs to change, did you write the empty change set rather than
  inventing one?
