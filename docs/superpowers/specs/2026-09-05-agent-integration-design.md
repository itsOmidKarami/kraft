# Agent Integration: Inbound Control Surface

> **Status:** design agreed 2026-09-05, not yet built.
> Companion documents: `docs/consolidated/01_conceptual_model.md` (§1.4 amended
> here, see §8), `docs/consolidated/02_orchestrator_core.md`,
> `docs/consolidated/03_plugin_adapters.md`.

## 1. Problem

Kraft is outbound-only. The executor starts agent sessions and injects their
context (`src/kraft/adapters/agent.py`, `_CTX`); results come back through
`$KRAFT_RESULT_PATH`. There is no inbound path — an agent session a human
started in a terminal cannot see the board, cannot create a work item, and
cannot act on a gate.

That gap costs two things. A session that has just finished a spec and a plan
has to be abandoned so the human can retype the work into the UI. And a human
already reading a diff in chat has to switch to `:8765` to approve the gate for
that diff.

The HTTP API already exposes almost every verb this needs: `POST /work-items`,
`POST /repos`, `POST /repos/probe`, the gate approve/reject pair, `/steer`,
`/pause`, `/resume`, `/search`, `/work-items`, `/work-items/{wid}`,
`/work-items/{wid}/events`. This design is largely a front door onto endpoints
that exist, plus the trust rules that make an agent a safe caller of them.

## 2. Scope

In scope: a client module, an MCP server, CLI subcommands, an installer, the
skills they install, and worker-side MCP injection.

Out of scope: additional agent vendors beyond the Claude Code path, a general
API-token subsystem, and any change to how chains execute.

## 3. Architecture

One core, two front doors.

```
      kraft mcp (stdio)          kraft <verb> (CLI)
              \                    /
               \                  /
              src/kraft/client.py
                      |  httpx, 127.0.0.1:8765
               src/kraft/api.py  (unchanged validation)
```

`client.py` holds one async function per operation. Both front doors are
dispatch tables over that module. Neither contains logic the other lacks.

The alternative — an MCP server talking HTTP directly, with no shared module —
was rejected because it strands hooks, `just` recipes, and any non-MCP agent,
and because it forces every test to stand up an MCP client.

The alternative of importing `kraft.store` in-process was rejected outright: it
would put a second writer on `orchestrator.db` and bypass both the API's
validation and the event bus.

### 3.1 `src/kraft/client.py`

Owns exactly three concerns, once:

- **Base URL** — derived from `access.yaml` (`bind`, `port`), with `KRAFT_HOST`
  / `KRAFT_PORT` overriding, matching `cli._bind`.
- **Auth** — see §5.
- **Identity** — see §4.

It holds no business logic. Validation stays in `api.py`, where it is today and
where the UI already exercises it.

### 3.2 Front doors

`kraft mcp` — a stdio MCP server. One tool per client function; schemas
generated from the function signatures so a tool cannot drift from its
implementation. Intended to be registered as a subprocess of the agent.

`kraft <verb>` — the same functions as CLI subcommands, JSON on stdout. This is
what makes hooks, shell pipelines, and non-MCP agents work without a second
implementation.

`kraft` with no arguments continues to seed-and-serve, unchanged.

## 4. Identity: `resolve_context()`

Returns `(work_item_id | None, origin)` where `origin` is `"worker"` or
`"user"`.

Resolution order:

1. `$KRAFT_WORK_ITEM_ID`, if set → origin `worker`. The executor injects this;
   `adapters/subprocess.py:118` already merges an `env` dict into the child
   environment, so this is an existing seam.
2. Otherwise, walk cwd upward looking for a directory whose parent is
   `run_dirs.worktrees`; that directory's name is the work item id (see
   `executor.py:329`, `worktree = run_dirs.worktrees / work_item_id`) → origin
   `user`.
3. Otherwise `(None, "user")`.

Every tool taking a `work_item_id` treats it as optional and defaults to this.
Without it, a human in a worktree would have to look an id up in the UI before
approving a gate from chat, which defeats the feature.

This is pure path logic and holds no state.

