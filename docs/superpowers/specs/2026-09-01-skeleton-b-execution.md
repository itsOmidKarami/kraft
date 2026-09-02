# Skeleton Chunk B — Execution (mini-spec)

**Status:** implemented 2026-09-01
**Date:** 2026-09-01
**Bead:** Kraft-rnx.2
**Parent design:** `docs/superpowers/specs/2026-09-01-kraft-walking-skeleton-design.md` (§3 executor loop, §4 adapters)
**Master plan:** `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` (Chunk B)
**Builds on:** Chunk A — `kraft.db`, `kraft.events`, `kraft.templates` (branch `skeleton-a-foundation`)

---

## 0. Scope

Async execution core. The executor is driven **directly from test code** in this
chunk — no HTTP API, no reattach (both Chunk C).

**Modules (all new, `src/kraft/`):**

- `kraft.store` — atomic row+event helpers for `work_items` / `worker_sessions`
  (deferred here from Chunk A review Q2). Every helper mutates a row **and**
  appends its event in the same `db.write()` transaction (design §2 invariant).
- `kraft.adapters.subprocess` — `worker_sessions` lifecycle, detached launch, log
  redirection, result resolution.
- `kraft.adapters.agent` — wraps `adapters.subprocess` to build the `claude`
  headless command with system-prompt context injection.
- `kraft.adapters.beads` — thin `bd --json` wrapper; called directly by the
  executor, not a chain node.
- `kraft.builtins` — `env_setup` (`git worktree add` via `adapters.subprocess`).
- `kraft.executor` — `intake()` + `run()`: materialize, walk nodes, `gather`
  node tasks, `needs_human` on failure, `completed` + `bd close` after the last
  node.

**Out (Chunk C):** `kraft.reattach`, `kraft.api`, `fixtures/fake-claude.sh`, the
`@e2e` tests with a real `claude`.

**Stack additions:** `psutil>=5` (capture `create_time()` for
`worker_sessions.pid_start_time` now — the PID-reuse guard is written in B even
though it is only *read* by Chunk C reattach). No other new deps. Tests stay
hermetic: `asyncio.run()` around an inner coroutine, no `pytest-asyncio`.

---

## 1. Run-directory layout

All runtime artifacts live under a single base dir (design §1 deviation:
`.kraft-run/`, not `~/.orchestrator/`).

```
kraft.paths.RunDirs(base: Path)
  .db         -> base / "orchestrator.db"
  .logs       -> base / "logs"            # {session_id}.log
  .results    -> base / "results"         # {session_id}.json
  .worktrees  -> base / "worktrees"       # {work_item_id}/
```

`RunDirs` is passed **explicitly** into `executor.intake` / `executor.run` and
flows down to the adapters. Production wires `RunDirs(Path(".kraft-run"))`; tests
wire `RunDirs(tmp_path)`. No module-level singleton. (Open Q7.)
`RunDirs(base).ensure()` mkdirs `logs/`, `results/`, `worktrees/`.

---

## 2. `kraft.store` — atomic row + event helpers

Each helper is a plain `fn(conn)` meant to be handed to `Database.write()`. It
does the row write and `events.append()` in one call, so the writer commits both
or neither.

### `work_items`

| helper | writes | event |
|---|---|---|
| `create_work_item(conn, *, id, bead_id, title, repo, chain_template, chain_definition)` | INSERT, `status='active'`, `current_node_id=NULL`, `created_at=updated_at=now` | `work_item_created` `{title, repo, chain_template}` |
| `load_chain(conn, wid, first_node_id)` | `current_node_id=first_node_id`, `updated_at` | `chain_loaded` `{chain_definition}` |
| `enter_node(conn, wid, node_id)` | `current_node_id=node_id`, `updated_at` | `node_started` `{node_id}` |
| `complete_node(conn, wid, node_id)` | `updated_at` only (current_node_id unchanged — advanced by the next `enter_node`) | `node_completed` `{node_id}` |
| `mark_needs_human(conn, wid, node_id, reason)` | `status='needs_human'`, `updated_at` | `work_item_needs_human` `{node_id, reason}` |
| `mark_completed(conn, wid)` | `status='completed'`, `updated_at` | `work_item_completed` `{}` |

`current_node_id` only ever moves forward at `enter_node`. On failure the loop
stops before the next `enter_node`, so it stays pointed at the failed node
(satisfies design test_verify_failure: "does not advance past verify").

### `worker_sessions`

