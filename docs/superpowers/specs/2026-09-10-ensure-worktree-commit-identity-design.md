# ensure_worktree pins commit identity (Kraft-mxdx)

## Problem

`ensure_worktree` (`src/kraft/builtins.py:112`) creates the worktree, copies
attachments, runs `uv sync`, and returns. It never establishes a git commit
identity for the worktree. `_commit_paths`'s docstring states the assumption
it relies on instead: "the worktree inherits the repo's git config, which is
where every other commit on this branch gets its author." That's only true
when the identity actually resolves.

Observed failure: a repo with no `[user]` block anywhere in its git config
chain (no `~/.gitconfig`, no repo-local `user.*`) hits `fatal: unable to
auto-detect email address` on the first commit a node attempts. The agent,
told to commit before it exits, invents an identity to get unblocked
(`kraft@local` was seen in the wild). Nothing in the Kraft repo, templates,
prompts, or agent config names `kraft@local` — pure improvisation.

The fabrication spreads: later nodes check `git log -1 --format='%an <%ae>'`
to see what the branch is already using, read back the fabricated identity,
and treat it as sanctioned. One hallucination becomes branch convention
across every subsequent node.

The failure surfaces three nodes late, as someone else's error. Nothing
between `plan` and `open_mr` looks at authorship — `commit_stragglers`
(`src/kraft/executor.py:511`) explicitly tolerates an unset `user.email` as a
best-effort degradation, logging a warning rather than failing the node. The
push at `open_mr` is what finally fails, rejected by the remote because
`kraft@local` isn't a verified committer email — several retries burned on
an identical rejection before an agent worked around it itself.

The submodule case is the part that actually bit: a submodule's gitdir lives
separately (under `.git/worktrees/<id>/modules/...`) and does not inherit
config the same way the main worktree does. The code commit that carries the
fabricated identity is in a submodule, and it's still on the branch.

## Fix

In `ensure_worktree`, right after `git worktree add` succeeds and before
`_copy_attachments`:

1. Resolve `user.name` and `user.email` from the **source repo** (not the new
   worktree) via `git_read` — same helper `ensure_worktree` already uses for
   `rev-parse HEAD`. This walks git's own config precedence (repo-local >
   global > system); no reimplementation.
2. If either is empty, `raise RuntimeError` naming the missing key and the
   repo path. Raised, not returned — same treatment as the `worktree add`
   failure immediately above it, so `api._guard` turns it into `needs_human`
   at worktree creation. Failing at node 0 with "no user.email in
   /path/to/repo" beats a push rejection at node 8.
3. If both resolve, write them into the new worktree's own git config
   (`git -C <worktree> config user.name/user.email`) so nothing downstream
   depends on inheritance holding.
4. Enumerate the worktree's submodules (`git submodule foreach
   --quiet --recursive`) and pin the same identity into each one's separate
   gitdir. This is the actual gap that bit in the observed failure.

A new top-level helper, `_pin_identity`, holds this logic — mirroring the
existing one-concern helpers in this file (`_commit_paths`,
`_copy_attachments`), each called once from `ensure_worktree`.

### Docstring updates

- `_commit_paths` (`src/kraft/builtins.py:31`): replace the "inherits the
  repo's git config" claim with a note that `ensure_worktree` pins identity
  into the worktree (and its submodules) at creation, so no `-c` override is
  needed here. This makes the docstring's claim true rather than
  aspirational.
- `commit_stragglers`'s comment (`src/kraft/executor.py:511`): drop "an
  unset user.email" from the list of tolerated best-effort failure causes —
  that cause is now structurally prevented before any node runs. The
  remaining causes (an index lock a co-task holds, a submodule `add -A`
  finds nothing to stage in) stand unchanged.

## Non-goals

- No repo-wide policy on *what* identity to use — whatever the source repo's
  git config already resolves to is authoritative, same as it was implicitly
  assumed to be before this fix.
- No enforcement elsewhere in the chain telling agents "never fabricate a
  git identity." Belt-and-braces; the real fix is that a resolved identity
  means an agent never meets the prompt that invites invention.

## Testing

One new case in `tests/test_builtins.py`: a repo built without `make_repo`'s
usual `user.email`/`user.name` config (or with it explicitly unset) →
`ensure_worktree` raises `RuntimeError` naming the missing key. Existing
tests already run through `make_repo`, which sets `user.email`/`user.name`
in repo-local config, so they exercise the pin-into-worktree path as a
side effect with no changes needed.
