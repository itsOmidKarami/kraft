# Skeleton Chunk A — Foundation (mini-spec)

**Status:** draft, awaiting review
**Date:** 2026-09-01
**Bead:** Kraft-rnx.1
**Parent design:** `docs/superpowers/specs/2026-09-01-kraft-walking-skeleton-design.md` (§2, §3)
**Master plan:** `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` (Chunk A)

---

## 0. Scope

Pure data layer. Three modules, no orchestration:

- `kraft.db` — SQLite schema, migrations, WAL, single asyncio-serialized writer.
- `kraft.events` — append an event inside a writer transaction; read events `after_seq`.
- `kraft.templates` — load `templates/*.yaml` + `templates/registry.yaml`, validate
  every referenced hook against the registry, materialize a chain to `chain_definition` JSON.

**Out (Chunks B/C):** the writer queue is the only async; no subprocess, no
executor, no adapters, no API, no reattach. No `work_items` / `worker_sessions`
row-mutation helpers — those writes are composed by the executor in Chunk B.
Chunk A ships only the schema for those tables plus the generic atomic-write seam.

**Stack:** Python 3.14, stdlib `sqlite3`, PyYAML, pytest. Code in `src/kraft/`,
tests in `tests/`, templates in `templates/`. Runtime DB at
`.kraft-run/orchestrator.db` (add `.kraft-run/` to `.gitignore`).

---

## 1. `kraft.db`

### 1.1 Schema (one embedded SQL string, `schema.sql` sibling or inline)

Tables exactly per design §2:

```sql
CREATE TABLE work_items (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  repo             TEXT NOT NULL,
  chain_template   TEXT NOT NULL,
  chain_definition TEXT NOT NULL,           -- JSON
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN ('active','needs_human','completed')),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,               -- JSON
  created_at   TEXT NOT NULL
);
CREATE INDEX idx_events_seq ON events(seq);

CREATE TABLE worker_sessions (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending','running','done','failed','capped_out','paused','unknown')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  exited_at      TEXT
);
CREATE INDEX idx_worker_sessions_status ON worker_sessions(status);
```

Notes:
- `events.type` is not CHECK-constrained — the type list in design §2 evolves per
  chunk; keep it open.
- `paused` / `capped_out` are in the CHECK for forward-compat, never written by
  skeleton code.
- `events` has no `ON DELETE` — nothing deletes work items in the skeleton.

### 1.2 Migrations

`PRAGMA user_version` gate. `SCHEMA_VERSION = 1`.

- Open connection → read `user_version`.
- `0` → execute the schema DDL in one transaction, set `user_version = 1`.
- `1` → no-op.
- `> 1` → raise (DB newer than code).

Re-running `migrate()` on an already-migrated DB does nothing. No down-migrations.

### 1.3 Connections & pragmas

On every connection open:
```
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

`Database` holds:
- one **writer** connection, used only by the writer task;
- one **reader** connection, used by `read()` / `kraft.events.read_after`.

WAL lets the reader see committed data while a write is in flight. Both
connections live on the same thread / event loop (skeleton is single-process,
single-loop); `check_same_thread` stays default.

### 1.4 Single-writer queue

```python
class Database:
    @classmethod
    async def open(cls, path: str | Path) -> "Database": ...
        # opens both connections, runs migrate(), starts the writer task

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T: ...
        # enqueue fn; writer runs it in ONE transaction:
        #   result = fn(conn); conn.commit()  -> future.set_result(result)
        #   on any exception: conn.rollback() -> future.set_exception(exc)

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T: ...
        # run fn on the reader connection, no transaction management

    async def close(self) -> None: ...
        # drain/stop the writer task, close both connections
```

- Queue is `asyncio.Queue[tuple[fn, Future]]`.
- Writer loop: one item at a time → total order over all writes.
- `fn` does **all** mutations for one logical change — e.g. a `work_items` row
  update **and** its `events.append` — so a row and its event commit together or
  not at all. This is the design §2 invariant.
- Exceptions in `fn` roll the whole transaction back; the awaiting caller sees
  the exception.
- `close()` after the queue is drained; in-flight `fn` finishes first.

`ponytail:` one global writer connection serializes all writes. Fine for a
single-orchestrator skeleton; shard by work-item only if write throughput ever
matters.

---

## 2. `kraft.events`

```python
def append(conn: sqlite3.Connection, work_item_id: str, type: str,
           payload: dict) -> int:
    # INSERT into events (payload = json.dumps(payload), created_at = _now())
    # return cursor.lastrowid  (the new seq)
    # MUST be called from inside a Database.write() fn — takes the live conn,
    # does not commit.

