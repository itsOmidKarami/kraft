# Skeleton Chunk C — Resilience + API (mini-spec)

**Status:** implemented 2026-09-02
**Date:** 2026-09-01
**Bead:** Kraft-rnx.3
**Parent design:** `docs/superpowers/specs/2026-09-01-kraft-walking-skeleton-design.md` (§5 reattach, §6 API, §7 fixture + tests)
**Master plan:** `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` (Chunk C)
**Builds on:** Chunk A (`kraft.db`, `kraft.events`, `kraft.templates`) + Chunk B
(`kraft.store`, `kraft.paths`, `kraft.adapters.*`, `kraft.builtins`,
`kraft.executor`). Chunk B handoff §13 (a/b/c) is the starting point.

---

## 0. Scope

The last skeleton chunk. Turns the test-driven execution core into a running
process: a long-lived HTTP orchestrator that survives its own crash and
reattaches detached worker processes on restart.

**Modules (all new, `src/kraft/`):**

- `kraft.reattach` — startup scan of non-terminal `worker_sessions`, psutil PID +
  `create_time` identity check, adopt / resolve-from-file / mark-unknown.
- `kraft.executor.resume` — new entrypoint beside `run`: continue a chain from
  `current_node_id` without restarting it. `run` is **not** modified.
- `kraft.api` — FastAPI app bound to `127.0.0.1`, no auth, 5 endpoints + a
  lifespan that runs reattach before serving.
- `kraft.__main__` — `python -m kraft` → uvicorn, env-configured. Exists so the
  e2e / reattach tests can launch the orchestrator as a real subprocess and
  `SIGKILL` it.

**Fixtures:**

- `fixtures/fake-claude.sh` — controllable fake agent (3 modes), used by the
  hermetic API + reattach tests.

**Stack additions:** `fastapi`, `uvicorn`, `httpx` (test client). No other new
runtime deps. Tests stay hermetic except `test_e2e_happy_path`, which is gated
(see §7).

**Out (post-skeleton, design §8):** everything already deferred — gates,
fix loops, policy engine, WebSocket/UI, auth, GitLab, federation, Codex.

---

## 1. Chunk B handoff — how C closes each gap

| Handoff §13 | Chunk C resolution |
|---|---|
| **(a)** recovery semantics unwritten | §2 below defines every non-terminal session state's meaning to reattach. |
| **(b)** `run` not resumable / idempotent | New `executor.resume` entrypoint (§3). `run` stays the fresh-start path; `resume` is the continue-from-`current_node_id` path. Neither calls the other. |
| **(c)** reattach has no `Registry` | The API/reattach layer **always reconstructs** the `Registry` from `templates/registry.yaml` at startup (it is validated there anyway, design §3). Bindings are **not** persisted on the work-item row. |

---

## 2. `kraft.reattach`

```
async def reattach(db, run_dirs, registry: Registry) -> ReattachSummary
```

Runs **once**, in the API lifespan, **before the server accepts connections**
(design §5, `02` §8). Never touches `attempt` (design §5.4).

### 2.1 Session scan

Query `worker_sessions WHERE status IN ('pending', 'running')`. Per row:

