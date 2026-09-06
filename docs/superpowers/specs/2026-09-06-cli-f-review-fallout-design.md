# CLI Sub-project F: Review Fallout

> **Status:** design 2026-09-06. Closes the six beads the CLI epic's final
> review left open (Kraft-bbg.6 through .11). Depends on A–E, all shipped.

## 1. Problem

The CLI epic shipped with six known gaps, each filed rather than fixed so the
epic could close on something honest. Five are small contract bugs. One
(`kraft doctor`) is a verb E deliberately deferred. They are grouped here
because they are all "the CLI is not yet what its own specs say it is", and
because four of them touch the same three files.

## 2. Scope, one section per bead

### 2.1 `render.table` measures painted strings (Kraft-bbg.9, bug)

`table()` computes column widths with `len()`, but `_render_list` and
`_render_repos` hand it cells that already carry ANSI escapes. A painted
`STATUS` measures ~9 characters wider than it draws, so on a tty the columns
after it are pushed right by exactly the width of the escape sequence — and
only for the painted rows, so the table shears. Invisible under pytest, which
has no tty and therefore never paints.

**Fix:** measure the visible width. `render` grows one regex and three
helpers — `strip_ansi`, `visible_width`, `_pad` — and `table` uses them for
column widths, padding and truncation. Painting stays where it is; measuring
becomes escape-aware, which is the smaller change and keeps `table` a function
callers hand finished strings to.

`_fit` (the last column, which truncates) drops the colour of a cell it has to
cut rather than slicing a string mid-escape: a truncated escape sequence
corrupts the rest of the terminal line, and a correct plain cell beats a
coloured broken one.

### 2.2 The truncation warning names nothing (Kraft-bbg.8)

Spec C §3 requires the truncation line to name the byte limit and the worktree
path. `render._diff_trailer` names neither, because `GET /work-items/{wid}/diff`
returns neither.

**Fix:** the payload carries them. `diff_max_bytes` and `worktree_path` join the
diff response, and `_diff_trailer` renders them when `truncated` is set.
`worktree_path` is not a new disclosure: `GET /work-items/{wid}` has always
returned it, to the same authenticated caller.

The alternative — drop the requirement from the spec — was rejected: the
sentence a reviewer reads at an approval gate should say how much they are
missing and where the rest is, and both facts are one dict key away.

### 2.3 `_cmd_logs` reaches past `client.py` (Kraft-bbg.7)

`_cmd_logs` calls `client._get("/worker-sessions/{sid}/log")` directly. It is
the only API path spelled outside `client.py`, which is the module whose whole
job is to be the one place that knows them — and the MCP door cannot read a log
backlog today without copying the same line.

**Fix:** `client.log_backlog(session_id, limit)`, next to `stream_log`. `limit`
is `None` for everything and `0` for none, so `kraft logs -n 0` keeps its
meaning without the CLI guarding a slice.

### 2.4 `create`'s no-repo error gives the wrong advice (Kraft-bbg.11)

`create_work_item` raises *"no repo: pass one, or run from a Kraft worktree"*.
The common case is standing in an ordinary git repo that simply is not connected,
where the fix is `kraft connect` — which the message does not mention. Spec A §8
asks for a message naming `kraft connect` or `--repo`.

**Fix, at the root rather than in the string:** `create_work_item` falls back to
`resolve_repo()` before giving up. That is the resolution the CLI already does
for itself in `_repo_scope`, so the CLI's behaviour does not change — but the MCP
door gains it, and the error now only fires when the cwd genuinely resolves to
no connected repo. The message then splits on the one question that decides the
advice: is this a git repo at all? If yes, name it and say `kraft connect`.

### 2.5 `path --json` and `repos` without colour (Kraft-bbg.10)

Two output-contract gaps.

`kraft path` inherits `--json` from the shared parent parser and silently
ignores it. **Fix: reject it**, the way `watch` does, pointing at
`kraft show --json`. `path` prints exactly one line so `cd "$(kraft path ID)"`
works; there is no JSON shape it could grow that would not be a worse spelling
of `show`.

`kraft repos` signals enabled/disabled with dim paint alone, so the distinction
vanishes under a pipe or `NO_COLOR` — though spec D §3 lists it as output.
**Fix:** a `STATE` column carrying the word. The dim paint moves from the name
to that cell, so there is one signal with colour as an accent rather than two
signals that can disagree.

### 2.6 `kraft doctor` (Kraft-bbg.6)

Built as E §3 sketches it, with E §4's bar respected: these checks and no
others, each one a question someone asks when Kraft is misbehaving.

| Check | Fails when |
|---|---|
| `server` | nothing is listening at the configured base URL |
| `health` | `/health` is degraded — **one line per reason**, not one line saying "degraded" |
| `templates` | the templates dir does not exist |
| `access.yaml` | it does not parse |
| `mcp token` | absent, empty, or group/other-readable |
| `agent cli` | `claude` is not on PATH. Passes loudly when it resolves to the `fixtures` fake, because a dev instance that looks like it is working is spending no tokens on purpose |
| `repo <name>` | a connected path is gone, or is no longer a git repo |
| `worktrees` | a directory under `run/worktrees/` has no work item row |

Exit 0 if every check passes, 1 otherwise. No `--fix`: doctor reports, the human
decides. `--json` emits the check list, so a hook can read it.

Server-dependent checks (`health`, `repo *`, `worktrees`) are skipped, not
failed, when `server` fails — a dead server would otherwise report as five
problems instead of one, which is exactly the unscannable output §4 warns about.

Checks live in `doctor.py`, not `cli.py`: `cli.py` is a dispatch table and has
stayed one. Rendering lives in `render.py` with every other renderer.

## 3. Testing

- `table` with a painted cell aligns identically to the same table unpainted
  (`strip_ansi` of the coloured render == the plain render). This is the
  regression test for §2.1 and needs colour forced on, since pytest has no tty.
- `_fit` on a painted over-long cell emits no escape byte.
- A truncated diff renders the byte limit and the worktree path; an untruncated
  one renders neither.
- `client.log_backlog` returns all lines for `None`, none for `0`, the tail for
  `n`; `kraft logs` still prints the same output through it.
- `create` from an unconnected git repo names `kraft connect` and that repo's
  path; from a connected repo it succeeds without `--repo`, through
  `resolve_repo`.
- `kraft path --json` exits 1 with a message naming `kraft show`.
- `kraft repos` piped (no tty) still distinguishes enabled from disabled.
- `doctor` on a healthy dev instance exits 0; with the token chmod'd 0644 it
  exits 1 and says so; with the server down it reports one server failure and
  skips the server-dependent checks rather than reporting five.

## 4. Decisions

1. **Measure, don't unpaint.** §2.1 could have moved painting into `table` via a
   per-column paint callback. Escape-aware measuring is fewer lines, and keeps
   `table`'s contract ("hand me finished strings") intact for every caller.
2. **`worktree_path` on the diff payload.** Already public on the item detail
   endpoint to the same caller; a reviewer told a diff is partial and not told
   where the rest is has been given half a warning.
3. **`path --json` is rejected, not honoured.** One-line output is the contract
   that makes `cd "$(kraft path ID)"` work; `kraft show --json` already answers
   the structured question.
4. **`create` resolves the repo rather than only rewording.** The wrong advice
   was a symptom of resolution stopping one step early. Fixing the resolution
   fixes both doors; rewording would have fixed neither.
5. **Doctor skips rather than fails behind a dead server.** One cause, one line.