def read_after(conn: sqlite3.Connection, after_seq: int,
               work_item_id: str | None = None) -> list[dict]:
    # SELECT seq, work_item_id, type, payload, created_at
    #   FROM events WHERE seq > ? [AND work_item_id = ?] ORDER BY seq ASC
    # payload -> json.loads; return list of dicts
    # exclusive of after_seq; pass 0 to get everything
```

`read_after` is called via `Database.read(lambda c: events.read_after(c, n, wid))`.
`work_item_id` filter is optional now (Chunk C's `GET /work-items/{id}/events`
passes it; Chunk A tests exercise both).

---

## 3. `kraft.templates`

### 3.1 Files shipped in `templates/`

`templates/quick-task.yaml` — verbatim from design §3:

```yaml
id: quick-task
nodes:
  - { id: env_setup,      tasks: [on.env.prepare],         gate_after: null }
  - { id: implementation, tasks: [on.implementation.start], gate_after: null }
  - { id: verify,         tasks: [on.test.run],            gate_after: null }
```

`templates/registry.yaml` — verbatim from design §3:

```yaml
hooks:
  on.env.prepare:          { kind: builtin,    handler: env_setup }
  on.implementation.start: { kind: agent,      command: claude }
  on.test.run:             { kind: subprocess, command: [pytest, -q] }
```

Registry schema (pinned for Chunk A):
- top-level key `hooks:` → mapping of hook-point string → binding.
- binding: `kind` is required, one of `builtin | agent | subprocess`.
- `kind: builtin` → requires `handler` (string).
- `kind: agent` → requires `command` (string).
- `kind: subprocess` → requires `command` (list of strings).
- Unknown `kind`, or a binding missing its required field → the **registry**
  fails to load (raise `RegistryError`). A broken registry is a startup failure,
  not a per-template one.
- The `command` for `on.implementation.start` is overridden to the fake-agent
  script in Chunk B/C tests — Chunk A just loads it as written.

### 3.2 API

```python
@dataclass(frozen=True)
class Registry:
    hooks: dict[str, dict]        # hook-point -> binding dict

def load_registry(path: str | Path) -> Registry:
    # parse + validate per 3.1; raise RegistryError on any problem

@dataclass(frozen=True)
class Template:
    id: str
    nodes: list[dict]            # [{id, tasks: [str], gate_after: None}]

@dataclass(frozen=True)
class TemplateSet:
    valid: dict[str, Template]   # id -> Template
    invalid: dict[str, str]      # id -> human-readable reason

def load_templates(dir: str | Path, registry: Registry) -> TemplateSet:
    # load every *.yaml in dir EXCEPT registry.yaml
    # for each: shape-check (id str, nodes list, each node has id + tasks list),
    #   then hook-check: every hook in every node.tasks must be a key in
    #   registry.hooks.
    # shape failure OR unknown hook -> invalid[id] = reason
    #   (reason names the template id and the offending hook / field)
    # a YAML parse error on one file -> invalid[<filename stem>] = reason,
    #   other files still load.
    # duplicate id across files -> second one invalid.

def materialize(template: Template) -> dict:
    # returns chain_definition JSON-serializable dict:
    #   {"template_id": template.id,
    #    "nodes": [{"id": n["id"], "tasks": list(n["tasks"]),
    #               "gate_after": n.get("gate_after")}]}
    # NO current_node_id key — the executor (Chunk B) sets the column separately.
    # tasks stay as hook-point strings ("matches the template"); the executor
    # resolves them against the registry at run time (registry is
    # startup-validated, so this is safe).