**Ordering constraint.** `origin` is `worker` only when `KRAFT_WORK_ITEM_ID` is
set. A worker session missing that variable resolves as `user` inside its own
worktree, and would then hold self-approval rights over the item running it —
precisely what §6 rule 2 exists to prevent. Environment inheritance through the
agent's subprocesses makes injection reliable in practice, but it means phase 4
(worker-side MCP) must not ship without the env injection in the same change.
Until phase 4, no worker has MCP at all and the gap cannot be reached.

## 5. Auth

Kraft's existing posture (`src/kraft/auth.py`): auth is off on localhost and on
for anything else; binding off-loopback without a password is refused.

The client inherits it:

- **No password set** (the loopback default) — no auth, exactly the trust model
  the browser UI has today. No new surface.
- **Password set** — the client sends a bearer token read from
  `$KRAFT_HOME/run/mcp-token`, mode `0600`, generated on first `serve` if
  absent. `api.py` accepts either the existing session cookie or this token.

  The check goes in the single `_authenticate` middleware (`api.py:227-253`),
  and specifically **after** the `sec-fetch-dest` SPA-shell branch. That branch
  returns `index.html` for any GET claiming to be a document navigation; a
  bearer check placed before it would leave that path reachable, and one placed
  inside it would answer MCP calls with HTML instead of JSON.

The token is stored by SHA-256 like session tokens are, so the file is the only
copy. A full scoped-token subsystem was rejected as a subsystem built for a
single-user loopback tool.

## 6. Trust rules

These are the load-bearing part of the design. An MCP tool call can start work
that spends real tokens, and workers themselves are callers (§7).

1. **MCP-created items land paused.** `NewWorkItem` gains `autostart: bool =
   True`; the client sends `False`. Existing UI callers are unaffected by the
   default. The human starts the item from the board after seeing it.

   The mechanism is smaller than it sounds, and rests on an existing
   accident that must be pinned down. `POST /work-items` today is
   `executor.intake()` — which only INSERTs, with `status` hardcoded to
   `'active'` (`store.py:51`) — followed by `_spawn()` (`api.py:294`).
   `autostart=False` therefore means: insert with `status='paused'` and skip
   `_spawn`. Nothing else is needed, because `POST /work-items/{wid}/resume`
   already starts such an item correctly: at `api.py:629` it computes

   ```python
   start = next((i for i, n in enumerate(chain["nodes"]) if n["id"] == row["current_node_id"]), 0)
   ```

   and a never-started item has `current_node_id IS NULL`, so the `next()`
   default of `0` resolves to node zero. No new status, no new endpoint, and no
   new UI control — "the human starts it" is the Resume button that already
   exists.

   That default was not written for this purpose. A refactor of that expression
   would silently strand every agent-created work item with no existing test
   failing, so phase 2 adds one that asserts a `paused`, `current_node_id IS
   NULL` item resumes at node zero.

   **The API needs no new UI; the interface does.** `PausedCard.tsx` renders for
   any paused item and assumes the pause interrupted something:

   ```tsx
   const attempt = paused.length ? paused[0].attempt + 1 : 2;
   ...
   relaunches {paused[0]?.hook_point ?? item.current_node_id} as attempt {attempt}
   ```

   With no sessions and a NULL `current_node_id`, an agent-created item renders
   "relaunches  as attempt 2" under copy reading "the attempt was killed, and
   resuming launches a fresh one". Every word of that is false for work that has
   never run once.

   Phase 2 therefore branches the card on `current_node_id === null`: one
   **Start** button, no attempt hint, and copy that says this item is waiting to
   begin. A distinct `queued` status was considered and rejected — it would ripple
   through `store.py`, the board's filters, and every status check in the API for
   a state that behaves exactly like `paused` in all of them.
2. **A worker cannot act on its own work item.** For `approve_gate`,
   `reject_gate`, `steer`, `pause`, and `resume`, the client rejects when
   `origin == "worker"` and the target resolves to the caller's own id. An agent
   approving its own gate would collapse the human-gate model
   (`01_conceptual_model.md` §6), so the check sits at the boundary the agent
   cannot route around, not in a skill's prose.
