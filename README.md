# Kraft

A local orchestrator for semi-autonomous software work. One FastAPI process plus a
React SPA: work items enter as **chains** — ordered nodes materialized from a YAML
template — and each node runs hook-point tasks through plugin adapters (a headless
agent, a subprocess, a builtin). Every retry loop is capped; hitting a cap escalates
to you with the full trace. Gates stop the chain where a human decision belongs.

Runs on your machine, binds loopback by default, and edits your repos through git
worktrees. Design docs: [`docs/consolidated/`](docs/consolidated/00_overview.md).

## Install and run

```bash
just setup      # uv sync + npm install
just install    # build the SPA, install the `kraft` command
kraft           # http://127.0.0.1:8765
```

State lives in `$KRAFT_HOME` (default `~/.kraft`):

| | |
|---|---|
| `~/.kraft/run/` | `orchestrator.db`, `index.db`, `logs/`, `results/`, `worktrees/` |
| `~/.kraft/templates/` | the YAML the Settings screens edit — chain templates, `registry.yaml`, `policy.yaml`, `repos.yaml`, `access.yaml` |

`templates/` is seeded from the packaged defaults on first run and never overwritten
after, so an upgrade cannot clobber an edited policy. It is a plain directory of
files on purpose: the Settings screens are an editor for something you can diff,
revert, and `git init` yourself.

Binding off loopback requires a password — set one in Settings → Access while still
on `127.0.0.1`. The process refuses to start on a LAN address without one.

`KRAFT_HOME=~/kraft-other kraft` gives you a second, fully separate instance.

## Develop

```bash
just dev        # backend + vite, state in .dev/, agents faked — UI on :5173
just dev-seed   # fill a running dev instance with work items in every state
just dev-reset  # throw .dev/ away
```

`just dev` puts `fixtures/bin` on `PATH` ahead of the real agent, where `claude` is a
symlink to [`fixtures/fake-claude.sh`](fixtures/fake-claude.sh) — the same fake the
test suite uses, so it cannot rot. A dev instance never spends tokens and never
touches `~/.kraft`.

`dev-seed` drives the real HTTP API rather than writing rows, so the data is whatever
the executor actually produces. It lands four work items in four states: completed, a
pending gate, failed, and paused mid-flight. A title containing `KRAFT_FAIL` or
`KRAFT_SLOW` steers that item's fake agent without affecting the others.

```bash
just test       # backend tests (args pass through: just test -k search)
just test-ui    # frontend unit tests
just e2e        # Playwright (see frontend/e2e/README.md)
just lint       # ruff check + format check
just fix        # autofix
```

Everything is `just` — run `just` for the full list.

## Layout

```
src/kraft/        orchestrator: api, executor, policy, store, adapters/, index/
frontend/         React SPA (vite)
templates/        default chain templates + registry/policy — the install seed
dev/seed.py       dev-instance seeder
fixtures/         fake agent + the PATH shim just dev uses
docs/consolidated/  the design this implements
```

## Requirements

Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 20+, git,
[`bd`](https://github.com/gastownhall/beads) for work-graph intake, and `claude` for
real runs (not needed for `just dev`). Semantic search is opt-in:
`just setup-vector` (downloads a ~130MB model on first search); without it `/search`
still works in FTS mode.