| helper | writes | event |
|---|---|---|
| `create_session(conn, *, id, work_item_id, node_id, hook_point, log_path, result_path)` | INSERT, `status='pending'`, `attempt=1`, `created_at=now`, `pid=NULL` | — (none; row must exist before spawn, design §4.1) |
| `session_running(conn, sid, pid, pid_start_time)` | `status='running'`, `pid`, `pid_start_time` | `worker_session_started` `{session_id, node_id, hook_point, pid}` |
| `session_exited(conn, sid, status)` | `status` (`done`\|`failed`), `exited_at=now` | `worker_session_exited` `{session_id, status}` |

---

## 3. `kraft.adapters.subprocess`

The generic launch/detach/result contract (design §4, `02` §6).

```
async def run_task(
    db, run_dirs, *,
    session_id, work_item_id, node_id, hook_point,
    cmd: list[str], cwd: str | Path, env: dict | None = None,
) -> str            # returns 'done' | 'failed'
```

1. `await db.write(store.create_session(id=session_id, ..., log_path, result_path))`
   — `log_path = run_dirs.logs/f"{session_id}.log"`,
   `result_path = run_dirs.results/f"{session_id}.json"`.
2. Open `log_path` (`"w"`). `Popen(cmd, cwd=str(cwd), start_new_session=True,
   stdout=log, stderr=STDOUT, env={**os.environ, **(env or {}),
   "KRAFT_RESULT_PATH": str(result_path)})`.
   - `FileNotFoundError` (binary missing) → `await db.write(store.session_exited(
     session_id, 'failed'))`, return `'failed'`.
3. `pid = proc.pid`; `pid_start_time = psutil.Process(pid).create_time()`
   (best-effort — if the process already exited, `psutil.NoSuchProcess` →
   `pid_start_time = None`). `await db.write(store.session_running(session_id,
   pid, pid_start_time))`.
4. `rc = await asyncio.to_thread(proc.wait)`.
5. **Resolve:**
   - `result_path` exists and non-empty → parse JSON; `status` field must be
     `'done'` or `'failed'`. Missing/other value or unparseable → `'failed'`.
   - else → `rc == 0` → `'done'`, non-zero → `'failed'`.
6. `await db.write(store.session_exited(session_id, status))`. Return `status`.

`run_task` never raises for a task-level failure — it returns `'failed'` and the
executor decides. It may raise for programmer error (bad args).

---

## 4. `kraft.adapters.agent`

Wraps `adapters.subprocess.run_task` for `claude` headless.

```
async def run_agent_task(
    db, run_dirs, *,
    session_id, work_item_id, node_id, hook_point,
    command: str,               # registry binding, "claude" or a fake
    title: str, task_instruction: str, repo_path: str,
    cwd: str | Path,
) -> str
```

- **Command:** `[command, "-p", task_instruction, "--append-system-prompt",
  injected_context, "--output-format", "json"]`.
- **Injected context (system prompt only — never a file in the target repo,
  the boundary under test, `01` §10):**
  ```
  You are working on a Kraft work item.
  Title: {title}
  Task: {task_instruction}
  Repo: {repo_path}
  ```
- Delegates to `run_task`. Result resolution is `run_task`'s (result file → exit
  code). Additionally, `run_task`'s `post_resolve` hook applies the envelope
  check: if the last non-blank log line is a JSON object with `is_error` true,
  return `'failed'`; otherwise return the base status unchanged. (A base
  `'failed'` therefore stays `'failed'`.)
  (Minimal envelope handling — Chunk B only ever runs the fake agent; full
  `claude` JSON-envelope parsing is Chunk C's concern when the `@e2e` tests land.
  Open Q3.)

---

## 5. `kraft.adapters.beads`

```
async def intake(title: str, *, bd_args: list[str] = ()) -> str    # returns bead_id
async def complete(bead_id: str, *, bd_args: list[str] = ()) -> None
```

- `intake`: `bd {bd_args} create --json --title {title} -d "Created by Kraft
  orchestrator." --type task` via `asyncio.to_thread(subprocess.run, ...,
  capture_output=True, check=True)`; parse stdout JSON → `["id"]`.
- `complete`: `bd {bd_args} close {bead_id}`.
- `bd_args` lets tests pass `["--db", str(tmp_bd_db)]` for an isolated tracker
  (Open Q4). Production passes nothing.
- `CalledProcessError` / missing `bd` propagates — the executor turns it into
  `needs_human` (intake failure) or logs-and-continues (completion failure;
  the chain already succeeded).

---

## 6. `kraft.builtins.env_setup`

```
async def env_setup(
    db, run_dirs, *,
    session_id, work_item_id, node_id, repo: str,
) -> str
```

