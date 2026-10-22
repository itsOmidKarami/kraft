# Antigravity

Sources: Kraft's `antigravity` harness profile
(`src/kraft/harnesses/antigravity.yaml`, checked against `agy --help` and runs
of agy 1.2.14) and the Antigravity CLI docs at antigravity.google/docs (headless
mode, conversations, schedule). Where an ability is marked unconfirmed, look for
it as discover.md says, then use the fallback named, never a guessed command.

Skills are not namespaced here: `kraft-lite:<name>` in the skills means the skill
`<name>`, so a child's instructions say `build-slice`, and the user invokes
`run-slices`. There is no plugin-root variable: `kl.py` is two directories above
the skill file. Copy this paragraph into each child's starting instructions.

## Start a child

- Local: first the worktree, on the slice branch
  (`git worktree add -b <slice-branch> <path> <fork sha>`); `agy` has no
  directory or branch flag and works where it starts. From inside it, in the
  background, its output to a log file:
  `agy -p "<starting instructions>" --model <model> --dangerously-skip-permissions --output-format stream-json > <log> 2>&1 &`.
  Without `--dangerously-skip-permissions` a headless child is refused every
  file write and shell command, and still exits 0 with `denied_actions` in its
  `result`: read that field when it finishes. `agy models` lists the model
  slugs. The log's first line, the `init` event, carries the child's
  `conversation_id`, its session id from then on.
- A headless `agy` whose output is not a terminal has hung, printing nothing
  (google-antigravity/antigravity-cli#318, closed without a fixed version;
  1.2.14 ran fine under Kraft). At each check-in, check the child's log is
  still growing.
- It needs a cached sign-in from one interactive `agy` run, or
  `"modelProvider": "gemini"` in its settings with `GEMINI_API_KEY`.
- Cloud: none documented; start the child locally.
- Do not use a subagent (`invoke_subagent`) for a slice: it dies with this
  session, and how to give it a model or branch is unconfirmed.
- Tags: unconfirmed; put the chain id and slice key in the instructions' first
  line. Conversations are kept per directory, so each worktree holds its own.

## Message a child

- `agy -p "<message>" --conversation <conversation-id> --dangerously-skip-permissions --output-format stream-json`,
  from the child's worktree, continues its conversation as a new run. Send it
  once the child's last run has exited; while it runs: unconfirmed, use the
  fallback, the messages file on the same machine.

## Be messaged

A child messaging its controller: unconfirmed; use the fallback. On the same
machine, `.kraft-lite/messages/<chain-id>/<key>.jsonl` in the controller's
checkout; otherwise comments on the child's draft PR. Each run's final reply is
also the `result` event in its log.

## Archive a child

Unconfirmed; the docs describe only deleting a conversation, which is
permanent: do not. A child leaves no process once its run ends. Use the
fallback, which is to list it for the user.

## Check in later

`/schedule` sets a timer that wakes this same conversation, through the agent's
built-in `schedule` tool; a restart loses it. Calling that tool without the user
typing `/schedule`, and scheduling in a `-p` session: unconfirmed. Call the tool
if this session lists it. If it is missing or refused, use the fallback: say
plainly that nothing will wake this session, and tell the user to invoke
`run-slices` to continue the run.

Child: scheduling its own wake-up is unconfirmed too. Its starting instructions
say `none; continue on the controller's check-in`.
