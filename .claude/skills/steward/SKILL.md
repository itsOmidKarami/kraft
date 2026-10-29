---
name: steward
description: Kraft's own rules for driving a pull request on itsOmidKarami/kraft to green - which checks to run before a push, how to regenerate generated files and lockfiles, how to answer each CI job, and what the description and labels must carry. Read by Claude Code sessions that drive or babysit a Kraft pull request; not for writing the change itself.
disable-model-invocation: true
---

# Steward a Kraft pull request

These are this repository's conventions for getting a pull request mergeable.
`CONTRIBUTING.md` is the source of truth: where it disagrees with this file, it
wins. Read its "What CI checks" table before working a red check.

## Before every push

Run what the change touches, from the repo root. Run `just setup` first in a
fresh checkout.

| The diff touches | Run |
|---|---|
| anything | `just lint` and `just check-tests` |
| `src/`, `tests/`, `config/` | `just test` on the changed area (`just test tests/<pkg> -n auto`) |
| a moved or renamed test, `docs/intent/` | `just intent` |
| `frontend/` | `just test-ui` |
| `vscode/` or a config model | `just test-vscode` |
| `plugins/kraft-lite/` | `just test plugins/kraft-lite/tests` |
| `docsite/` or a root `*.md` | `just ci-checks` |

**Never run `pytest` directly.** A hook blocks it; `just test` is the door.
`just ci-test` reproduces CI's `lint` and unit jobs on the full suite (about
14 minutes); keep it for when a targeted run cannot reproduce a failure.

## Regenerate, never hand-edit

- Lockfiles: `uv lock` for `uv.lock`; `npm install` inside `frontend/`,
  `vscode/` or `docsite/` for that folder's `package-lock.json`. In a conflict,
  take `main`'s lockfile, then rerun the command.
- `vscode/schemas/`: `just schemas`.
- `frontend/src/types/vocab.generated.ts`: `just vocab`.
- `tests/api/api_shapes.json`: `just shapes`.
- `src/kraft/templates/lucide_icons.txt`: `just icons`.
- `CHANGELOG.md`, `vscode/CHANGELOG.md`, plugin manifest versions and
  `_Added in X.Y_` notes: never touched by a pull request. The release
  writes them.

## Merge conflicts

The repo merges by squash, so a merge commit on the branch costs nothing:
merge `origin/main` into the branch, never rebase or force-push a branch you
did not create. After a merge, rerun the regenerate steps for any generated
file either side changed.

## Answering each check

- **`release impact declared`**: the pull request needs exactly one
  `release::*` label. Set it on a pull request you opened (CONTRIBUTING.md,
  "Pull requests and release labels" says which). On anyone else's, say which
  label you would pick and let the maintainer apply it. Never add
  `notes::highlight`; that is the maintainer's call.
- **`removals declared`**: the description is missing a `## Removed tests` or
  `## Removed requirements` line for something the diff deletes or renames.
  Fix the description, not the code. Check it locally with
  `uv run python dev/check_removals.py origin/main BODY.md`.
- **`lint`**: `just fix` covers ruff. A `dev/check_docs_*.py` failure names the
  page and the rule; CONTRIBUTING.md's "User-facing docs" explains each.
- **An intent pin** (`python -m kraft.intent` in the first test shard):
  repoint it with `just intent-repoint OLD NEW`.
- **`e2e (real CLIs)`, `playwright`, `ui contract`**: real failures as often as
  not. Reproduce with the command in CONTRIBUTING.md's table before calling
  one a flake.
- **`docs nudge`, `tests nudge`**: comments, never failures. A docs nudge saying
  source moved without its docs page is usually right: update the page in this
  pull request (CONTRIBUTING.md, "User-facing docs"). A tests nudge is
  information; act on it only if it names a test you deleted without declaring.
- **`test report`, `codeql`**: `test report` only reports. Treat a `codeql`
  alert like a security review finding.

## The description

Keep the template's sections. A pull request that ships something has one
user-facing line under `## Changelog`; a `release::none` one leaves it empty.
A new docs page, section or CLI verb carries `_Added in next_` under its
heading, never a version number.

## Ask the user first

- Pushing a `kraft/<branch>-screenshots` branch for a pull request that changes
  a screen. CONTRIBUTING.md, "A pull request that changes a screen", says what
  it holds.
- Any change to `.github/workflows/`, `RELEASING.md` or
  `.claude/skills/release/`, unless the pull request is about them.
- Changing a label on a pull request you did not open.
