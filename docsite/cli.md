# The `kraft` command

`kraft` with no arguments serves. Subcommands talk to a running server. Every
verb accepts `--json`, which prints the raw API payload — the same value
`kraft admin mcp` hands an agent — except `view watch` and `repo path`, which
each refuse it for their own reason (`watch` streams a redrawn board rather
than a value; use `kraft view events --json` instead, `path` already prints
one plain line meant for `cd`, so `kraft view show --json` is the structured
form). An id is optional wherever the work item can be inferred from the
directory you are standing in.

## Everyday verbs

```bash
kraft view list                      # the board, scoped to the repo you are in
kraft view list --all --status=paused
kraft view show                      # the work item whose worktree you are in
kraft item create "fix the flaky test" --description "..."   # files it paused; a human starts it
kraft item create "ship the thing" --spec .engineering/specs/x.md   # skips the spec node
kraft item approve                   # approve whichever gate is pending
kraft item reject --note "the plan skips migrations"
kraft item pause / kraft item resume --steer "try the other adapter"
kraft item retry                     # re-run the node a stopped item stopped on
kraft view search "retry policy"
```

### Addressing work by path

Retry, skip and resume address chain work by its canonical path: `node`,
`node.step` or `node.step.task` (a `tasks:` node's one step is `main`, so
`implementation.main.implement`). `kraft view show --json` lists every path.

```bash
kraft item retry --path verification.review               # that step, and everything after it
kraft item retry --path verification.review.code_review   # that task; its finished siblings stand
kraft item retry --path spec                              # a node that already completed, and on
kraft item retry --restart                                # the whole chain from its first node
kraft item resume --steer "keep the old API" \
  --steer-task verification.review.code_review="check the auth module first"
kraft item skip --path verification.review.code_review    # only that task; its siblings keep running
```

A retry keeps everything the earlier run did and runs on a new run fork. It
reopens every gate it has to rerun; a gate before the retried work keeps its
decision. `resume --steer` reaches every paused agent task; `--steer-task`
gives one its own, and naming a task that is not a paused agent task is
refused. Pause is always the whole work item.

## Following a running item

```bash
kraft view logs -f            # the current session's log, until it stops
kraft view logs --session <id> -n 0
kraft view events             # node transitions, gate decisions, escalations
kraft view watch              # a live board, redrawn on every event
```

`kraft view logs --json` emits NDJSON — one object per line — because a stream has
no end to close an array on.

## Reviewing before you approve

```bash
kraft view diff --stat        # how big is it
kraft view diff --name-only   # changed and untracked paths
kraft view diff               # the coloured body, through $PAGER
kraft view docs               # specs, plans and summaries linked to the item
kraft view doc <id> --open    # open one in an editor on the server's machine
kraft view artifact <id>      # the document a pending gate is actually about
```

A truncated diff always says so on its last line, and files the agent wrote
without `git add` are listed separately — they are invisible in a unified diff.

## Less common item verbs

```bash
kraft item skip --note "already fixed upstream"   # advance past the current node/gate, unrun
kraft item progress 3        # a worker saying it started plan task 3 -- not one you type by hand
kraft item escalate --message "the fix loop keeps missing the same edge case"
kraft item escalate --message "..." --new-thread  # a fresh agent session, not the latest thread
kraft item set-chain --template quick-task        # switch a not-yet-started item's chain
kraft item set-overrides --model opus --effort high
kraft item set-overrides --clear                  # back to the template's own binding
kraft item set-node-override --node verify --auto-escalate-stuck
kraft item mr-label --id <id> release::patch      # relabel the MR; re-creates its pipeline
kraft item abandon --yes                          # drops the item, reclaims its worktree
kraft item complete --reason "shipped by hand"    # end it as completed; add --close-beads to close its beads
kraft item cancel --reason "superseded by #412"   # end it as cancelled; the worktree stays
```

`progress` is what a worker session itself calls to report which plan task it
started — you'll see it in logs more than type it. `escalate` is the manual
door onto the same path `kraft admin` and the board's own auto-escalation use
to ask an agent to help resolve a `needs_human` stop. `abandon` destroys
uncommitted work in the item's worktree; `--yes` is required, not optional,
on purpose. `complete` and `cancel` are the explicit terminal actions: each
needs a `--reason`, stops whatever is running, and records the reason on the
item's timeline.

## Repos and worktrees

```bash
kraft repo list              # what is connected; `*` marks the one you are in
kraft repo connect            # connect the current repo (safe to repeat)
kraft repo disconnect         # forget it again; work items are untouched
cd "$(kraft repo path <id>)"  # into the item's worktree; `kraft repo cd` is an alias
kraft repo path --shell       # a shell function that does the cd for you
kraft repo open <id>          # the worktree in an editor
```

A connected repo's entry in `repos.yaml` carries how Kraft prepares a worktree
for it:

| Key | What it does |
|---|---|
| `setup_command` | Run in every new worktree before any node starts. Required — `""` means "deliberately nothing". A repo with no `setup_command` stops its next work item. |
| `env` | Literal variables every worker for this repo gets. |
| `env_passthrough` | Names of variables to carry over from the daemon's own environment, for what the baseline allowlist does not cover. |

`kraft repo connect` probes a `setup_command` from the repo's markers; check it
before trusting it, and `kraft admin doctor` reports any repo still undeclared.
Editing a repo's settings stays in the UI. Full field list, including
`test_scopes`, `forge`, and `default_chain_template`:
[Configuration](configuration.md#reposyaml-connected-repos).

## Service and admin

```bash
kraft admin start --port 9000  # the same as bare `kraft`; flag > env > access.yaml
kraft admin stop               # SIGTERM to the pid in run/kraft.pid
kraft admin restart            # stop, then start again the same way it was running
kraft admin health             # exit 1 when degraded, reasons on stdout
kraft admin doctor             # every check in one pass; exit 1 if any fails
kraft admin reindex [--repo P] # rescan documents into the search index
kraft admin reload             # reread templates/registry from disk, no restart
kraft admin update [--restart] # install the newest release (brew upgrade, if that's how you installed)
kraft admin init [--repo]      # register the MCP server and skills; see Agent integration
kraft admin mcp                # serve the MCP tools over stdio
```

A non-loopback bind still refuses to start without a password, flag or not. The
server runs in the foreground, so Ctrl-C stops the one in front of you;
`kraft admin stop` is for the one you started somewhere else. A second start
against the same run directory is refused while the first is alive.

`kraft admin install-service` and `kraft admin uninstall-service` register or
remove Kraft as an OS service unit (`KeepAlive`/`Restart=always`), for a
machine you want it running on without a terminal open. `kraft admin restart`
remembers how the server was running: through the service manager if one is
installed, back into the background if it was `--detach`ed, or — if it was
running attached to a terminal — stopped with a note that only that terminal
can bring it back. `kraft admin update --restart` chains the same restart
onto a successful update.

The verbs live in four groups — `item` acts, `view` reads, `repo` is
repositories and their worktrees, `admin` is this machine's server. Typing an
old flat verb prints where it moved.

## Shell completion

`kraft` ships tab completion for zsh (and any other shell `argcomplete`
supports) via [`argcomplete`](https://github.com/kislyuk/argcomplete). Add one
line to `~/.zshrc`:

```zsh
eval "$(register-python-argcomplete kraft)"
```

then `kraft it<TAB>` completes to `kraft item`, `kraft item <TAB>` lists every
`item` subcommand — `create approve reject pause resume retry skip progress
escalate abandon`, and more — and so on down the verb tree. Takes effect after
your next `kraft` install or `uv sync`.
