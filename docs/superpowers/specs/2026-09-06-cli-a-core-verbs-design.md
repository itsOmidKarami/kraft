# CLI Sub-project A: Core Verbs and the Dispatch Framework

> **Status:** design proposed 2026-09-06, not yet built.
> Parent: `docs/superpowers/specs/2026-09-05-agent-integration-design.md` (§3.2
> promised `kraft <verb>`; only `mcp` and `init` were built).
> Siblings: CLI sub-projects B (watching), C (reviewing), D (repo/worktree),
> E (service/admin), all dated 2026-09-06.

## 1. Problem

`src/kraft/client.py` holds nine operations over the local API. `kraft mcp`
exposes all nine to agents. Nothing exposes them to a human at a terminal:
`cli.py` dispatches on three string compares (bare `kraft` serves, `kraft mcp`,
`kraft init`) and `raise SystemExit` on anything else.

So the two front doors the agent-integration design named — MCP and CLI — are
one door and a promise. A human who wants to see the board opens a browser,
even when the answer is one line of text and they are already in a terminal
inside the worktree the answer is about.

This sub-project builds the second door and the conventions every later CLI
sub-project plugs into. It is the foundation: B, C, D and E add verbs to the
table A defines, and add functions to the `client.py` A leaves shaped for them.

## 2. Scope

In scope: argparse dispatch in `cli.py`, the output contract (human table by
default, `--json` everywhere), context resolution extended from work item to
repo, one connect-error message shared by both doors, and eight verbs over
functions `client.py` already has.

Out of scope, each with its own spec: log/event/live watching (B), diff and
document reading (C), repo management verbs (D), service and admin verbs (E).
Editing templates, registry, policy, access or notify stays in the UI — those
are YAML with schema validation and a form is the right editor for them.

**No new client.py operations.** A is a front door onto the existing nine. That
is what makes it a safe foundation: if the verb layer is wrong, no server
behaviour changed.

## 3. Verbs

| Verb | client.py call | Implicit context |
|---|---|---|
| `kraft list [--status S] [--repo R] [--all]` | `list_work_items` | cwd repo scopes it |
| `kraft show [ID]` | `get_work_item` | cwd work item |
| `kraft create TITLE [--repo R] [--chain T]` | `create_work_item` | cwd repo |
| `kraft approve [ID] [--gate G]` | `approve_gate` | cwd work item |
| `kraft reject [ID] --note N [--gate G]` | `reject_gate` | cwd work item |
| `kraft pause [ID]` | `pause` | cwd work item |
| `kraft resume [ID] [--steer S]` | `resume` | cwd work item |
| `kraft search QUERY [--limit N]` | `search` | none |

`kraft` with no arguments still seeds-and-serves, unchanged. That constraint is
why `cli.main` reads `sys.argv[1:]` itself before handing anything to argparse:
an empty argv never reaches the parser.

Positional-ID-optional is deliberate and matches the MCP tools: `kraft approve`
inside a worktree is the common case, `kraft approve Kraft-abc` is the explicit
one. An id given always wins over an inferred one.

`--all` on `list` drops the cwd-repo scoping and shows every repo's board; it
is the flag you reach for when the answer surprised you.

Verb names are reserved across all five sub-projects at once, so nothing here
collides with `logs`/`events`/`watch` (B), `diff`/`docs`/`doc` (C),
`repos`/`connect`/`path` (alias `cd`)/`open` (D), `serve`/`health`/`reindex`
(E), or the existing `mcp` and `init`.

## 4. Output contract

This is the part both readers share, so it is stated once and every later
sub-project obeys it.

- **Default: human.** Aligned columns, no borders, header row dimmed. Column
  widths from `shutil.get_terminal_size()`; the last column truncates rather
  than wraps, because a wrapped table stops being scannable. Timestamps render
  relative ("4m ago"), ids in full — a Kraft id is short already and a truncated
  id cannot be pasted back into the next command.
- **`--json`: exact.** `json.dumps(result, indent=2)` of precisely what
  `client.py` returned, no re-shaping. The CLI must never become a second
  definition of what a work item is; that is `client.py`'s job, and the MCP door
  reads the same value.
