# Cursor

Sources: Cursor's CLI and Cloud Agents API documentation. Where an ability is
marked unconfirmed, look for it as discover.md says, then use the fallback
named, never a guessed tool.

## Start a child

- Cloud: the Cloud Agents API, `POST https://api.cursor.com/v1/agents`, with a
  Cursor API key the user provides. Body: `prompt`, `repos` (`url`,
  `startingRef`), `model` (`model.id`). It works on a generated `cursor/...`
  branch unless `workOnCurrentBranch` is true, which makes the starting ref its
  branch: push the slice branch first, then start the child on it.
- Local: through the shell, `agent -p --model <model> "<starting instructions>"`
  runs the Cursor CLI without a terminal; `-w` runs it in a git worktree. Tell it
  to check out the slice branch there.
- Tags: unconfirmed; put the chain id and slice key in the instructions' first
  line. `GET /v1/agents` lists cloud agents.

## Message a child

- Cloud: `POST /v1/agents/{id}/runs` with a `prompt` starts a follow-up run. It
  answers `409 agent_busy` while a run is active; send it at the next check-in.
- Local: `--resume [chatId]` resumes a chat and `-p` runs without a terminal,
  but the docs do not show the two together on a running chat: unconfirmed;
  use the fallback, the messages file on the same machine.

## Be messaged

A child messaging its controller: unconfirmed; use the fallback. On the same
machine, `.kraft-lite/messages/<chain-id>/<key>.jsonl` in the controller's
checkout; otherwise comments on the child's draft PR.

## Archive a child

- Cloud: `POST /v1/agents/{id}/archive`; `POST /v1/agents/{id}/unarchive`
  restores it. `DELETE` is permanent: do not use it.
- Local: unconfirmed; use the fallback, which is to list it for the user.

## Check in later

Unconfirmed; use the fallback. Say plainly that nothing will wake this session,
and tell the user to invoke `kraft-lite:run-slices` to continue the run.
`GET /v1/agents/{id}` reads a cloud child's status when it is woken.

Child: scheduling its own wake-up is unconfirmed too. Its starting instructions
say `none; continue on the controller's check-in`.
