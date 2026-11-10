---
title: CLI
navigation:
  title: Overview
description: Every kraft verb, grouped by what it does, and found by what you want to do.
---

`kraft` with no arguments starts the server. Every other command talks to a
running server. Verbs live in four groups: `item` acts, `view` reads, `repo`
manages repositories and their [worktrees](/concepts/vocabulary#worktree), and `admin` runs this machine's
server. Typing an old flat verb (`kraft list`) prints where it moved.
`kraft console` opens the [terminal UI](/reference/console); `kraft console --server URL` opens it
against a Kraft on another machine.

`kraft <group> <verb> --help` prints every flag for one verb. Each group page
describes its verbs and the flags that change what they do. Wherever a
command takes a [work item](/concepts/vocabulary#work-item) ID, you can omit it when you run the command from
inside that item's worktree, or from a repo's own checkout while an
[in-place item](/guides/run/run-items-in-place)'s branch is checked out there.

You can also give the start of an ID instead of all of it: 4 characters or
more, as long as no other item's ID starts the same way. A worker inside a
network sandbox must still give its own item's whole ID.

## Find a verb by what you want to do

Each command links to its entry.

### Filing work

| I want to | Command |
|---|---|
| Connect a repo to Kraft | [`kraft repo connect`](/reference/cli/repo#repo-connect) |
| Check that a repo's setup and test commands work | [`kraft repo connect --verify`](/reference/cli/repo#verify-a-repos-commands) |
| Stop tracking a repo | [`kraft repo disconnect`](/reference/cli/repo#repo-disconnect) |
| File a work item | [`kraft item create "title"`](/reference/cli/item#filing-a-work-item) |
| File it with a spec or plan I wrote | [`kraft item create "title" --spec PATH --plan PATH`](/reference/cli/item#attach-a-spec-or-plan) |
| File it and start it at once | [`kraft item create "title" --autostart`](/reference/cli/item#filing-a-work-item) |
| Revise a spec or plan before the item starts | [`kraft item set-attachments`](/reference/cli/item#item-set-attachments) |
| Switch the [chain](/concepts/vocabulary#chain) of an item that hasn't started | [`kraft item set-chain`](/reference/cli/item#item-set-chain) |
| Start an item that was filed paused | [`kraft item resume`](/reference/cli/item#item-resume) |

### Watching

| I want to | Command |
|---|---|
| See the board | [`kraft view list`](/reference/cli/view#view-list) |
| See where one item stands | [`kraft view show`](/reference/cli/view#what-view-show-prints) |
| Find a spec, plan or summary | [`kraft view search "query"`](/reference/cli/view#view-search) |
| List an item's specs, plans and summaries, or read one | [`kraft view docs`](/reference/cli/view#view-docs), [`kraft view doc ID`](/reference/cli/view#view-doc) |
| Follow the agent's log | [`kraft view logs -f`](/reference/cli/view#view-logs) |
| See what happened to an item, in order | [`kraft view events`](/reference/cli/view#view-events) |
| Watch the board live | [`kraft view watch`](/reference/cli/view#view-watch) |
| Watch the board and act on it in a terminal UI | [`kraft console`](/reference/console) |
| See the repos Kraft knows | [`kraft repo list`](/reference/cli/repo#repo-list) |
| Go to an item's worktree | [`kraft repo path`](/reference/cli/repo#repo-path) or [`kraft repo open`](/reference/cli/repo#repo-open) |
| See what the worktrees use, item by item | [`kraft view storage`](/reference/cli/view#view-storage) |

### At a gate

| I want to | Command |
|---|---|
| Read the document the [gate](/concepts/vocabulary#gate) is about | [`kraft view artifact`](/reference/cli/view#view-artifact) |
| Read the change | [`kraft view diff`](/reference/cli/view#view-diff) |
| Compare two attempts, or the change since my last review | [`kraft view compare`](/reference/cli/view#view-compare) |
| Approve | [`kraft item approve`](/reference/cli/item#item-approve) |
| Reject, and say why | [`kraft item reject --note "why"`](/reference/cli/item#item-reject) |
| Comment on a line of the change | [`kraft item comment`](/reference/cli/item#item-comment) |
| Send my comments with an outcome | [`kraft item review`](/reference/cli/item#item-review) |
| Read the item's merge request, pipelines, job logs, merged requests or labels | [`kraft view mr`, `pipelines`, `jobs`, `job-log`, `merged`, `labels`](/reference/cli/view#reading-the-forge) _Added in 2.5_ |
| List [review threads](/concepts/vocabulary#review-thread) | [`kraft view threads`](/reference/cli/view#view-threads) |
| Mark a thread resolved, or reopen it | [`kraft item resolve`](/reference/cli/item#item-resolve), [`kraft item reopen`](/reference/cli/item#item-reopen) |
| Pass a gate without running it | [`kraft item skip`](/reference/cli/item#item-skip) |
| Label an item's merge request | [`kraft item mr-label`](/reference/cli/item#item-mr-label) |

### When it stops

| I want to | Command |
|---|---|
| See why it stopped | [`kraft view show`](/reference/cli/view#what-view-show-prints), then [`kraft view logs`](/reference/cli/view#view-logs) |
| Run the stopped [node](/concepts/vocabulary#node) again | [`kraft item retry`](/reference/cli/item#item-retry) |
| Redo one step or task, or the whole chain | [`kraft item retry --path PATH`, `--restart`](/reference/cli/item#item-retry) |
| Give it more budget | [`kraft item raise-budget --usd N`](/reference/cli/item#item-raise-budget) |
| Raise a time or token [cap](/concepts/vocabulary#cap) | [`kraft item set-policy`](/reference/cli/item#item-set-policy) |
| Steer an agent that is paused | [`kraft item resume --steer "..."`](/reference/cli/item#item-resume) |
| Pause a running item | [`kraft item pause`](/reference/cli/item#item-pause) |
| Ask an agent to help with the stop | [`kraft item escalate --message "..."`](/reference/cli/item#item-escalate) |
| Skip the node it stopped on | [`kraft item skip`](/reference/cli/item#item-skip) |
| Pick another model or effort for it | [`kraft item set-overrides`](/reference/cli/item#item-set-overrides), or [`kraft item set-node-override`](/reference/cli/item#item-set-node-override) |
| End it as done, or as cancelled | [`kraft item complete`](/reference/cli/item#item-complete), [`kraft item cancel`](/reference/cli/item#item-cancel) |
| Drop it and delete its worktree | [`kraft item abandon --reason "..." --yes`](/reference/cli/item#item-abandon) |

### Housekeeping

| I want to | Command |
|---|---|
| Archive finished items and free their worktrees | [`kraft item archive`](/reference/cli/item#item-archive) |
| Bring an archived item back under Done | [`kraft item restore`](/reference/cli/item#item-restore) |
| Check that the server is up | [`kraft admin health`](/reference/cli/server#what-health-prints) |
| Check the whole install | [`kraft admin doctor`](/reference/cli/server#what-doctor-checks) |
| Start, stop or restart the server | [`kraft admin start`, `stop`, `restart`](/reference/cli/server#run-the-server) |
| Run Kraft as a service | [`kraft admin install-service`](/reference/cli/server#admin-install-service) |
| Install the newest release | [`kraft admin update`](/reference/cli/server#admin-update) |
| Reread the templates and [policy](/concepts/vocabulary#policy) after editing them | [`kraft admin reload`](/reference/cli/server#admin-reload) |
| Check my chains | [`kraft admin templates lint`](/reference/cli/server#admin-templates-lint) |
| Read a chain or a [library](/concepts/vocabulary#library) component | [`kraft admin templates show`](/reference/cli/server#admin-templates-show), [`templates library`](/reference/cli/server#admin-templates-library) |
| See the [harness profiles](/concepts/vocabulary#harness-profile) and what selects each | [`kraft admin harnesses`](/reference/cli/server#admin-harnesses) |
| Rescan documents into the search index | [`kraft admin reindex`](/reference/cli/server#admin-reindex) |
| Register Kraft with an agent | [`kraft admin init`](/reference/cli/server#admin-init) |

`kraft item progress` and `kraft item reply` are [called by a worker](/reference/cli/item#verbs-a-worker-calls), not by you.

## Output format

Every read and action verb accepts `--json`, which prints the raw API payload.
It is the same call the matching [MCP tool](/reference/mcp-tools) makes, but not
always the same value:

- `kraft view show --json` prints the item's full detail (its effective chain, [worker](/concepts/vocabulary#worker) sessions and stop), where the `get_work_item` tool hands an agent a trimmed item.
- `kraft item create --json` prints a trimmed object, not the raw payload: `id`, `status` and `title`, plus `slots` (when `--autostart` queued the item), `repo_warning`, `bead_warning` or `duplicate_warning` when one applies.
- `kraft view list --json` is scoped to the repo you are in unless you pass `--all` or `--repo`; `list_work_items` is never scoped.

Each verb prints this without `--json`, and this with it:

| Verb | Without `--json` | With `--json` |
|---|---|---|
| `console` | A terminal UI. | Not accepted. |
| `view watch` | A board it redraws rather than returning a value. | Not accepted. Use `kraft view events --json`. |
| `repo path` | One plain line meant for `cd`. | Not accepted. Use `kraft view show --json`. |
| `admin start`, `stop`, `restart`, `update` | Status lines. | Not accepted. |
| `admin install-service`, `uninstall-service` | Status lines. | Not accepted. |
| `admin mcp`, `admin permission-hook` | A protocol on stdio. | Not accepted. |
| `admin worker serve`, `attach`, `local` | Status lines on stderr, or a protocol on stdio. | Not accepted. |
| `admin addon enable`, `disable` | One line. | Not accepted. Use `kraft admin addon list --json`. |
| `admin init` | It reports an existing MCP registration before printing one `kraft: wrote PATH` line per file. | Accepted and ignored. |
| `kraft item approve`, `reject`, `pause`, `unblock`, `resume`, `retry`, `raise-budget`, `skip`, `complete`, `cancel`, `escalate` | One line saying what they did, most of them with where the item stands now, such as `approved spec_approval on 4f2c…; the item is now running`. | The API payload. |
| `kraft item review approve` or `request-changes` at a pending gate | The same one line. | The API payload. |
| `kraft item review request-changes` or `comment`, with no gate pending | The review's fields as `key  value` lines. | The API payload. |
| `kraft item abandon`, and every `set-*` verb except `set-chain` | The same `key  value` lines. | The API payload. |
| `kraft item set-chain` | The chain the item now runs. | The API payload. |
| `kraft item archive` | The preview as a table with a total, then one line per item. | The preview, or the bulk results with `--yes`. |
| `kraft item restore` | One line saying what was restored. | The API payload. |
| `kraft item comment`, `resolve`, `reopen` | The thread and where it sits. | The API payload; `resolve` and `reopen` print the thread. |

The item itself is in `--json`, or in `kraft view show`.

The reads you script against print as follows: [`view list`](/reference/cli/view#what-view-list-prints), [`view show`](/reference/cli/view#what-view-show-prints), [`repo list`](/reference/cli/repo#what-repo-list-prints), [`admin health`](/reference/cli/server#what-health-prints), [`admin doctor`](/reference/cli/server#what-doctor-checks) and [`item create`](/reference/cli/item#what-create-prints). With `--json`, all but `item create` print the API's own answer: see the [HTTP API](/reference/http-api) pages.

`kraft view logs --json` and `kraft view events -f --json` print NDJSON, one
object per line, because a stream has no end on which to close an array.
`kraft view events --json` without `-f` prints one JSON array.

## Shell completion

`kraft` completes verbs with [`argcomplete`](https://github.com/kislyuk/argcomplete),
in any shell `argcomplete` supports. Register it by adding
`eval "$(register-python-argcomplete kraft)"` to your shell's rc file.

## In this section

- [Item verbs](/reference/cli/item): file, approve, reject, pause, unblock, resume, retry, skip, escalate and set policy on a work item.
- [View verbs](/reference/cli/view): read the board, follow a running item, and review its diff and documents.
- [Repo verbs](/reference/cli/repo): connect repositories and reach their worktrees.
- [Server verbs](/reference/cli/server): start, stop, restart, update, reload, check and inspect the server.
- [Add-on verbs](/reference/cli/addon): collections, install, update, validate.
- [Machine verbs](/reference/cli/worker): register and run machines.
- [Forge verbs](/reference/cli/forge): name, edit, test forge accounts.
