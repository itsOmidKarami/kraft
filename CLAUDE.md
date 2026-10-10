# Project Instructions for AI Agents

## Build & Test

Everything goes through `just` — run `just` for the list.

```bash
just setup      # uv sync + npm ci
just test       # backend tests affected by your changes (testmon); --no-testmon for all
just test-ui    # frontend typecheck + unit tests
just test-py 3.12  # the unit tier on another Python (CI runs 3.12, 3.13, 3.14); `requires-python` is the floor
just lint       # ruff check + format check
```

**Never call `pytest` / `uv run pytest` directly.** Always go through `just
test` (add `-k pattern` or a path to target specific tests; use
`--no-testmon` for a full run). Calling pytest raw skips testmon's
change-tracking and burns the full ~14min suite.

**Testing guideline:** see `docs/testing.md`. Two tiers (unmarked unit,
mocked at the adapter seam; `@pytest.mark.e2e("<cli>")` for a real CLI's
contract) and one rule for every pin: mutate the code it covers and confirm
that same test fails, or it isn't proof of anything. `just check-tests`
enforces what can be checked mechanically.

## Running Kraft

Two ways, and neither is `python -m kraft` by hand.

```bash
just dev        # dev instance: state in .dev/, agents faked, UI on :5173
just dev-seed   # fill a running dev instance with work items in every state
just dev-reset  # throw .dev/ away

just install    # build the SPA, install the `kraft` command; then run `kraft`
```

`just dev` puts `fixtures/bin` (a `claude` symlink to `fixtures/fake-claude.sh`)
ahead of the real agent on PATH, so a dev instance never spends tokens. A work
item title containing `KRAFT_FAIL` or `KRAFT_SLOW` steers its own fake agent.

### The `kraft` command

Every MCP tool but `permission_request` is also a subcommand, so a hook or a
non-MCP agent gets the same surface; `item abandon`, `item archive` and
`item restore` are the `item` verbs with no tool. `--json` prints the raw API
payload on every verb except `view watch`, `repo path`, and the `admin` ones
that manage the server process (`start`, `stop`, `restart`, the service verbs,
`update`, `mcp`, `permission-hook`), `admin plugin enable` and `disable`, and
`admin init` (it accepts `--json` and
ignores it).

