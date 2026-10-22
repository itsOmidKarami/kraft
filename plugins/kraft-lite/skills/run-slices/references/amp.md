# Amp

Sources: Kraft's `amp` harness profile (`src/kraft/harnesses/amp.yaml`, checked
against `amp --help` of 0.0.1790121694 and a run on 0.0.1790142911) and the Amp
manual at ampcode.com/docs (execute mode, streaming JSON, spawning orbs,
threads, automations). Where an ability is marked unconfirmed, look for it as
discover.md says, then use the fallback named, never a guessed command.

Skills are not namespaced here: `kraft-lite:<name>` in the skills means the skill
`<name>`, so a child's instructions say `build-slice`, and the user invokes
`run-slices`. There is no plugin-root variable: `kl.py` is two directories above
the skill file. Copy this paragraph into each child's starting instructions.

Amp has no model flag. `-m` picks a mode (`low`, `medium`, `high`, `ultra`),
which sets model, effort and tools together: `-m medium` is the middle tier.
A child needs a signed-in `amp` or `AMP_API_KEY` (an `sgamp_` access token) in
its environment. With neither, a headless run does not fail at once: it prints
a device-login prompt and waits about five minutes before it exits 1, so check
the log's first lines.

## Start a child

- Local: first the worktree, on the slice branch
  (`git worktree add -b <slice-branch> <path> <fork sha>`); Amp has no directory
  or branch flag and works where it starts. From inside it, in the background,
  its output to a log file:
  `amp --no-archive-after-execute -m medium --stream-json -x "<starting instructions>" > <log> 2>&1 &`.
  `-x` archives the thread when it ends, and an archived thread cannot be
  continued; `--no-archive-after-execute` keeps it open. The thread id
  (`T-...`) is the `session_id` on the log's first `system`/`init` line.
- Cloud (orb):
  `amp --no-archive-after-execute -ox "<starting instructions>" --project <owner/repo> --mode medium --title "<chain-id> <key>: <slice title>"`
  starts a thread on Amp's servers, prints its URL and exits. `-ox` archives the
  thread when its turn ends, and an archived thread refuses `threads continue`;
  whether the flag keeps an orb thread open is unconfirmed. Choosing the orb's
  branch: unconfirmed; use the fallback, which is to name the branch in the
  instructions and have the child confirm it in its first message.
- Tags: unconfirmed; put the chain id and slice key in the instructions' first
  line and the title. `amp threads list` lists threads.

## Message a child

- Local: `amp threads continue <thread-id> --no-archive-after-execute -x "<message>"`,
  from the child's worktree, runs the message as a new turn on its thread. Send
  it once the child's last run has exited; while it runs: unconfirmed, use the
  fallback, the messages file on the same machine.
- Orb: `amp threads continue <thread-id> -ox "<message>"` is documented, but
  the orb thread may have been archived at its turn's end (above), and an
  archived thread refuses it: unconfirmed; use the fallback, comments on the
  child's draft PR.

## Be messaged

A child messaging its controller: unconfirmed; use the fallback. On the same
machine, `.kraft-lite/messages/<chain-id>/<key>.jsonl` in the controller's
checkout; otherwise comments on the child's draft PR.

## Archive a child

`amp threads archive <thread-id>`. An archived thread cannot be continued, so
archive only after the slice merges. Restoring one: unconfirmed. If the command
refuses an orb thread, use the fallback, which is to list it for the user to
archive at ampcode.com.

## Check in later

Unconfirmed; use the fallback. Amp automations wake a thread on a schedule, but
the docs describe them as set by the user in plain words, and name no tool the
controller could call. Say plainly that nothing will wake this session, and tell
the user to invoke `run-slices` to continue the run.

Child: scheduling its own wake-up is unconfirmed too. Its starting instructions
say `none; continue on the controller's check-in`.
