---
name: prepare
description: Use before starting non-trivial work - defines a spec (with review), optionally
  a plan (with review) if asked for one, then judges whether the work should happen inline in
  this session or be handed off to Kraft as a work item.
---

# Preparing work

This is the layer above `handoff`: it defines the work first, then decides where
it should run. Read `handoff`'s SKILL.md before the handoff step below if you
haven't already.

## 1. Spec

Run a spec skill - default `superpowers:brainstorming` - unless the user named a
different one. Its own self-review and user-review gate cover "review the spec";
don't add a second review pass on top of it.

## 2. Judge

Right after the spec, before touching a plan:

- **Inline** - the spec came out spike- or bounded-classified. Go straight to
  the normal dev workflow (TDD, etc.) in this session. No plan doc needed:
  that's already bounded's own terminal state.
- **Handoff** - the spec came out architectural-classified, or the user already
  said the work is too big for this session, needs gates, or spans sessions.
  Use `handoff` to file it, attaching the spec. Kraft's own chain plans it -
  don't write a plan doc just to hand it straight to something that writes its
  own.

**Explicit user override wins outright.** If the user already said "do this
inline" or "hand this to Kraft," skip judgment and honor that instead - still
write the spec first.

## 3. Plan - only if the user explicitly asked for one

A plan is bound to the code as it is right now; a spec isn't. A spec written
today is still good in two days, a plan might not be - so don't produce one
speculatively.

If asked, run a plan skill - default `superpowers:writing-plans` - with its own
review gate. Re-run the judgment above with the plan in hand (it sharpens the
same inline/handoff call - e.g. a plan that turns out to be one small task can
downgrade an architectural spec to inline), and attach the plan alongside the
spec if handing off.

## 4. Auto mode

If the user said `auto` (as an argument, e.g. `/prepare auto ...`) or used
phrasing like "do this in auto" / "don't wait for me": skip the pre-execution
confirmation in step 2 (or 3), act on the judgment immediately, and report the
outcome afterward instead of asking first.

Otherwise: state the judgment and a one-line reason, then stop and wait for the
user to confirm or override it before executing.
