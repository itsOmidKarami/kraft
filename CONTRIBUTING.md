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

See [`docs/testing.md`](docs/testing.md) for the shape a test should take: the
two tiers, the shared fixtures, and the mutate-then-confirm-it-fails procedure
that is the only thing that actually proves a test pins something.
`just check-tests` enforces what of that can be checked mechanically.

Requirements: Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 20+, git.
`claude` is only needed for real agent runs, not for `just dev` or the tests.

## Releasing

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump and no bump commit to forget.

That removes the loud failure and leaves a quiet one: if nobody ever makes a
tag, nothing fails — `main` just accumulates untagged commits while the install
instructions keep serving a release from months ago. So every pull request
declares what it ships, and a maintainer cuts a release from whatever has
merged since the last one.

Put exactly one of these labels on your pull request:

| Label | Means |
|---|---|
| `release::major` | a breaking change to the CLI, the API, or on-disk state |
| `release::minor` | a new capability that does not break an existing one |
| `release::patch` | a fix to something that already shipped |
| `release::none` | no bump of its own: it goes out with the next release |

`release::none` is a first-class answer, and the expected one for documentation,
comments, CI configuration and test-only changes. It does not keep a change out
of a release (every merged change is in the next one); it only adds no weight
to the bump and no line to the notes.

Nothing checks that the declared impact matches the diff. The label is a claim by
its author; review is what tests it.

A pull request that ships something writes its user-facing line in the
`## Changelog` section of its description. Do not edit `CHANGELOG.md`: the
release writes it. A pull request that leaves the section empty is listed by
its title.

**Pull requests from forks are not asked for a label** — only people with write
access can apply one. A maintainer labels the pull request before merging. One
merged without a label stops the next release, naming it; label it (labels
can still be changed after merge) and run the release again.

### Cutting a release

Actions → **release** → **Run workflow** on `main`. Tick **dry run** first to see
the version and notes on the run's summary page without releasing anything.
Leave **sha** empty to release main's tip, or give a commit on main to release
an earlier point. That is useful while the tip's tests are still running. It
cannot be a commit behind the newest release.

`.github/workflows/release.yml` collects every pull request merged since the
previous `vX.Y.Z` tag and bumps by the largest label among them: two
`release::minor` and three `release::patch` is a minor release. If all of them
are `release::none`, there is no version to bump and it releases nothing. The notes are those pull requests'
`## Changelog` sections, grouped into breaking changes, new features and fixes.

It refuses to run until `test` has passed on the commit being released. Then it
builds the wheel, smoke-tests it, pushes the tag, creates the GitHub Release
with the wheel attached, and publishes to PyPI. The tag is created locally
before the build (setuptools-scm reads the version from it) and pushed only
after the smoke test passes, so a failed build leaves nothing behind.

### Plugin manifest versions

`plugins/kraft/.claude-plugin/plugin.json` and `plugins/kraft-lite/.claude-plugin/plugin.json`
carry their own `version` field, shown in `/plugin list`. You never edit this by
hand and pull requests never touch it: `release.yml` stamps both files with
`dev/stamp_plugin_versions.py` right after it tags a release, writes the
release notes into `CHANGELOG.md`, then opens and auto-merges a
`release::none` pull request with both. Doing this on a
release, rather than asking every in-flight pull request to predict its own
future version, is what a hand-stamped file could never do without conflicting
with every other open pull request the moment a release lands.

Opening that pull request needs its own credential: the default `GITHUB_TOKEN`
can't be used, because GitHub suppresses further workflow runs triggered by
`GITHUB_TOKEN`, which would leave the PR's required status checks pending
forever. `release.yml` mints a short-lived token from a GitHub App installed
on this repo instead (`RELEASE_BOT_APP_ID` / `RELEASE_BOT_PRIVATE_KEY`),
scoped to just contents and pull-request writes on `kraft`.

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

## User-facing docs

`docsite/` is the published documentation site (`mkdocs build`, deployed to
GitHub Pages by `.github/workflows/docs.yml` on every push to `main`) — not
to be confused with `docs/intent/` below, which nobody but a contributor
reads. If your change touches any of these, update the matching page in the
same pull request, not as a follow-up:

| Source | Docs page |
|---|---|
| A `kraft` subcommand or flag (`src/kraft/cli/*.py`) | `docsite/cli.md` |
| A `library.yaml` component key, or a `policy.yaml` / `repos.yaml` / `access.yaml` / `intake.yaml` field (`src/kraft/templates/models.py`, `library.py`, `config.py`, `policy.py`) | `docsite/configuration.md` |
| A chain template's node fields, or a new default chain | `docsite/concepts.md` |
| A harness (`src/kraft/harnesses/*.yaml`, `harness.py`) | `docsite/harnesses.md` |
| An MCP tool (`src/kraft/mcp.py`) | `docsite/agent-integration.md` |
| `access.yaml` / remote-access behaviour | `docsite/remote-access.md`, and `SECURITY.md` if it's security-relevant |

Run `uv run --group docs mkdocs build --strict` before you push — it fails
on a broken internal link or anchor, though not on a page that's merely gone
stale prose-wise. A stale-but-still-linking page is exactly the kind of gap
`docs/intent/`'s `enforced-by:` pinning doesn't catch either; there is no
automated backstop for "this paragraph no longer describes the code," only
for "this file/anchor no longer exists." Read the page you're touching, not
just the code.

## Design documents

Specs and implementation plans are not committed. `.engineering/` and
`docs/superpowers/` are gitignored. `design/` and `docs/consolidated/` are
gitignored too and live only in the maintainer's private archive — if you see
them still tracked in the tree, they haven't been scrubbed from history yet.
`docs/intent/` is the exception: it states intended behaviour as pinned
requirements and is maintained with the code.