```

`materialize` requires `template` to be from `TemplateSet.valid` — caller's
responsibility (Chunk C's `POST /work-items` checks the resolvable set and
returns `422` otherwise).

### 3.3 Chain Validator scope

This is the design §2/§12 "Chain Validator", **startup tier only**: hooks
referenced must exist. No cycle checks (chains are linear lists), no gate-config
checks (gates deferred), no runtime re-validation.

---

## 4. Tests (`tests/`, all hermetic — no `claude`, no network)

Async tests use `asyncio.run()` around an inner coroutine — no `pytest-asyncio`
dependency.

| test | asserts |
|---|---|
| `test_migrations` | migrate an empty file → the 3 tables + indexes exist; migrate again → no error, `user_version` still 1, no duplicate objects. |
| `test_atomic_write` | a `write(fn)` where `fn` inserts a `work_items` row **and** `events.append`s, then raises → neither the row nor the event is present. A `write(fn)` that succeeds → both present, same `updated_at`/`created_at` wiring. |
| `test_after_seq` | append N events across several `write()` calls → `read_after(0)` returns all in ascending `seq`; `read_after(k)` returns only `seq > k`; `read_after(k, work_item_id=x)` filters by item too. |
| `test_template_validation` | `load_templates` on a good dir → `quick-task` in `valid`. On a dir containing a template with hook `on.bogus` → that id in `invalid` with a reason naming `on.bogus`; `quick-task` (also in that dir) still in `valid`. |
| `test_chain_materialization` | `materialize(valid["quick-task"])` → `{"template_id": "quick-task", "nodes": [env_setup, implementation, verify]}`, each node's `tasks` equals the YAML list, `gate_after` is `None`, no `current_node_id` key. Result round-trips through `json.dumps`/`loads`. |

Test fixtures:
- good templates dir = the real `templates/`.
- bad templates dir = a `tmp_path` with a copied `registry.yaml` + `quick-task.yaml`
  + a hand-written `bad.yaml` referencing `on.bogus`. Never ship a broken
  template in `templates/`.

---

## 5. Chunk A "done when" (from master plan) — verification checklist

- [x] Migrations create the schema from empty; re-running is a no-op. → `test_migrate_creates_schema_from_empty`, `test_migrate_is_idempotent` (+ `test_migrate_atomicity_rolls_back_on_error`, `test_migrate_rejects_newer_db`)
- [x] A row update + event append are atomic (fail mid-write → neither lands). → `test_row_and_event_commit_atomically`
- [x] `after_seq` reads return events in `seq` order, exclusive of the given seq. → `test_append_returns_increasing_seq_and_read_after_is_exclusive`, `test_read_after_filters_by_work_item`
- [x] A template with an unknown hook is rejected with a clear error and excluded
      from the resolvable set; valid templates still resolve. → `test_unknown_hook_quarantines_only_that_template`, `test_load_registry_rejects_bad_bindings`
- [x] Materializing `quick-task.yaml` produces the 3-node `chain_definition` with
      `current_node_id` unset. → `test_materialize_quick_task_from_shipped_templates`
- [x] Hermetic tests green: `uv run pytest -v` → 24 passed, output pristine, no `e2e` collected.

---

## 6. Open questions for review

1. **`chain_definition` shape** — pinned above as a near-passthrough
   (`{template_id, nodes:[{id,tasks,gate_after}]}`), tasks left as hook strings,
   resolved against the registry at run time. Alternative: bake the resolved
   adapter binding into each task at materialize time ("frozen at intake").
   Chose passthrough: matches the `test_chain_materialization` wording
   ("matches the template") and keeps registry config out of the frozen blob.
   OK?

2. **No row helpers in Chunk A** — `work_items` / `worker_sessions` insert/update
   helpers land in Chunk B with the executor that owns those writes. Chunk A's
   atomicity test uses raw SQL in its `write(fn)`. OK, or want thin
   `create_work_item()` / `update_work_item()` helpers now?

3. **Reader connection** — one shared reader connection on the same loop, vs. a
   fresh short-lived connection per `read()`. Went with one shared; simplest and
   correct for a single-loop skeleton.

4. **`_now()`** — `datetime.now(UTC).isoformat()` (e.g. `2026-09-01T13:00:00+00:00`).
   Good enough, or want a `Z`-suffixed form?
