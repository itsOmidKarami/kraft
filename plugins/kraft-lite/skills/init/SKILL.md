---
name: init
description: Use once per repo before running a Kraft Lite chain - detects the test command and which installed skills can serve each chain hook, then writes a registry the human can edit.
---

# Setting up Kraft Lite in this repo

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" detect` from the repo root. It prints the
test command it found and, for each hook, every installed skill that plausibly
serves it.

Write `.kraft-lite/registry.yaml` from that output. Fill every hook in. For each
one, add a comment listing the other candidates detect returned, so the human can
see what you passed over:

    on.spec.requested:
      kind: skill
      skill: superpowers:brainstorming
      prompt: Agree requirements and write a spec before any code.  # used if the skill is missing
      # also found: (none)

Rules for filling it in:

- Exactly one candidate: use it, no question.
- Two or more: ask the human, once, listing them. One message, all the ambiguous
  hooks together - not one question per hook.
- None: write `kind: prompt` with a one-line instruction describing the node's
  job. The chain still runs.
- `on.test.run` and `on.ci.poll` are `kind: subprocess` — but only when detect
  found a command for them. Use `test_command` and `ci_command` verbatim. Either
  one being `null` means `kind: prompt` instead: for `on.ci.poll`, an instruction
  to report the pipeline's state once, by whatever means this repo has. Never
  write a command detect did not report — a binding that cannot run here reads as
  finished until the node fails.

Every `skill` entry gets a `prompt` sibling. A renamed or uninstalled skill then
degrades to an instruction instead of stopping the chain.

Finish by printing the path and saying it is meant to be edited and committed.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
