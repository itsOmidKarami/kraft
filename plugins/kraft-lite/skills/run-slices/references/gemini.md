# Gemini CLI

Sources: Kraft's `gemini` harness profile (`src/kraft/harnesses/gemini.yaml`,
checked against `gemini --help` of 0.46.0 and 0.61.0) and the Gemini CLI docs at
geminicli.com/docs (CLI reference, headless mode, session management). Where an
ability is marked unconfirmed, look for it as discover.md says, then use the
fallback named, never a guessed command.

Skills are not namespaced here: `kraft-lite:<name>` in the skills means the skill
`<name>`, so a child's instructions say `build-slice`, and the user invokes
`run-slices`. There is no plugin-root variable: `kl.py` is two directories above
the skill file. Copy this paragraph into each child's starting instructions.

Gemini CLI has no session tools and no cloud agent: every child is local, and
every ability below is a command run through the shell tool.

## Start a child

- First the worktree, on the slice branch:
  `git worktree add -b <slice-branch> <path> <fork sha>`. The CLI has no
  directory flag and runs where it starts. Its `--worktree` is experimental, and
  naming its branch with it is unconfirmed.
- Then, from inside it, in the background, its output to a log file:
  `gemini --skip-trust -m <model> --approval-mode yolo -o stream-json -p "<starting instructions>" > <log> 2>&1 &`.
  `--skip-trust`: a headless run refuses a folder it does not trust, and every
  worktree is a new one. `yolo`: `auto_edit` still asks before a shell command,
  and a headless child has nobody to answer. `-m` takes a `gemini-...` model.
- Activating a skill shows a consent prompt; whether `-p` with `yolo` answers it
  is unconfirmed. Check the child's log for a skill activation left pending or
  denied, and if you find one, tell the user.
- It needs `GEMINI_API_KEY` (or Gemini Code Assist) in the environment.
- Session id: unconfirmed; record the worktree path as the session id. Sessions
  are kept per folder, so the worktree identifies its child.
- Tags: unconfirmed; put the chain id and slice key in the instructions' first
  line and in the worktree's folder name.

## Message a child

Unconfirmed. `--resume` takes `latest` or an index, not a session id, and the
docs do not show it with `-p`. Use the fallback: on the same machine, the
messages file `.kraft-lite/messages/<chain-id>/<key>.jsonl`, which the child
reads at its own channel checks (nothing wakes it); otherwise a comment on its
draft PR. A child whose run
has ended has exited: to continue it, start a fresh child on its branch
(section 4).

## Be messaged

A child messaging its controller: unconfirmed; use the fallback. On the same
machine, `.kraft-lite/messages/<chain-id>/<key>.jsonl` in the controller's
checkout; otherwise comments on the child's draft PR.

## Archive a child

There is nothing to archive: a headless child exits when its run ends. Never run
`gemini --delete-session`, which deletes the conversation for good. The fallback
is to list the worktree and branch for the user to delete once the slice is
merged (section 9).

## Check in later

Unconfirmed; use the fallback. Gemini CLI's hooks run commands but wake no
session. Say plainly that nothing will wake this session, and tell the user to
invoke `run-slices` to continue the run.

Child: scheduling its own wake-up is unconfirmed too. Its starting instructions
say `none; continue on the controller's check-in`.
