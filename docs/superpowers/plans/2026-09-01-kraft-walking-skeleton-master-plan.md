# Kraft Walking Skeleton — Master Plan

**Status:** active
**Date:** 2026-09-01
**Design:** `docs/superpowers/specs/2026-09-01-kraft-walking-skeleton-design.md`
**Epic:** Kraft-rnx

---

## 0. What this is

An index, not a design. The walking skeleton is built as **3 chunks**, each with
its own mini-spec, its own implementation plan, and its own execution thread.
This keeps any single thread's context scoped to one chunk instead of all 9
build steps.

The chunks form a hard dependency chain — **A → B → C**, sequential, not
parallel. B needs A's schema and event bus; C needs B's adapters and executor.

## 1. Chunks

| Chunk | Bead | Design sections covered | Depends on |
|---|---|---|---|
| **A — Foundation** | Kraft-rnx.1 | §2 state model, §3 templates + validation, event bus | — |
| **B — Execution** | Kraft-rnx.2 | §3 executor loop, §4 adapters | A |
| **C — Resilience + API** ✅ | Kraft-rnx.3 | §5 reattach, §6 API, §7 fixture + tests | B |

### Chunk A — Foundation

**Modules:** `kraft.db`, `kraft.events`, `kraft.templates`

**Scope:**
- SQLite schema (`work_items`, `events`, `worker_sessions`), migrations, WAL,
  single asyncio-serialized writer that writes a row change + its event in one
  transaction.