```bash
kraft view list [--all] [--status=paused]   # the board, scoped to the cwd's repo
kraft view show [ID]                        # ID defaults to the worktree you are in
kraft item create "title" [--description "..."] [--spec P] [--plan P] [--no-auto-gate] [--after ID] [--autostart]  # files it paused unless --autostart; auto-gate is on unless --no-auto-gate
kraft item set-attachments [ID] [--spec P] [--plan P] [--drop KIND]  # revise a not-yet-started item's documents
kraft item approve [ID] / kraft item reject [ID] --note "why"
kraft item pause [ID] / kraft item resume [ID] --steer "..."
kraft item unblock [ID] [--dependency ID]   # drop what a blocked or paused item still comes after
kraft item retry [ID] [--steer "..."]       # the only door back onto a stopped item
kraft item raise-budget [ID] --usd N|none   # raise the dollar cap that stopped it (its own or its policy's), and retry
kraft item skip [ID] [--note "..."]         # advance past the current node or gate without running it
kraft item escalate [ID] --message "..."    # ask an agent to help with a needs_human stop
kraft item complete [ID] --reason "..." / kraft item cancel [ID] --reason "..."
kraft item abandon [ID] --yes               # drop an item, deleting its worktree and branch
kraft item archive [ID ...] [--reclaimable] [--yes]  # archive finished items, deleting their worktrees; previews, then needs --yes
kraft item restore [ID]                     # put an archived item back under Done
kraft item set-chain [ID] --chain C      # a not-yet-started item's chain
kraft item set-overrides [ID] [--model M] [--effort E] / kraft item set-node-override [ID] --node N [...]
kraft item set-policy [ID] --policy KEY=VALUE [--clear]
kraft item mr-label LABEL...                # label this item's merge request
kraft item progress K [ID]                  # a worker saying it started plan task K
kraft item reply THREAD --body "..." [--claim fixed|answered|should_fix]  # a worker answering a review thread
kraft view threads [ID] [--open]              # review threads, drafts marked
kraft view compare [ID] [--from T] [--to T] [--nodes a,b] [--stat|--name-only] [-w]  # T: base|attempt:N|last_review|latest, default base to latest
kraft item comment [ID] --body "..." [--file P --lines A-B [--side old|new] [--start-side old|new]] [--label must-fix|question|nit] [--suggest "..."]
kraft item comment --reply THREAD --body "..."
kraft item resolve THREAD / kraft item reopen THREAD
kraft item review [ID] comment|approve|request-changes [--summary "..."] [--node N]
kraft view search "query"
kraft view logs [ID] [-f] [-n N]            # a worker session's log; --json is NDJSON
kraft view storage                          # worktree disk use against the quota and limit, item by item
kraft view events [ID] [--after N] [--type T]
kraft view watch                            # live board, needs a terminal
kraft view diff [ID] [--stat|--name-only] [-w]   # truncation and untracked always shown
kraft view docs [ID] [--attachment spec|plan] / kraft view doc DOC_ID [--open [EDITOR]]  # --attachment: what it was filed with
kraft view artifact [ID]                    # the doc the pending gate is about
kraft repo list                             # `*` marks the repo you are in
kraft repo connect [PATH] [--test-command C] [--setup-command C] [--no-tests] [-y] [--verify [--timeout MIN] [--on-host]]
kraft repo disconnect [PATH]
kraft repo path [ID] (alias cd) / kraft repo open [ID]
kraft admin start [--host H] [--port P]      # same as bare `kraft`
kraft admin stop                             # SIGTERM to run/kraft.pid
kraft admin restart [-y]                     # stop, then start again the same way it was running; -y skips the question
kraft admin install-service / kraft admin uninstall-service  # launchd or systemd --user unit
kraft admin health                           # exit 1 when degraded
kraft admin doctor                           # every check at once; exit 1 on any
kraft admin update [--restart] [-y] [--channel stable|rc|beta|alpha]  # install the newest release; --restart also restarts
kraft admin reindex [--repo PATH]
kraft admin reload                           # reread the template library, policy.yaml and intake.yaml from disk, no restart
kraft admin templates lint                   # check every chain in the library; exit 1 on any error
kraft admin templates show ID [--resolved]   # a chain file as written, or expanded
kraft admin templates library [ID]           # the library's components
kraft admin harnesses [ID]                   # harness profiles and the tasks that select each
kraft admin plugin validate PATH             # check a collection or plugin directory; installs nothing, exit 1 on any problem
kraft admin plugin collection add SOURCE [--ref REF] [--auto-update] / list / update [NAME] / auto-update NAME on|off / remove NAME
kraft admin plugin install PLUGIN@COLLECTION [--as ALIAS] [--auto-update] [--re-install] [-y]  # shows a review; writes nothing until accepted
kraft admin plugin update [PLUGIN@COLLECTION ...] [-y | --check]  # --check exits 0 current, 1 waiting, 2 refused, 3 could not check
kraft admin plugin auto-update PLUGIN@COLLECTION on|off / enable|disable PLUGIN@COLLECTION / uninstall PLUGIN@COLLECTION / list
kraft admin init [--repo] / kraft admin mcp  # register Kraft with an agent
kraft admin permission-hook codex|cursor     # run by a harness's pre-tool hook, not by hand
```

Verbs live in four groups: `item` acts, `view` reads, `repo` is repositories and
their worktrees, `admin` is this machine's server. Typing an old flat verb
(`kraft list`) prints where it moved.

Installed Kraft keeps state in `$KRAFT_HOME` (default `~/.kraft`): `run/` for the
databases, logs and worktrees, `config/` for the YAML the Templates and
Settings screens edit (`templates/` before 2.0), seeded from the packaged
defaults in the repo's `config/` on first run and never overwritten after. Only
`library.yaml` and `chains/` are templates; the rest is configuration.

## Architecture Overview

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Conventions & Patterns

### Specs and plans are not committed

Design docs and implementation plans do not go into git. `.engineering/` and
`docs/superpowers/` are both gitignored (Kraft writes its own specs, plans,
review briefs and session notes there).

When a brainstorm produces a spec and a plan for Kraft, hand them over as work
item attachments instead:

```bash
kraft item create "title" --spec PATH --plan PATH
```

**An attachment is snapshotted at intake.** Intake copies the file into
`~/.kraft/run/attachments/<work-item-id>/`, and that copy, not the path you
passed, is what `builtins.ensure_worktree` copies into the worker's worktree
when the item runs. A stored copy that has gone missing fails the item before
its worktree is made, so it never runs with its gates trimmed and no document.

Editing the original after filing changes nothing on its own. To revise a spec
or plan before the item starts, re-attach it in place instead of abandoning and
re-filing:

```bash
kraft item set-attachments [ID] --spec PATH   # copied again; --plan likewise
kraft item set-attachments [ID] --drop spec   # removes it and puts its gate back
```

