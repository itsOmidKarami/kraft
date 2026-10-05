---
title: CLI
navigation:
  title: Overview
description: Every kraft verb, grouped by what it does.
---

`kraft` with no arguments starts the server. Every other command talks to a
running server. Verbs live in four groups: `item` acts, `view` reads, `repo`
manages repositories and their worktrees, and `admin` runs this machine's
server. Typing an old flat verb (`kraft list`) prints where it moved.

`kraft <group> <verb> --help` prints every flag for one verb. Each group page
below describes its verbs and the flags that change what they do. Wherever a
command takes a work item ID, you can omit it when you run the command from
inside that item's worktree.

## In this section

- [Item verbs](/reference/cli/item): file, approve, reject, pause, resume, retry, skip, escalate, and set policy on a work item.
- [View verbs](/reference/cli/view): read the board, follow a running item, and review its diff and documents.
- [Repo verbs](/reference/cli/repo): connect repositories and reach their worktrees.
- [Admin verbs](/reference/cli/admin): run, check, update, and configure this machine's server.

## Output format

Every read and action verb accepts `--json`, which prints the raw API payload.
It is the same call the matching [MCP tool](/reference/mcp-tools) makes, but not
always the same value:

- `kraft view show --json` prints the item's full detail (its effective chain, worker sessions and stop), where the `get_work_item` tool hands an agent a trimmed item.
- `kraft view list --json` is scoped to the repo you are in unless you pass `--all` or `--repo`; `list_work_items` is never scoped.

These verbs do not print the payload:

| Verb | Use instead |
|---|---|
| `view watch` | `kraft view events --json` (`watch` redraws a board rather than returning a value) |
| `repo path` | `kraft view show --json` (`path` prints one plain line meant for `cd`) |
| `admin start`, `stop`, `restart`, `update` | None; these print status lines. |
| `admin install-service`, `uninstall-service` | None. |
| `admin mcp`, `admin permission-hook` | None; these speak a protocol on stdio. |
| `admin init` | None. It accepts `--json` and ignores it; it prints one `kraft: wrote PATH` line per file. |

### Without `--json`

| Verb | What it prints |
|---|---|
| `kraft item approve`, `reject`, `pause`, `resume`, `retry`, `raise-budget`, `skip`, `complete`, `cancel` and `escalate` | One line saying what they did, most of them with where the item stands now, such as `approved spec_approval on 4f2c…; the item is now running`. |
| `kraft item review approve` or `request-changes` at a pending gate | The same one line. |
| `review request-changes` and `review comment`, with no gate pending | The review's fields as `key  value` lines. |
| `abandon` and the `set-*` verbs but `set-chain` | The same `key  value` lines. |
| `set-chain` | The chain the item now runs. |
| `kraft item comment`, `resolve` and `reopen` | The thread and where it sits. |

The item itself is in `--json`, or in `kraft view show`.

`kraft view logs --json` and `kraft view events -f --json` print NDJSON, one
object per line, because a stream has no end on which to close an array.
`kraft view events --json` without `-f` prints one JSON array.

## Shell completion

`kraft` completes verbs with [`argcomplete`](https://github.com/kislyuk/argcomplete),
in any shell `argcomplete` supports. Register it by adding
`eval "$(register-python-argcomplete kraft)"` to your shell's rc file.