- `worktree = run_dirs.worktrees / work_item_id`; `branch = f"kraft/{work_item_id}"`.
- `cmd = ["git", "worktree", "add", str(worktree), "-b", branch]`, `cwd=repo`,
  `hook_point="on.env.prepare"`.
- Delegates to `adapters.subprocess.run_task`. Returns its status.
- Precondition: `repo` is a git repo with at least one commit (the test fixture
  does `git init` + initial commit).

---

## 7. `kraft.executor`

### `intake`

```
async def intake(db, run_dirs, *, title, repo, template: Template,
                 bd_args: list[str] = ()) -> str      # returns work_item_id
```

1. `wid = uuid4().hex`.
2. `bead_id = await adapters.beads.intake(title, bd_args=bd_args)`.
   Failure here → raises; caller (test / future API) surfaces it. No
   `work_items` row is written.
3. `chain_definition = json.dumps(templates.materialize(template))`.
4. `await db.write(store.create_work_item(id=wid, bead_id=bead_id, title=title,
   repo=repo, chain_template=template.id, chain_definition=chain_definition))`.
5. Return `wid`.

### `run`

```
async def run(db, run_dirs, *, work_item_id, registry: Registry) -> str
        # returns terminal status: 'completed' | 'needs_human'
```

1. Read the `work_items` row (`db.read`). `chain = json.loads(chain_definition)`,
   `repo = row["repo"]`, `bead_id = row["bead_id"]`,
   `worktree = run_dirs.worktrees / work_item_id`.
2. `await db.write(store.load_chain(work_item_id, chain["nodes"][0]["id"]))`.
3. For each `node` in `chain["nodes"]`:
   1. `await db.write(store.enter_node(work_item_id, node["id"]))`.
   2. `results = await asyncio.gather(*(
        _dispatch(db, run_dirs, task, node, row, registry, worktree)
        for task in node["tasks"]))`
      (skeleton nodes are single-task; the `gather` fan-out is built anyway,
      design §3.2).
   3. Any result `== 'failed'` → `await db.write(store.mark_needs_human(
      work_item_id, node["id"], reason=f"task failed in node {node['id']}"))`;
      return `'needs_human'`.
   4. Else → `await db.write(store.complete_node(work_item_id, node["id"]))`.
4. `await db.write(store.mark_completed(work_item_id))`.
5. `await adapters.beads.complete(bead_id, bd_args=bd_args)` — best-effort;
   log and swallow `CalledProcessError` (chain already done). *(bd_args on the
   work item: store it? re-thread it? — see Open Q4.)*
6. Return `'completed'`.

### `_dispatch(db, run_dirs, task_hook, node, work_item_row, registry, worktree) -> str`

```
binding = registry.hooks[task_hook]          # registry is startup-validated
session_id = uuid4().hex
kind = binding["kind"]

if kind == "builtin" and binding["handler"] == "env_setup":
    return await builtins.env_setup(db, run_dirs, session_id=..., repo=work_item_row["repo"])

if kind == "agent":
    return await adapters.agent.run_agent_task(
        db, run_dirs, session_id=...,
        command=binding["command"],
        title=work_item_row["title"],
        task_instruction=work_item_row["title"],   # Open Q2
        repo_path=work_item_row["repo"],
        cwd=worktree, hook_point=task_hook)

if kind == "subprocess":
    return await adapters.subprocess.run_task(
        db, run_dirs, session_id=...,
        cmd=binding["command"], cwd=worktree, hook_point=task_hook)

raise RuntimeError(f"unhandled binding kind {kind!r} for {task_hook!r}")
```

`node_id` passed to every adapter = `node["id"]`.

---

## 8. Test fixture + fake agent (chunk-local)

Chunk B's done-criteria need a repo with a failing test and an agent that fixes
it. Design §7 lists `fixtures/sample-repo/` and `fixtures/fake-claude.sh` under
Chunk C. Resolution (Open Q1): build **minimal** versions under `tests/support/`
now; Chunk C adds `fixtures/fake-claude.sh` (richer env-var modes) and the
`@e2e` path.

- `tests/support/sample_repo/` — plain tree, **no `.git`**:
  - `calc.py` → `def add(a, b):\n    return a - b\n` (bug)
  - `test_calc.py` → `from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n`
- `tests/support/fake_agent.py` — invoked as
  `python fake_agent.py -p <instr> --append-system-prompt <ctx> --output-format json`:
  - default: rewrite `calc.py` `a - b` → `a + b` in `cwd`, print
    `{"type":"result","is_error":false}`, exit 0.
  - `KRAFT_FAKE_AGENT=noop`: print the same envelope, change nothing, exit 0
    (drives `test_verify_failure`).
