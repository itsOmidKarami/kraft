# Codex

Confirmed from the Codex CLI command reference. Where an ability is marked
unconfirmed, use the fallback named, not a guessed command.

## Start a child

- Local: through the shell, `codex exec -m <model> "<starting instructions>"`
  runs a non-interactive session. Run it in the slice's own worktree, on the
  slice branch, so its commits land there.
- Cloud: `codex cloud exec --env <ENV_ID> "<starting instructions>"` submits a
  cloud task (`codex cloud` lists the environments). Choosing its model or
  branch: unconfirmed; use the fallback, which is to name both in the
  instructions and have the child confirm them in its first message.
- Tags: unconfirmed; put the chain id and slice key in the instructions' first
  line. `codex cloud list --json` lists cloud tasks.

## Message a child

- `codex queue --thread <session-id> --message "<message>"` queues a message for
  an existing session.
- `codex exec resume <session-id> "<message>"` resumes a non-interactive session
  with a follow-up.
- Messaging a cloud task: unconfirmed; use the fallback, PR comments.

## Be messaged

A child messaging its controller: unconfirmed; use the fallback. On the same
machine, `.kraft-lite/messages/<chain-id>/<key>.jsonl` in the controller's
checkout; otherwise comments on the child's draft PR.

## Archive a child

`codex archive <session>`; `codex unarchive <session>` restores it. Both take a
saved session's id or name. Archiving a cloud task: unconfirmed; use the
fallback, which is to list it for the user.

## Check in later

Codex thread automations return to the same thread on a schedule. Whether the
controller can create one itself: unconfirmed; use the fallback. Say plainly
that nothing will wake this session, and tell the user to invoke
`kraft-lite:next` to continue the run.
