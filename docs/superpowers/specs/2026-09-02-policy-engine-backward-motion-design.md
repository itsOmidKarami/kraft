# Policy Engine + Backward-Motion Coordinator (Fix Loop) — Design

**Status:** design, approved in chat, awaiting written-spec review
**Date:** 2026-09-02
**Bead:** Kraft-ue7 (folds in Kraft-2v0)
**Consolidated design refs:** `docs/consolidated/02_orchestrator_core.md` §7 (all), §4.3, §8, §11; `docs/consolidated/01_conceptual_model.md` §9
**Master plan:** post-skeleton effort #2 of `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` §3

---

## 0. What this is

The walking skeleton + effort #1 give us a chain executor that walks nodes, runs
tasks via `asyncio.gather`, stops at gates, and drops to `needs_human` the instant
any task fails. There is no retry, no fix loop, no cap enforcement, and no
`retry_counters` table.

This effort adds the first slice of `02` §7:

1. A `kraft.policy` module: cap values from `templates/policy.yaml`, cap resolution,
   breach check (attempts **and** wall-clock).
2. A `retry_counters` table and the project's **first real schema migration**
   (`SCHEMA_VERSION` 1 → 2).
3. The backward-motion coordinator's **entry point A** (`02` §7.2), scoped to the
   `verify` node: while the node's measuring tasks come back non-clean, run a
   bounded fix loop — bump the counter, then (if not breached) one fix-scoped
   `on.implementation.start` and re-measure — capped by `verify_fix_loop`.
   Breach → `capped_out` sessions + `needs_human`.