- `tests/support/repo.py` — helper: copy `sample_repo` to `tmp_path`, `git init
  -b main`, `git add -A`, `git -c user.email=… -c user.name=… commit -m init`,
  return the path.
- Registry override for tests: load the real `templates/registry.yaml`, then
  replace `on.implementation.start` with `{kind: agent, command:
  "<python> tests/support/fake_agent.py"}`. **Blocker:** the registry `command`
  for `agent` is a single string (Chunk A `load_registry`), but the fake needs
  `[python, script]`. Options: (a) `command: "python3"` + agent adapter always
  prepends nothing and the script path comes from… no. (b) let the test build a
  `Registry(hooks={...})` object directly instead of going through YAML — the
  `agent` command string is `f"{sys.executable} {script}"` and the agent adapter
  does `shlex.split(command)`. Pick (b); note that `adapters.agent` must
  `shlex.split(command)` rather than treat it as one argv token. Real use
  (`command: "claude"`) splits to `["claude"]`, unchanged.

---

## 9. Tests (`tests/`, all hermetic)

| test | asserts |
|---|---|
| `test_subprocess_detach` | `run_task` with `["sleep", "5"]`; capture pid; close the DB and let the loop end; `psutil.pid_exists(pid)` still true (own session, outlived the "orchestrator"). Kill it in teardown. |
| `test_result_file_over_exit_code` | cmd = `sh -c 'echo {"status":"failed"} > "$KRAFT_RESULT_PATH"; exit 0'` → resolved `'failed'`. cmd exiting 3 with no file → `'failed'`. cmd exiting 0, no file → `'done'`. |
| `test_session_lifecycle_events` | one `run_task`: row goes `pending`→`running` (pid + pid_start_time non-null) →`done`/`failed` (exited_at set); events `worker_session_started` then `worker_session_exited` appended in `seq` order. |
| `test_missing_binary` | `run_task(cmd=["kraft-nonexistent-bin"])` → `'failed'`, session row `failed`, no crash. |
| `test_env_setup_creates_worktree` | real git repo via `tests/support/repo.py` → `env_setup` → `run_dirs.worktrees/{wid}` exists, `git branch --list kraft/{wid}` non-empty, status `'done'`. |
| `test_beads_adapter_roundtrip` | `intake("t", bd_args=["--db", tmp])` returns an id that `bd --db tmp show` finds `open`; `complete(id, bd_args=…)` → status `closed`. |
| `test_executor_happy_path` | sample repo + default fake agent + direct `Registry` → `intake` then `run` → returns `'completed'`; `calc.py` in the worktree contains `a + b`; the `bd` issue is `closed`; events end `…node_completed(verify) → work_item_completed`. |
| `test_verify_failure` | `KRAFT_FAKE_AGENT=noop` → `run` returns `'needs_human'`; `work_items.status='needs_human'`, `current_node_id='verify'`; no `node_completed` event for `verify`; `bd` issue still `open`. |
| `test_node_gather_fanout` | a hand-built 2-task node (both `subprocess` `["true"]`) → `asyncio.gather` resolves both, two `worker_sessions` rows, one `node_completed`. |

---

## 10. Chunk B "done when" (from master plan) — checklist

- [x] `adapters.subprocess` launches a detached child that outlives the parent
      → `test_subprocess_detach`
- [x] Result resolution handles both a JSON result file and a bare exit code
      → `test_result_file_over_exit_code`
- [x] Executor runs `quick-task` end to end in-process with the fake agent:
      `env_setup → implementation → verify → completed`, bead created and closed
      → `test_executor_happy_path`
- [x] `test_verify_failure`: no-op fake agent → `verify` fails → `needs_human`,
      `current_node_id` stops at `verify` → `test_verify_failure`
- [x] Hermetic tests green: `uv run pytest -q` (Chunk A's 24 + Chunk B's, no
      `e2e` collected)

---

## 11. Resolved decisions (was: open questions)

**All recommendations accepted.** Notes:

