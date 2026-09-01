# Kraft Walking Skeleton — Design

**Status:** design agreed, not yet built
**Date:** 2026-09-01
**Bead:** Kraft-rnx
**Consolidated specs:** `docs/consolidated/` (this slice implements a subset of `02_orchestrator_core.md` and `03_plugin_adapters.md`)

---

## 0. Purpose

First build slice of the Kraft orchestrator. A vertical cut through the
orchestrator core that runs one work item end to end through a three-node chain,
using two real external plugins.

It exists to put the three riskiest design bets under an automated test before
any more of the system is built:

1. **Chain executor** — materialize a chain from a template, walk its nodes in
   order, fan out a node's tasks, stop cleanly on failure.
2. **Detached, reattachable subprocess** — a plugin process launched detached
   survives an orchestrator crash and is reattached (not re-spawned) on restart.
3. **Per-invocation context injection** — the agent receives its task only via an
   injected system prompt, never via a file written into the target repo.

Everything not serving one of those three goals is deferred (§8).

## 1. Scope

### In

- The `quick-task` chain, cut to `env_setup → implementation → verify`. No MR,
  no CI, no merge.
- SQLite state model: `work_items`, `events`, `worker_sessions` (§2).
- Event table with global `seq` ordering; polled delivery (no WebSocket).
- Subprocess adapter: detached launch, log redirection, result resolution,
  `worker_sessions` lifecycle.
- Agent adapter (wraps the subprocess adapter): builds the `claude` headless
  command, injects task/context via `--append-system-prompt`.
- Beads adapter: thin `bd --json` wrapper, called directly by the executor at
  intake and completion (not modelled as chain hooks).
- Chain executor: per-work-item asyncio task, walks nodes, `gather()`s node
  tasks, transitions the work item to `needs_human` on any task failure.
- Reattach: startup scan of non-terminal `worker_sessions`, psutil PID +
  create-time identity check, executor resume.
- Minimal FastAPI surface bound to `127.0.0.1`, no auth: `POST /work-items`,
  `GET /work-items/{id}`, `GET /work-items/{id}/events`,
  `GET /worker-sessions/{id}/log`, `GET /health`.
- A checked-in fixture repo (failing test) and a fake-agent script for hermetic
  tests.

### Out

See §8 for the full deferred list with spec pointers. Headlines: gates,
backward-motion coordinator / fix loops, policy engine as a component, YAML cap
config, pause/steer/resume, WebSocket + UI, MCP status-callback server, auth,
indexer/search, GitLab, cross-repo federation, Codex.

### Deviations from the consolidated specs

Recorded here rather than edited into `docs/consolidated/` (those are still under
review — bead Kraft-q0o):

- **Python 3.14+** (consolidated `02` §2 says `3.12+`, a floor written months
  ago with no design reason; local default is 3.14.7).
- `on.test.run` runs as a direct `pytest` subprocess, not routed through the
  execution worker plugin. Still a real `worker_sessions` row + subprocess +
  result, so the executor path is fully exercised.
- `on.intake` / `on.completed` are not hooks in the skeleton; the executor calls
  `bd` directly.
- `env_setup` does `git worktree add` itself; the spec's env-prepare-as-repo-
  Makefile-convention plugin is deferred.
- `GET /work-items/{id}` returns **all** `worker_sessions` for the item, not the
  node-scoped set (`02` addendum §4) — better for debugging a skeleton.
- Runtime state lives in a gitignored `.kraft-run/` in this repo, not
  `~/.orchestrator/` (`02` §2) — easier to inspect and wipe between test runs.
- Caps (`MAX_ATTEMPTS`, `WALL_CLOCK_MIN`) are hardcoded constants with a
  `ponytail:` comment pointing at the future YAML cap schema (`02` §7.1, §13).
  No retry loop is wired — one attempt per task, failure → `needs_human`.

## 2. State model — `.kraft-run/orchestrator.db`

SQLite, WAL. Every state change and its event are written in **one transaction**
through a single asyncio-serialized writer, so a row and its event never diverge
(`02` §4).

Dropped from the consolidated schema: `retry_counters` (caps hardcoded, an
`attempt` int on the session covers the skeleton), `auth_sessions` (no auth),
`work_item_repos` (no federation).

