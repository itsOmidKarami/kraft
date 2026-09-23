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

Requirements: Python 3.14+ (`requires-python` in `pyproject.toml`),
[uv](https://docs.astral.sh/uv/), and git. The frontend and the docs site build
on Node 22 in CI; `frontend/package.json` sets no `engines` floor, so use the
same major. `claude` is only needed for real agent runs, not for `just dev` or
the tests. Semantic search is opt-in: `just setup-vector` downloads a model of
roughly 130 MB on first use, and search works without it in full-text mode.
[`bd`](https://github.com/gastownhall/beads) is optional: with it, every work
item gets a tracked bead, and without it Kraft files work anyway and says so.

`just dev-seed` fills a running dev instance with work items in every state by
driving the real HTTP API, and `just dev-reset` throws `.dev/` away. A work item
title containing `KRAFT_FAIL` or `KRAFT_SLOW` steers that item's fake agent.

## Layout

```text
src/kraft/        orchestrator: api, executor, policy, store, adapters/, index/
frontend/         React SPA (vite)
templates/        the V1 library, chains, harness profiles and policy: the install seed
dev/seed.py       dev-instance seeder
fixtures/         fake agent and the PATH shim just dev uses
docs/intent/      intended behaviour as pinned requirements, each tied to a test
docsite/          the published documentation site
plugins/          the Claude Code plugins: kraft and kraft-lite
```

## Tests

```bash
just test       # backend tests affected by your change (testmon); --no-testmon for all
just e2e        # Playwright (see frontend/e2e/README.md)
just test-ui    # frontend unit tests
just intent     # check that every enforced-by pin in docs/intent/ still resolves
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

## Pull requests and release labels

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump and no bump commit to forget.

That removes the loud failure and leaves a quiet one: if nobody ever makes a
tag, nothing fails — `main` just accumulates untagged commits while the install
instructions keep serving a release from months ago. So every pull request
declares what it ships, and a maintainer cuts a release from whatever has
merged since the last one (see [RELEASING.md](RELEASING.md)).

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

### Check that your label worked

A maintainer can run the release workflow as a dry run (see
[RELEASING.md](RELEASING.md#cut-a-release)). On the run's summary page, confirm
that the version bump matches the largest label among the merged pull requests
and that your pull request's `## Changelog` line appears under the right
heading. If your pull request is missing or listed by its title, fix the label
or the section in the description and run the dry run again.

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

`docsite/` is the published documentation site (a Nuxt/Docus app; `nuxt
generate`, deployed to GitHub Pages by `.github/workflows/docs.yml` on every
push to `main`) — not to be confused with `docs/intent/` below, which nobody
but a contributor reads. Its pages live under `docsite/content/`, one
Markdown file per page with MDC syntax for the odd embedded component; a
page's route follows its folder (`content/2.reference/3.configuration.md` is
served at `/reference/configuration`) and a nested MDC component block needs
one more `:` per level of nesting (`::` → `:::` → `::::`) or the parser
closes the wrong block. If your change touches any of these, update the
matching page in the same pull request, not as a follow-up:

| Source | Docs page |
|---|---|
| A `kraft` subcommand or flag (`src/kraft/cli/*.py`) | `docsite/content/2.reference/2.cli.md` |
| A `library.yaml` component key, or a `policy.yaml` / `repos.yaml` / `access.yaml` / `intake.yaml` field (`src/kraft/templates/models.py`, `library.py`, `config.py`, `policy.py`) | `docsite/content/2.reference/3.configuration.md` |
| A chain template's node fields, or a new default chain | `docsite/content/1.guide/2.concepts.md` |
| A harness (`src/kraft/harnesses/*.yaml`, `harness.py`) | `docsite/content/2.reference/5.harnesses.md` |
| An MCP tool (`src/kraft/mcp.py`) or a Claude Code plugin skill (`plugins/kraft/skills/`) | `docsite/content/2.reference/6.agent-integration.md` |
| `access.yaml` / remote-access behaviour | `docsite/content/2.reference/7.remote-access.md`, and `SECURITY.md` if it's security-relevant |

Run `npm ci && npx nuxt generate` in `docsite/` before you push. It fails on
a page that doesn't parse, but it does not validate every internal link or
anchor; a link to a page or heading
that no longer exists renders instead of failing the build. A
stale-but-still-linking page is exactly the kind of gap `docs/intent/`'s
`enforced-by:` pinning doesn't catch either; there is no automated backstop
for "this paragraph no longer describes the code," only for "this file no
longer parses." Read the page you're touching, not just the code.

## Design documents

Specs and implementation plans are not committed. `.engineering/` and
`docs/superpowers/` are gitignored. `design/` and `docs/consolidated/` are
gitignored too and live only in the maintainer's private archive — if you see
them still tracked in the tree, they haven't been scrubbed from history yet.
`docs/intent/` is the exception: it states intended behaviour as pinned
requirements and is maintained with the code.
