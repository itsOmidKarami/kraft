# Claude Code

Contents: Start a child, Message a child, Be messaged, Archive a child, Check in
later, then which channel each pairing of controller and child has.

Tools can be deferred: listed by name only until loaded. Search for them (for
example with ToolSearch) before deciding one is missing.

## Start a child

**Cloud controller.** `claude-code-remote:create_session`, with:

- `source_url`: the repo; `source_revision`: the feature branch, or the earlier
  slice's branch for an early start;
- `outcome_branch`: the slice branch, so the child's pushes need no approval;
- `model`: always set; `tags`: `["<chain-id>"]`; `title`: the slice key and title;
- `prompt`: the starting instructions.

It returns the session id. A cloud controller starts cloud children.

**Local controller, local child.** Run through Bash:
`claude --bg --name "<chain-id>-<key>" --model <model> "<starting instructions>"`.
It prints a short id. The child runs under a supervisor without a terminal, and
moves into its own worktree under `.claude/worktrees/` before editing, so tell
it to check out the slice branch there. Tags: unconfirmed; the name carries the
chain id instead, and `claude agents --json` lists the sessions.

**Local controller, cloud child.** `claude --cloud "<starting instructions>"`
from a checkout on the child's base branch, pushed: the cloud clones the remote
at the current branch, not the local checkout. Choosing the cloud child's model
or its branch from this command: unconfirmed; use the fallback, which is to
name both in the instructions and have the child confirm them in its first
message.

## Message a child

- Cloud to cloud: `claude-code-remote:send_message` (`session_id`, `message`),
  under 64 KiB a message. An archived session takes no messages.
- Local to local: `SendMessage`, addressed by the name `ListAgents` shows.
  `notify_when_idle: true` asks for one notice when that session next goes idle.
- Local to cloud: `claude -p "<message>" --cloud <session-id>` queues one message
  and exits. `SendMessage` also reaches a cloud session while this session is
  connected to Remote Control.

## Be messaged

- Cloud child to cloud controller: `claude-code-remote:send_message` to the
  controller's session id.
- Local child to local controller: `SendMessage` to the controller's name, on
  the same machine and on the same side of a container boundary.
- Cloud child to local controller: impossible; a cloud session cannot message
  back to another machine. Use the draft PR's comments, and the controller's
  check-ins are what wake it.
- An arriving message starts a turn in an idle session, so a controller that
  ends its turn is still reachable this way.

## Archive a child

- Cloud: `claude-code-remote:archive_session`; reopen with
  `claude-code-remote:unarchive_session`.
- Local: `claude stop <id>` stops it and keeps it listed; `claude respawn <id>`
  brings it back with its conversation. At wrap-up, `claude rm <id>` removes it
  and the worktree it made, once its branch is pushed.
- A cloud child started from a local controller: archiving it from the terminal
  is unconfirmed; use the fallback, which is to list it for the user to archive
  at claude.ai/code.

## Check in later

- Cloud: `claude-code-remote:send_later` (`delay_minutes` or `at`) delivers a
  message back into this session, and survives the container being reclaimed.
- Local: `CronCreate` with a one-shot or recurring schedule (`CronList`,
  `CronDelete` to manage). It fires only while this session is running and idle,
  and is restored on `--resume`. Recurring tasks expire after seven days.
- To learn when a local child finishes: `SendMessage` with `notify_when_idle`.

## Reading a child

- Cloud: `claude-code-remote:get_session` gives its status, `status_bucket`
  and a summary of its last turn. `claude-code-remote:list_sessions` filters by
  tag, which is how resuming finds the run's children.
- `claude-code-remote:list_events` is the transcript, and overflows; use it only
  with a small `limit` and a `kinds` filter, when the summary is not enough.
- Local: `claude logs <id>` prints a session's recent output.

## Which channel

| Controller | Child | Child reaches controller by |
|---|---|---|
| cloud | cloud | session message |
| local | local | session message, else `.kraft-lite/messages/` files |
| local | cloud | PR comments only |
