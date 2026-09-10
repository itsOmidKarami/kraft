# Plan: ensure_worktree pins commit identity (Kraft-mxdx)

Spec: `docs/superpowers/specs/2026-09-10-ensure-worktree-commit-identity-design.md`

## Step 1 — `_pin_identity` helper + wiring in `ensure_worktree`

File: `src/kraft/builtins.py`

- Add `_pin_identity(repo: Path, worktree: Path, work_item_id: str) -> None`
  near `_commit_paths`/`_copy_attachments`:
  - `name = git_read(repo, "config", "--get", "user.name", expected_failure=True)`
  - `email = git_read(repo, "config", "--get", "user.email", expected_failure=True)`
  - Missing either → `raise RuntimeError(f"no {missing} configured in {repo}; set it before Kraft can commit")`
  - Else: `git -C <worktree> config user.name/user.email`.
  - Enumerate submodules: `git -C <worktree> submodule foreach --quiet --recursive echo $sm_path`
    (via `git_read`, `expected_failure=True` — no submodules is a normal, silent
    empty result, not a fault) and pin the same name/email into
    `worktree / <each path>`.
- In `ensure_worktree`, call `_pin_identity(Path(repo), worktree, work_item_id)`
  right after the `git worktree add` success check (after the existing
  `raise RuntimeError(...)` for a failed `add`) and before the
  `_copy_attachments` call.

## Step 2 — Docstring/comment updates

- `_commit_paths` docstring (`src/kraft/builtins.py:31`): replace the
  "worktree inherits the repo's git config" sentence with one stating
  `ensure_worktree` pins identity into the worktree and its submodules at
  creation.
- `commit_stragglers` comment (`src/kraft/executor.py:511`): remove "an
  unset user.email" from the tolerated-failure list.

## Step 3 — Test

File: `tests/test_builtins.py`

- New test: build a repo whose local git config has no `user.name`/
  `user.email` (construct directly rather than via `make_repo`, or `git
  config --unset` after `make_repo`), call `ensure_worktree`, assert it
  raises `RuntimeError` with the missing key named in the message.
- Run targeted: `uv run pytest -q tests/test_builtins.py -k identity`
  (plus the existing `ensure_worktree`/`_commit_paths` tests in the same
  file, to confirm the pin-into-worktree path is a no-op for repos that
  already have identity configured — which is all of them, via `make_repo`).

## Out of scope

- No changes to `commit_stragglers`'s actual commit logic — only its
  comment.
- No new CLI/MCP surface. This is entirely inside `ensure_worktree`'s
  existing call path.
