---
name: init
description: "Writes `.kraft-lite/bindings.yaml`, which binds a Kraft Lite chain's tasks to installed skills, instructions, or the repo's own test and CI commands where the defaults do not fit. Use when setting up Kraft Lite in a repo, when its bindings are stale, or when the repo still has a pre-V1 `.kraft-lite/registry.yaml`."
---

# Setting up Kraft Lite in this repo

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.

If `.kraft-lite/registry.yaml` exists, it is an older Kraft Lite's: do not write
bindings by hand. Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" migrate`, which writes
`.kraft-lite/bindings.yaml` from it, renames it `registry.yaml.migrated`, and
moves each unfinished run to a new id. Report what it wrote, what it dropped and
each run's new id, then go to the last paragraph.

Every binding is optional. A task with none runs on its default: an agent task's
own skill or prompt, and an instruction for each builtin and forge task. The file
holds only what differs. A child walking a slice needs nothing more: its slice
defaults are built in.

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" detect` from the repo root, with
`--chain <id or path>` for a chain other than the default. It prints
`test_command`, `ci_command`, `candidates` (for each agent task under `tasks`,
builtin under `builtins` and forge target under `forge`, the installed skills that
plausibly serve it) and `proposed`: the bindings that need no question - an agent
task with exactly one candidate, the test command for
`kraft.verify_changed_test_scopes`, the CI command for `mr.ci`.

Write `.kraft-lite/bindings.yaml` from `proposed` plus the human's choices:

- An agent task with two or more candidates, and a builtin or forge task with
  any, is the human's call: a skill bound there replaces the default. Ask once, in
  one message listing them all with their candidates - not one question per
  task - and write nothing for them until they answer.
- A task with no candidate gets no entry.
- Never write a command detect did not report: a binding that cannot run here
  reads as finished until the node fails. A `null` `test_command` or `ci_command`
  leaves that task on its default.
- Every `skill` entry gets a `prompt` sibling (the one `proposed` gives, or one
  line on the task's job), so a renamed or uninstalled skill degrades to an
  instruction instead of stopping the chain.

      tasks:
        spec_author:
          kind: skill
          skill: superpowers:brainstorming
          prompt: Produce the work item's specification.
      builtins:
        kraft.verify_changed_test_scopes:
          kind: subprocess
          command: [just, test]
      forge:
        mr.ci:
          kind: subprocess
          command: [gh, pr, checks]

If `.kraft-lite/bindings.yaml` already exists, build the new one, show the human
a diff against it, and ask; do not overwrite one they have edited. Where it holds
a different command than detect printed, the person's edit wins: leave it, let
the diff show the difference, and let the human choose. `start` refuses a file it
cannot use and names the entry, but catching a gap now is cheaper.

Then print the path and say it is meant to be edited and committed. Tell the human
to add `.kraft-lite/decisions/`, `.kraft-lite/messages/` and `.kraft-lite/proposals/`
to `.gitignore`: a sliced run writes its decisions log, messages and proposals
there, and they are not for the repo. `bindings.yaml` stays committed.
