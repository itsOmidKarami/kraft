---
name: prepare
description: "Writes the spec for non-trivial work, then judges whether it runs inline in this session or is handed to Kraft. Use before starting work that needs a spec, or when unsure whether work belongs inline or in Kraft, or how to split it (sub-agents, slices or separate work items); kraft:handoff does the filing once that is decided."
---

# Preparing work

This is the layer above `kraft:handoff`: it defines the work first, then decides where
it should run.

## 1. Spec

Run a spec skill - default `superpowers:brainstorming` - unless the user named a
different one. If the skill isn't installed, say so and ask which to use instead.
Its own self-review and user-review gate cover "review the spec";
don't add a second review pass on top of it.

## 2. Judge

Right after the spec, before touching a plan. Read the class (spike, bounded or
architectural) off the spec. If the spec skill gave it no such classification,
judge it yourself by those three classes and say that you did:

- **Inline** - the spec came out spike- or bounded-classified. Go straight to
  the normal dev workflow (TDD, etc.) in this session. No plan doc needed:
  that's already bounded's own terminal state.
  Two rules for the tests you write there: a fix adds a row, not a function
  (find the test that already covers the behaviour and add a parametrized case
  with a readable id; a new test function is for a new behaviour), and look in
  the repo's shared test helpers and fixtures before writing one, since a
  copied helper is a second place to keep current. The repo's own testing
  guidance (its `CLAUDE.md`, `AGENTS.md` or CONTRIBUTING) says where they live.
- **Handoff** - the spec came out architectural-classified, or the user already
  said the work is too big for this session, needs gates, or spans sessions.
  Use `kraft:handoff` to file it, attaching the spec. Kraft's own chain plans it -
  don't write a plan doc just to hand it straight to something that writes its
  own.

**Explicit user override wins outright.** If the user already said "do this
inline" or "hand this to Kraft," skip the inline/handoff judgment and honour
that instead - still write the spec first, and still decide how parts are built
below.

**Then decide how independent parts are built.** Slices don't replace
sub-agents. Sub-agents are the default, inline or inside one Kraft item: they
share the worktree and need no setup or join. A part earns more only when it
makes sense on its own: a coherent piece with its own tests, which its own
implement, verify and review loop can check without another part's code. Size
is a weak hint, not the test. Parts that stand alone like that move an
inline-classified item to a handoff, unless the user said "inline": then keep
it inline with sub-agents, and say which parts would stand alone. Otherwise:

- **Separate work items**, when each part can merge on its own and is a full
  pull request in its own right. One `kraft:handoff` per part, each attaching
  a spec for that part only, each its own merge request.
- **Slices**, when the parts must land together, or are too small to be worth
  a merge request each. The same stands-alone test, held to a lower floor: each
  part earns its own loop but needn't be a whole pull request. One handed-off
  item, which Kraft's plan splits into child
  work items, built in parallel and joined into one merge request. Each child
  has its own worktree, setup, implementer session and review. Name each part
  in the spec, with what it covers, so the plan slices along them.

When in doubt, use sub-agents. State the sub-agents, slices or separate-items
call in the same message as the inline/handoff judgment.

Whether to confirm these calls before executing them is step 4's rule.

## 3. Plan - only if the user explicitly asked for one

A plan is bound to the code as it is right now; a spec isn't. A spec written
today is still good in two days, a plan might not be - so don't produce one
speculatively.

If asked, run a plan skill - default `superpowers:writing-plans` - with its own
review gate. Re-run the judgment above with the plan in hand (it sharpens the
same inline/handoff call - e.g. a plan that turns out to be one small task can
downgrade an architectural spec to inline), and attach the plan alongside the
spec if handing off. If the parts are slices, end the plan with a `## Slices`
section, one bullet per slice (`- **Slice 1: Parser.** Tasks 1-3.`), nothing
but task numbers after "Tasks", two to six slices by default. A slice can't use
another's work, and tasks two slices share stay outside every slice: Kraft
builds those first.

**Do not put a full-test-suite run in the plan.** A plan headed for Kraft runs
under a chain whose verification node already runs the suite (and local review)
after implementation, with its own fix loop - a task step that re-runs it is
redundant work the chain repeats anyway. Per-task targeted tests (the test the
task itself is about) stay in the plan; only the suite-wide run is out.

## 4. Auto mode

If the user said `auto` (as an argument, e.g. `/prepare auto ...`) or used
phrasing like "do this in auto" / "don't wait for me": skip the pre-execution
confirmation in step 2 (or 3), act on the judgment immediately, and report the
outcome afterward instead of asking first.

Otherwise: state the judgment and a one-line reason, then stop and wait for the
user to confirm or override it before executing.
