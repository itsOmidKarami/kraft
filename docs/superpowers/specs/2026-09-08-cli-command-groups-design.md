# CLI command groups and a server stop — design

Date: 2026-09-08
Status: approved (design), plan pending

## Problem

`kraft` has 28 top-level verbs registered flat into one `add_subparsers`
(`cli._add_verbs`). `kraft --help` is an undifferentiated wall, and nothing in
the list says which verbs act on a work item, which only read, and which drive
the machine. The count grows with every feature — `retry` and `disconnect`
landed this week.

Separately, there is no way to stop a running server. `_serve()` calls
`uvicorn.run` in the foreground, so Ctrl-C is the only stop, and a server
started under launchd or in a forgotten terminal has to be found with `ps`.

## Goals

- Four command groups, so the tree teaches the tool.
- One name per command. No aliases, no hidden compatibility surface.
- `kraft admin stop` stops a running server.
- Every removed verb tells the user its new name.

## Non-goals

- Daemonizing. The server stays foreground; `stop` is a signal, not a service
  manager. `serve --detach`, log redirection and `status` are out.
- Windows. `stop` uses `os.kill`/`SIGTERM`.
- Changing any verb's flags, output, or the payloads `--json` prints. The MCP
  door and the CLI must keep returning the same values (`cli.emit`'s contract).

## The command tree

```
kraft                       # still serves, unchanged
kraft item    create approve reject pause resume retry abandon
kraft view    list show search logs events watch diff docs doc artifact
kraft repo    list connect disconnect path (cd) open
kraft admin   start stop health doctor reindex init mcp
```

Four decisions inside that tree:

- **`serve` becomes `start`.** `start`/`stop` is the pair a reader expects, and
  `serve` has no opposite. Bare `kraft` still serves, so `kraft admin start` is
  the named form, not the common one.
- **`repos` becomes `repo list`.** The plural verb was carrying the grouping
  that the group now carries.
- **`retry` sits with `pause`/`resume`** — the same lifecycle family; CLAUDE.md
  already calls it "the only door back onto a stopped item".
- **`item` acts, `view` reads.** The split is by effect, not by subject: both
  groups are about work items, but only one of them changes anything.

`init` and `mcp` stay in `admin`: they configure an agent's access to this
machine's Kraft, which is the same job as `doctor` and `reindex`.

## Removed verbs

Every one of the 28 flat verbs is removed, including `serve` and the two that
landed this week. A `MOVED` dict maps old verb to new path:

```python
MOVED = {"list": "view list", "approve": "item approve", "serve": "admin start", ...}
```

`main()` checks `args[0]` against it before argparse sees argv, and exits 2 with
`kraft: 'list' moved to 'kraft view list'`. Checking before argparse matters:
argparse's own error for an unknown choice prints the four group names and
nothing about where the verb went.

The map is data, so it costs one line per verb and one parametrized test over
all 28 entries. It is not an alias table — nothing in it runs a command.

## Implementation

`_add_verbs` splits into `_add_item`, `_add_view`, `_add_repo`, `_add_admin`,
each taking the group's `add_subparsers` object and the shared `--json` parent
parser (`_json_flag()`). The 28 `_cmd_*` functions, every renderer, and
`emit()` are untouched — this change is entirely in parser construction and
dispatch.

Each group parser sets `required=True` on its subparsers, so `kraft view` alone
prints that group's help rather than a traceback.

## Stop

`_serve()` writes the pid to `run_dir/kraft.pid` after `_bind()` succeeds —
after, so a run refused for binding a LAN address without a password never
leaves a file behind — and removes it in a `finally`.

If the pidfile exists and names a live process, `start` refuses:

```
kraft: already running (pid 4171) — kraft admin stop
```

This catches the real footgun, which is two servers on one `KRAFT_HOME` with
different ports: same databases, same worktrees, no port conflict to reveal it.
A pidfile naming a dead process is stale, not a conflict: `start` says so,
removes it, and continues.

`kraft admin stop` reads the pidfile, sends `SIGTERM`, then polls until the
process is gone, up to ~5 seconds, and reports which happened. No pidfile, or a
stale one, is not an error — it prints that nothing is running and exits 0, so
`kraft admin stop` in a teardown script is safe to run twice.

The pidfile lives beside the databases in `run/` because it belongs to one
`KRAFT_HOME`; a dev instance under `.dev/` and an installed one under `~/.kraft`
must be able to run at once, which is what `just dev` relies on.

## Testing

- Parametrized over `MOVED`: every old verb exits 2 and names its new path.
- Each group's verbs parse and dispatch to the same `_cmd_*` as before.
- `kraft view` with no verb prints help, exit 2.
- Pidfile round trip: serve writes it, `stop` terminates the process, file gone.
- Stale pidfile: `start` proceeds, `stop` reports nothing running, exit 0.
- Double start: second one refuses, exit non-zero, first still alive.
- Existing `test_cli_verbs.py`, `test_cli_repos.py`, `test_cli_watching.py`,
  `test_cli_doctor.py`, `test_cli_admin.py`, `test_gates.py`, `test_ws.py`
  move to the new argv. Mechanical.

## Blast radius

- `src/kraft/cli.py` — the change.
- `src/kraft/paths.py` — `RunDirs.pid`.
- Hint strings that name a command, each of which must move with it:
  `client.py:133` (`kraft serve` → `kraft`), `client.py:155` (`kraft abandon`),
  `render.py:39` (`kraft list`), `render.py:259` (`kraft artifact`),
  `client.py:227` (`kraft logs`), `client.py:451` (`kraft connect`),
  `index/ingest.py:155` (`kraft docs`), `api.py:1563` (`kraft watch`),
  `doctor.py` (`kraft doctor`).
- `README.md`, `CLAUDE.md`, `AGENTS.md` — the verb tables.
- The `kraft:*` plugin skills call MCP tools, not the CLI. Unaffected.

## Risks

- **Muscle memory and existing hooks break.** Deliberate: one surface, and the
  `MOVED` message is the migration path. Anything scripted against `kraft list`
  fails loudly at the next run rather than silently doing something else.
- **A pidfile can outlive a `SIGKILL`ed server.** Handled as stale everywhere it
  is read; no path treats its existence alone as "running".