### `work_items`

| col | type | notes |
|---|---|---|
| `id` | TEXT PK | uuid |
| `bead_id` | TEXT | bd issue id from `bd create` at intake |
| `title` | TEXT | |
| `repo` | TEXT | absolute path to the target repo |
| `chain_template` | TEXT | `"quick-task"` |
| `chain_definition` | TEXT | JSON — materialized node list, frozen at intake |
| `current_node_id` | TEXT | |
| `status` | TEXT | CHECK `active \| needs_human \| completed` |
| `created_at` | TEXT | ISO 8601 |
| `updated_at` | TEXT | ISO 8601 |

### `events`

| col | type | notes |
|---|---|---|
| `seq` | INTEGER PK AUTOINCREMENT | global order; drives `after_seq` catch-up |
| `work_item_id` | TEXT | FK `work_items.id` |
| `type` | TEXT | see list below |
| `payload` | TEXT | JSON |
| `created_at` | TEXT | ISO 8601 |

Event types: `work_item_created`, `chain_loaded`, `node_started`,
`node_completed`, `worker_session_started`, `worker_session_exited`,
`session_reattached`, `session_unknown`, `work_item_needs_human`,
`work_item_completed`.

### `worker_sessions`

| col | type | notes |
|---|---|---|
| `id` | TEXT PK | uuid |
| `work_item_id` | TEXT | FK `work_items.id` |
| `node_id` | TEXT | |
| `hook_point` | TEXT | `on.env.prepare`, `on.implementation.start`, `on.test.run` |
| `pid` | INTEGER | nullable until spawned |
| `pid_start_time` | REAL | psutil `create_time()` — PID-reuse guard |
| `log_path` | TEXT | `.kraft-run/logs/{id}.log` |
| `result_path` | TEXT | `.kraft-run/results/{id}.json` |
| `status` | TEXT | CHECK `pending \| running \| done \| failed \| capped_out \| unknown` |
| `attempt` | INTEGER | written as `1`; no retry loop in the skeleton |
| `created_at` | TEXT | ISO 8601 |
| `exited_at` | TEXT | nullable |

`capped_out` and `paused` are in the CHECK constraint for forward-compatibility
but are not produced by skeleton code paths.

## 3. Chain executor + template format

### `templates/quick-task.yaml`

```yaml
id: quick-task
nodes:
  - { id: env_setup,      tasks: [on.env.prepare],         gate_after: null }
  - { id: implementation, tasks: [on.implementation.start], gate_after: null }
  - { id: verify,         tasks: [on.test.run],            gate_after: null }
```

### `templates/registry.yaml`

Skeleton stand-in for the full plugin registry (`02` §6). Maps hook points to
adapter bindings.

```yaml
hooks:
  on.env.prepare:          { kind: builtin,    handler: env_setup }
  on.implementation.start: { kind: agent,      command: claude }
  on.test.run:             { kind: subprocess, command: [pytest, -q] }
```

The `command` for `on.implementation.start` is overridable to the fake-agent
script in tests.

### Template validation

On load, every hook referenced by every template must exist in
`registry.yaml`. An unknown hook marks that template invalid; it is excluded
from the intake-resolvable set and listed in `GET /health`. Other templates are
unaffected. This is the skeleton's Chain Validator (`02` §12), startup tier
only.

### Executor loop

Per work item, one asyncio task:

1. Materialize the chain from the template → write `chain_definition`, set
   `current_node_id` to the first node, emit `chain_loaded`.
2. For each node in order:
   1. Emit `node_started`.
   2. `asyncio.gather()` every task in `node.tasks`, each launched through its
      bound adapter. (Skeleton nodes are single-task; the `gather` path is built
      regardless.)
   3. All tasks resolve `done` → emit `node_completed`, advance
      `current_node_id` in the same transaction.
   4. Any task resolves `failed` → set `status = needs_human`, emit
      `work_item_needs_human`, stop the loop.
3. Last node completed → set `status = completed`, emit `work_item_completed`,
   call `bd close <bead_id>`.

No automatic retry, no fix loop, no gate handling.

## 4. Plugin adapters

### `adapters.subprocess`

The launch/detach/result contract (`02` §6):

1. Write the `worker_sessions` row `status = pending` **before** spawning — the
   reattach scan depends on the row existing first.
