---
name: prepare
description: "Use before starting non-trivial work that needs a spec, including before a spec skill such as superpowers:brainstorming starts, when unsure whether it should run inline in this session or be handed to Kraft, or when deciding how to split it into sub-agents, slices or separate work items. kraft:handoff does the filing once that is decided."
---

# Preparing work

This is the layer above `kraft:handoff`: it defines the work first, then decides where
it should run.

## 1. Spec

Before writing one, `search(q)` for the area: the decision may already be in
another repo's spec or plan (`kraft:board` says how). A hit that settles it goes
to the user before the spec skill runs; otherwise pass it to the spec skill.
With no `kraft` tools, skip the search and say that handing off isn't possible.
Then run a spec skill - default `superpowers:brainstorming` - unless the user
named a different one. If none is installed, write a half-page spec (goal,
chosen approach, parts, out of scope), show it, and get approval. The spec
skill's own review and approval cover "review the spec"; don't add a second
review pass on top of it.

Stop when the spec is approved. If the spec skill then hands on to a plan
skill (brainstorming invokes `superpowers:writing-plans`), don't follow it:
this overrides that hand-off, not the spec skill's approval gate. Come back to
step 2; step 3 says when a plan is written. If the spec skill did a quick task
itself, there is nothing left to judge. If it splits the request into several
independent projects, run steps 1 and 2 for each, in the order agreed: each is
its own inline task or work item.

## 2. Judge

Right after the spec, before touching a plan. Size the work by what the spec
describes; the spec skill may already have said it (`superpowers:brainstorming`
sizes work as a quick task, a small change, or a project with a written
design). If it didn't, size it yourself and say that you did:

- **Inline** - a quick task, a small change, or a spike the user agreed to.
  Once confirmed (below), the normal dev workflow (TDD, etc.) in this session,
  with no plan doc. A fix adds a row, not a function: extend the test that
  already covers the behaviour, reuse the repo's shared helpers (its `CLAUDE.md`,
  `AGENTS.md` or CONTRIBUTING says where they live), and write a new test
  function only for a new behaviour.
- **Handoff** - a project with a written design, or the user already said the
  work is too big for this session, needs gates, or spans sessions.
  Use `kraft:handoff` to file it, attaching the spec. Kraft's own chain plans it -
  don't write a plan doc just to hand it straight to something that writes its
  own.

**Explicit user override wins outright.** If the user asked to file the work
as it is and skip the spec, that's `kraft:handoff` alone, with no spec. If they
already said "do this inline" or "hand this to Kraft," skip the inline/handoff
verdict and honour that instead - still write the spec first. It replaces the
verdict, not the parts call: if parts stand alone, send the message below for
that and wait; if none do, act.

**Then decide how independent parts are built.** Slices don't replace
sub-agents. Sub-agents are the default, inline or inside one Kraft item: they
share the worktree and need no setup or join. A part earns more only when it
makes sense on its own: a coherent piece with its own tests, which its own
implement, verify and review loop can check without another part's code. Size
is a weak hint, not the test, and a part of a task or two almost never earns a
slice. So:

- **Sub-agents**, when the user said "inline" (name the parts that would stand
  alone) or no part stands alone.
- **Separate work items**, when each part can merge on its own and is a full
  merge request in its own right. One `kraft:handoff` per part, each attaching
  a spec for that part only, each its own merge request.
- **Slices**, when the parts must land together. The same stands-alone test,
  held to a lower floor: each part earns its own loop but needn't be a whole
  merge request. One handed-off item, which Kraft's plan splits into child work
  items, built in parallel and joined into one merge request. Each child has its
  own worktree, setup, implementer session and review. Name each part in the
  spec, with what it covers, so the plan slices along them.

For slices or separate items, add a short Parts section to the approved spec
and show just that section in the message below; the user's go covers it.
Cut a spec per part only after the go.

Then send one message and stop: the verdict (inline or handoff), the parts
call (sub-agents, slices or separate items, with names) and a one-line reason.
Wait for the user to confirm or override it before acting: they know what a
filed item costs them. Auto (step 4), or an override whose parts call needs no
question, skips the wait.

## 3. Plan - only if the user explicitly asked for one

A plan is bound to the code as it is right now; a spec isn't. A spec written
today is still good in two days, a plan might not be - so don't produce one
speculatively.

If asked, run a plan skill - default `superpowers:writing-plans` - with its own
review gate. On a handoff, skip its execution-method question and its hand-on:
the plan is attached, not executed here. Re-run the judgement above with the
plan in hand (it sharpens the same inline/handoff call - e.g. a plan that turns
out to be one small task can move a project-sized spec to inline), and attach
the plan alongside the spec if handing off. If the parts are slices, end the
plan with a `## Slices` section, one bullet per slice (`- **Slice 1: Parser.**
Tasks 1-3.`), nothing but task numbers after "Tasks", two to six slices by
default. A slice can't use another's work, a task belongs to one slice at most,
and tasks two slices share stay outside every slice: Kraft builds those first.

**Do not put a full-test-suite run in the plan.** A plan headed for Kraft runs
under a chain whose verification node already runs the suite (and local review)
after implementation, with its own fix loop - a task step that re-runs it is
redundant work the chain repeats anyway. Per-task targeted tests (the test the
task itself is about) stay in the plan; only the suite-wide run is out.

## 4. Auto mode

If the user said `auto` after the skill name (`/kraft:prepare auto ...`) or
"do this in auto": skip step 2's confirmation, including after step 3 re-runs
the judgement, act on it immediately, and report the outcome afterwards instead
of asking first. Auto never skips the spec skill's own approval.