Once the item has started, its documents are fixed (the call answers 409): its
worktree already holds them, committed on its branch. `kraft item create` warns
when an open item in the same repo has the same title or implements a bead you
named (a bead is an issue in [beads](https://github.com/gastownhall/beads), the
optional `bd` issue tracker); read that warning before filing twice.

### Pull requests

The release label, the `## Changelog` section, the docs page a change must
update, and the `## Removed tests` block are all in
[CONTRIBUTING.md](CONTRIBUTING.md). Read it before opening one.

### The test tree mirrors the source tree

A test for `src/kraft/<pkg>/<mod>.py` lives at `tests/<pkg>/test_<mod>.py` — not
`tests/test_<pkg>_<mod>.py`. Flat names under a hierarchical source is how the
layout drifted the first time. A module with no package mirrors nothing and
stays at `tests/test_<mod>.py`.

**Every `tests/` subdirectory needs an empty `__init__.py`.** This is
load-bearing, not tidiness. The mirrored tree has duplicate basenames (about
twenty: `test_gates.py` exists under `api/`, `executor/` and the root,
and so on for `test_db`, `test_auth`, `test_review`, `test_run` and more),
which pytest's default prepend import mode rejects as a hard collection error.
Packages fix it with no config change, and they keep `tests/` on `sys.path` —
which the many files doing `from support.harness import ...` depend on. Do not
"simplify" this by switching to `--import-mode=importlib`; that drops `tests/`
off `sys.path` and breaks every one of them.

Two things bite when you move or add a nested test:

- `Path(__file__)` paths are written relative to `tests/`, so a file one level
  down needs `parents[1]` where the root wanted `parents[0]`. Resolve them
  against the filesystem rather than trusting the arithmetic.
- Moving a test file breaks its `enforced-by:` pins in `docs/intent/`. CI runs
  `python -m kraft.intent`, which exits 1 on a broken pin, so repoint them in
  the same change and confirm with `just intent`.

### Writing tests

Read `docs/testing.md` before the first test. The short version a worker needs:

- **A fix adds a row, not a function.** Find the test that covers the behavior
  and add a `parametrize` case with an `id`. A new `def test_` is for a new
  behavior.
- **Grep `tests/support/` before writing a helper.** `support.harness` has
  `make_repo`, `commit_all`, `connect_repo`, `git`, `write` and the `v1_*`
  chain builders; `support.api` has the client pollers (`_poll_events`,
  `_await_gate`, `_post_default`); `tests/conftest.py` has `database`,
  `run_dirs`, `repo`, `client`, `templates_dir`, `item_on`, `bd`.
  `just check-tests` refuses a helper body that already exists elsewhere.
- **Declare a fold in one line:** `tests/x.py::test_prefix_* -- folded into ...`
  under `## Removed tests`, and repoint intent pins with
  `just intent-repoint OLD NEW`.

### Marking a task already done in a reused plan

A plan attached to one work item is sometimes reused for a later item that
only implements one of its tasks — the rest already merged elsewhere. Say so
on the heading itself, not in prose the tooling can't read:

```
### Task 7: Suggest a setup command at connect time [DONE]
```

`kraft.progress.parse_tasks()` reads the `[DONE]` tag and keeps the board's
"Task N of M" from drifting onto a task that was never in scope for the
running work item.

## Kraft Workers

A session with `$KRAFT_WORK_ITEM_ID` set is a Kraft worker, running in a
throwaway git worktree on its own branch. It commits everything it changes
before it exits — uncommitted work never reaches the merge request and is
destroyed with the worktree. This overrides any standing instruction not to
run `git commit` (the maintainer's own agent profile has one), for commits
only: a worker still does not push, merge, sync Dolt (the versioned database
beads stores its issues in), or close beads. Kraft does those itself.

A worker's environment is built from an allowlist, not inherited from whatever
shell started the Kraft daemon: `PATH`, `HOME`, the usual locale and proxy
vars, the nine `KRAFT_*` that locate the instance (`KRAFT_HOME`, `KRAFT_RUN_DIR`,
`KRAFT_CONFIG_DIR` and its 1.x name `KRAFT_TEMPLATES_DIR`, `KRAFT_SKILLS_DIR`, `KRAFT_HOST`, `KRAFT_PORT`,
`KRAFT_DAEMON_PID`, `KRAFT_DAEMON_PORT`) plus the ones Kraft sets per session, and
Claude Code's credential vars (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`; another agent's key needs `env_passthrough`). Any other `KRAFT_*` var, and anything else a
repo needs, is declared in its `repos.yaml` entry — `env:` for literal values,
`env_passthrough:` to name a var the daemon already has. Do not assume a
variable from your own shell is present in a worktree.

@CLAUDE.local.md