4. A `fix_cycle_started` event.
5. Kraft-2v0: on restart, `executor.resume` honors a gate that was approved just
   before the crash instead of re-requesting it (losing the human's approval).

It does **not** add entry point B (gate reject-loops), `mr_checks_fix_loop`,
per-work-item `config_overrides`, usage/cost logging, the `execution_worker` retry
budget, the guidance gate, pause/steer, or the §11 concurrency ceiling. Those are
separate efforts. See §11.

---

## 1. Scope

### In

- `src/kraft/policy.py` (new) — `load_policy`, `resolve_cap`, `check`.
- `templates/policy.yaml` (new) — loop cap defaults.
- `src/kraft/db.py` — `SCHEMA_VERSION = 2`, `retry_counters` table, forward-only
  migration runner.
- `src/kraft/store.py` — `bump_counter`, `read_counter`, `mark_sessions_capped_out`.
- `src/kraft/templates.py` — validate a node's `fix_loop` key.
- `src/kraft/executor.py` — fix-loop branch in `_walk_node`; thread `policy` param
  through `run` / `resume`.
- `src/kraft/executor.py` — `resume` honors an already-approved gate at the
  current node instead of re-requesting it (Kraft-2v0).
- `src/kraft/api.py` — load `policy.yaml` into `app.state`, thread it into
  `executor.run` / `resume` calls; surface `invalid_policy` in `GET /health`.
- `templates/default.yaml` — `verify` node gains `fix_loop: verify_fix_loop`.
- Tests: `test_policy.py`, `test_fix_loop.py` (new); `test_db.py`,
  `test_reattach.py`, `test_templates.py` (extend).

### Out (later efforts — see §11)

- Entry point B: gate reject re-invoking the producing hook, `<gate>_reject_loop`
  counters (`02` §7.2). `gate_rejected` stays terminal, as shipped in effort #1.
- `mr_checks_fix_loop` and the `mr_checks` node fix loop — `on.ci.poll` /
  `on.review.mr.run` are `noop`, there is no CI or GitLab adapter yet (effort #5).
- Per-work-item `config_overrides` — needs a `work_items` column; no solo-user need
  yet. `resolve_cap` takes only the hook/loop key.
- Cost/token usage logging from `worker_sessions.usage_reported_json` — the real
  Execution Worker (`03` §2) that self-reports usage is not built.
- The `(work_item_id, execution_worker)` producer retry budget and the coordinator
  **carve-out** against it — that budget does not exist yet, so the carve-out is a
  no-op this effort.
- `plan_diverged` → guidance gate routing (`01` §8) — the guidance gate is not
  built and the adapters cannot produce a `plan_diverged` status. The fix loop
  does not branch on the fix task's status at all this effort.
- Structured `findings[]` / `failures[]` result payloads — adapters resolve to
  `"done"` / `"failed"` only. "Non-clean" == a measuring task returned `"failed"`.
- Pause / steer / resume, `paused` status (`02` §10.2).
- The per-hook-type `max_concurrent` ceiling (`02` §11).

---

## 2. Design decisions

### A. `kraft.policy` is cap math + config only

New module `src/kraft/policy.py`. Pure, synchronous, no DB handle of its own —
it is handed rows and returns verdicts.

```python
@dataclass(frozen=True)
class Cap:
    attempts: int
    wall_clock_s: int

@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap

def load_policy(path: Path) -> Policy: ...
def resolve_cap(policy: Policy, key: str) -> Cap:
    return policy.loops.get(key, policy.default)

def check(*, count: int, started_at: str, cap: Cap, now: str) -> str:
    """Return 'ok' or 'breached'. Breached when count > cap.attempts
    OR (now - started_at) >= cap.wall_clock_s.

    Called after bump_counter, so `count` is the number of fix cycles that
    have now run. `attempts: 3` therefore allows 3 fix cycles; the 4th bump
    (count == 4) breaches. The counter row is left at that breaching value."""
```

The **backward-motion coordinator is not a module**. It is a loop inside
`executor._walk_node`, taken only for nodes that carry a `fix_loop` key. `policy.py`
owns the cap numbers and the breach predicate; the executor owns the control flow.
This keeps the new surface to one small pure module plus one executor branch.

Rejected: a `kraft.coordinator` / `kraft.backward_motion` module. There is one
call site (the `verify` node walk). A module with one caller and no independent
state is indirection without isolation benefit. When entry point B and
`mr_checks` land and there are three call sites with shared counter semantics,
extracting a coordinator is the right move — not now.

### B. `policy.yaml` holds the numbers; no per-work-item override

`02` §7.1: caps "default per hook type, overridable per work item via
`config_overrides` — override wins if set, else the hook-type default."

This effort ships the **default half only**. `templates/policy.yaml`:

```yaml
# Cap defaults for loop-bounded work. Every number here is a placeholder
# (02_orchestrator_core.md §13) — tune against real runs.
loops:
  verify_fix_loop:   { attempts: 3, wall_clock_s: 3600 }
default:             { attempts: 3, wall_clock_s: 3600 }
```

`load_policy` mirrors `templates.load_registry`: `yaml.safe_load`, shape-check
(every entry has int `attempts` ≥ 1 and int `wall_clock_s` ≥ 1), build the frozen
`Policy`. A malformed `policy.yaml` raises at load; `api.py` catches it and lists
the file in `GET /health`'s `invalid_policy` field (mirroring `invalid_templates`),
and the process refuses to accept work — same posture as an invalid registry.

Per-work-item `config_overrides` is a **documented gap** (§11). Adding it later is
a `work_items` column + one branch in `resolve_cap`; nothing in this design
precludes it.

Rejected: per-hook `caps:` blocks in `registry.yaml` (muddies the registry's
binding purpose; loop keys like `verify_fix_loop` are not hooks); hardcoded
constants in `policy.py` (a separate file costs nothing and matches the
registry/template config pattern already in the repo).

### C. `retry_counters` stores the resolved cap, snapshotted at first fire

`02` §7.1: "Result written into `retry_counters` the first time a loop-bounded
hook fires, not re-resolved per attempt."

```sql
CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,              -- e.g. 'verify_fix_loop'
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,           -- snapshot of the resolved Cap
  cap_wall_s    INTEGER NOT NULL,           -- snapshot of the resolved Cap
  started_at    TEXT NOT NULL,              -- timestamp of the first fire
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
);
```

`started_at` anchors the wall-clock check. A restart mid-loop therefore does not
reset the wall-clock budget — the counter row survives the crash and
`started_at` is unchanged. This matches `02` §8's "reattach never touches
`retry_counters`."

Store helper:

```python
def bump_counter(conn, work_item_id, key, cap: policy.Cap) -> tuple[int, str]:
    """Insert (count=1, snapshot cap, started_at=now) or increment count.
    Returns (new_count, started_at)."""
```

`read_counter(conn, work_item_id, key) -> sqlite3.Row | None` for the tests and
for `/health`-adjacent introspection later.

### D. First real schema migration

`db.py` today: `migrate(conn)` runs the `;`-split `SCHEMA_SQL` inside an explicit
`BEGIN`/`commit` on `user_version == 0`, no-ops on `== SCHEMA_VERSION`, raises
"newer than code" on `> SCHEMA_VERSION`, and **raises** on anything between:

```python
raise RuntimeError(f"no migration path from schema v{version} to v{SCHEMA_VERSION}")
```

Replace that middle raise with a forward-only runner, inside the same
`BEGIN`/`commit` block the v0 path uses:

```python
_MIGRATIONS: dict[int, list[str]] = {
    1: ["CREATE TABLE retry_counters ( ... )"],   # v1 -> v2
}

# when 0 < version < SCHEMA_VERSION:
conn.execute("BEGIN")
try:
    for v in range(version, SCHEMA_VERSION):
        for stmt in _MIGRATIONS[v]:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {v + 1}")
    conn.commit()
except BaseException:
    conn.rollback()
    raise
```

`SCHEMA_SQL` (the from-empty path) also gains the `retry_counters` `CREATE TABLE`,
so a fresh DB lands at v2 directly and a v1 DB migrates. No down-migrations — a v2
DB opened by v1 code still raises "newer than code", unchanged. A `PRAGMA
user_version` bump is not DML and does not itself open a transaction, matching the
existing code's handling.

Rejected: folding `retry_counters` into `SCHEMA_SQL` only and bumping the version
without a runner — every existing dev DB (there are several worktrees) would hit
the "no migration path" raise and have to be deleted. The runner is ~8 lines and
we need it for every future schema change anyway.

### E. `fix_loop` is a node key, validated by the template loader

`default.yaml`'s `verify` node gains `fix_loop: verify_fix_loop`. Shape:
`fix_loop` is absent/`null` (no loop) or a non-empty string (the counter key).

`templates.load_templates` validation, alongside the existing `gate_after` check:
a node with a non-string-or-empty `fix_loop` → template **invalid**. A node with
`fix_loop` set **and** an empty `tasks` list → invalid (nothing to measure).

`quick-task.yaml` gets no `fix_loop` key → its `verify` node behaves exactly as
today. `materialize` already passes unknown node keys through into
`chain_definition`, so no `materialize` change (confirm in the plan; add a
pass-through if needed).

Rejected: hardcoding `node["id"] in {"verify", "mr_checks"}` in the executor —
couples control flow to node names, and a template author renaming `verify` or
adding a second measuring node would silently lose the fix loop.

### F. "Non-clean" == a measuring task returned `"failed"`

The adapters (`adapters/subprocess.py`, `adapters/agent.py`) resolve every task to
exactly `"done"` or `"failed"` today — `_resolve_result_file` clamps anything else
to `"failed"`, and `agent._envelope_is_error` maps `is_error` to `"failed"`. There
are no `"plan_diverged"` / `"error"` statuses in practice and no structured
`findings[]` / `failures[]` payloads — the real Execution Worker and review
adapter (`03` §2, §4) are later efforts.

So the fix loop's trigger is: after the node's measuring tasks run, **any** of
them returned `"failed"` or raised. Today, in `default.yaml`'s `verify` node, that
is `on.test.run` (pytest subprocess, non-zero exit → `"failed"`);
`on.review.local.run` is `noop` and always `"done"`.

The fix task's own return status is **not branched on** — whatever it returns, the
loop re-measures, and the measuring result drives the next bump/breach. A broken
fix agent therefore burns the budget and escalates, which is correct.
`"plan_diverged"` → guidance-gate routing is a documented gap (§11); it needs a
real status from the Execution Worker first.

The fix task's injected prompt is generic:

> The checks in node `<node_id>` failed for this work item. The failing task
> logs are at `<log_path>` (one line per failed task). Fix the code so they
> pass. Make no unrelated changes.

Structured failure payloads are a documented gap (§11); when they land, the fix
prompt gets the structured detail and the trigger can gain a severity threshold
(`02` §7.2 defers that too).

### G. Kraft-2v0 — approve-then-crash re-gates the work item

Correction to effort #1 §11's framing. `reattach` **already** collects every
`status = 'active'` work item into `summary.resumed_work_items`, and `api.lifespan`
**already** spawns `executor.resume(...)` for each. So an approve-then-crash item
is *not* stalled — but `resume` mishandles it:

After `POST .../gates/{gate}/approve`, `store.approve_gate` sets `status = 'active'`
and appends `gate_approved`, but **does not advance `current_node_id`** — it stays
on the gate node (e.g. `spec`). The approve endpoint's own `run(start_index=g+1)`
starts *after* the gate. But on crash-recovery, `resume` computes
`start = index(current_node_id)` = the gate node, runs `_reconcile_current_node`
on it (re-`complete_node` → redundant `node_completed`, the Kraft-gbt defect),
then `_maybe_gate(nodes[start])` fires `request_gate` **again** — the human's
approval is lost and they must re-approve.

Fix, in `executor.resume`, before the `_reconcile_current_node` call: if
`nodes[start]` has a `gate_after` and the work item's event log shows that gate
was last `gate_approved` (most recent `gate_*` event for the item is
`gate_approved` with that gate name), then the gate is already cleared —
advance `start` by 1 and skip the gate re-check for that node. Reconciliation and
the walk then resume from the post-gate node, which correctly handles partial
progress there via the existing `_reconcile_current_node`.

Helper (executor-local, mirrors `api._pending_gate` inverted):

```python
def _gate_cleared(db, work_item_id, gate) -> bool:
    """True iff the most recent gate_* event for the item is gate_approved <gate>."""
```

This never touches `retry_counters`. `reattach` and `api.lifespan` are unchanged
for this fix; only `resume` changes.

Rejected: advancing `current_node_id` in `approve_gate` (breaks effort #1's
`test_gates.py` assertion that a work item awaiting a gate reports
`current_node_id == "<gate node>"`, and misrepresents state — the next node has
not started). Rejected: a `worker_sessions` "pending executor" marker row
(`worker_sessions` models subprocesses, not the in-process walk).

**Scope note:** this also fixes the Kraft-gbt double-`node_completed` *for the
gate-node resume path only*. The general `_reconcile_current_node` idempotency
issue (Kraft-gbt) stays open for non-gate nodes.

---

## 3. `policy.yaml`

```yaml
# templates/policy.yaml
# Cap defaults for loop-bounded work. Every number is a placeholder
# (docs/consolidated/02_orchestrator_core.md §13) — tune against real runs.
loops:
  verify_fix_loop:   { attempts: 3, wall_clock_s: 3600 }
default:             { attempts: 3, wall_clock_s: 3600 }
```

Loaded once at startup next to `registry.yaml`. `attempts` is the number of fix
cycles allowed before breach; `wall_clock_s` bounds total elapsed time from the
first cycle. Both must be positive integers or `load_policy` raises.

---

## 4. Schema — `retry_counters` + migration

`SCHEMA_VERSION` 1 → 2.

`retry_counters` table per §2.C. Added both to `SCHEMA_SQL` (fresh-DB path) and as
`_MIGRATIONS[1]` (v1 → v2 path).

Migration runner per §2.D: forward-only, one statement list per version step,
`PRAGMA user_version` bumped after each step, all inside `_ensure_schema`'s
existing transaction.

`work_items.status` CHECK unchanged — `needs_human` already covers cap breach
(`02` §4.3, `00` glossary). `worker_sessions.status` CHECK already includes
`capped_out`.

---

## 5. `policy.py`

Per §2.A. Three functions, two frozen dataclasses. No imports from `kraft.db` /
`kraft.store` — it receives primitives (`count`, `started_at`, `cap`, `now`) and
returns strings. `now` and `started_at` are ISO-8601 strings produced by
`store._now`; `check` parses both with `datetime.fromisoformat` and compares.

---

## 6. Executor changes

### 6.1 Thread `policy` through

`run`, `resume`, and the internal `_walk_node` gain a `policy: Policy` parameter,
passed like `registry`. `api.py` builds it once in the lifespan handler and stores
it on `app.state`; `reattach` receives it from the same place.

### 6.2 `_walk_node` — fix-loop branch

Current `_walk_node`: enter node → `gather` tasks → any failure → `mark_needs_human`
+ return `"needs_human"` → else `complete_node` + return `"ok"`.

New: if `node.get("fix_loop")` is set, wrap the measure-and-advance in a loop.
Structure the current `_walk_node` body as an inner `_measure_node(...)` that
enters the node, `gather`s the tasks, and returns `"ok"` / `"needs_human"` (no
`complete_node` — the caller decides). Then:

```
async def _walk_node(...):
    key = node.get("fix_loop")
    if not key:                                   # unchanged path
        r = await _measure_node(...)
        if r == "needs_human": return "needs_human"
        await db.write(store.complete_node(wid, node_id)); return "ok"

    cap = policy.resolve_cap(policy_obj, key)
    while True:
        r, failed_hooks = await _measure_node(..., report_failed=True)
        if r == "ok":
            await db.write(store.complete_node(wid, node_id)); return "ok"
        count, started_at = await db.write(store.bump_counter(wid, key, cap))
        if policy.check(count=count, started_at=started_at, cap=cap, now=store._now()) == "breached":
            await db.write(store.mark_sessions_capped_out(wid, node_id))
            await db.write(store.mark_needs_human(
                wid, node_id, f"{key} exhausted after {count - 1} fix cycle(s)"))
            return "needs_human"
        await db.write(lambda c: events.append(
            c, wid, "fix_cycle_started",
            {"node_id": node_id, "cycle": count, "failed_tasks": failed_hooks}))
        await _dispatch(db, run_dirs, "on.implementation.start", node, row, registry,
                        worktree, instruction_override=_FIX_PROMPT.format(
                            node_id=node_id, log_path=<fix-log path>))
        # fix task status is not branched on; loop re-measures
```

Notes:

- The first `_measure_node` (cycle 0) runs the tasks once with **no** counter
  bump — the counter counts *fix* cycles. First bump happens only after cycle 0
  comes back non-clean.
- `bump_counter` is the single source of `count` and `started_at`; `check` is
  pure. `count` after the Nth bump == N. Breach fires when `count > cap.attempts`,
  i.e. on the `(attempts + 1)`-th bump, after `attempts` fix cycles have run. The
  needs-human reason reports `count - 1` completed fix cycles.
- Each `_measure_node` call re-enters the node (`store.enter_node`), re-emitting
  `node_started` per cycle — acceptable and informative; the UI can collapse
  repeats. (Alternative: enter once before the loop. Plan picks one; entering per
  cycle is simpler and matches "a node may execute more than one cycle of its own
  tasks", `02` §7.3.)
- The fix task goes through the existing `_dispatch` agent path with a new
  `instruction_override` parameter (see §6.5). Its `worker_sessions` row has
  `node_id` = the fix-loop node (`verify`), `hook_point` = `on.implementation.start`
  — distinguishable from the real `implementation` node's task only by `node_id`
  and the surrounding `fix_cycle_started` event.
- A non-`fix_loop` node: behavior byte-identical to today.

### 6.3 `mark_sessions_capped_out`

```python
def mark_sessions_capped_out(conn, work_item_id, node_id) -> None:
    # UPDATE worker_sessions SET status='capped_out', exited_at=?
    #   WHERE work_item_id=? AND node_id=? AND status NOT IN ('done','capped_out')
```

Scoped to the node. `02` §7.2 step 2: on breach, "every measuring task in the
node → `capped_out`". A measuring session that ended `failed` on the final cycle
is exactly the offending task — flip it to `capped_out` (the distinct terminal
status `02` §4.3 defines). A session that ended `done` (a clean co-task, e.g. the
`noop` `on.review.local.run`) is left alone. `02` §7.1's "wait until *all* node
tasks terminal before dropping to needs_human" nuance is not separately modelled —
the fix loop only reacts once every measuring task has returned, so they are all
terminal at breach time anyway (see §11).

### 6.4 `resume()` and gates

`resume` passes `policy` into `_reconcile_current_node` → `_walk_node`, so a
crash-recovered `verify` node re-enters the fix loop with its counter row intact.
The gate change is §7; nothing else in `resume` moves for the fix loop.

### 6.5 `instruction_override` on `_dispatch` / `run_agent_task`

`_dispatch(db, run_dirs, task_hook, node, work_item_row, registry, worktree)` gains
a keyword-only `instruction_override: str | None = None`. Only the `kind == "agent"`
branch consults it: it passes `task_instruction=instruction_override or
work_item_row["title"]` to `run_agent_task` (whose signature already has
`task_instruction`). `builtin` / `subprocess` branches ignore it. Every existing
`_dispatch` call omits the kwarg → unchanged.

Fix prompt constant in `executor.py`:

```python
_FIX_PROMPT = (
    "The checks in node {node_id} failed for this work item. Fix the code so they "
    "pass. Make no unrelated changes. Failing-task detail: {log_path}"
)
```

`log_path` points at a per-cycle text file the loop writes listing the hook points
that returned `"failed"` (the plan pins where — `run_dirs.logs / f"{wid}-{node}-fix{cycle}.txt"`).

---

## 7. `resume` — honor an already-approved gate at the current node

Per §2.G. `reattach` and `api.lifespan` are unchanged (they already resume every
`active` work item). Only `executor.resume` changes:

```python
# in resume(), after computing `start` from current_node_id, before
# _reconcile_current_node:
node = nodes[start]
gate = node.get("gate_after")
if gate and _gate_cleared(db, work_item_id, gate):
    # approval survived the crash; the gate has already been passed.
    start += 1
    if start == len(nodes):                      # gate was on the last node
        await db.write(lambda c: store.mark_completed(c, work_item_id))
        ... beads.complete ...
        return "completed"
    node = nodes[start]
```

Then `_reconcile_current_node(node, ...)` and the tail loop proceed from `node`.
The `_maybe_gate(nodes[start])` call that currently follows reconciliation must
**not** re-fire for a node whose gate we just cleared — guard it with the same
`_gate_cleared` check, or skip it when `start` was advanced here.

`_gate_cleared(db, work_item_id, gate) -> bool` — reads the item's events, walks
backward, returns `True` iff the first `gate_*` event found is `gate_approved`
with `payload["gate"] == gate`. Lives in `executor.py`.

`policy` is also threaded into `resume` → `_reconcile_current_node` → `_walk_node`
so a crash-recovered `verify` node re-enters its fix loop (§6.4).

---

## 8. Events + API

- **New event:** `fix_cycle_started` — payload
  `{"node_id": str, "cycle": int, "failed_tasks": [str]}`. `events.type` is
  free-text, no schema change.
- **No new endpoints.** `GET /work-items/{id}/events?after_seq=` already streams
  it.
- `GET /health` response gains `invalid_policy: [str]` (filenames / reasons),
  parallel to `invalid_templates`. The reattach summary is unchanged (it already
  reports `resumed_work_items`).
- `POST /work-items` and the gate endpoints from effort #1 are unchanged apart
  from passing `policy=` into the `executor.run` / `resume` spawns.

---

## 9. Error handling

| Situation | Behavior |
|---|---|
| `policy.yaml` malformed / missing | `load_policy` raises at startup; file listed in `/health` `invalid_policy`; process refuses work (same as invalid registry) |
| Measuring task fails, cycles remain | bump counter, emit `fix_cycle_started`, run one fix task, re-measure |
| Measuring task fails, cap breached (`count > attempts`) | node's sessions → `capped_out`; `work_items.status = needs_human`; reason names the loop + `count - 1` completed fix cycles; walk stops |
| Fix task returns `"failed"` / raises | not branched on; loop re-measures; the re-failing measurement drives the next bump — a broken fix agent burns the budget and escalates |
| Wall-clock exceeded between cycles | next `policy.check` returns `breached` even if `count <= attempts` → `capped_out` + `needs_human` |
| Restart mid-fix-loop | counter row (incl. `started_at`) survives; `reattach` → `resume` → `_walk_node` re-enters the loop; wall-clock budget continues from original `started_at` |
| Restart after gate approve, before/at post-gate node (approve-then-crash) | `reattach` already lists the `active` item; `resume` sees the current node's gate is `gate_approved`, advances past it, resumes from the post-gate node — approval is **not** lost |
| `quick-task` verify fails | no `fix_loop` key → existing path: `needs_human`, stop (unchanged) |

---

## 10. Testing

All hermetic. Fix-loop tests use `make_repo` (the `sample_repo` fixture — a repo
whose `calc.py` has a failing test, `a - b` where the test wants `a + b`) plus the
existing `fake_agent.py`. That agent already supports the modes needed:
`KRAFT_FAKE_AGENT=fix` (rewrites `a - b` → `a + b`, so the *first* fix task makes
`on.test.run` pass) and `KRAFT_FAKE_AGENT=noop` (touches nothing, so the test
never passes → cap breach). No new fake-agent modes.

### `test_policy.py` (new)

- `load_policy` parses the shipped `templates/policy.yaml`; `verify_fix_loop`
  present with the expected `Cap`.
- `load_policy` on a malformed doc (negative `attempts`, missing `wall_clock_s`,
  non-dict) raises.
- `resolve_cap` returns the loop entry when present, `default` when absent.
- `check`: `ok` at `count == attempts`; `breached` at `count == attempts + 1`;
  `breached` when `now - started_at >= wall_clock_s` with `count <= attempts`
  (inject `now` and `started_at` as strings).

### `test_db.py` (extend)

- Fresh DB opens at `user_version == 2` with a `retry_counters` table.
- A hand-built `user_version == 1` DB (run `SCHEMA_SQL` minus `retry_counters`,
  set `PRAGMA user_version = 1`) migrates to 2 on open; `retry_counters` exists;
  existing rows in other tables untouched.
- Re-opening a v2 DB is a no-op.
- A `user_version == 3` DB still raises "newer than code".

### `test_fix_loop.py` (new)

Driving `executor.run` in-process with the `default` template (or a minimal
2-node `impl → verify(fix_loop)` template fixture) and a fake agent:

- **Fix succeeds cycle 1** (`KRAFT_FAKE_AGENT=fix`): cycle-0 `on.test.run` fails →
  1 `fix_cycle_started` (cycle 1) → fix task rewrites `calc.py` → re-measure clean
  → node completes, chain proceeds. `retry_counters` row for `verify_fix_loop`:
  `count == 1`.
- **Cap breach** (`KRAFT_FAKE_AGENT=noop`, `policy.yaml` fixture
  `verify_fix_loop.attempts: 2`): 2 `fix_cycle_started` events → 3rd bump breaches
  → `verify` measuring sessions `capped_out`, `work_items.status == needs_human`,
  `current_node_id == "verify"`, `retry_counters.count == 3`.
- **Wall-clock breach:** `policy.yaml` fixture `wall_clock_s: 0` → first `check`
  after cycle-0 failure breaches regardless of `attempts`; `needs_human`,
  `count == 1`.
- **No `fix_loop`** (regression guard): `quick-task` with a failing test →
  `needs_human` immediately, no `retry_counters` row, no `fix_cycle_started`.

### `test_resume.py` (extend)

- **Approve-then-crash keeps the approval.** Build a `default`-template work item,
  walk to the `spec` gate, `store.approve_gate(spec_approval)` (leaves
  `status='active'`, `current_node_id='spec'`, `gate_approved` in the log). Call
  `executor.resume(...)`. Assert: no second `gate_requested` for `spec_approval`;
  the walk proceeds to the `plan` node (next `gate_requested` is `plan_approval`);
  no duplicate `node_completed` for `spec`.
- **Gate not yet approved on resume still gates.** Same setup but *without*
  `approve_gate` → `resume` re-requests `spec_approval` (unchanged behavior for
  the genuine "crashed while awaiting" case).
- Existing `test_resume.py` scenarios (quick-task, no gates) stay green.

### `test_templates.py` (extend)

- `default.yaml` valid; `verify` node has `fix_loop == "verify_fix_loop"`.
- A template with `fix_loop: 123` (non-string) or `fix_loop: ""` → invalid.
- A node with `fix_loop` set and `tasks: []` → invalid.

### Regression

Full existing suite green. `quick-task` has no `fix_loop`; `policy` is threaded
but only consulted inside the fix-loop branch.

---

## 11. Known gaps (documented, not fixed here)

- **Per-work-item `config_overrides`.** `resolve_cap` takes only a key. `02` §7.1's
  override path needs a `work_items.config_overrides` column (or reuse of an
  existing JSON field) + one branch. No solo-user need yet.
- **Entry point B — gate reject-loops.** `gate_rejected` stays terminal (effort
  #1 behavior). `spec_approval` / `plan_approval` / `chain_finalized` re-invoking
  their producing hook, bounded by `<gate>_reject_loop`, with the note in the
  system prompt, is a follow-up. The `retry_counters` table and `policy.py` are
  built to absorb it — new keys, same helpers.
- **`mr_checks_fix_loop`.** The `mr_checks` node's hooks (`on.ci.poll`,
  `on.review.mr.run`) are `noop`; there is no CI, no GitLab adapter, no MR branch
  to push fix commits to. Lands with effort #5. The `fix_loop` node key + the
  `_walk_node` branch already generalize — `mr_checks` just needs
  `fix_loop: mr_checks_fix_loop` and its cross-repo cadence rules (`02` §7.2).
- **`execution_worker` retry budget + carve-out.** `02` §7.2's carve-out says a
  coordinator-launched fix task must not increment the
  `(work_item_id, execution_worker)` producer budget. That budget is not built,
  so the carve-out is a no-op here. When the producer budget lands, the fix task
  dispatch must pass a "don't count this" flag.
- **`plan_diverged` → guidance gate.** Not handled: the adapters cannot emit a
  `plan_diverged` status and the guidance gate (`01` §8) is unbuilt. When the real
  Execution Worker adds the status, the fix loop grows a branch that routes to the
  guidance gate and resumes on `on.guidance.provided` with the counter preserved.
- **Structured measuring results.** "Non-clean" is `status == "failed"`. No
  `findings[]` / `failures[]`, so no severity threshold (`02` §7.2 defers the
  threshold anyway) and the fix prompt is generic. Lands with the real Execution
  Worker / review adapter.
- **`capped_out` sibling semantics.** `02` §7.1: on breach under node concurrency,
  siblings are *not* killed and the work item drops to `needs_human` only once
  *every* node task is terminal. This effort's fix loop reacts to *already
  terminal* measuring results, so in practice all tasks are terminal at breach
  time — but the "wait for siblings" logic is not explicitly implemented. Revisit
  when a node has a genuinely long-running measuring task alongside a fast one.
- **§11 concurrency ceiling.** The per-hook-type `max_concurrent` limit is not
  enforced; the fix task occupies no counted slot.

---

## 12. Build order

1. **`policy.py` + `policy.yaml` + `test_policy.py`.** Pure, no deps. Green first.
2. **Schema: `retry_counters` + migration runner + `store.bump_counter` /
   `read_counter` + `test_db.py`.** DB layer, no executor.
3. **`templates`: `fix_loop` validation + `default.yaml` `verify` node +
   `test_templates.py`.** Confirm `materialize` passes the key through.
4. **`executor`: thread `policy`; extract `_measure_node`; `_walk_node` fix-loop
   branch; `store.mark_sessions_capped_out`; `instruction_override` on `_dispatch`
   (§6.5); `_FIX_PROMPT`; `test_fix_loop.py`.** The core.
5. **`executor.resume`: `_gate_cleared` + skip an already-approved gate at the
   current node + `test_resume.py`.** Closes Kraft-2v0.
6. **`api`: load `policy`, thread it through the `run`/`resume` spawns; `/health`
   `invalid_policy`.** Wire-up.
7. Close Kraft-ue7 and Kraft-2v0.
