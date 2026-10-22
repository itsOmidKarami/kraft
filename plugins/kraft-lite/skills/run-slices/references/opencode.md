# OpenCode

Sources: Kraft's `opencode` harness profile
(`src/kraft/harnesses/opencode.yaml`, checked against `opencode run --help` of
1.18.32, 2.0.0 and 2.0.15), the CLI docs at opencode.ai/docs/cli, and the v2 API
reference at opencode.ai/v2/docs/api. Where an ability is marked unconfirmed,
look for it as discover.md says, then use the fallback named, never a guessed
command.

Skills are not namespaced here: `kraft-lite:<name>` in the skills means the skill
`<name>`, so a child's instructions say `build-slice`, and the user invokes
`run-slices`. There is no plugin-root variable: `kl.py` is two directories above
the skill file. Copy this paragraph into each child's starting instructions.

Use OpenCode 2.0.0 or newer (npm's `opencode-ai` is still 1.x). It has no cloud
agent: every child is local.

## Start a child

- First the worktree, on the slice branch
  (`git worktree add -b <slice-branch> <path> <fork sha>`), and start the child
  from inside it. From there, in the background, its output to a log file:
  `opencode run --format json --auto -m <provider/model> -- "<starting instructions>" > <log> 2>&1 &`.
  Without `--auto`, `run` rejects every permission ask. `-m` takes
  `provider/model`, with any reasoning variant in the id (`provider/model#high`).
  Every JSON line carries the `sessionID`, the session id from then on.
- Tags: unconfirmed (`--title` is in 1.x's help only); put the chain id and
  slice key in the instructions' first line. `opencode session list --format json`
  lists sessions.

## Message a child

- `opencode run --format json --auto --session <session-id> -- "<message>"`,
  from the child's worktree, continues its session and runs until that turn
  ends.
- Sending while the child's own run is still going: unconfirmed; send it once
  its last `run` has exited, else use the fallback, the messages file on the
  same machine.

## Be messaged

A child messaging its controller: unconfirmed; use the fallback. On the same
machine, `.kraft-lite/messages/<chain-id>/<key>.jsonl` in the controller's
checkout; otherwise comments on the child's draft PR.

## Archive a child

The v2 API archives a session (`PATCH /api/session/{sessionID}` with
`{"archived": true}`), but how to reach it from the shell is unconfirmed, and the
CLI has no archive command. Use the fallback, which is to list the child for the
user. Never run `opencode session delete`: it deletes the session and its
children for good.

## Check in later

Unconfirmed; use the fallback. Say plainly that nothing will wake this session,
and tell the user to invoke `run-slices` to continue the run. When woken,
`opencode session export <session-id>` prints a child's session as JSON.

Child: scheduling its own wake-up is unconfirmed too. Its starting instructions
say `none; continue on the controller's check-in`.
