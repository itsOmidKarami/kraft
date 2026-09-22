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
kraft item create "fix it now" --autostart   # starts it at once; refused (403) from a Kraft worker
kraft item create "ship the thing" --spec .engineering/specs/x.md   # skips the spec node
kraft item create "backport the fix" --base-branch release/1.2   # starts from, and merges into, release/1.2
kraft item create "small fix" --skip-nodes spec,plan --budget 5 \
  --node-override implementation.attempts=2   # intake fields POST /work-items takes; --budget none lifts the cap
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
refused. A paused agent task resumes its own session when its harness can
(Claude's `--resume`), told the steer and to carry on; otherwise it restarts
with its brief and the steer. A steer is refused while the item is running
(pause it first) and when the pause stopped no agent task, such as a test
run or a CI wait: there is nothing there for it to steer. Pause is always the
whole work item.

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
kraft item set-policy --policy merge_request_feedback.ci.await_ci.total_time_cap_minutes=60
kraft item set-policy --clear                     # drop the item's own policy override
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

### A work item's own policy

`kraft item create --policy KEY=VALUE` (repeatable) and `kraft item
set-policy` give one work item its own policy override, without touching its
chain template or any other item. `FIELD=VALUE` applies item-wide;
`PATH.FIELD=VALUE` applies to one node, step or task by canonical path and
everything under it. A value is read as YAML, so `deny_tools=[WebFetch]` is a
list.

```bash
kraft item create "quick fix" \
  --policy time_cap_minutes=45 \
  --policy merge_request_feedback.ci.await_ci.total_time_cap_minutes=60 \
  --policy verification.max_attempts=4
kraft item set-policy <id> --policy max_attempts=2   # replaces the whole override
```

The fields are the ones `policy.yaml` explains (see [Configuration](configuration.md)):
`timeout_minutes`, `max_attempts` (execution nodes only) and
`allowed_harnesses` move within the administrator `maxima`, and win over what
the chain authored. `allowed_tools`, `deny_tools` and `sandbox` only
tighten: a list intersects with what each task already allows. The caps --
`time_cap_minutes`, `total_time_cap_minutes`, `token_budget` and
`budget_usd` -- are the work item's own item-wide: raising one above the
chain's, up to the administrator maximum, is how a person unsticks a capped
item before a retry (Ruling 198). On a path they only tighten, and are
refused, naming both scopes, above the cap that scope already has (a wait
task's total cap is its timeout). `wait_timeout_minutes` is retired (Ruling 196) and refused, naming
`total_time_cap_minutes`. An operational value past its maximum, a path
the chain does not have, or a key that is not a policy field is refused,
naming the field. `set-policy` works on any item that has not ended: on a
running or waiting one it binds from the next node the item enters and from
the next observation of a wait it is parked on, whose new timeout counts from
when the wait started.

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

`kraft repo connect` probes a `setup_command` and a test command from the
repo's markers, and prints the test command with the file it came from (a
justfile with a `test` recipe proposes `just test` ahead of any manifest). Check
both before trusting them; `kraft admin doctor` reports any repo still undeclared.
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
kraft admin reload             # reread the template library and policy.yaml, no restart
kraft admin update [--restart] [-y] # install the newest release (brew upgrade, if that's how you installed)
kraft admin templates lint     # check every chain in the installed library; exit 1 on any error
kraft admin templates show ID  # one chain file as its author wrote it
kraft admin templates show ID --resolved  # the same chain with its library components expanded
kraft admin templates library      # every library.yaml component, its kind, and the chains using it
kraft admin templates library ID   # one component: its definition, users and lint issues (tasks.implementer, or a unique bare name)
kraft admin harnesses         # every harnesses.yaml profile, its provider, and the library tasks selecting it
kraft admin harnesses ID      # one profile: provider, executable, defaults, the tasks and chains using it
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

Agent registrations run `kraft admin mcp` by name, so they get whichever
`kraft` is first on PATH. A second, older install ahead of this one (a Homebrew
formula beside a `uv tool` install, say) keeps every MCP session on its code
after an update. `kraft admin update` warns when that is the case, and
`kraft admin doctor` fails its `kraft on PATH` row.

A home still holding the pre-V1 template configuration (a `registry.yaml` and
no `library.yaml`) is not converted and not overwritten. The server starts
degraded and refuses new work, and `kraft admin update` says what will change,
asks, and only then moves the whole directory to a `templates.pre-v1-<time>`
backup beside it and installs the V1 configuration. `-y` accepts without the
question; with no terminal and no `-y` it changes nothing. `access.yaml`,
`notify.yaml`, `theme.yaml`, `repos.yaml`, `intake.yaml`, `steering/` and
`harnesses/` are carried across. `policy.yaml` starts from the V1 default and
keeps your value for every key V1 still has; each key it drops is printed with
its old value. The old chains and registry stay only in the backup. There is no
migration helper. An update interrupted mid-swap is finished by the next start
or `kraft admin update`, never reseeded over.

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
