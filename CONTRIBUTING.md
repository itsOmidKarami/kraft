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
- [uv](https://docs.astral.sh/uv/). If you don't have a supported Python
  (3.12 or newer, `requires-python` in `pyproject.toml`), the first `uv sync`
  downloads one. CI tests 3.12, 3.13 and 3.14; `just test-py 3.12` reproduces
  one version's failure locally. Code must run on 3.12: no syntax or stdlib
  API added later (3.13's `Path.read_text(newline=)`, 3.14's t-strings,
  unparenthesized `except A, B:`), and every module under `src/` starts with
  `from __future__ import annotations`, which lint enforces.
- git, and bash and [jq](https://jqlang.org) for the tests that run CI's own
  scripts (`tests/test_ci_workflow.py`).
- Node 22 with npm, for the frontend, the docs site and the VS Code extension.
  CI builds on Node 22; no `package.json` sets an `engines` floor, so use the
  same major.

`claude` is only needed for real agent runs, not for `just dev` or the tests.
Semantic search is opt-in: `just setup-vector` downloads a model of roughly
130 MB on first use, and search works without it in full-text mode.
[`bd`](https://github.com/gastownhall/beads) (beads, an issue tracker) is
optional: with it, every work item gets a tracked bead, and without it Kraft
files work anyway and says so.

## Getting set up

```bash
just setup      # uv sync + npm ci
just setup-plugins  # the agent skills below, opt-in
just dev        # backend + vite, state in .dev/, agents faked, UI on :5173
```

`just setup-plugins` installs [superpowers](https://github.com/obra/superpowers)
and [impeccable](https://impeccable.style) for every agent this repo is worked
on with, and for Claude Code also
[ponytail](https://github.com/DietrichGebert/ponytail),
[techdocs-authoring](https://github.com/pkloehn1/techdocs-authoring), and the
engineering and design plugins from
[knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins). It is opt-in: `just setup` runs it only in a setup Kraft runs (a
worker's worktree, on this machine or another), so a contributor's agents get
nothing they didn't ask for. It skips whatever is already
installed, and a missing tool or a failed download is a warning, not a failed
setup. To refresh them, run `claude plugin update <id>` and `npx skills update -p`:

- **Claude Code**, when `claude` is on your `PATH`: as plugins.
  `.claude/settings.json` turns them on, but Claude Code does not download a
  plugin from an outside source for you, so each clone installs them once.
- **Codex, Cursor, Gemini CLI, OpenCode, Amp, Antigravity and GitHub Copilot**,
  when `npx` is on your `PATH`: as skills in `.agents/skills/`, the folder all
  of them read. They are gitignored; beads is the only skill committed there.

A fresh git worktree has no gitignored files, so a worktree only gets the
`.agents/skills/` copies after `just setup-plugins` runs in it (or `just setup`,
when Kraft runs it). For Kraft workers, set this
repo's `setup_command` to `just setup` in its `repos.yaml` entry (or pass
`--setup-command "just setup"` to `kraft repo connect`); Kraft runs it in every
new worktree. Claude Code's plugins live in `~/.claude` and need no setup per
worktree.

superpowers is off in Kraft workers: its skills ask a person questions and push
or merge a finished branch, and a worker does neither. So is ponytail, which
puts a session into its own coding mode where a worker follows Kraft's
instructions. For Claude Code, `.claude/hooks/kraft-worker-plugins.sh` refuses
every superpowers and ponytail skill in a session with `$KRAFT_WORK_ITEM_ID` set
and tells the session and its subagents so.

The worker hook cannot stop another plugin's own hooks. ponytail's still brief
each worker in its mode, and techdocs-authoring's turns back a worker's first
commit once, with a checklist for its message. Both plugins read an off switch
from the environment, so add them to `env:` in this repo's `repos.yaml` entry:

```yaml
env:
  PONYTAIL_DEFAULT_MODE: "off"
  TECHDOCS_SHORT_FORM_GATE: "off"
```

ponytail keeps its mode in one file per checkout, so an in-place item still
shares it with your own sessions there.

For the other agents, a setup Kraft runs copies only impeccable into the
worktree. An in-place item runs in your own checkout, so other agents there
still see the superpowers copies you installed.

Cloud agents start from a fresh clone, so give them the same step. Copilot's
coding agent already runs `just setup` (`.github/workflows/copilot-setup-steps.yml`).
For Codex cloud, Cursor background agents and the like, add this to the
environment's setup script:

```bash
for source in obra/superpowers pbakaus/impeccable; do
  npx -y skills@1.7.4 add "$source" --skill '*' --agent codex cursor gemini-cli opencode amp antigravity github-copilot --copy -y
done
```

Claude Code's cloud sessions (claude.ai/code, the Claude app) never install the
plugins a repository's settings turn on. Install them in the cloud environment's
setup script instead, which runs before Claude Code starts and covers every
session in that environment, whatever repositories it clones:

```bash
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install superpowers@claude-plugins-official
claude plugin install impeccable --marketplace pbakaus/impeccable
claude plugin install ponytail --marketplace DietrichGebert/ponytail
claude plugin install techdocs-authoring --marketplace pkloehn1/techdocs-authoring
claude plugin install engineering --marketplace anthropics/knowledge-work-plugins
claude plugin install design --marketplace anthropics/knowledge-work-plugins
```

`just dev` puts `fixtures/bin` on `PATH` ahead of the real agent, where `claude`
is a symlink to `fixtures/fake-claude.sh` — the same fake the test suite uses, so
it cannot rot. A dev instance never spends tokens and never touches `~/.kraft`.

Run `just` for the full list of recipes.

`just dev-seed` fills a running dev instance with work items in every state by
driving the real HTTP API, and `just dev-reset` throws `.dev/` away. A work item
title containing `KRAFT_FAIL` or `KRAFT_SLOW` steers that item's fake agent.
`just dev-seed --rich` instead fills it for judging the dashboard under load: about
40 items over every board state, long and non-ASCII text, a 30-node chain, three
repos (one on a long path), and a review with a 41-file diff and long threads. It
also uses `KRAFT_HANG` (an agent that stays running), `KRAFT_MANY_FILES` (a wide
change) and `KRAFT_COST_<usd>` (what a run reports it cost). Usage and cost all
land in the current week: the API gives a caller no way to backdate a row.

To run the VS Code extension from source, open the `vscode/` folder in VS Code
and press F5 (the first run installs its npm dependencies). **Run Extension
(dev daemon)** opens an Extension Development Host on the seeded repo, talking
to `just dev`'s daemon (state in `.dev/`, port 8766); **Run Extension
(installed Kraft)** talks to `~/.kraft`, and acts on your real work items. Both
rebuild the extension first.

### Pre-commit hooks

`.pre-commit-config.yaml` runs ruff, whitespace, YAML and file-size checks,
and two local hooks. In a fresh clone, `uv run pre-commit install` sets them
up. In a checkout that uses beads, `bd hooks install` writes its hooks to the
git directory (`.git/hooks/`, shared by every linked worktree); there, append
this below beads' block in `.git/hooks/pre-commit` instead:

```sh
if command -v uv >/dev/null 2>&1; then
  uv run --no-sync pre-commit run || exit 1
fi
```

Keep `core.hooksPath` unset, or pointing outside the working tree:
`git config --get core.hooksPath` prints nothing. Do not run
`bd hooks install --beads` or `--shared`: they point it at `.beads/hooks/` or
`.beads-hooks/`, files a branch can change, so checking out someone else's
branch would run its hooks as you. `.beads/hooks/` is gitignored for the same
reason.

Either way, `uv run pre-commit run --all-files` runs them by hand. CI is the
real gate; the hooks only catch things sooner.

The two local hooks need `just` on `PATH`:

- `config-schemas-current` runs `just schemas` when `src/kraft/` or
  `vscode/schemas/` changes, and fails the commit if that rewrote a schema.
  Stage the regenerated files and commit again.
- `shipped-models-smoke` runs `just smoke-models` when `config/` or
  `src/kraft/harnesses/` changes. It launches the real `claude` CLI and spends
  a few cents, so it is opt-in: it needs `KRAFT_SMOKE_MODELS=1`, plus
  `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Without them it prints
  `SKIPPED` and lets the commit through.

### Previewing the docs site

```bash
just docs       # cd docsite && npm ci && npm run dev
```

The site is served at <http://localhost:3000/>. The dev server logs a few
warnings as it starts; the site works regardless.

`just docs` serves `main`'s pages alone. To see the site as it publishes, with
the latest release at <http://localhost:8000/>, each older major at
`/vN/` and `main` at <http://localhost:8000/next/>, run `just docs-site`.

## Finding something to work on

[Start here](https://github.com/itsOmidKarami/kraft/issues/1131) lists the open
starter issues by area and size. Issues labelled
[`good first issue`](https://github.com/itsOmidKarami/kraft/labels/good%20first%20issue)
are small and self-contained. For anything bigger than a bug fix, open an issue
first and say what you plan to do, so nobody spends a week on a change that
does not fit.

Comments, commit messages and the changelog cite IDs like `Kraft-y0g2`. They
point into the maintainer's private issue tracker (beads), which you cannot
see. Read one as "this was discussed"; the text beside it says what matters.
Don't add new ones: cite a GitHub issue or pull request number instead.

The frontend and its tests carry a second set of references of the
same kind. **UX V2** is the design programme behind the 2.0 interface;
**ux2-W\<n>** (often written just `W<n>` in a comment) is one of its numbered
waves of work; **spec §n**, **brief**, **Decided n**, **R\<n>** and
**Ruling n** are a numbered section, decision or review finding in that
programme's notes; and `design/handoff_*` is a folder of design handoffs. Those
notes are the maintainer's private ones: `design/` is gitignored and none of it
can be opened from a clone. Read them as you read a `Kraft-` ID. The earlier
fix programme, plain `W0`–`W14`, is the same: its briefs and receipts were
tracked under `frontend/` until the 2.x cleanup and are in git history only. The
rules the code actually enforces are stated in the tests that enforce them and
summarised in [`frontend/README.md`](frontend/README.md), so ask there, or in
an issue, when a comment's own words don't say what a rule is for. Don't add
new ones.

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

**Kraft's own collection.** `config/.kraft/addon.json` publishes `config/`'s
`library.yaml` and `chains/` as the add-on `kraft-default`, and `addons/` holds
the add-ons that build on it. An installed instance sees an update only when an
add-on's `version` changes, so a change to `config/library.yaml`,
`config/chains/` or an add-on under `addons/` raises that add-on's `version` in
the same pull request: patch for a fix, minor for something new, major for a
removed or renamed chain or task. When the change uses something the last
release cannot read, raise `requires.kraft` too.
`dev/check_addon_versions.py` (in `just ci-checks` and CI's lint job) fails when
an add-on changed against the pull request's base and its version is not
higher. Two pull requests that raise one add-on to the same version both pass
alone, and the second fails on `main`: merge `main` into the second and raise
it again before merging it.
`tests/addons/test_kraft_sdlc_collection.py` fails when a change to `config/`
breaks an add-on built on it. In this repository, `kraft-superpowers` chains are
refused their superpowers skills: `.claude/hooks/kraft-worker-plugins.sh` turns
superpowers off in Kraft workers.

The dot-directories are mostly the maintainer's agent tooling. You can ignore
them unless you use the same tools:

| Directory | What it is | Do you need it? |
|---|---|---|
| `.github/` | CI workflows, issue and PR templates, CODEOWNERS | Yes: CI lives here |
| `.claude-plugin/` | the marketplace manifest that publishes `plugins/` to Claude Code and Codex | Only to rename a plugin or add one |
| `.cursor-plugin/` | the same marketplace for Cursor | Only to rename a plugin or add one |
| `.claude/` | Claude Code settings: a hook that blocks raw `pytest` (needs `jq`), the plugins it turns on (superpowers, impeccable, ponytail, techdocs-authoring, engineering, design), and a hook that turns superpowers and ponytail off in Kraft workers | Only with Claude Code |
| `.beads/` | beads config and git hooks for the maintainer's private tracker | No |
| `.agents/` | a beads skill for agent sessions; `just setup` adds superpowers and impeccable beside it, gitignored | Only with an agent other than Claude Code |
| `.codex/` | Codex hooks that call `bd`; they fail without beads installed | No |
| `.kraft-lite/` | this repo's Kraft Lite bindings (`bindings.yaml`) | No |
| `.kraft/` | `collection.json`, which publishes `config/` and `addons/` as the collection `kraft-sdlc` | Only to change what the collection lists |
| `.gitlab/` | the PR template for GitLab, from before the move to GitHub; a symlink to `.github/`'s | No |

## Tests

```bash
just test       # backend tests affected by your change (testmon); --no-testmon for all
just e2e        # Playwright (see frontend/e2e/README.md)
just ui-contract # the SPA's behaviours and icon-only audit in a browser on a mocked API (frontend/e2e/contract/README.md)
just demo-smoke # the dashboard demo boots, and every things-to-try Show me finds its target (frontend/README.md)
just test-ui    # frontend typecheck and unit tests (see frontend/README.md for when the browser suites are required too)
just test-vscode # VS Code extension: schemas current, typecheck, unit tests
just intent     # check that every enforced-by pin in docs/intent/ still resolves
just lint       # ruff check + format check
just fix        # autofix
```

**Do not call `pytest` directly.** `just test` goes through testmon's
change-tracking; a raw invocation skips it and runs the full ~14 minute suite.
A Claude Code hook blocks it for agent sessions.

**Your first `just test` is a full run.** testmon has no record yet of which
tests touch which code (it keeps one in `.testmondata`), so it runs everything;
later runs are change-selected. To start with one area, name it:

```bash
just test tests/cli -n auto            # one directory, in parallel
just test tests/cli/test_service.py -k systemd
```

See [`docs/testing.md`](docs/testing.md) for the shape a test should take: the
two tiers, the shared fixtures, and the mutate-then-confirm-it-fails procedure
that is the only thing that actually proves a test pins something.
`just check-tests` enforces what of that can be checked mechanically.
[`docs/intent/README.md`](docs/intent/README.md) explains the intent tree and
its `enforced-by:` pins, which break when you rename a pinned test.

### What CI checks

Every pull request runs these, with two exceptions decided by the paths it
changes. A docs-only one skips the jobs marked *(code)* and *(python)*, and
`docs tests` runs every unit test file that names a docs path in their place.
It is docs-only when every file it changes is a root-level `*.md`,
`docsite/**`, `.github/assets/**`, or `docs/*.md` (not `docs/intent/`). One
that changes those and `frontend/**`, and nothing else and no `.py` file,
skips the job marked *(python)* and runs the unit tier on Python 3.14 alone:
no Python moved, so every unit test still runs, once. A push to `main` runs
everything. `just ci-test` runs `lint` and the unit tier, in CI's order, on
the full suite. `just test-targeted` is `just ci-test` with the tests testmon
picks, and the full suite when it cannot see the change; Kraft's own
verification runs it.

| CI job | What it runs | Run it locally with |
|---|---|---|
| `lint` | ruff check and format, `dev/check_docs_coverage.py`, `dev/check_docs_walls.py`, `dev/check_docs_redirects.py`, `dev/check_docs_landings.py`, `dev/check_docs_shell.py`, `dev/check_docs_ledes.py`, `dev/check_docs_added_in.py`, `dev/check_addon_versions.py`, `dev/check_tests.py` | `just ci-test`, or `just lint` and `just check-tests` |
| `changes` | decides whether the pull request is docs-only, or changes no Python | nothing to run |
| `test (python 3.12 / 3.13 / 3.14, 1/3 … 3/3)` *(code)* | the unit tier (`-m "not e2e"`) in three shards on each supported Python, with `python -m kraft.intent` in the first; on 3.14 alone when the pull request changes no Python | `just ci-test`, or `just test` and `just intent`; `just test-py 3.12` for another version |
| `test report` *(code)* | the shards' junit annotations and the coverage comment; reports only, so it is not part of `test` | nothing to run |
| `docs tests` (docs-only pull requests) | the unit test files that name a docs path, on Python 3.14 | `just test` on the files the `git grep` in `test.yml`'s `docs-tests` job lists |
| `test` | passes only when `lint`, every `test (python …)` leg, `e2e (real CLIs)`, `frontend`, `vscode`, `playwright` and `ui contract` all pass; a job that skips because of the paths changed (*(python)*, and on a docs-only pull request *(code)* too) counts as passing, and on a docs-only pull request `docs tests` must pass instead. Branch protection requires it alongside those jobs by name; once the ruleset names only `test` (with the kraft-lite checks, `removals declared` and `release impact declared`), the supported range and the job list can change without editing repo settings | nothing to run |
| `e2e (real CLIs)` *(python)* | the e2e tier against real `bd`, bubblewrap, docker and podman | `just test -m e2e --no-testmon`; a test whose CLI is missing skips |
| `kraft-lite on python 3.10 / 3.14` | `plugins/kraft-lite/tests` with nothing installed but pytest | `just test plugins/kraft-lite/tests` |
| `frontend` *(code)* | `npm ci`, `npm run build` (which typechecks), `npm test` | `just test-ui` |
| `vscode` *(code)* | typecheck, unit tests, integration tests | `just test-vscode`, then `npm run test:integration` in `vscode/` |
| `playwright` *(code)* | the browser e2e suite against a fixture server | `just e2e-ci` |
| `ui contract` *(code)* | the UI contract and icon-only audit: the built SPA in a browser on a mocked API, no Kraft server (`frontend/e2e/contract/`); then the dashboard demo's smoke suite (`frontend/e2e/demo/`) | `just ui-contract`, then `just demo-smoke` |
| `removals declared` | `dev/check_removals.py` against the PR description | see below |
| `release impact declared` | exactly one `release::*` label; it lives in `pr-labels.yml`, not `test.yml`, so labelling a pull request never starts or cancels the test run | see [Pull requests and release labels](#pull-requests-and-release-labels) |
| `docs` (only when `docsite/`, `.github/assets/`, `frontend/public/icon.svg` or the docs build scripts change) | `dev/build_docs_site.sh`: the latest release's pages, each older major's and `main`'s, all with this branch's site code; then `dev/check_llm_docs.py` (no root-relative links in `raw/*.md`, no landing-page anchors in `llms-full.txt`) and, on a PR, a link check | `just docs-site` |
| `docs nudge` | a comment when source moved without its docs page, or the API without the demo's mocks; never fails | nothing to run |
| `tests nudge` | the PR's test-tree delta as a comment (`dev/test_shape_report.py --diff`), only on one of the three signs its `worth_saying` states; never fails | `uv run python dev/test_shape_report.py --diff origin/main HEAD` |
| `codeql` | GitHub's static analysis | nothing to run |

**Removed tests.** If your pull request deletes a test function, a frontend
test file, or an intent `## REQ` heading, list each one in its description,
or `removals declared` fails:

```markdown
## Removed tests
- tests/test_old.py::test_gone -- replaced by tests/test_new.py::test_here

## Removed requirements
- some-req-name -- superseded by other-req-name
```

A renamed test counts as a removal, so list its old id. The PR template has
these sections, commented out; [`docs/testing.md`](docs/testing.md) has the
full rule. To check before you push, save the description to a file and run
`uv run python dev/check_removals.py origin/main BODY.md`.

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

The release's headline change can also carry `notes::highlight`, next to its
`release::` label. Its entry then opens the release notes under **Highlights**
instead of sitting among the others (a `release::major` one is also kept under
Breaking changes), and its impact still counts toward the bump. Keep it for the change the release is about: rarely more than one or two
per release. Write its `## Changelog` section as a headline, and give it a
picture when the change is something to see: one screenshot or GIF on its own
line, indented under the bullet after a blank line, so the number stays on the
bullet's words and the picture stays in its entry. Link it by its full URL,
since the notes are read on GitHub, in `CHANGELOG.md` and on the Marketplace:

```markdown
- `kraft console`: a terminal UI for Kraft.

  ![The console's board, its peek on an item at a gate](https://raw.githubusercontent.com/itsOmidKarami/kraft/main/.github/assets/console.png)
```

Use a picture the docs already show (`.github/assets/`, taken by `just
docs-screenshots`), or add one there in the same pull request, and link it on
`main`: the release points the link at its own tag, so the notes keep this
release's picture after `main`'s is retaken. A file dropped into the
description on GitHub works too: its `user-attachments` URL never changes, so
nothing is pinned. A dropped image may stay right under the bullet as the
editor writes it; a dropped video needs the blank line, since GitHub plays one
only when it stands alone in its paragraph.
Keep pictures to highlights: an entry under New or Fixes is one line. A
`release::none` pull request with the label stops the release, since there is
no entry to highlight. See [RELEASING.md](RELEASING.md#cut-a-release).

**Pull requests from forks are not asked for a label** — only people with write
access can apply one. A maintainer labels the pull request before merging. One
merged without a label stops the next release, naming it; label it (labels
can still be changed after merge) and run the release again.

### Check that your label worked

A maintainer can run the release workflow as a dry run (see
[RELEASING.md](RELEASING.md#cut-a-release)). On the run's summary page, confirm
that the version bump matches the largest label among the merged pull requests
and that your pull request's `## Changelog` line appears under the right
heading. A highlighted pull request's line is under Highlights, and a major one
is under Breaking changes as well. If your pull request is missing or listed by its title, fix the label
or the section in the description and run the dry run again.

### A pull request that changes a screen

A pull request that changes a screen does three things beyond the change
itself:

- Run an impeccable polish pass on each changed screen.
- Attach before and after screenshots of each changed screen, at 1280px and
  390px wide, in light and dark. Take them from a seeded `just dev` instance
  (`just dev-seed` fills it), the "before" from `main` and the "after" from your
  branch.
- Push the pictures to a separate `kraft/<branch>-screenshots` branch, not to the
  pull request's own branch, and link them in the pull request body.

A starter issue (`good first issue` or `help wanted`) says which screenshots it
needs instead: none, or one before and after at 1280px in light. The
maintainer does the polish pass.

A screen that also appears in a README or docs screenshot (`.github/assets/`)
is retaken with `just docs-screenshots` too; see the screen row under
[User-facing docs](#user-facing-docs).

In a cloud session, pushing that branch needs the user's approval in the session
that does the pushing. Ask for it; do not assume it.

`kraft/pr-686-screenshots` is the example. It holds two files at its root, no
folders and no code: `before-skipped-task.png` and `after-skipped-task.png`,
named for the state and the screen. Pull request 686's body links them in a
Before and After table, by their full `raw.githubusercontent.com` URLs pinned to
the branch's commit. That pull request shows one screen at one width and one
theme; with the four combinations, put the width and the theme in each file name
as well.

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
push to `main`). It is not `docs/intent/` below, which only a contributor
reads. If your change touches any of the sources below, update the matching
page in the same pull request, not as a follow-up.

Page rules:

- Pages live under `docsite/content/`, one Markdown file per page, with MDC
  syntax for the odd embedded component.
- A page's route follows its folder, minus the number prefixes:
  `content/5.reference/03.configuration/03.policy.md` is served at
  `/reference/configuration/policy`. A folder's `index.md` is its landing
  page, and its `.navigation.yml` sets its sidebar title.
- A diagram is markup, not an image: a `::diagram-flow` (stages in order,
  with branches, loops and groups), `::diagram-graph` (things and the edges
  that leave each, such as the status state machine) or `::diagram-sequence`
  (numbered messages), its content in the block's YAML and its `caption`
  saying in words what it shows. The components are
  `docsite/app/components/Diagram*.vue`; each one's comment has the fields,
  and an existing page (`2.concepts/1.how-a-work-item-runs.md`) is the
  example. They draw the site's one chain language: Plex Mono, `→` between
  stages, a gate (`gate: you approve`) as the violet chip, a loop as `↻`.
  Run `npx nuxt dev` in `docsite/` and look at it in both colour modes and
  at a phone's width.
- A nested MDC component block needs one more `:` per level of nesting
  (`::` then `:::` then `::::`), or the parser closes the wrong block.
- No walls of text: a paragraph or list item is at most 80 words and a
  table cell at most 40. `dev/check_docs_walls.py` fails the `lint` job on
  a longer one; run it on a page with
  `uv run python dev/check_docs_walls.py PAGE.md`.
- A page shows its `description` as the lede under the title, so the first
  paragraph must carry on from it, not say it again. Delete a paragraph that
  only restates it. `dev/check_docs_ledes.py` fails the `lint` job when
  65% or more of the content words in the paragraph's first sentence are
  already in the description. The same check fails a description with a
  backtick in it: the lede, the search result and the meta tags show it as
  one.
- A code block that lists commands to pick from, not to run in order, is
  fenced ` ```bash each `: every line gets its own copy button, where the
  block's one button would copy them all as a single paste.
- When you move or rename a page, or a heading that something links to, add
  an entry to `docsite/content/redirects.yml` in the same pull request. The
  file shows the two forms: an old page to its new address, and an old
  `/page#heading` to its new one. An installed copy of Kraft, a bookmark and
  a search result all keep opening the old address.
  `dev/check_docs_redirects.py` (the `lint` job) fails on a bad entry, and on
  any docs address that Kraft itself links to and that no longer resolves.
  It also fails a `](/page#heading)` link between pages that lands on no
  heading, or on an address the file forwards (write the target), so a
  moved heading cannot leave a dead link behind.
- Mark what is new. A page, section or CLI verb that is new since the latest
  release carries `_Added in next_` on its own line directly under its heading
  (a new page: under its title, which covers its sections), or at the end of
  its table cell (a row in "Verbs at a glance", a flag row). Write exactly
  that, never a version: the release stamps it into `_Added in 2.1_`
  ([RELEASING.md](RELEASING.md#docs-added-in-notes)), and a new major strips
  every note. Never put it in heading text, which would leak into the table of
  contents, search titles and the sidebar. On the site it is a chip ("Unreleased",
  then "Added in 2.1"); in the Markdown, `llms-full.txt` and search it stays plain
  words. `dev/check_docs_added_in.py` (the `lint` job) compares `docsite/content`
  with the latest release tag and fails on a new page, heading or verb with no
  note, on a note in the wrong place or naming a release that does not exist,
  and warns on a `next` note on something the release already had. A heading
  is not new if its page moved (`redirects.yml`), or if you say so under it:
  `<!-- renamed from: Old heading -->` for an old heading under a new name
  (`/page#Old heading` from another page, `/old/page` for a page split or
  renamed), `<!-- not new: why -->` for new text about something that already
  shipped. The comment does not render, and the release deletes it.
- No comment in a shell block (`bash`, `sh`, `shell`, `zsh`, `console`): an
  interactive zsh, the macOS default, does not read `#` as one, so a
  pasted `cmd # note` fails with `command not found: #`. Put the remark in
  the sentence above the block. `dev/check_docs_shell.py` (the `lint` job)
  fails a line that starts with `#` or has one after whitespace outside
  quotes.
- When you add, move or remove a page, update its folder's `index.md`: the
  list under `## In this section` names the pages and sub-folders of that
  folder, in file name order. A sub-folder with no `index.md` of its own is
  listed as its pages, and a page deeper inside a sub-folder may be listed
  too. Link a page of another section under another heading.
  `dev/check_docs_landings.py` (the `lint` job) fails when the list and the
  folder disagree. Number prefixes in one folder all have the same number of
  digits (`01.` to `10.`), because the sidebar sorts by name.

| Source | Docs page |
|---|---|
| A `kraft` subcommand or flag (`src/kraft/cli/*.py`) | `docsite/content/5.reference/01.cli/` |
| The console (`src/kraft/console/`) | `docsite/content/5.reference/01.console.md` |
| A `library.yaml` component key, or a `policy.yaml` / `repos.yaml` / `access.yaml` / `intake.yaml` field (`src/kraft/templates/models.py`, `src/kraft/templates/library.py`, `src/kraft/config.py`, `src/kraft/policy.py`) | `docsite/content/5.reference/03.configuration/` |
| A chain template's node fields | `docsite/content/5.reference/04.chain-nodes/index.md` |
| How a subprocess task runs, or a result-file field (`src/kraft/adapters/subprocess.py`, `src/kraft/findings.py`, `src/kraft/usage.py`) | `docsite/content/5.reference/04.chain-nodes/2.subprocess-tasks.md`, `4.result-file.md` |
| The fix loop or its judge (`src/kraft/executor/walk.py`, `dispatch.py`) | `docsite/content/5.reference/04.chain-nodes/3.fix-loop.md` |
| A new default chain, or a change to the core vocabulary | `docsite/content/2.concepts/1.how-a-work-item-runs.md` (the shipped chains, node by node), `docsite/content/2.concepts/4.vocabulary.md` (a term) |
| Trigger behaviour (`src/kraft/triggers.py`) | `docsite/content/5.reference/07.triggers.md` |
| The permission gate (`src/kraft/harnesses/*.yaml`, `permission_rules.py`, `permission_hooks.py`, `grants.py`) | `docsite/content/5.reference/06.permissions.md` |
| A harness (`src/kraft/harnesses/*.yaml`, `harness.py`) | `docsite/content/5.reference/05.harnesses/` |
| An MCP tool (`src/kraft/mcp.py`) | `docsite/content/5.reference/09.mcp-tools.md` |
| A Kraft add-on's or collection's format, `addons.yaml`, `addons.lock`, or how an add-on loads, updates or is restored (`src/kraft/addons/`, `src/kraft/cli/addon.py`) | `docsite/content/5.reference/03.configuration/12.addons.md` (the facts), `docsite/content/3.guides/2.customize/4.share-chains-with-addons.md` (the steps), `docsite/content/5.reference/01.cli/5.admin.md` (a `kraft admin addon` verb), `docsite/content/4.troubleshooting/6.addon-problems.md` (a message a `kraft admin addon` verb prints, quoted as it prints it), and `docsite/content/6.project/1.security.md` when it changes what an add-on may carry |
| A repo library, the `.kraft/` folder a connected repo shares (`src/kraft/addons/repo_library.py`, `kraft repo library`) | `docsite/content/5.reference/03.configuration/13.repo-libraries.md` (the facts), `docsite/content/3.guides/2.customize/4.share-chains-with-addons.md` (the steps), `docsite/content/5.reference/01.cli/4.repo.md` (the verb), and `docsite/content/6.project/1.security.md` when it changes what accepting trusts |
| A Claude Code plugin skill (`plugins/kraft/skills/`) | `docsite/content/3.guides/1.day-to-day/4.agent-integration.md` |
| A `KRAFT_*` environment variable, or a variable passed to workers (`src/kraft/worker/env.py`) | `docsite/content/5.reference/03.configuration/11.environment-variables.md` |
| A new event type (`events.append`), or the notification webhook (`src/kraft/notify.py`) | `docsite/content/5.reference/10.events.md` |
| A stop reason (`store.mark_needs_human`) or a `kraft admin doctor` check | `docsite/content/4.troubleshooting/1.why-did-my-item-stop.md` (a stop reason; a sandbox, Kit or workspace-member one goes on `docsite/content/4.troubleshooting/5.sandbox-and-kit-stops.md`, with a row in the first page's table; one that needs numbered steps gets an entry on `docsite/content/4.troubleshooting/2.stop-reasons-in-detail.md`), `docsite/content/4.troubleshooting/4.doctor-failures.md` (a doctor check) |
| `access.yaml` / remote-access behaviour | `docsite/content/3.guides/4.run/01.remote-access.md` (and `02.put-a-proxy-in-front-of-kraft.md` for the `Host` check behind a proxy), and `SECURITY.md` if it's security-relevant |
| A screen or its behaviour (`frontend/src/ng/`) | the pages that show or name it: `docsite/content/1.get-started/2.first-work-item.md` (the board), `docsite/content/5.reference/00.web-ui/` (each screen: the board, an item's page, the review page, Templates and Settings, Analytics), `docsite/content/3.guides/1.day-to-day/2.first-run.md` (the setup wizard), `docsite/content/3.guides/1.day-to-day/3.file-a-work-item.md` (the composer), `docsite/content/3.guides/1.day-to-day/5.review-a-change.md` (the review page), `docsite/content/3.guides/1.day-to-day/8.kraft-on-a-phone.md` (the phone), `docsite/content/3.guides/4.run/01.remote-access.md` (Access), and any other page that names the screen or a button on it (`git grep` its label under `docsite/content/`). If the screen is in a README or docs screenshot (`.github/assets/*.png`), retake it with `just docs-screenshots` (the web UI), `just console-screenshot` or `just vscode-screenshots`: [`frontend/README.md`](frontend/README.md#retaking-the-screenshots-in-githubassets) has the details |

Run `npm ci && npx nuxt generate` in `docsite/` before you push. It fails on
a page that doesn't parse, but a link to a page or heading that no longer
exists renders instead of failing the build. The docs workflow catches those:
on a pull request it runs [lychee](https://lychee.cli.rs) over the built
pages and their Markdown, and fails on a broken internal link, a missing anchor, or an external
link that does not answer. Nothing catches "this paragraph no longer describes the
code", and `docs/intent/`'s `enforced-by:` pinning doesn't either. Read the
page you're touching, not just the code.

### Versions of the docs

The site publishes three kinds of version: the latest stable release's pages at
[`/`](https://kraft-sdlc.dev/), each older major frozen at `/vN/` (today `/v1/`,
from the last `v1` release), and `main`'s
at [`/next/`](https://kraft-sdlc.dev/next/). Write docs on `main`, in the same
pull request as the code. They appear at `/next/` when it merges and become the
default at the next stable release. There is nothing else to do. Link to
`/next/` for a feature that hasn't shipped in a release yet.

A section that is new since the last release says so with an "Added in"
note, which the release turns into the version that shipped it (see
[User-facing docs](#user-facing-docs) above). The `/next/` build also lists
those notes on an Unreleased page, linked from its version banner and marked
in the sidebar: `dev/gen_docs_unreleased.py` writes it at build time, so there
is no list to keep. A release stamps every note into a number, which leaves
that page nothing to list, so a released site has none.

`dev/docs_versions.py` picks the tags from the repo's tags, so nothing is
configured: `/` is the highest `vX.Y.Z` tag, and each lower major with a
`docsite/content` is built from its own highest such tag. When `v3.0.0` ships,
`2.x` becomes `/v2/` with no code change. Only `/` is indexed; `/vN/` and
`/next/` carry a `noindex` tag and stay out of the sitemap.

Every version is built with `main`'s site code (`docsite/app`,
`nuxt.config.ts`), so a theme or component fix reaches the release docs
without a release, while their text stays what the release shipped. The flip
side is that a docs-only fix to a released page, such as a typo, waits for the
next release too. A frozen major keeps the words of the components that hold
them: `dev/build_docs_site.sh` builds it with its own tag's `StageStrip.vue`,
so the landing page still names the stages the way that release did. If a
page of a frozen major ever shows text from a newer component, add the
component to that list.

Each version also has the dashboard demo of its own code beside its pages:
the web UI on sample data, with no server (`frontend/demo/`). `main`'s is at
`/next/demo/`, a release's at `/demo/` or `/vN/demo/`, built from that tag's
frontend with an `npm ci` of its lockfile, because the demo's mocks describe
that release's API. A tag from before the demo has none. Link to it as
`/demo/`, like a page: each build gives it its own base, and
`docsite/app/plugins/demo.ts` loads it from the server rather than the router.

The demo answers from the UI contract's mocks (`frontend/e2e/contract/mocks/`),
so a change to what it shows is a change to them. They are held to the server
by `tests/api/api_shapes.json`, which `just shapes` rewrites, and to the
vocabulary by `frontend/demo/sampleCoverage.test.ts`. When a response
changes, run `just shapes` and update the mock in the same PR; the `docs
nudge` workflow comments when `src/kraft/api/` or `src/kraft/vocab/` moved
without either.

If a frozen major stops building with `main`'s components, build it with its
own tag's site code instead:
`KRAFT_DOCS_OWN_SITE="v1" dev/build_docs_site.sh OUT_DIR` runs an `npm ci` of
that tag's lockfile and its own `nuxt generate`. The limits: it has the old
look and no version menu or banner (the tag's config knows nothing of them), a
tag that hard-codes its origin (`v1.4.0` does: `itsomidkarami.github.io`) puts
that origin in its canonical, `og:url` and sitemap URLs, and its pages are made
`noindex` by a tag added after the build. Verify it with
`KRAFT_DOCS_OWN_SITE=v1 just docs-site` before relying on it.

## The model price table

`src/kraft/prices.json` is what Kraft prices a *running* agent session with,
until the agent reports its own cost when it exits, and a finished one whose
agent reported none (Codex, Cursor, Amp). It is a snapshot of
models.dev's Anthropic and OpenAI listings, with Anthropic cache writes priced
at the one-hour tier Claude Code uses (2x input). Nothing refreshes it
automatically: when a provider's prices change, or a harness starts running a
model the file does not list, run `just refresh-prices`, review the diff, and
commit it. A model missing from the file gets no estimate, so its running
sessions count as unpriced towards the budget until they exit, and a finished
one that reported no cost counts $0 toward the item and daily caps (with a
`spend_unpriced` warning) and stops a `budget_usd`.

## Design documents

Specs and implementation plans are not committed. `.engineering/` and
`docs/superpowers/` are gitignored. Kraft itself writes a work item's
documents under `$KRAFT_HOME/run/items/<id>/docs/`, outside the repository;
`.engineering/` stays ignored for your own specs and for worktrees where an
older Kraft wrote them. `design/` and `docs/consolidated/` are
gitignored too.
`docs/intent/` and `docs/templates-v1-design.md` are the exceptions: the
first states intended behaviour as pinned requirements, the second is the
templates design draft, and both are maintained with the code.