2. `Popen(cmd, cwd=<worktree>, start_new_session=True,
   stdout=stderr=<log_path>)` — own process group, survives orchestrator death.
3. Pass `KRAFT_RESULT_PATH=<result_path>` in the environment.
4. Record `pid` and psutil `create_time()`; set `status = running`, emit
   `worker_session_started`.
5. Await exit via `asyncio.to_thread(proc.wait)`.
6. **Resolve the result:** if the result file exists and is non-empty, parse
   `{ status, ... }` from it; otherwise `exit 0 → done`, non-zero → `failed`.
   Set `status`, `exited_at`; emit `worker_session_exited`.

### `adapters.agent`

Wraps `adapters.subprocess` for `claude`:

- Command: `claude -p "<task instruction>" --append-system-prompt
  "<injected context>" --output-format json`, run with `cwd` set to the
  worktree.
- **Injected context** — system prompt only, never a repo file: the work-item
  title, the task instruction, the repo path. `CLAUDE.md` in the target repo is
  left untouched. This is the context-injection boundary (`01` §10) under test.
- Result: parse `claude`'s JSON envelope from the captured log; exit code as
  fallback. The skeleton trusts stdout over asking the model to write a file.

### `adapters.beads`

Thin `bd --json` wrapper. **Not** a chain node in the skeleton — the executor
calls it directly:

- Intake: `bd create --title=… --json` → `bead_id`.
- Completion: `bd close <bead_id>`.

Models the spec's work-graph plugin closely enough to prove the generic
subprocess path with a second real binary, without building `on.intake` /
`on.completed` as hooks yet.

### `env_setup` (builtin)

Computes the worktree path from the work-item id, then calls
`adapters.subprocess` with
`git worktree add .kraft-run/worktrees/{id} -b kraft/{id}`. Gets a
`worker_sessions` row like any task — one executor code path for all node tasks.

## 5. Reattach

On startup, **before the API accepts new work** (`02` §8):

1. Query `worker_sessions` where `status IN (pending, running)`.
2. Per row:
   - **`pending`** (row written, spawn never confirmed): set `status = unknown`,
     emit `session_unknown`, set the parent work item to `needs_human`.
   - **`running`** with a `pid`:
     - `psutil.pid_exists(pid)` **and** `Process(pid).create_time()` matches
       `pid_start_time` → still our process, alive → **reattach**: a background
       `asyncio.to_thread` waits on the pid, then runs the normal result
       resolution. Emit `session_reattached`.
     - pid gone, or `create_time` mismatch (PID reused) → it exited while the
       orchestrator was down. Result file present → resolve from it and emit
       `session_reattached`; otherwise set `status = unknown` and emit
       `session_unknown`.
3. **Resume the executor** for each `work_items.status = active`: rebuild the
   per-work-item asyncio task from `chain_definition` + `current_node_id`.
   - A live or just-resolved session in the current node → await it, then
     continue walking.
   - No session rows for the current node (crashed between nodes) → launch the
     current node's tasks fresh. Safe because `current_node_id` only advances in
     the same transaction as `node_completed`.
4. Reattach never touches `attempt` (`02` §8).

## 6. API + events

Bind `127.0.0.1` only. No auth (`02` §9 engages auth only on LAN bind). No
WebSocket.

| Endpoint | Behaviour |
|---|---|
| `POST /work-items` | Body `{ title, repo?, chain_template? }`. `repo` defaults to the fixture path; `chain_template` defaults to `quick-task`. Validate the template against the registry → `bd create --json` → `bead_id` → insert `work_items` (`status = active`, chain materialized, `current_node_id` = first node) → emit `work_item_created` + `chain_loaded` → spawn the executor task → `201 { id, bead_id, status, chain_definition, current_node_id }`. Invalid template → `422`. |
| `GET /work-items/{id}` | Full `work_items` row + all `worker_sessions` for the item. |
| `GET /work-items/{id}/events?after_seq=N` | `[{ seq, type, payload, created_at }]` ordered by `seq`, `seq > N`. Primary progress-tracking mechanism. |
| `GET /worker-sessions/{id}/log` | `text/plain`, raw log file contents, no pagination (`02` §10.1). |
| `GET /health` | `{ status, invalid_templates: [...], reattach_summary: {...} }`. Surfaces template-validation problems and the last startup reattach outcome. |

