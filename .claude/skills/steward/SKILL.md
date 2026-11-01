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
| anything | `just ci-checks` (CI's whole `lint` job, plus the intent check) |
| `src/`, `tests/`, `config/` | `just test` on the changed area (`just test tests/<pkg> -n auto`) |
| `frontend/` | `just test-ui` |
| `vscode/` or a config model | `just test-vscode` |
| `plugins/kraft-lite/` | CI's own command: `uv run --python 3.10 --no-project --with pytest pytest plugins/kraft-lite/tests -q` |
| `docsite/` or `frontend/src/` (the `docs` job) | `dev/build_docs_site.sh docsite/dist-site && python3 dev/check_llm_docs.py docsite/dist-site`; `just docs-site` ends in a server that never exits |

**Never run `pytest` directly.** A hook blocks it; `just test` is the door.
The kraft-lite row is the one exception: CI runs it on Python 3.10 with
nothing installed, which `just test` cannot reproduce. `just ci-test`
reproduces CI's `lint` and unit jobs on the full suite (about 14 minutes);
keep it for when a targeted run cannot reproduce a failure. A unit shard red
on one Python version only: `just test-py 3.12` (or `3.13`, `3.14`).

## Regenerate, never hand-edit

- Lockfiles: `uv lock` for `uv.lock`; `npm install` inside `frontend/`,
  `vscode/`, `vscode/dev/screenshots/` or `docsite/` for that folder's
  `package-lock.json`. In a conflict, take `main`'s lockfile, then rerun the
  command.
- `vscode/schemas/`: `just schemas`.
- `frontend/src/types/vocab.generated.ts`: `just vocab`.
- `tests/api/api_shapes.json`: `just shapes`.
- `src/kraft/templates/lucide_icons.txt`: `just icons`.
- `frontend/src/ng/theme/theme.css` and `src/kraft/console/theme.py`:
  `uv run python dev/gen_theme.py`.
- Never touched by a pull request, because the release writes them:
  `CHANGELOG.md`, `vscode/CHANGELOG.md`, `_Added in X.Y_` notes, and the
  `version` in the files `dev/stamp_plugin_versions.py` lists (the plugin
  manifests under `plugins/`, `vscode/package.json`,
  `vscode/package-lock.json`).
- The opposite rule for add-ons: a change to `config/library.yaml`,
  `config/chains/` or `addons/<name>/` raises that add-on's `version` in
  `config/.kraft/addon.json` or `addons/<name>/.kraft/addon.json`, in the same
  pull request (CONTRIBUTING.md, "Kraft's own collection", says by how much).

## Merge conflicts

The repo merges by squash, so a merge commit on the branch costs nothing:
merge `origin/main` into the branch, never rebase or force-push a branch you
did not create. After a merge, rerun the regenerate steps for any generated
file either side changed. Never push to a branch whose pull request already
merged: cut a fresh one from `origin/main` (CONTRIBUTING.md, "Branch hygiene").

## Answering each check

- **`release impact declared`**: the pull request needs exactly one
  `release::*` label. Set it on a pull request you opened (CONTRIBUTING.md,
  "Pull requests and release labels" says which). On anyone else's, say which
  label you would pick and let the maintainer apply it. Never add
  `notes::highlight`; that is the maintainer's call.
- **`removals declared`**: the description is missing a `## Removed tests` or
  `## Removed requirements` line for something the diff deletes or renames.
  Fix the description, not the code. Check it locally with
  `uv run python dev/check_removals.py origin/main BODY.md`. Editing the body
  starts no run: re-run the failed `removals declared` job afterwards.
- **`lint`**: `just fix` covers ruff. A `dev/check_docs_*.py` failure names the
  page and the rule; CONTRIBUTING.md's "User-facing docs" explains each. A
  `check_addon_versions.py` failure means an add-on changed without its
  `version` raised (above).
- **An intent pin** (`python -m kraft.intent` in the first test shard):
  repoint it with `just intent-repoint OLD NEW`.
- **`e2e (real CLIs)`, `playwright`, `ui contract`**: real failures as often as
  not. Reproduce with the command in CONTRIBUTING.md's table before calling
  one a flake. `e2e (macOS sandbox, …)` is in the `test` gate too and cannot
  be reproduced on Linux: read its log and fix from that.
- **`docs tests`**: a docs-only pull request's stand-in for the unit tier. It
  runs the unit test files that name a docs path; CONTRIBUTING.md's table says
  how to find them.
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

- Any change to `.github/workflows/`, `RELEASING.md` or
  `.claude/skills/release/`, unless the pull request is about them.
- Changing a label on a pull request you did not open.
