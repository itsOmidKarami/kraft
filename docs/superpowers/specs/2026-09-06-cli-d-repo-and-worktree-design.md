# CLI Sub-project D: Repo and Worktree Context

> **Status:** design proposed 2026-09-06, not yet built. Depends on A.

## 1. Problem

A resolves the cwd to a connected repo (`resolve_repo`), but gives no way to see
what is connected, connect something new from the terminal, or get from a work
item to the checkout its agent is editing.

`client.ensure_repo()` exists — it is an MCP tool — and `GET /repos`,
`POST /repos/probe`, `PATCH /repos`, `DELETE /repos` and
`POST /work-items/{wid}/open-worktree` are all live on the server. This
sub-project is mostly a front door onto them, plus the one genuinely new thing:
`cd`.

## 2. Scope

Verbs: `kraft repos`, `kraft connect`, `kraft path` (alias `cd`), `kraft open`.

Out of scope: disconnecting a repo — `DELETE /repos` stays UI-only, see §6 —
and editing a repo's chain template, model, deny-tools or steering
files. Those are the Settings → Repos form, which validates as it edits; a
`kraft repo set k=v` would need to reimplement that validation on the client
side or ship a footgun. `PATCH /repos` stays UI-only.

## 3. Verbs

**`kraft repos`** — connected repos: name, path, default chain, enabled. Marks
the row containing the cwd, which makes it the answer to "why did my last
command pick that repo".

**`kraft connect [PATH]`** — `client.ensure_repo`, defaulting to the cwd.
Already idempotent by construction (409 → probe → `already_connected: true`), so
the verb prints "already connected" and exits 0 rather than treating it as
failure.

**`kraft path [ID]`, alias `kraft cd [ID]`** — prints the work item's
`worktree_path` and nothing else. A subprocess cannot change its parent's
directory, so `path` is the honest name; `cd` is registered as an argparse alias
because it is what a hand will type. `--shell` prints the wrapper function to
add to a profile, for anyone who wants a real `cd`.

**`kraft open [ID]`** — `POST /work-items/{wid}/open-worktree`, the same editor
launch the UI's "Open worktree" button does, with the same 501 when the server
is headless.

## 4. Not adding a second repos reader

`resolve_repo` (A §5) reads `GET /repos`; so does `kraft repos`. Neither parses
`repos.yaml`. Stated again here because D is where the temptation is strongest —
`kraft repos` looks like it should work with the server down, and the cost of
making it do so is a config reader that drifts from the API's shaping.

## 5. Testing

- `repos` marks the cwd's repo and only that row.
- `connect` on an unconnected path registers it; a second `connect` prints
  "already connected" and exits 0.
- `connect` on a non-git directory surfaces the API's 400 text.
- `path` prints exactly one line with no trailing decoration — asserted
  strictly, because it is consumed by `$(...)`. `cd` resolves to the same verb.
- `open` on a headless server surfaces the 501 as a `kraft:` message.

## 6. Decisions

1. **`kraft path`, with `cd` as an alias.** Honest primary name, memorable
   alias, and argparse gives the alias for free.
2. **No `kraft disconnect`.** It was the only destructive verb in the CLI, and
   dropping it drops `--force`, tty confirmation and the work-item-count warning
   with it. The UI already does this safely, with the affected items on screen.
   It is also the one repo operation nobody performs from a terminal mid-task.