- **Q4 adjustment (revised during planning):** `bd`'s workspace is git-repo /
  cwd scoped — `BEADS_DB` alone does not select an initialized tracker. So
  `adapters.beads.intake/complete` take an optional `cwd`, and `executor.intake/
  run` take an optional `bd_cwd` threaded to them. Tests build an isolated
  tracker with `isolated_bd(tmp_path)` = a tmp git repo + `bd init --prefix TEST`,
  and pass its path as `bd_cwd`. Production passes `bd_cwd=None` (inherit the
  orchestrator's cwd). `bd show <id> --json` returns a JSON **array**; ids carry
  a random suffix (`TEST-xxx`).

Original text, for the record:

1. **Fixture placement** — minimal `tests/support/sample_repo` + `fake_agent.py`
   in Chunk B, Chunk C adds `fixtures/fake-claude.sh` + `@e2e`. Recommended.
   Alternative: pull the whole `fixtures/` forward into B.
2. **Task instruction source** — `_dispatch` currently passes `work_item.title`
   as the agent's `task_instruction`. So intake is called with
   `title="make the failing test pass"`. Alternative: a separate
   `task_instruction` column / API field, `title` stays human-readable. Adds a
   column not in the Chunk A schema (design §2 has no such column) → would be a
   schema v2. Recommend: title-as-instruction for the skeleton, note it.
3. **Agent envelope parsing** — Chunk B does a minimal `is_error` check on the
   last log line. Full `claude` `--output-format json` envelope parsing deferred
   to Chunk C with the real-`claude` `@e2e` tests. OK?
4. **`bd_args` threading** — tests need an isolated `bd` DB. `intake` takes
   `bd_args`; but `run`'s completion `bd close` also needs it. Options:
   (a) store `bd_args` nowhere, re-pass to `run` as a param (test-only kwarg);
   (b) `BEADS_DB` env var set by the test, adapters read nothing special;
   (c) a `work_items.bd_args` column (ugly). Recommend (b) — zero API surface,
   `adapters.beads` stays clean. Revisit if bd honors an env override cleanly.
5. **`kraft.store` as one module** vs. helpers colocated with each adapter.
   One module keeps the "row + event in one txn" invariant reviewable in one
   place. Recommended.
6. **`psutil` dependency** — added in B (writes `pid_start_time`), not C. OK?
7. **`RunDirs` injection** — explicit param through every call vs. a
   `configure(base)` module singleton. Explicit is more test-friendly and has no
   global state; slightly more verbose. Recommended.

---

## 12. Build order (tests alongside each module)

1. `kraft.paths` (`RunDirs`) — trivial, no test of its own
2. `kraft.store` — helpers + `test_session_lifecycle_events` scaffolding
3. `kraft.adapters.subprocess` — `test_subprocess_detach`,
   `test_result_file_over_exit_code`, `test_missing_binary`
4. `kraft.adapters.beads` — `test_beads_adapter_roundtrip`
5. `kraft.builtins.env_setup` — `test_env_setup_creates_worktree`
6. `kraft.adapters.agent` — exercised via the executor tests
7. `tests/support/` fixture + fake agent
8. `kraft.executor` — `test_executor_happy_path`, `test_verify_failure`,
   `test_node_gather_fanout`

---

## 13. Handoff to Chunk C

Chunk B's execution core makes three assumptions that Chunk C (reattach + HTTP
API) must not silently inherit. They are documentation, not Chunk B code changes.

### (a) `worker_sessions` recovery semantics are unwritten

`adapters.subprocess.run_task` writes the session row `status='pending'` (pid
NULL, no event) *before* `Popen`, and only writes `status='running'` + `pid` +
`pid_start_time` + the `worker_session_started` event *after* the spawn returns.
If the orchestrator dies in that window a detached child exists with no recorded
pid against a `pending` row — it cannot be reattached by pid identity. Chunk C's
reattach must define: on startup a `pending` session (NULL pid) ⇒ `status='unknown'`
⇒ escalate its parent work item to `needs_human`. The schema already carries the
`unknown` status for exactly this case. Chunk C's reattach design also has to
state what `running` rows, a NULL `pid_start_time` (psutil missed
`create_time()`), and the currently-unused `capped_out` / `paused` / `attempt`
columns mean to the reattach path.

### (b) `executor.run` is not resumable or idempotent

`run` always starts at `nodes[0]`, and `store.load_chain` unconditionally resets
`current_node_id` to the first node. Re-invoking `run` on an existing work item
re-enters every node and spawns duplicate sessions — the second `env_setup` would
`git worktree add` onto an already-existing path, fail, and flip the item to
`needs_human`. Chunk C's "resume from `current_node_id`" cannot call the existing
`run` to continue an interrupted chain; it needs either a separate resume entry
point or a re-entrancy guard inside `run` that skips already-completed nodes.

### (c) Chunk C reattach has no `Registry`

`intake` persists `chain_definition` (the materialized node list) on the
work-item row, but the `Registry` (hook→binding table) is only ever a call-time
argument to `run` — it is never persisted. A fresh reattach/resume entry point
(e.g. invoked from the HTTP API after a restart) has the row and the chain but no
bindings to hand to `_dispatch`. Chunk C must decide: persist the resolved
bindings alongside `chain_definition` at intake, or have the API/reattach layer
always reconstruct the `Registry` from `templates/registry.yaml`.
