# Contributing

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Your first pull request

A plain git pull request is fine: you don't need Kraft, beads or a coding agent
to contribute.

1. Pick an issue from [Start here](https://github.com/itsOmidKarami/kraft/issues/1131),
   or any [`good first issue`](https://github.com/itsOmidKarami/kraft/labels/good%20first%20issue),
   and comment on it so nobody doubles up.
2. Fork the repository, clone your fork, and install just, uv and Node 22
   ([Requirements](#requirements)).
3. Run `just setup`. It installs the Python and frontend dependencies, and
   nothing into your coding agents.
4. Make the change and run the test the issue names: `just test <path> -n auto`
   for Python, `just test-ui` for the frontend, `just ci-checks` for a docs page.
   `just lint` checks the formatting.
5. Open a pull request that says `Fixes #<number>`, with one user-facing line
   under `## Changelog`. A maintainer applies the release label.

In return, you get a first reply within a day and a review within three days. On a
first pull request, the maintainer finishes the changelog, docs-note and test
chores rather than sending it back. For a UI fix, the issue says which
screenshots it needs, and the maintainer does the design polish pass. The rest
of this guide is the full reference; a starter issue rarely needs it.

## Requirements

- [just](https://just.systems), the command runner every step below uses:
  `brew install just`, `cargo install just`, or `uv tool install rust-just`.
- [uv](https://docs.astral.sh/uv/). The first `uv sync` downloads Python 3.14
  if you have none. Kraft runs on 3.12 or newer, so [Python versions](#python-versions)
  says what that means for your code.
- git, and bash and [jq](https://jqlang.org) for the tests that run CI's own
  scripts (`tests/test_ci_workflow.py`).
- Node 22 with npm, for the frontend, the docs site and the VS Code extension.
  CI builds on Node 22, so use the same major.

`claude` is only needed for real agent runs, not for `just dev` or the tests.
Semantic search is opt-in: `just setup-vector` downloads a model of roughly
130 MB on first use, and search works without it in full-text mode.
[`bd`](https://github.com/gastownhall/beads) (beads, an issue tracker) is
optional: with it, every work item gets a tracked bead, and without it Kraft
files work anyway and says so.

### Python versions

`.python-version` pins a checkout's venv to Python 3.14, the version CI builds
with. Without the pin, uv picks the newest Python installed. Code must run on
3.12 (`requires-python` in `pyproject.toml`): no syntax or stdlib API added
later (3.13's `Path.read_text(newline=)`, 3.14's t-strings, unparenthesized
`except A, B:`). Every module under `src/` starts with
`from __future__ import annotations`, which lint enforces. [What CI
checks](docs/ci.md) lists the versions CI tests;
`just test-py 3.12` reproduces one version's failure locally.

## Getting set up

```bash
just setup      # uv sync + npm ci
just dev        # backend + vite, state in .dev/, agents faked, UI on :5173
```

`just dev` puts `fixtures/bin` on `PATH` ahead of the real agent, where `claude`
is a symlink to `fixtures/fake-claude.sh`, the same fake the test suite uses. A
dev instance never spends tokens and never touches `~/.kraft`. Run `just` for
the full list of recipes.

`just dev-seed` fills a running dev instance with work items in every state by
driving the real HTTP API, and `just dev-reset` throws `.dev/` away. A work item
title containing `KRAFT_FAIL` or `KRAFT_SLOW` steers that item's fake agent.
`just dev-seed --rich` fills it for judging the dashboard under load: about 40
items, long and non-ASCII text, a 30-node chain, three repos and a wide review.

To run the VS Code extension from source, open the `vscode/` folder in VS Code
and press F5 (the first run installs its npm dependencies). **Run Extension
(dev daemon)** talks to `just dev`'s daemon (state in `.dev/`, port 8766);
**Run Extension (installed Kraft)** talks to `~/.kraft` and acts on your real
work items. Both rebuild the extension first.

`just setup-plugins` installs coding-agent skills and plugins. It is opt-in, and
only for people who work here with an agent: [Agent tooling](docs/agent-tooling.md)
has it. To preview the docs site, see [Writing the docs](docs/writing-docs.md#previewing-the-docs-site).

### Pre-commit hooks

`.pre-commit-config.yaml` runs ruff, whitespace, YAML and file-size checks,
and three local hooks. In a fresh clone, `uv run pre-commit install` sets them
up; `uv run pre-commit run --all-files` runs them by hand. CI is the real gate;
the hooks only catch things sooner. A checkout that uses beads has its own
hooks: [Agent tooling](docs/agent-tooling.md#beads-and-git-hooks) says how to
combine them.

The three local hooks need `just` on `PATH`:

- `vale` runs `just vale` when a Markdown file changes, and fails the commit
  on any alert. See [Voice and spelling](docs/writing-docs.md#voice-and-spelling).
- `config-schemas-current` runs `just schemas` when `src/kraft/` or
  `vscode/schemas/` changes, and fails the commit if that rewrote a schema.
  Stage the regenerated files and commit again.
- `shipped-models-smoke` runs `just smoke-models` when `config/` or
  `src/kraft/harnesses/` changes. It launches the real `claude` CLI and spends
  a few cents, so it is opt-in: it needs `KRAFT_SMOKE_MODELS=1`, plus
  `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Without them it prints
  `SKIPPED` and lets the commit through.

## Finding something to work on

[Start here](https://github.com/itsOmidKarami/kraft/issues/1131) lists the open
starter issues by area and size. Issues labelled
[`good first issue`](https://github.com/itsOmidKarami/kraft/labels/good%20first%20issue)
are small and self-contained. For anything bigger than a bug fix, open an issue
first and say what you plan to do, so nobody spends a week on a change that
doesn't fit.

Comments, commit messages and the changelog cite IDs like `Kraft-y0g2`. They
point into the maintainer's private issue tracker (beads), which you can't
see. Read one as "this was discussed"; the text beside it says what matters.
Don't add new ones: cite a GitHub issue or pull request number instead.

The frontend and its tests carry a second set of references of the same kind:
**UX V2**, **ux2-W\<n>** (often just `W<n>`), **spec §n**, **Decided n**, **R\<n>**,
**Ruling n**, `design/handoff_*` and the earlier plain `W0`–`W14`. They point
into the maintainer's private design notes (`design/` is gitignored), or into
git history.

Read them as you read a `Kraft-` ID, and ask in an issue, or see
[`frontend/README.md`](frontend/README.md), when a comment's own words don't say
what a rule is for. Don't add new ones.

## Repository layout

```text
src/kraft/        the orchestrator: api/, executor/, store/, adapters/, worker/, cli/, index/, ...
frontend/         the React SPA (vite), see frontend/README.md; e2e/ is Playwright, e2e/contract/ the UI contract
config/           the packaged configuration: the default library, chains, harness profiles and policy, an install's seed, and the add-on kraft-default
addons/           the other add-ons of Kraft's own collection, which build on kraft-default
tests/            backend tests, mirroring src/kraft/ (CLAUDE.md says why that matters)
dev/              the dev-instance seeder, CI check scripts, release and codegen helpers
fixtures/         the fake agent and the PATH shim `just dev` uses
docs/             the testing guideline, the intent tree, the templates design draft
docs/intent/      intended behaviour as pinned requirements, each tied to a test
docsite/          the published documentation site
plugins/          the Claude Code plugins: kraft and kraft-lite
vscode/           the VS Code extension
install.sh        the one-line installer the README points to
```

A change to `config/` or `addons/` may need an add-on version raise: [Maintainer notes](docs/maintainers.md#krafts-own-collection) says when.

The dot-directories (`.claude/`, `.agents/`, `.codex/`, `.beads/` and the rest)
are mostly the maintainer's agent tooling. Ignore them unless you use the same
tools: [Agent tooling](docs/agent-tooling.md#the-dot-directories) lists each.
`.github/` is the exception, because CI lives there.

## Tests

```bash
just test       # backend tests affected by your change (testmon); --no-testmon for all
just e2e        # Playwright (see frontend/e2e/README.md)
just ui-contract # the SPA's behaviours and icon-only audit in a browser on a mocked API (frontend/e2e/contract/README.md)
just demo-smoke # the dashboard demo boots, and every things-to-try Show me finds its target (frontend/DEMO.md)
just test-ui    # frontend typecheck and unit tests (see frontend/README.md for when the browser suites are required too)
just test-vscode # VS Code extension: schemas current, typecheck, unit tests
just intent     # check that every enforced-by pin in docs/intent/ still resolves
just lint       # ruff check + format check
just fix        # autofix
```

**Don't call `pytest` directly.** `just test` goes through testmon's
change-tracking; a raw invocation skips it and runs the full ~14 minute suite.
A Claude Code hook blocks it for agent sessions.

**Your first `just test` is a full run.** testmon has no record yet of which
tests touch which code (it keeps one in `.testmondata`), so it runs everything;
later runs are change-selected. `just test` runs in parallel (`-n` your logical
CPUs + 2) unless you pass `-n` yourself (`-n 0` stays in one process), name a single
test, or ask for live output (`-s`, `--capture`, `--log-cli-level`, `--trace`),
which a worker would lose. To start with one area, name it:

```bash
just test tests/cli                    # one directory
just test tests/cli/test_service.py -k systemd
```

See [`docs/testing.md`](docs/testing.md) for the shape a test should take: the
two tiers, the shared fixtures and the mutate-then-confirm-it-fails procedure
that is the only thing that actually proves a test pins something.
`just check-tests` enforces what of that can be checked mechanically.
[`docs/intent/README.md`](docs/intent/README.md) explains the intent tree and
its `enforced-by:` pins, which break when you rename a pinned test.

[What CI checks](docs/ci.md) lists every job a pull request runs and the command
that reproduces it locally.

## Removed tests

If your pull request deletes a test function, a frontend test file or an intent
`## REQ` heading, list each one in its description, or `removals declared` fails:

```markdown
## Removed tests
- tests/test_old.py::test_gone -- replaced by tests/test_new.py::test_here

## Removed requirements
- some-req-name -- superseded by other-req-name
```

A renamed test counts as a removal, so list its old ID. The PR template has
these sections, commented out; [`docs/testing.md`](docs/testing.md) has the
full rule. To check before you push, save the description to a file and run
`uv run python dev/check_removals.py origin/main BODY.md`.

## Pull requests and release labels

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump and no bump commit to forget.

That removes the loud failure and leaves a quiet one: if nobody ever makes a
tag, nothing fails: `main` just accumulates untagged commits while the install
instructions keep serving a release from months ago. Every pull request
therefore declares what it ships, and a maintainer cuts a release from whatever has
merged since the last one (see [RELEASING.md](RELEASING.md)).

Put exactly one of these labels on your pull request:

| Label | Means |
|---|---|
| `release::major` | a breaking change to the CLI, the API or on-disk state |
| `release::minor` | a new capability that doesn't break an existing one |
| `release::patch` | a fix to something that already shipped |
| `release::none` | no bump of its own: it goes out with the next release |

`release::none` is a first-class answer, and the expected one for documentation,
comments, CI configuration and test-only changes. It doesn't keep a change out
of a release (every merged change is in the next one); it only adds no weight
to the bump and no line to the notes.

Nothing checks that the declared impact matches the diff. The label is a claim by
its author; review is what tests it.

A pull request that ships something writes its user-facing line in the
`## Changelog` section of its description. Don't edit `CHANGELOG.md`: the
release writes it. A pull request that leaves the section empty is listed by
its title.

The release's headline change can also carry `notes::highlight`. [RELEASING.md](RELEASING.md#highlights)
says when to use it and how to write its entry.

**Pull requests from forks aren't asked for a label**: only people with write
access can apply one. A maintainer labels the pull request before merging. One
merged without a label stops the next release, naming it; label it (labels
can still be changed after merge) and run the release again. [Check that your
label worked](RELEASING.md#check-that-your-label-worked) says how.

## A pull request that changes a screen

Attach before and after screenshots of each changed screen, at 1280px and 390px
wide, in light and dark, from a seeded `just dev` instance. A starter issue
(`good first issue` or `help wanted`) says which it needs instead: none, or one
before and after at 1280px in light, and the maintainer does the polish pass.
[Maintainer notes](docs/maintainers.md#a-pull-request-that-changes-a-screen) has
the full procedure.

## Branch hygiene

Two ways a branch you're reusing by hand can quietly cost you work:

**Don't reuse a branch after its pull request merges.** This repo merges by
squash, so the branch's own commits never become ancestors of `main`; only
their squashed equivalent does. Push more commits onto that same branch
afterwards and `git diff` against its old merge-base re-presents everything the
squash already landed. Cut a fresh branch from `origin/main` instead.

**Rebuilding a branch by `git reset` + cherry-pick can silently orphan a
commit.** Resetting a branch to an earlier point and re-picking commits drops
anything you forgot to include, with no warning, and the commit survives only as a
dangling object until the next `git gc`. Before you reset, capture the tip:

```bash
old=$(git rev-parse mybranch)
git reset --hard <earlier-point>
git cherry-pick <sha1> <sha2> ...
git log --oneline "$old" --not mybranch   # non-empty means something is missing
```

## Maintainer notes

[Maintainer notes](docs/maintainers.md) covers Kraft's own collection, screen
changes in full and the model price table (`just refresh-prices`).

## Design documents

Specs and implementation plans aren't committed. `.engineering/` and
`docs/superpowers/` are gitignored. Kraft itself writes a work item's
documents under `$KRAFT_HOME/run/items/<id>/docs/`, outside the repository;
`.engineering/` stays ignored for your own specs and for worktrees where an
older Kraft wrote them. `design/` and `docs/consolidated/` are
gitignored too.
`docs/intent/` and `docs/templates-v1-design.md` are the exceptions: the
first states intended behaviour as pinned requirements, the second is the
templates design draft, and both are maintained with the code.