| Row state | Action |
|---|---|
| `pending` (row written, `Popen` never confirmed — pid NULL) | `store.session_exited(sid, 'unknown')`* → emit `session_unknown` → set parent work item `needs_human`. A detached child may exist unrecorded; it is abandoned (skeleton limitation, design §8 error-handling). |
| `running`, pid alive **and** `psutil.Process(pid).create_time() == pid_start_time` | **Adopt.** Emit `session_reattached` now (process still up). Spawn a background `asyncio.create_task` that `await asyncio.to_thread(_wait_pid, pid)` then resolves: **result file present → parse `{status}`; absent/empty → `failed`** (a reattached non-child's exit code is unreadable — `psutil.Process.wait()` returns `None` for non-children, so a worker that writes no result file cannot be verified after a restart). Then `worker_session_exited`. Task handle returned in `adopted` (§2.3). The skeleton's fake agent (§5.1) always writes the result file, so the happy path resolves cleanly. |
| `running`, pid gone **or** `create_time` mismatch (PID reused), **result file present + non-empty** | Resolve from the file → `store.session_exited(sid, <parsed>)`, emit `session_reattached`. |
| `running`, **conservative fallback** — pid identity unconfirmable (`pid_start_time` NULL, or mismatch) **and** no usable result file | `store.session_exited(sid, 'unknown')` → emit `session_unknown` → parent `needs_human`. Never wait on an unverified PID. |

\* `session_exited` currently only accepts `'done' | 'failed'` in its event
payload but writes whatever status it is given to the row (the CHECK constraint
already allows `'unknown'`). Add `'unknown'` as an accepted value and a dedicated
`session_unknown` event emitter to `kraft.store` (small change, §6).

`_wait_pid(pid)`: `psutil.Process(pid).wait()` (works on non-children, unlike
`os.waitpid`); `psutil.NoSuchProcess` → return immediately.

### 2.2 Executor resume

For each `work_items WHERE status = 'active'` (schema uses `'active'`, not the
design prose's `active`), spawn one `executor.resume` task (§3), passing the
`adopted` map. Store the task in `app.state.tasks[wid]`.

### 2.3 `ReattachSummary`

```
@dataclass
class ReattachSummary:
    scanned: int
    adopted: list[str]          # session ids still running, waited in background
    resolved_from_file: list[str]
    unknown: list[str]          # sessions -> parent forced needs_human
    resumed_work_items: list[str]
```

Returned to the lifespan, stashed on `app.state.reattach_summary` for `/health`.
Also returns `adopted_tasks: dict[str, asyncio.Task]` keyed by **session id** —
not part of the serialisable summary, handed straight to `resume`.

---

## 3. `kraft.executor.resume`

```
async def resume(
    db, run_dirs, *, work_item_id: str, registry: Registry,
    adopted: dict[str, asyncio.Task], bd_cwd: str | None = None,
) -> str            # 'completed' | 'needs_human'
```

Continue-from-`current_node_id`. **Does not** call `store.load_chain` (that
resets `current_node_id`) and **does not** re-enter completed nodes.

1. Read the row. `chain = json.loads(row["chain_definition"])`,
   `nodes = chain["nodes"]`, `cur = row["current_node_id"]`.
   - `cur is None` → chain never loaded; nothing ran. Fall back to `run()`
     semantics is wrong here — instead call `store.load_chain` + start at node 0.
     (Only reachable if the crash landed between `create_work_item` and the
     first `load_chain`; rare but defined.)
2. `start = index of cur in nodes`. Walk `nodes[start:]`:
   1. **Current node only** (`node["id"] == cur`), decide per task:
      - A non-terminal `worker_sessions` row exists for `(work_item_id, node_id)`
        **and** its id is in `adopted` → `await adopted[sid]`, then read the row's
        final `status` (`done` → ok, else → `needs_human`, stop).
      - A **terminal** session row exists for the node (reattach already resolved
        it, e.g. from file) → read its `status`, same branching. Do **not**
        re-dispatch.
      - No session row for the node → dispatch fresh via the existing
        `_dispatch` (crash landed between `enter_node` and the first
        `create_session`; safe because `current_node_id` only advances with
        `node_completed`).
      - On success → `store.complete_node`, advance.
   2. **Subsequent nodes** (`node["id"] != cur`) → identical to `run`'s loop:
      `enter_node` → `gather(_dispatch...)` → `complete_node` or
      `mark_needs_human`.
3. Last node done → `store.mark_completed` → `beads.complete` (best-effort, same
   swallow-and-log as `run`). Return `'completed'`.

### 3.1 Refactor note

`run`'s per-node body (steps enter → gather → evaluate → complete/needs_human) is
duplicated by `resume` step 2.ii. Extract a private
`async _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree) -> str`
(`'ok' | 'needs_human'`) used by both. Keep it in `kraft.executor`. This is the
one allowed refactor of Chunk B code — it is in service of the current goal and
removes a copy, not speculative.

---

## 4. `kraft.api`

FastAPI, `uvicorn`. Bind `127.0.0.1` only, no auth (`02` §9 — auth engages on LAN
bind only). No WebSocket.

### 4.1 Lifespan

```
@asynccontextmanager
async def lifespan(app):
    app.state.tasks = {}
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR", ".kraft-run"))); run_dirs.ensure()
    db = await Database.open(run_dirs.db)
    registry = load_registry(TEMPLATES_DIR / "registry.yaml")
    tset = load_templates(TEMPLATES_DIR, registry)
    summary, adopted = await reattach(db, run_dirs, registry)
    for wid in summary.resumed_work_items:
        app.state.tasks[wid] = asyncio.create_task(
            executor.resume(db, run_dirs, work_item_id=wid, registry=registry,
                            adopted=adopted, bd_cwd=BD_CWD))
    app.state.db, app.state.run_dirs, app.state.registry = db, run_dirs, registry
    app.state.templates = tset
    app.state.reattach_summary = summary
    try:
        yield
    finally:
        for t in app.state.tasks.values():
            t.cancel()
        await db.close()
```

- `TEMPLATES_DIR` = repo `templates/` (resolved from `kraft.__file__` → repo
  root, or a `KRAFT_TEMPLATES_DIR` override for tests that need a bad template).
- `BD_CWD` = `os.environ.get("KRAFT_BD_CWD")` (None → inherit orchestrator cwd).
- Task bookkeeping: every spawned executor task gets
  `t.add_done_callback(lambda _t, wid=wid: app.state.tasks.pop(wid, None))`.

### 4.2 Endpoints (design §6)

| Endpoint | Behaviour |
|---|---|
| `POST /work-items` | Body `{title, repo, chain_template?}`. `repo` is **required** — `422` if absent (deviation from design §6's "defaults to the fixture path": once the fixture is a per-test tmp `git init` copy, a static default path is meaningless; every caller passes an explicit path). `chain_template` default `"quick-task"`. Template not in `app.state.templates.valid` → `422 {detail: "unknown or invalid template"}`. Else: `wid = executor.intake(db, run_dirs, title=…, repo=…, template=…, bd_cwd=BD_CWD)` → spawn `executor.run(..., work_item_id=wid, registry=…, bd_cwd=BD_CWD)` task → `app.state.tasks[wid] = task` → `201 {id, bead_id, status, chain_definition, current_node_id}` (re-read the row for the response). |
| `GET /work-items/{id}` | `404` if absent. Else full `work_items` row (parse `chain_definition` to JSON) + **all** `worker_sessions` rows for the item (design §1 deviation — not node-scoped). |
| `GET /work-items/{id}/events?after_seq=N` | `after_seq` default `0`. `[{seq, type, payload, created_at}]`, `seq > N`, ordered by `seq`. `events.read_after` from Chunk A. `404` if the work item is absent. Primary progress mechanism. |
| `GET /worker-sessions/{id}/log` | `404` if the session or its `log_path` is absent. Else `PlainTextResponse` of the raw file, no pagination (`02` §10.1). |
| `GET /health` | `200 {status: "ok"|"degraded", invalid_templates: {stem: reason}, reattach_summary: {...}}`. `status = "degraded"` iff `invalid_templates` non-empty. `reattach_summary` is `asdict(app.state.reattach_summary)`. |

`intake` currently raises if `bd create` fails → in `POST` that becomes a `502`
(bd unavailable) rather than a 500; catch `CalledProcessError` / `FileNotFoundError`
around `intake`.

### 4.3 `kraft.__main__`

```
uvicorn.run("kraft.api:app", host="127.0.0.1",
            port=int(os.environ.get("KRAFT_PORT", "8765")), log_level="info")
```

No reload, single worker (the single-writer DB queue assumes one process).

---

## 5. Fixtures + tests

### 5.1 `fixtures/fake-claude.sh`

`#!/usr/bin/env bash`. Invoked by the agent adapter as
`fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format json`
with `cwd` = the worktree. Ignores the flags it does not need. Modes via env:

| `KRAFT_FAKE_CLAUDE` | Behaviour |
|---|---|
| unset / `fix` | rewrite `calc.py`: `a - b` → `a + b`; write `{"status":"done"}` to `$KRAFT_RESULT_PATH`; print `{"type":"result","is_error":false}`; exit 0. |
| `noop` | write `{"status":"done"}` to `$KRAFT_RESULT_PATH`, print the same envelope, change nothing, exit 0 (agent "succeeded" but left the bug → `verify` then fails). |
| `slow` | `sleep "${KRAFT_FAKE_CLAUDE_DELAY:-10}"`, then behave as `fix`. Used by `test_reattach` — SIGKILL the orchestrator during the sleep; the detached script keeps sleeping, then patches + writes the result file. |

**Every mode writes `$KRAFT_RESULT_PATH`** — required for the adopted-session
resolution after a restart (§2.1).

Registry override for the hermetic API tests: build a `Registry` with
`on.implementation.start` → `{kind: "agent", command: "<repo>/fixtures/fake-claude.sh"}`.
`adapters.agent` already `shlex.split`s the command, so a bare path works.
(`tests/support/fake_agent.py` from Chunk B stays — still used by the Chunk B
executor unit tests; the shell script is the one the *API-level* tests point at
because it can `sleep`.)

### 5.2 Test helpers

- Reuse Chunk B's `isolated_bd(tmp_path)` (tmp git repo + `bd init --prefix TEST`).
- Reuse Chunk B's sample-repo helper (`tests/support/repo.py` / `harness.py`).
- New `tests/support/server.py`: context manager that launches
  `python -m kraft` as a subprocess with `KRAFT_RUN_DIR`, `KRAFT_PORT`,
  `KRAFT_BD_CWD`, `KRAFT_TEMPLATES_DIR` set; polls `GET /health` until `200`;
  yields a `httpx.Client` bound to the base URL + the `Popen` handle (so a test
  can `.kill()` it). Picks a free port with a transient socket bind.

### 5.3 Tests

Hermetic (CI gate — no real `claude`, no tokens):

| test | asserts |
|---|---|
| `test_health_reports_invalid_templates` | `KRAFT_TEMPLATES_DIR` pointing at a dir with one bad-hook template → `GET /health` → `status: "degraded"`, the bad stem listed. Valid `quick-task` still resolvable. (design `test_template_validation`) |
| `test_post_materializes_chain` | `POST /work-items` → `201`, `chain_definition.nodes` == the template's, `current_node_id == "env_setup"`. (design `test_chain_materialization`) |
| `test_post_invalid_template_422` | `POST {chain_template: "does-not-exist"}` → `422`. |
| `test_api_happy_path` | in-process ASGI (`httpx.ASGITransport`) + `fake-claude.sh` `fix` + `isolated_bd` → `POST` → poll `GET .../events` until `work_item_completed` → `calc.py` in the worktree has `a + b`; `GET /work-items/{id}` status `completed`; the `TEST-xxx` bead is `closed`; target repo `CLAUDE.md` (if any) untouched. |
| `test_reattach_running_session` | real subprocess server (`tests/support/server.py`), `fake-claude.sh` `slow` (delay 15). `POST` → poll events for `worker_session_started`, confirm the pid is alive → `server.kill()` (SIGKILL) → relaunch on the same `KRAFT_RUN_DIR` → poll `/health` → `reattach_summary.adopted` contains the session; poll events → `session_reattached` with the **same pid**, then chain runs to `work_item_completed`; `calc.py` patched. |
| `test_reattach_pending_unknown` | hand-insert a `worker_sessions` row `status='pending'` (pid NULL) + an `active` work item into a fresh DB → start the server → `/health` `reattach_summary.unknown` has the session; `GET /work-items/{id}` → `needs_human`; `session_unknown` event present. |
| `test_reattach_dead_pid_from_file` | `running` row, pid = a definitely-dead pid, `pid_start_time` set, a result file present with `{"status":"done"}` → start server → session resolved `done` from the file, `session_reattached` emitted, no `unknown`. |

e2e (gated — skipped unless `KRAFT_E2E=1` **and** `claude` on `PATH`):

| test | asserts |
|---|---|
| `test_e2e_happy_path` | real subprocess server, registry uses real `claude` with `--model claude-haiku-4-5-20251001` (added to the agent command only under the e2e registry), `isolated_bd`, the one-line `calc.py` task. `POST` → poll events → `work_item_completed`; `calc.py` fixed; `TEST-xxx` bead `closed`; **target repo `CLAUDE.md` never created / untouched** (the context-injection boundary, design §0 goal 3). |

`@pytest.mark.e2e`; `conftest` skips the mark unless `os.environ.get("KRAFT_E2E") == "1"`.
Reattach-with-real-`claude` is **not** a test — `test_reattach_running_session`
covers the reattach logic hermetically; a real-`claude` reattach is a manual
check if ever needed.

---

## 6. Small changes to Chunk A/B modules

Minimal, in service of C:

- `kraft.store`: accept `'unknown'` in `session_exited` and add
  `session_unknown(conn, sid)` emitting the `session_unknown` event
  (`{session_id}`). Add `mark_needs_human` call path is unchanged.
- `kraft.executor`: extract `_walk_node` (§3.1); `run` calls it. No behaviour
  change to `run`.
- `kraft.events`: confirm a `read_after(conn, work_item_id, after_seq)` exists
  (Chunk A `after_seq` read) — if it is DB-wide, add a work-item filter for the
  endpoint. (Check during implementation; may be a no-op.)
- `pyproject.toml`: add `fastapi`, `uvicorn`, `httpx` (httpx to the dev group).

No schema migration. `worker_sessions.status` already allows `'unknown'`.

---

## 7. Build order (tests alongside each module)

1. `kraft.store` tweak (`unknown` status + event) — unit test in `test_store.py`.
2. `kraft.executor._walk_node` extraction — existing `test_executor.py` stays green.
3. `kraft.executor.resume` — `test_executor.py`: resume from a mid-chain
   `current_node_id` with a pre-seeded completed node, no adopted task.
4. `kraft.reattach` — `test_reattach.py` unit tests: pending→unknown,
   dead-pid-from-file, live-pid adopt (spawn a real `sleep` subprocess, seed a
   `running` row, assert adopt + wait).
5. `kraft.api` + `kraft.__main__` — `test_api.py` hermetic (ASGITransport).
6. `tests/support/server.py` + `fixtures/fake-claude.sh`.
7. Subprocess-server tests: `test_reattach_running_session`,
   `test_reattach_pending_unknown`.
8. `test_e2e_happy_path` (gated).

---

## 8. Chunk C "done when" (from master plan) — checklist

- [ ] `test_e2e_happy_path` (real `claude`, gated): `POST` → events →
      `work_item_completed`; `calc.py` fixed; bead closed; target `CLAUDE.md`
      untouched. (hermetic gate green; real-claude e2e unverified in CI — run
      locally with KRAFT_E2E=1)
- [x] `test_reattach_running_session` (hermetic, `fake-claude.sh slow`):
      `SIGKILL` mid-`implementation` → restart → `session_reattached`, **same
      pid**, chain finishes to `completed`.
- [x] `GET /health` surfaces invalid templates and the last reattach summary.
- [x] All Chunk A + B hermetic tests still green (`uv run pytest -q`, no `e2e`).

---

## 9. Resolved decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Resume interrupted chain | **New `executor.resume` entrypoint.** `run` untouched; shared `_walk_node` helper extracted. |
| 2 | `running` session, PID identity unconfirmable | **Conservative** — resolve from result file if present + non-empty; else `unknown` + parent `needs_human`. Never wait on an unverified PID. |
| 3 | `bd` tracker in e2e | **Isolated per-test** (`isolated_bd` + `KRAFT_BD_CWD`). Real Kraft tracker never touched. |
| 4 | `fixtures/fake-claude.sh` | **Build it** — the hermetic reattach test needs a pausable fake (`slow` mode) that real `claude` cannot provide. |
| 5 | e2e token cost | Real `claude` only in `test_e2e_happy_path`, gated behind `KRAFT_E2E=1` (skipped by default even with `claude` installed), `--model claude-haiku-4-5-20251001`, minimal one-line task. Reattach coverage is hermetic. |
| 6 | Registry on reattach (handoff §13c) | **Reconstruct from `templates/registry.yaml`** at startup. Bindings not persisted. |
| 7 | Runtime config | Env vars: `KRAFT_RUN_DIR` (`.kraft-run`), `KRAFT_PORT` (`8765`), `KRAFT_BD_CWD` (inherit), `KRAFT_TEMPLATES_DIR` (repo `templates/`). |

---

## 10. Open risks for the implementation thread

- **Adopted-session ↔ resume coordination.** The background waiter from
  `reattach` and `resume` walking the current node must not both drive the same
  session. Contract: `reattach` returns `adopted: dict[session_id, Task]`;
  `resume` for the current node awaits `adopted[sid]` instead of re-dispatching,
  then reads the row's final status. Verify the waiter writes
  `session_exited` **before** its task completes so `resume`'s row read is fresh.
- **`db.read` on a shared reader connection under FastAPI concurrency.** All
  endpoint handlers are async and call `db.read` synchronously on the event-loop
  thread — no true parallelism, but a slow read blocks the loop. Acceptable for
  the skeleton; note it, do not fix.
- **`psutil.Process(pid).wait()` on a non-child** across the restart — confirm it
  returns promptly when the process is already gone (`NoSuchProcess`) and blocks
  correctly when alive.
- **`git init` inside `tests/support/sample_repo` copies** — the e2e/subprocess
  tests run a real server with a real `.kraft-run`; ensure each test gets a
  unique `KRAFT_RUN_DIR` (tmp_path) so worktrees / DBs don't collide.
