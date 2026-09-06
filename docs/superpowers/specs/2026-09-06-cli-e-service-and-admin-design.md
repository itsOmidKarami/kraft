# CLI Sub-project E: Service and Admin

> **Status:** design proposed 2026-09-06, not yet built. Depends on A.

## 1. Problem

Every other CLI sub-project assumes a server is already running, and A's
connect-error message tells you to run `kraft serve` — a command that does not
exist. Bare `kraft` serves, and that is the only way in.

Meanwhile `GET /health` already reports invalid templates, invalid policy, the
reattach summary and index state, and `POST /index/rescan` reindexes a repo or
everything. Neither is reachable outside a browser.

## 2. Scope

Verbs: `kraft serve`, `kraft health`, `kraft reindex`. Plus one change to
`cli.py`'s existing serve path.

Out of scope: stopping or restarting a server (`kraft serve` runs in the
foreground; Ctrl-C stops it — a `kraft stop` implies pidfiles, ownership and
staleness for no gain over job control), daemonising, and any config-editing
verb. The parent packaging design settled where state lives; E does not reopen
it.

## 3. Verbs

**`kraft serve [--host H] [--port P]`** — exactly what bare `kraft` does today,
via the same `_serve()`. Bare `kraft` stays as-is and is documented as the alias.
The flags override `access.yaml` for one run, matching what `KRAFT_HOST` and
`KRAFT_PORT` already do — and they route through the same `_bind()`, so the
refusal to bind a non-loopback address without a password still applies. A flag
must not be a way around a security check that an env var respects.

**`kraft health`** — `GET /health`, rendered: status, bind, invalid templates,
invalid policy, index counts, reattach summary. Exits `0` on `ok` and `1` on
`degraded`, so it is usable in a shell conditional.

**`kraft doctor` — deferred, own bead.** Sketched here so the next person does
not redesign it from scratch. The checks a human runs when something is wrong,
in one pass, each line a check and a verdict:

- server reachable at the configured base URL (and if not, the A §6 sentence)
- `/health` status and each degraded reason spelled out
- templates dir exists and was seeded; access.yaml parses
- MCP token file present and 0600
- agent CLI (`claude`) on PATH — and whether it is the `fixtures/bin` fake, which
  is worth saying out loud because a dev instance that looks like it is working
  is spending no tokens on purpose
- each connected repo's path still exists and is still a git repo
- orphaned worktrees under `run/worktrees/` with no matching work item row

Exits 0 if every check passes, 1 otherwise. No `--fix`: doctor reports, the
human decides. The orphan check is read-only for the same reason.

It is deferred because it is the only piece of E that is not a thin wrapper, and
because `health` already answers the server's own view of itself. Each check
above should have to be wanted once before it is written — that is the only
thing that stops a doctor command from growing output nobody reads.

**`kraft reindex [--repo R]`** — `POST /index/rescan`, printing the
inserted/updated/renamed/deleted counts it returns.

## 4. What `doctor` must not become

Every check above answers a question someone actually asks when Kraft is not
behaving. The failure mode of a doctor command is that it grows checks nobody
reads, and its output stops being scanned. New checks earn their place by having
been needed once.

## 5. Testing

- `serve --port` reaches `uvicorn.run` with that port (`uvicorn.run` patched).
- `serve --host 0.0.0.0` with no password still raises the existing SystemExit.
  This is the security-regression test for §3 and is not optional.
- `health` exit code follows `status`; degraded reasons rendered.
- `reindex --repo` on an unknown repo surfaces the API's 404 text.

## 6. Decisions

1. **`doctor` is deferred to its own bead.** `health` ships in E; nothing else
   in this spec depends on doctor existing.
2. **`kraft serve` takes `--host` and `--port`.** They route through the same
   `_bind()` as the env vars, so the refusal to bind a non-loopback address
   without a password still fires — asserted in §5. The precedence is
   flag > env > `access.yaml`, documented in `--help` and in the README's run
   section.