- **Colour** only when stdout is a tty and `NO_COLOR` is unset. Status colours:
  active green, needs_human yellow, paused dim, completed default, failed red.
- **Errors** go to stderr as `kraft: <message>`, never to stdout, so
  `kraft list --json | jq` is safe to pipe under failure. Exit codes: `0` ok,
  `1` operation failed (including a refused self-action), `2` usage error.
- **No pagination, no `--watch`, no interactive prompts** in A. `less` exists;
  B owns live views.

### 4.1 Rendering lives in one module

`src/kraft/render.py`: `table(rows, columns)`, `kv(dict)`, `relative_time(iso)`,
`status_style(status)`. `cli.py` chooses a renderer per verb and never formats
inline. B and C add renderers here (a log line, a diff stat block) rather than
growing `cli.py`.

## 5. Context resolution

`client.resolve_context()` answers "which work item is this session standing
in": `$KRAFT_WORK_ITEM_ID` → a cwd under `run/worktrees/<id>` → nothing. It is
unchanged; the self-action guard depends on its exact semantics.

New, beside it:

```python
async def resolve_repo(cwd: Path | None = None) -> str | None:
    """The connected repo the cwd is inside, or None."""
```

`git rev-parse --show-toplevel` from the cwd, then match that path — and each of
its parents — against `GET /repos`. Parent-walking is what makes a submodule
checkout resolve to the connected superproject.

**Why the API and not `repos.yaml` directly:** a local YAML read would work
without a server, but it puts a second reader on config the API already owns and
shapes. Every verb that uses the answer needs the server anyway. One data path.

**Precedence, once, for every verb:** explicit flag/positional → resolved work
item's repo → cwd repo → error naming both ways to fix it.

**Not doing branch resolution.** `work_items` has no `branch` column, and the
worktree path is derived (`run/worktrees/<id>`), so a branch name cannot be
mapped back to a work item without new schema or a `git worktree list` reverse
lookup. Directory resolution already covers the case that matters, because
Kraft's own worktrees are where its branches are checked out. If a human ever
checks a Kraft branch out somewhere else, they can pass the id.

## 6. When no server is running

`client.http()` gains one wrapper that turns `httpx.ConnectError` into:

```
kraft: no Kraft server at http://127.0.0.1:8765 — start one with `kraft serve`
```

In `client.py`, not `cli.py`, so an agent hitting a dead server through MCP gets
the same sentence instead of a traceback. This is the only new failure handling
in A; everything else stays where the parent design put it, in `api.py`.

## 7. argparse

The current `cli.py` comment says argparse is "deliberately not used: serving
must stay the zero-argument default, and one string compare is the whole
dispatch." The second half stops being true at eight verbs with flags. The first
half is preserved by the argv check in §3, and the comment gets rewritten to say
so rather than being silently contradicted.

Subparsers, one function per verb, each ≤15 lines: parse, call `asyncio.run` on
a `client` coroutine, hand the result to a renderer. `cli.py` stays under ~250
lines; if a verb needs more than that, the logic belongs in `client.py` where
MCP can reach it too.

## 8. Testing

- One test per verb, invoking `cli.main([...])` with `client` pointed at the
  ASGI app, asserting on captured stdout. Tests the dispatch, not httpx.
- `--json` output parses and equals the `client.py` return value — the
  anti-drift check between the doors.
- Context: cwd in a worktree resolves the item; cwd in a connected repo scopes
  `list` and defaults `create`; cwd in an unconnected repo errors with a message
  naming `kraft connect` (D) or `--repo`.
- Bare `kraft` still serves: argv-empty path reaches `_serve`, asserted with
  `uvicorn.run` patched.
- Connect error: no server → the §6 sentence on stderr, exit 1, empty stdout.
- Self-action guard still fires through the CLI door with
  `$KRAFT_WORK_ITEM_ID` set.

## 9. Decisions this spec makes

1. argparse replaces string-compare dispatch; zero-arg serve preserved by an
   argv check before the parser.
2. Human table by default, `--json` byte-for-byte from `client.py`.
3. `resolve_repo()` is new; `resolve_context()` is untouched.
4. Branch-based resolution is out until something asks for it.
5. No new API endpoints, no new client operations.
