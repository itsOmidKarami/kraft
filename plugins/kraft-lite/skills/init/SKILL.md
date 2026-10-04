---
name: init
description: "Writes `.kraft-lite/registry.yaml`, which binds each hook of a Kraft Lite chain to an installed skill, an instruction, or the repo's own test and CI commands. Use when that file is missing or stale (a hook has no binding) in a repo about to run a chain; `kraft-lite:start` sends you here when it is missing."
---

# Setting up Kraft Lite in this repo

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.

If `.kraft-lite/registry.yaml` already exists, build the new one, show the human a
diff against it, and ask; do not overwrite one they have edited.

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" detect` from the repo root. It prints the
test command (`test_command`) and CI status command (`ci_command`) it found and,
for each hook, every installed skill that plausibly serves it. For a chain other
than the packaged default, add `--chain <path-to-chain.json>` so the hooks listed
are that chain's.

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
  found a command for them. Use `test_command` and `ci_command` verbatim as the
  entry's `command`:

      on.test.run:
        kind: subprocess
        command: [just, test]

  Either one being `null` means `kind: prompt` instead: for `on.ci.poll`, an
  instruction to report the pipeline's state once, by whatever means this repo
  has. Never write a command detect did not report — a binding that cannot run
  here reads as finished until the node fails.

  When an existing registry already holds a different `test_command` or `ci_command`
  than detect printed, the person's edit wins: leave it, let the diff show the
  difference, and let the human choose.

Every `skill` entry gets a `prompt` sibling. A renamed or uninstalled skill then
degrades to an instruction instead of stopping the chain.

Verify before you finish: every hook `detect` listed has a key at column zero in
the file with an indented body under it. `kraft-lite:start` refuses a chain whose
hooks lack a binding, so a gap surfaces there, but catching it now is cheaper.

Then print the path and say it is meant to be edited and committed.
