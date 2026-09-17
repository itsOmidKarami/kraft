# Contributing

## Getting set up

```bash
just setup      # uv sync + npm install
just dev        # backend + vite, state in .dev/, agents faked, UI on :5173
```

`just dev` puts `fixtures/bin` on `PATH` ahead of the real agent, where `claude`
is a symlink to `fixtures/fake-claude.sh` — the same fake the test suite uses, so
it cannot rot. A dev instance never spends tokens and never touches `~/.kraft`.

Run `just` for the full list of recipes.

## Tests

```bash
just test       # backend tests affected by your change (testmon)
just test-ui    # frontend unit tests
just e2e        # Playwright
just lint       # ruff check + format check
just fix        # autofix
```

**Do not call `pytest` directly.** `just test` goes through testmon's
change-tracking; a raw invocation skips it and runs the full ~14 minute suite.
A Claude Code hook blocks it for agent sessions.

Requirements: Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 20+, git.
`claude` is only needed for real agent runs, not for `just dev` or the tests.

## Releasing

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump and no bump commit to forget.

That removes the loud failure and leaves a quiet one: if nobody ever makes a
tag, nothing fails — `main` just accumulates untagged commits while the install
instructions keep serving a release from months ago. So every pull request
declares what it ships, and merging is what tags.

Put exactly one of these labels on your pull request:

| Label | Means |
|---|---|
| `release::major` | a breaking change to the CLI, the API, or on-disk state |
| `release::minor` | a new capability that does not break an existing one |
| `release::patch` | a fix to something that already shipped |
| `release::none` | nothing a user of Kraft receives |

`release::none` is a first-class answer, and the expected one for documentation,
comments, CI configuration and test-only changes. It is not an escape hatch — it
is the declaration that this change ships nothing.

Nothing checks that the declared impact matches the diff. The label is a claim by
its author; review is what tests it.

**Pull requests from forks are not asked for a label** — only people with write
access can apply one. A maintainer labels the pull request before merging. An
unlabelled merge reads as `release::none` and ships nothing.

### What happens on merge

`.github/workflows/release.yml` reads the merged pull request's label, computes
the next tag, builds the wheel, smoke-tests it, then pushes the tag, creates the
GitHub Release with the wheel attached, and publishes to PyPI. The tag is created
locally before the build (setuptools-scm reads the version from it) and pushed
only after the smoke test passes, so a failed build leaves nothing behind.

### Plugin manifest versions

`plugins/kraft/.claude-plugin/plugin.json` and `plugins/kraft-lite/.claude-plugin/plugin.json`
carry their own `version` field, shown in `/plugin list`. Nothing derives it
automatically the way the wheel's version comes from the tag, so a
`release::{major,minor,patch}`-labelled pull request must already bump both
files to the version that label will tag — the lint job checks this
(`dev/check_plugin_version.py`) and fails with the exact command to run if
they're stale:

```bash
python3 dev/stamp_plugin_versions.py <version>   # no leading v
```

## Branch hygiene

Two ways a branch you're reusing by hand can quietly cost you work:

**Don't reuse a branch after its pull request merges.** This repo merges by
squash, so the branch's own commits never become ancestors of `main` — only
their squashed equivalent does. Push more commits onto that same branch
afterward and `git diff` against its old merge-base re-presents everything the
squash already landed. Cut a fresh branch from `origin/main` instead.

**Rebuilding a branch by `git reset` + cherry-pick can silently orphan a
commit.** Resetting a branch to an earlier point and re-picking commits drops
anything you forgot to include — no warning, and the commit survives only as a
dangling object until the next `git gc`. Before you reset, capture the tip:

```bash
old=$(git rev-parse mybranch)
git reset --hard <earlier-point>
git cherry-pick <sha1> <sha2> ...
git log --oneline "$old" --not mybranch   # non-empty means something is missing
```

## Design documents

Specs and implementation plans are not committed. `.engineering/` and
`docs/superpowers/` are gitignored. `design/` and `docs/consolidated/` are
gitignored too and live only in the maintainer's private archive — if you see
them still tracked in the tree, they haven't been scrubbed from history yet.
`docs/intent/` is the exception: it states intended behaviour as pinned
requirements and is maintained with the code.
