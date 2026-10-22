---
name: init
description: "Writes `.kraft-lite/bindings.yaml`, which binds a Kraft Lite chain's tasks to installed skills, instructions, or the repo's own test and CI commands where the defaults do not fit. Use when setting up Kraft Lite in a repo, when its bindings are stale, or when the repo still has a pre-V1 `.kraft-lite/registry.yaml` or a run a Kraft Lite verb says to `migrate`."
---

# Setting up Kraft Lite in this repo

If `$CLAUDE_PLUGIN_ROOT` is unset, `kl.py` is two directories above this file;
it needs `python3` 3.10 or newer, and without one, tell the user and stop.

A pre-V1 `.kraft-lite/registry.yaml` with no `.kraft-lite/bindings.yaml`, or a
verb saying `Run: python3 kl.py migrate`: see the last section first.

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

Write `.kraft-lite/bindings.yaml` from `proposed` plus the user's choices:

- An agent task with two or more candidates, and a builtin or forge task with
  any, is the user's call: a skill bound there replaces the default. Ask once, in
  one message listing them all with their candidates - not one question per
  task - and write nothing for them until they answer.
- A task with no candidate gets no entry.
- Never write a command detect did not report: a binding that cannot run here
  reads as finished until the node fails. A `null` `test_command` or `ci_command`
  leaves that task on its default.
- Every `skill` entry gets a `prompt` sibling (the one `proposed` gives, or one
  line on the task's job). It is required - `start` refuses the file without it -
  and it is what runs when the skill is missing.

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

If `.kraft-lite/bindings.yaml` already exists, build the new one, show the user
a diff against it, and ask; do not overwrite one they have edited. Where it holds
a different command than detect printed, the user's edit wins: leave it, let
the diff show the difference, and let the user choose. `start` refuses a file it
cannot use and names the entry, but catching a gap now is cheaper.

Then print the path and say it is meant to be edited and committed. Tell the user
to add `.kraft-lite/decisions/`, `.kraft-lite/messages/` and `.kraft-lite/proposals/`
to `.gitignore`: a sliced run writes its decisions log, messages and proposals
there, and they are not for the repo. After a migrate, `.kraft-lite/registry.yaml.migrated`
goes there too, or is deleted. `bindings.yaml` stays committed, and so does
`.kraft-lite/harnesses/`, what a sliced run learned about a harness's tools.

## A pre-V1 directory: migrate

A directory is an older Kraft Lite's when it has `.kraft-lite/registry.yaml` and
no `.kraft-lite/bindings.yaml`, or when a verb refuses with
`Run: python3 kl.py migrate`. Write no bindings by hand: run
`python3 "$CLAUDE_PLUGIN_ROOT/kl.py" migrate`. It prints `bindings` - what it
wrote to `bindings.yaml` from the registry, with `dropped`, the hooks no V1 task
replaces; `{}` when there was no registry, so no file was written - and
`chains`, each unfinished run's old id (`from`) and new one (`to`), which every
later verb takes. Report each, say a written `bindings.yaml` is to be committed
and the registry is now `registry.yaml.migrated`, then go straight to the
setup's last paragraph above, the one starting `Then print the path`. A run that finished before V1 needs no
migrate: `state` says `done`; its `summary`, `export` and other reports need the
previous Kraft Lite, and `kl.py` refuses every chain verb but `state` for it.

It refuses, and moves nothing, in these cases; any other refusal names its own
way on: follow it.

- A run it cannot move (sliced, a slice's child, on another legacy chain, with
  nodes the old default lacks, or with its chain record missing): tell the user
  the two ways on it names - finish that run on the previous Kraft Lite version,
  or `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" migrate --abandon <chain-id>`, which
  closes the run's open records, labelled `kraft-abandoned`, and keeps them -
  and run nothing until they choose. Then run `migrate` again. `--abandon`
  refuses an id that is not an unfinished pre-V1 run.
- Registry hooks no V1 task replaces: take each out of the registry, run
  `migrate`, then bind it in `bindings.yaml` by hand, under the task the user
  says it serves.
- A registry Lite cannot read (named with its file and line), one that is not a
  mapping of hooks, or an entry that would make a bindings file Lite refuses,
  such as a `skill` with no `prompt`: correct it in the registry, run `migrate`
  again.
- `bindings.yaml` and `registry.yaml` both present: move `bindings.yaml` aside
  (`.kraft-lite/bindings.yaml.mine`), run `migrate`, then show the user a diff of
  the two files and merge them by hand, their own entries kept unless they say
  otherwise. Delete `bindings.yaml.mine` once merged.
- `the bindings did not read back as written`: a fault in Lite; report it.

With both files present and no verb refusing, `bindings.yaml` is what the walk
reads: leave the registry alone and carry on with the setup above.