3. **Worker-created items are attributed.** `parent_work_item_id` is stamped
   from the resolved context, so a runaway loop reads as a tree on the board
   rather than as anonymous rows.

Rule 2 is enforced client-side because the client is the only component that
knows `origin`. `api.py` sees an authenticated local caller either way.

## 7. Worker-side MCP

Sessions the executor starts receive `KRAFT_WORK_ITEM_ID` and
`KRAFT_SESSION_ID` per-invocation, through the existing `env` merge in
`adapters/subprocess.py:118`.

**The executor does not inject an MCP config.** An earlier draft of this section
said it would, which contradicts `01_conceptual_model.md` §1.2: the core
"hardcodes no vendor — not beads, not superpowers, not Claude Code, not Codex".
The only way to hand a config to Claude Code per-invocation is a `--mcp-config`
flag, and `adapters/agent.py` builds its command from `registry.yaml` precisely
so that the adapter does not know which vendor it is launching. Putting a
Claude-specific flag there would make the generic agent adapter vendor-aware.

It is also unnecessary. A worker runs with its cwd inside the worktree, so a
user-scope `kraft init` registration is already in effect for it — the tools are
there because the human installed them, not because Kraft smuggled them in.
Env injection is the part Kraft must do, because `KRAFT_WORK_ITEM_ID` is the
only thing that distinguishes a worker from a human (§4) and therefore the only
thing that makes the §6 rule 2 guard real.

It buys a worker the ability to search other repos, read its own item's history,
and file follow-up items instead of burying them in a session summary. The cost
is reentrancy, which §6 rules 2 and 3 exist to bound: a worker's writes create
new paused items, and its act-tools cannot touch the chain currently running it.

## 8. Install: `kraft init`

Two scopes, chosen per invocation.

- **Default (user scope)** — registers the server by shelling out to
  `claude mcp add --scope user kraft -- kraft mcp`, and writes the skills to
  `~/.claude/skills/kraft/`. Machine-wide; every repo gets Kraft tools with no
  per-repo files.
- **`--repo`** — writes `.mcp.json` and `.claude/skills/kraft/` into the cwd
  repo. Travels with the clone, allows per-repo customization.

User-scope registration delegates to the `claude` CLI rather than editing
`~/.claude.json` directly: that file is large, shared, agent-owned user state,
and hand-editing it is how an installer corrupts somebody's whole configuration.
The repo-scope `.mcp.json` is written directly because its schema is small,
documented, and the file belongs to the repo Kraft was pointed at. When `claude`
is not on `PATH`, `kraft init` prints the exact command instead of guessing —
a missing agent CLI is a thing to report, not to work around.

Skills are written as plain directories in both scopes; nothing there is shared
mutable state.

The skills are prose over the tools: when to create a work item versus keep
working in the current session, how to hand off after spec and plan are done,
how to read the board.

### 8.1 Amendment to conceptual model §1.4

§1.4 currently reads that the app's process is "never written into `AGENTS.md`,
`CLAUDE.md`, or any other ambient, repo-owned file." `kraft init --repo` writes
repo-owned files, so the principle is narrowed rather than dropped:

> §1.4 governs what Kraft's executor injects into sessions it starts: that
> context is per-invocation and never persisted to a repo-owned file. Files a
> human explicitly opts into by running `kraft init --repo` are that human's
> choice, not Kraft leaking its process into a repo.

The distinction that matters is *ambient versus opted-into*. An executor
silently editing `CLAUDE.md` is the failure §1.4 was written against; a human
running an installer is not.

`01_conceptual_model.md` §1.4 is updated in phase 2, in the same change that
ships `kraft init --repo`. The amendment cannot lag the code that depends on
it.

## 9. Phasing

Each phase is usable on its own.

1. **Read path.** `client.py`, `resolve_context()`, `kraft mcp` with
   `list_work_items`, `get_work_item`, `search`. Nothing mutates; proves the
   transport and the identity logic.
2. **Write path.** `create_work_item` (paused), `ensure_repo` (probe, then
   register if the cwd repo is unknown), plus the `autostart` field on
   `NewWorkItem`.
