---
title: CLI
description: Every kraft verb, grouped by what it does.
---

`kraft` with no arguments starts the server. Every other command talks to a
running server. Verbs live in four groups: `item` acts, `view` reads, `repo`
manages repositories and their worktrees, and `admin` runs this machine's
server. Typing an old flat verb (`kraft list`) prints where it moved.

`kraft <group> <verb> --help` prints the flags for one verb. Each group page
below lists every flag. Wherever a command takes a work item ID, you can omit it
when you run the command from inside that item's worktree.

## In this section

- [Item verbs](/reference/cli/item): file, approve, reject, pause, resume, retry, skip, escalate, and set policy on a work item.
- [View verbs](/reference/cli/view): read the board, follow a running item, and review its diff and documents.
- [Repo verbs](/reference/cli/repo): connect repositories and reach their worktrees.
- [Admin verbs](/reference/cli/admin): run, check, update, and configure this machine's server.

## Output format

Every read and action verb accepts `--json`, which prints the raw API payload,
the same value `kraft admin mcp` hands an agent. These verbs do not:

| Verb | Use instead |
|---|---|
| `view watch` | `kraft view events --json` (`watch` redraws a board rather than returning a value) |
| `repo path` | `kraft view show --json` (`path` prints one plain line meant for `cd`) |
| `admin start`, `stop`, `restart`, `update` | None; these print status lines. |
| `admin install-service`, `uninstall-service` | None. |
| `admin mcp`, `admin permission-hook` | None; these speak a protocol on stdio. |

`kraft view logs --json` prints NDJSON, one object per line, because a stream
has no end on which to close an array.

## Shell completion

`kraft` completes verbs with [`argcomplete`](https://github.com/kislyuk/argcomplete),
in any shell `argcomplete` supports. Register it with
`register-python-argcomplete kraft`.