- `kraft.events`: append (inside the writer's transaction), read `after_seq`.
- `kraft.templates`: load `templates/*.yaml` + `templates/registry.yaml`,
  validate every referenced hook exists in the registry (startup-tier Chain
  Validator), materialize a chain (`chain_definition` JSON) from a template.

**Out:** anything async beyond the writer queue, any subprocess, the executor,
the API.

**Done when:**
- Migrations create the schema from empty; re-running is a no-op.
- A row update + event append are atomic (test: kill mid-write → neither lands).
- `after_seq` reads return events in `seq` order, exclusive of the given seq.
- A template with an unknown hook is rejected with a clear error and excluded
  from the resolvable set; valid templates still resolve.
- Materializing `quick-task.yaml` produces the 3-node `chain_definition` with
  `current_node_id` unset (executor sets it).
- Hermetic tests green: `test_template_validation`, `test_chain_materialization`,
  plus schema/atomicity/`after_seq` unit tests.

### Chunk B — Execution

**Modules:** `kraft.adapters.subprocess`, `kraft.adapters.agent`,
`kraft.adapters.beads`, `kraft.executor`

**Scope:**
- `adapters.subprocess`: write `worker_sessions` row `pending` before spawn;
  `Popen(start_new_session=True)` with log redirection and `KRAFT_RESULT_PATH`;
  record `pid` + psutil `create_time()`; await via `asyncio.to_thread`; resolve
  result (result file → parse; else exit code); emit
  `worker_session_started` / `worker_session_exited`.
- `adapters.agent`: wrap `subprocess` for `claude -p … --append-system-prompt …
  --output-format json`, `cwd` = worktree; inject task/title/repo-path via the
  system prompt only; parse result from the log, exit code as fallback.
- `adapters.beads`: `bd create --json` at intake, `bd close` at completion.
  Called directly by the executor, not a chain node.
- `env_setup` builtin: compute worktree path, call `adapters.subprocess` with
  `git worktree add`.
- `kraft.executor`: per-work-item asyncio task — materialize (via A), walk nodes
  in order, `asyncio.gather()` node tasks, advance `current_node_id` +
  `node_completed` in one transaction, `needs_human` + stop on any task failure,
  `completed` + `bd close` after the last node.

**Out:** reattach, the HTTP API. The executor is driven directly from test code
in this chunk.

**Done when:**
- `adapters.subprocess` launches a detached child that outlives the parent
  process (`test_subprocess_detach`).
- Result resolution handles both a JSON result file and a bare exit code.
- The executor runs `quick-task` end to end when driven in-process with the
  fake agent: `env_setup` → `implementation` → `verify` → `completed`, bead
  created and closed.
- `test_verify_failure`: fake no-op agent → `verify` fails → `needs_human`,
  `current_node_id` stops at `verify`.
- Hermetic tests green (fake agent; no real `claude` needed in this chunk).

### Chunk C — Resilience + API

**Modules:** `kraft.reattach`, `kraft.api`; `fixtures/sample-repo/`,
`fixtures/fake-claude.sh`

**Scope:**
- `kraft.reattach`: on startup, before the API accepts work — scan
  `pending`/`running` `worker_sessions`; `pending` → `unknown` + parent
  `needs_human`; `running` → psutil PID + `create_time` identity check →
  reattach (background wait + result resolution) or resolve-from-file or
  `unknown`; then rebuild the per-work-item asyncio task from `chain_definition`
  + `current_node_id` and continue walking. Never touches `attempt`.
- `kraft.api`: FastAPI on `127.0.0.1`, no auth. `POST /work-items`,
  `GET /work-items/{id}`, `GET /work-items/{id}/events?after_seq=`,
  `GET /worker-sessions/{id}/log`, `GET /health`.
- Fixture repo (failing test) + fake-agent script + the pytest fixture that
  `git init`s a tmp copy.
- e2e tests `test_happy_path` and `test_reattach` (`@pytest.mark.e2e`, skip if
  `claude` absent).

**Done when:**
- `test_happy_path` (real `claude`): `POST` → poll events → `work_item_completed`;
  `calc.py` fixed; bead closed; target repo's `CLAUDE.md` untouched.
- `test_reattach` (real `claude`): `SIGKILL` mid-`implementation` → restart →
  `session_reattached`, same `claude` pid, chain finishes to `completed`.
- `GET /health` surfaces invalid templates and the last reattach summary.
- All hermetic tests from A and B still green.

## 2. Workflow per chunk

1. **Mini-spec** — extract the relevant design sections into
   `docs/superpowers/specs/2026-09-01-skeleton-<letter>-<name>.md`, add the
   chunk-local "done" criteria from §1 above, resolve any chunk-local ambiguity.
2. **`writing-plans`** — turn the mini-spec into an implementation plan.
3. **New thread** — `executing-plans` against that plan, with review
   checkpoints. Claim the chunk bead (`bd update <id> --claim`), close on done.
4. Return here, start the next chunk.

## 3. After the skeleton

The skeleton proves the three risk goals. The next master plans (own
spec → plan → implement cycles, in this rough order) come off the deferred table
in the design doc §8:

The walking skeleton is now green — all 3 chunks (A, B, C) are built and the
hermetic suite passes (70 passed / 1 skipped) — so the deferred master plans
below can begin.

1. ✅ Full `default.yaml` + gates (`01` §8)
2. ✅ Policy engine as a component + backward-motion coordinator + fix loops
   (`02` §7)
3. ✅ WebSocket transport + the React UI (`05`) — shipped as **3A** (WS
   backend: `Database.on_commit`, `Broadcaster`, `WS /ws/events`,
   `GET /work-items` + `/templates`, static SPA serving) and **3B**
   (`frontend/` Vite + React 18 + TS SPA: Board, New Work Item modal, Work
   Item Detail with chain stepper / current-node panel / event timeline /
   log modal / inline gates; one global WS with backoff reconnect;
   Playwright e2e). Deferred UI slices (auth, pause/steer, search panel,
   cross-repo federation panels) tracked against their own efforts.
4. Indexer + search (`04`) — **4A shipped**: second SQLite index DB,
   `.engineering/**/*.md` artifact ingestion (open-tag `kind`, content-hash
   rename/delete diff), event + startup-scan triggers (no poll loop), FTS
   `GET /search` / `GET /documents/{id}` / `POST /index/rescan`. 4B
   (session-summary ingestion + `document_links` + `/work-items/{id}/documents`,
   blocked on the Execution Worker) and 4C (`sqlite-vec` + local embedding +
   hybrid search) tracked as Kraft-bj9.2 / Kraft-bj9.3.
5. GitLab adapter + `open_mr` / `mr_checks` / `merge` (`03` §6)
6. Cross-repo federation (`06`)

Each is a separate effort; none starts until the skeleton is green.