## 7. Fixture + tests

### `fixtures/sample-repo/`

Plain file tree, **no `.git`** (avoids an embedded repo inside Kraft). A pytest
fixture copies it to a tmp dir and runs `git init` + initial commit at test
start; that tmp repo is the `repo` passed to `POST /work-items`.

```
calc.py        →  def add(a, b): return a - b      # bug: should be +
test_calc.py   →  assert add(2, 3) == 5            # fails until fixed
```

Task instruction: *"make the failing test pass"*. `verify` runs `pytest -q` in
the worktree.

### `fixtures/fake-claude.sh`

A script the registry can point at instead of `claude`. Modes via env var:
patch `calc.py` and emit a success envelope, or no-op (leave the bug in place).

### Tests (pytest)

| test | real `claude`? | asserts |
|---|---|---|
| `test_template_validation` | no | a template with an unknown hook is excluded and listed in `/health` |
| `test_chain_materialization` | no | `POST` → `chain_definition` matches the template, `current_node_id = env_setup` |
| `test_subprocess_detach` | no | orchestrator process exit leaves a launched child alive (`start_new_session`) |
| `test_verify_failure` | fake (no-op) | `verify` fails → `status = needs_human`, chain stops, `current_node_id` does not advance past `verify` |
| `test_happy_path` | yes (`@e2e`) | `POST` → poll events → `work_item_completed`; `calc.py` is fixed; the `bd` issue is closed; `CLAUDE.md` in the target repo is untouched |
| `test_reattach` | yes (`@e2e`) | `SIGKILL` the orchestrator mid-`implementation` → restart → `session_reattached` fires, the `claude` pid is unchanged (not re-spawned), the chain finishes to `completed`. The test polls for `worker_session_started` and confirms the process is alive before killing; if `claude` finishes too fast to catch, the task instruction is padded so the run is long enough to interrupt reliably. |

`@pytest.mark.e2e` tests skip when `claude` is not on `PATH`. The hermetic tests
are the CI gate; e2e tests run locally.

## 8. Build order, error handling, deferred

### Build order

Hermetic tests are written alongside each module.

1. `kraft.db` — schema, migrations, single-writer queue
2. `kraft.events` — append-in-transaction, read `after_seq`
3. `kraft.templates` — load, validate, materialize
4. `kraft.adapters.subprocess` — `worker_sessions` lifecycle, detach, result resolution
5. `kraft.adapters.agent` + `kraft.adapters.beads`
6. `kraft.executor` — walk nodes, `gather`, `needs_human` on failure
7. `kraft.reattach` — startup scan, psutil identity check, executor resume
8. `kraft.api` — FastAPI endpoints + `/health`
9. e2e tests — `test_happy_path`, `test_reattach`

### Error handling

- adapter binary missing / non-zero exit / unparseable result → session
  `failed` → work item `needs_human`
- `git worktree add` fails → `env_setup` task `failed` → `needs_human`
- template invalid at intake → `POST` returns `422`
- crash mid-transaction → SQLite atomic rollback; event and row never diverge
- orphaned worktrees after `needs_human` → left in place for inspection;
  cleanup is deferred

### Deferred (with consolidated-spec pointers)

| Deferred | Spec |
|---|---|
| Gates (spec / plan / chain-review / human-review) | `01` §8 |
| Backward-motion coordinator + fix loops | `02` §7.2 |
| Policy engine as a component + YAML cap config | `02` §7.1, §13 |
| Pause / steer / resume | `02` §10.2 |
| WebSocket transport + normalized client store + UI | `05` |
| MCP status-callback server | `03` §1 |
| Auth layer | `02` §9 |
| Indexer + search | `04` |
| GitLab adapter / `open_mr` / `mr_checks` / `merge` | `03` §6, `06` §3 |
| Cross-repo federation / `work_item_repos` / hydration hub | `06` |
| Codex execution worker | `03` §7 |
| Env-prepare as a repo Makefile-convention plugin | `03` §5a |
| `on.intake` / `on.completed` as real hooks | `02` §10.1 |
| Full `default.yaml` template | `01` §3.2 |
