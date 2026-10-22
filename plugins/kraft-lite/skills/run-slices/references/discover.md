# Discovering a harness's abilities

Use this when this harness has no guide in `references/`, or its guide marks an
ability unconfirmed. Do it before section 2's checklist, and before any
fallback. `<harness>` is the harness's own id: `gemini`, `amp`, `goose`.

## A local guide first

Read `.kraft-lite/harnesses/<harness>.md` if it exists: an earlier run wrote it.
Run the harness's version command (`<cli> --version`). When the version matches
the one the file was checked on, use the file and do not research again.
Re-check an ability when the version differs, or when one of its commands fails.

## Where to look, in this order

1. Your own tool list, deferred tools included: search for a tool (for example
   with ToolSearch) before deciding it is missing.
2. Kraft's harness profile, if there is one: `src/kraft/harnesses/<harness>.yaml`
   in a Kraft checkout, or `kraft admin harnesses <harness>` where `kraft` is
   installed. Its facts outrank the web docs.
3. The harness CLI's `--help`, then each subcommand's (`<cli> <command> --help`)
   that could start, message, list, archive or schedule a session.
4. The harness's official documentation.

## What counts as confirmed

An ability is CONFIRMED only when help output, a tool's own description, Kraft's
profile or the official docs state the command or tool and what it does.
Anything else is UNCONFIRMED: a command remembered from training, a third-party
page, a flag that looks right, a tool whose purpose is not stated.

Run only read-only commands: `--help`, `--version`, and list commands. Never
guess a command or flag, and never test one by starting a real child: a trial
child is a session doing real work on the repo.

## Write it down

Write `.kraft-lite/harnesses/<harness>.md` in a shipped guide's shape:

```
# <Harness>

Checked on <cli> <version>, <date>. Sources: <each help command and doc page read>.

## Start a child
CONFIRMED (`<cli> run --help`): `<exact command>`, run from the slice's worktree.

## Message a child
UNCONFIRMED: <what was looked for, where>. Fallback: the messages file.

## Be messaged
## Archive a child
## Check in later
```

Every ability gets CONFIRMED or UNCONFIRMED, its source, and for UNCONFIRMED its
fallback. Add whether skills are namespaced here and whether a plugin-root
variable is set; without one, `kl.py` is two directories above the skill file.
Then log it:
`KL decide --question "Harness abilities for <harness>" --answer "<confirmed abilities>; .kraft-lite/harnesses/<harness>.md" --why "checked on <cli> <version>"`.

## Use what you found

Use only CONFIRMED abilities. Each UNCONFIRMED one takes its fallback: the
messages file or PR comments for messages, a list for the user in place of
archiving, and for checking in, telling the user nothing wakes this session, so
they invoke the run-slices skill to continue the run. With no way to start a
child, walk the slices yourself (section 10).

Tell the user what you found, ability by ability, and offer the file as a
shipped guide: `skills/run-slices/references/<harness>.md` in Kraft Lite.
