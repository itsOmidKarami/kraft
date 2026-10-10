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

`kraft <group> <verb> --help` prints every flag for one verb. Each group page
describes its verbs and the flags that change what they do. Wherever a
command takes a [work item](/concepts/vocabulary#work-item) ID, you can omit it when you run the command from
inside that item's worktree.

## Find a verb by what you want to do

Each command links to its entry.

### Filing work

| I want to | Command |
|---|---|
| Connect a repo to Kraft | [`kraft repo connect`](/reference/cli/repo#connect-a-repo) |
| Check that a repo's setup and test commands work | [`kraft repo connect --verify`](/reference/cli/repo#verify-a-repos-commands) |
| Stop tracking a repo | [`kraft repo disconnect`](/reference/cli/repo#disconnect-a-repo) |
| File a work item | [`kraft item create "title"`](/reference/cli/item#filing-a-work-item) |
| File it with a spec or plan I wrote | [`kraft item create "title" --spec PATH --plan PATH`](/reference/cli/item#attach-a-spec-or-plan) |
| File it and start it at once | [`kraft item create "title" --autostart`](/reference/cli/item#filing-a-work-item) |
| Revise a spec or plan before the item starts | [`kraft item set-attachments`](/reference/cli/item#change-an-items-setup) |
| Switch the [chain](/concepts/vocabulary#chain) of an item that has not started | [`kraft item set-chain`](/reference/cli/item#change-an-items-setup) |
| Start an item that was filed paused | [`kraft item resume`](/reference/cli/item#approve-reject-pause-resume-retry-raise-a-budget) |

### Watching

| I want to | Command |
|---|---|
| See the board | [`kraft view list`](/reference/cli/view#reading-the-board) |
| See where one item stands | [`kraft view show`](/reference/cli/view#what-view-show-prints) |
| Find a spec, plan or summary | [`kraft view search "query"`](/reference/cli/view#reading-the-board) |
| List an item's specs, plans and summaries, or read one | [`kraft view docs`, `kraft view doc ID`](/reference/cli/view#reviewing-before-you-approve) |
| Follow the agent's log | [`kraft view logs -f`](/reference/cli/view#read-a-sessions-log) |
| See what happened to an item, in order | [`kraft view events`](/reference/cli/view#following-a-running-item) |
| Watch the board live | [`kraft view watch`](/reference/cli/view#following-a-running-item) |
| See the repos Kraft knows | [`kraft repo list`](/reference/cli/repo#what-repo-list-prints) |
| Go to an item's worktree | [`kraft repo path`](/reference/cli/repo) or [`kraft repo open`](/reference/cli/repo) |
| See what the worktrees use, item by item | [`kraft view storage`](/reference/cli/view#worktree-disk-use) |

### At a gate

| I want to | Command |
|---|---|
| Read the document the [gate](/concepts/vocabulary#gate) is about | [`kraft view artifact`](/reference/cli/view#reviewing-before-you-approve) |
| Read the change | [`kraft view diff`](/reference/cli/view#reviewing-before-you-approve) |
| Compare two attempts, or the change since my last review | [`kraft view compare`](/reference/cli/view#reviewing-threads-and-comparing-attempts) |
| Approve | [`kraft item approve`](/reference/cli/item#approve-reject-pause-resume-retry-raise-a-budget) |
| Reject, and say why | [`kraft item reject --note "why"`](/reference/cli/item#approve-reject-pause-resume-retry-raise-a-budget) |
| Comment on a line of the change | [`kraft item comment`](/reference/cli/item#comment-on-a-change) |
| Send my comments with an outcome | [`kraft item review`](/reference/cli/item#send-a-review) |
| List [review threads](/concepts/vocabulary#review-thread) | [`kraft view threads`](/reference/cli/view#reviewing-threads-and-comparing-attempts) |
| Mark a thread resolved, or reopen it | [`kraft item resolve`, `kraft item reopen`](/reference/cli/item#reviewing-a-change) |
| Pass a gate without running it | [`kraft item skip`](/reference/cli/item#skip-escalate-end-or-drop-an-item) |
| Label an item's merge request | [`kraft item mr-label`](/reference/cli/item#label-the-merge-request) |

### When it stops

| I want to | Command |
|---|---|
| See why it stopped | [`kraft view show`](/reference/cli/view#what-view-show-prints), then [`kraft view logs`](/reference/cli/view#read-a-sessions-log) |
| Run the stopped [node](/concepts/vocabulary#node) again | [`kraft item retry`](/reference/cli/item#approve-reject-pause-resume-retry-raise-a-budget) |
| Redo one step or task, or the whole chain | [`kraft item retry --path PATH`, `--restart`](/reference/cli/item#addressing-work-by-path) |
| Give it more budget | [`kraft item raise-budget --usd N`](/reference/cli/item#raise-a-cap-that-stopped-an-item) |
| Raise a time or token [cap](/concepts/vocabulary#cap) | [`kraft item set-policy`](/reference/cli/item#a-work-items-own-policy) |
| Steer an agent that is paused | [`kraft item resume --steer "..."`](/reference/cli/item#addressing-work-by-path) |
| Pause a running item | [`kraft item pause`](/reference/cli/item#approve-reject-pause-resume-retry-raise-a-budget) |
| Ask an agent to help with the stop | [`kraft item escalate --message "..."`](/reference/cli/item#escalate-a-stopped-item) |
| Skip the node it stopped on | [`kraft item skip`](/reference/cli/item#skip-escalate-end-or-drop-an-item) |
| Pick another model or effort for it | [`kraft item set-overrides`](/reference/cli/item#set-item-wide-overrides), or [`kraft item set-node-override`](/reference/cli/item#one-nodes-agent-tasks) |
| End it as done, or as cancelled | [`kraft item complete`, `kraft item cancel`](/reference/cli/item#skip-escalate-end-or-drop-an-item) |
| Drop it and delete its worktree | [`kraft item abandon --yes`](/reference/cli/item#skip-escalate-end-or-drop-an-item) |

### Housekeeping

| I want to | Command |
|---|---|
| Archive finished items and free their worktrees | [`kraft item archive`](/reference/cli/item#archive-and-restore-finished-items) |
| Bring an archived item back under Done | [`kraft item restore`](/reference/cli/item#archive-and-restore-finished-items) |
| Check that the server is up | [`kraft admin health`](/reference/cli/admin#what-health-prints) |
| Check the whole install | [`kraft admin doctor`](/reference/cli/admin#what-doctor-checks) |
| Start, stop or restart the server | [`kraft admin start`, `stop`, `restart`](/reference/cli/admin#run-the-server) |
| Run Kraft as a service | [`kraft admin install-service`](/reference/cli/admin) |
| Install the newest release | [`kraft admin update`](/reference/cli/admin#update-kraft) |
| Reread the templates and [policy](/concepts/vocabulary#policy) after editing them | [`kraft admin reload`](/reference/cli/admin#reload-the-configuration) |
| Check my chains | [`kraft admin templates lint`](/reference/cli/admin#lint-the-templates) |
| Read a chain or a [library](/concepts/vocabulary#library) component | [`kraft admin templates show`, `templates library`](/reference/cli/admin#show-a-chain) |
| See the [harness profiles](/concepts/vocabulary#harness-profile) and what selects each | [`kraft admin harnesses`](/reference/cli/admin) |
| Rescan documents into the search index | [`kraft admin reindex`](/reference/cli/admin) |
| Register Kraft with an agent | [`kraft admin init`](/reference/cli/admin) |

`kraft item progress` and `kraft item reply` are [called by a worker](/reference/cli/item#verbs-a-worker-calls), not by you.

## Output format

Every read and action verb accepts `--json`, which prints the raw API payload.
It is the same call the matching [MCP tool](/reference/mcp-tools) makes, but not
always the same value:

- `kraft view show --json` prints the item's full detail (its effective chain, [worker](/concepts/vocabulary#worker) sessions and stop), where the `get_work_item` tool hands an agent a trimmed item.
- `kraft item create --json` prints a trimmed object, not the raw payload: `id`, `status` and `title`, plus `slots` (when `--autostart` queued the item), `repo_warning`, `bead_warning` or `duplicate_warning` when one applies.
- `kraft view list --json` is scoped to the repo you are in unless you pass `--all` or `--repo`; `list_work_items` is never scoped.

These verbs do not print the payload:

| Verb | Use instead |
|---|---|
| `view watch` | `kraft view events --json` (`watch` redraws a board rather than returning a value) |
| `repo path` | `kraft view show --json` (`path` prints one plain line meant for `cd`) |
| `admin start`, `stop`, `restart`, `update` | None; these print status lines. |
| `admin install-service`, `uninstall-service` | None. |
| `admin mcp`, `admin permission-hook` | None; these speak a protocol on stdio. |
| `admin plugin enable`, `disable` | `kraft admin plugin list --json` (these print one line). |
| `admin init` | None. It accepts `--json` and ignores it; it prints one `kraft: wrote PATH` line per file. |

### Without `--json`

| Verbs | What they print |
|---|---|
| `kraft item approve`, `reject`, `pause`, `unblock`, `resume`, `retry`, `raise-budget`, `skip`, `complete`, `cancel`, `escalate` | One line saying what they did, most of them with where the item stands now, such as `approved spec_approval on 4f2c…; the item is now running`. |
| `kraft item review approve` or `request-changes` at a pending gate | The same one line. |
| `kraft item review request-changes` or `comment`, with no gate pending | The review's fields as `key  value` lines. |
| `kraft item abandon`, and every `set-*` verb except `set-chain` | The same `key  value` lines. |
| `kraft item set-chain` | The chain the item now runs. |
| `kraft item archive` | The preview as a table with a total, then one line per item. |
| `kraft item restore` | One line saying what was restored. |
| `kraft item comment`, `resolve`, `reopen` | The thread and where it sits. |

The item itself is in `--json`, or in `kraft view show`.

The reads you script against print as follows: [`view list`](/reference/cli/view#what-view-list-prints), [`view show`](/reference/cli/view#what-view-show-prints), [`repo list`](/reference/cli/repo#what-repo-list-prints), [`admin health`](/reference/cli/admin#what-health-prints), [`admin doctor`](/reference/cli/admin#what-doctor-checks) and [`item create`](/reference/cli/item#what-create-prints). With `--json`, all but `item create` print the API's own answer: see the [HTTP API](/reference/http-api) pages.

`kraft view logs --json` and `kraft view events -f --json` print NDJSON, one
object per line, because a stream has no end on which to close an array.
`kraft view events --json` without `-f` prints one JSON array.

## Shell completion

`kraft` completes verbs with [`argcomplete`](https://github.com/kislyuk/argcomplete),
in any shell `argcomplete` supports. Register it by adding
`eval "$(register-python-argcomplete kraft)"` to your shell's rc file.

## In this section

- [Item verbs](/reference/cli/item): file, approve, reject, pause, unblock, resume, retry, skip, escalate, and set policy on a work item.
- [View verbs](/reference/cli/view): read the board, follow a running item, and review its diff and documents.
- [Repo verbs](/reference/cli/repo): connect repositories and reach their worktrees.
- [Admin verbs](/reference/cli/admin): run, check, update, and configure this machine's server.