3. **Act path.** `approve_gate`, `reject_gate`, `pause`, `resume`, and the §6
   rule-2 self-action guard.

   **No standalone `steer` tool.** `POST /work-items/{wid}/steer` refuses
   anything that is not already paused (`api.py:628`), so the one situation an
   agent would reach for it — a running item going wrong — is the one situation
   it returns 409. The API comment on `/pause` says why: "there is no stdin
   channel into a one-shot agent CLI, so pause and steer are one mechanism".
   `POST /resume` already carries `steer` in its body, so the honest surface is
   `pause()` then `resume(steer=...)`, which is exactly what the UI's
   `PausedCard` does. Exposing `/steer` separately would add a tool whose only
   distinct use is an error.
4. **Worker-side MCP.** Executor injects `KRAFT_WORK_ITEM_ID` and
   `KRAFT_SESSION_ID` into the sessions it starts (§7). This is what turns the
   §6 rule 2 guard from theatre into enforcement, so it must not be split from
   phase 3.
5. **Handoff — superseded, not shipped.** This phase was built and then removed
   from the branch before merge. `docs/superpowers/specs/2026-09-05-intake-from-existing-artifacts-design.md`
   (bead `Kraft-dgh`, now merged to `main` as "Start a work item from a spec and plan that already exist") covers the same ground and
   decides against the mechanism this phase used:

   > Attachments trim the chain; there is no start-node selector. Attaching a
   > plan is the same statement as "skip the plan phase", so it is one action,
   > not two that have to agree.

   Three concrete reasons that design wins, recorded so this is not re-proposed:

   * **A start-node skip hands the implementer nothing.** `executor.py:110`
     builds the agent instruction as `instruction_override or
     work_item_row["title"]`. Skipping to the implementation node launches an
     agent that has never seen the spec or the plan — it receives the title and
     guesses. The attachment design injects the document paths into the
     instruction, which is the entire point of a handoff.
   * **A skipped node is still a node.** `WorkItemDetail.tsx:90` renders
     `chain_definition.nodes`, so a positioned item shows `spec` and `plan`
     permanently un-run. Trimming at `materialize()` stores the short chain, and
     the progress display, gate list and timeline are correct with no frontend
     change.
   * **Node ids are free text; gate names are a closed vocabulary.** Trimming on
     `gate_after ∈ GATE_NAMES` keeps working on a template that calls its node
     `specification`. A `start_node` selector validates against that template's
     own ids and skips the wrong thing without complaining.

   The MCP handoff tool returns as a thin wrapper over `attachments` once
   `Kraft-dgh` lands. The removed implementation is kept on the branch
   `parked/agent-integration-phase-5` for reference.

`kraft init` and the skills ship with phase 2, when there is something worth
installing. The §8.1 amendment to `01_conceptual_model.md` ships in that same
phase, because `--repo` writes repo-owned files the current §1.4 forbids.

## 10. Testing

- `client.py` against the existing FastAPI `TestClient` — no MCP client in the
  loop.
- `resolve_context()` table-tested over synthetic paths and env; pure logic.
- Trust rules (§6) tested as client-level rejections, one case per rule.
- One real-uvicorn smoke test for `kraft mcp`. `TestClient` runs in-process and
  will not catch a missing transport dependency — the same trap that hid the
  absent WebSocket library until `tests/test_ws.py` ran against real uvicorn.
- The `--repo` installer asserts on the file set it writes, into `tmp_path`.

## 11. Rejected

- **MCP-only, no CLI** — strands hooks and non-MCP agents; forces an MCP client
  into every test.
- **CLI-only, skills shell out** — loses structured schemas and the tool
  discovery an agent gets from MCP for free.
- **In-process client** — second writer on `orchestrator.db`, bypasses
  validation and the event bus.
- **Scoped API tokens** — a subsystem for a single-user loopback tool.
- **Autostart by default** — an agent loop could queue work unattended. A
  `policy.yaml` knob was considered and deferred; it can be added once the
  paused default has been lived with.
