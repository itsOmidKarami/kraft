# Full `default.yaml` Chain + Scheduled Gates — Design

**Status:** design, approved in chat, awaiting written-spec review
**Date:** 2026-09-02
**Bead:** Kraft-kfi
**Consolidated design refs:** `docs/consolidated/01_conceptual_model.md` §3, §8; `docs/consolidated/02_orchestrator_core.md` §4, §7.2 (entry point B only), §10.1
**Master plan:** post-skeleton effort #1 of `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` §3

---

## 0. What this is

The walking skeleton ships one template (`quick-task.yaml`: `env_setup → implementation → verify`, no gates) and an executor that walks nodes with zero gate handling. This effort adds:

1. The full 10-node `default.yaml` chain from `01` §3.2.
2. The four scheduled human gates: `spec_approval`, `plan_approval`, `chain_finalized`, `human_review_approval`.
3. The API surface to approve/reject a gate.

It does **not** add the policy engine, retry counters, the backward-motion reject-loop coordinator, pause/steer, the guidance gate, or any real plugin behind the nine currently-unbound hooks. Those are separate efforts (#2 onward). The nine unbound hooks get **noop placeholder bindings** so the full chain is walkable hermetically end to end.

---

## 1. Scope

### In

- `templates/default.yaml` — the 10-node chain, verbatim from `01` §3.2.
- `templates/registry.yaml` — noop bindings for the nine unbound hooks.
- `kraft.templates` — validate `gate_after` against the known gate set.
- `kraft.builtins` — a `noop` builtin handler.
- `kraft.executor` — dispatch the `noop` handler; stop the walk at a node with `gate_after`.
- `kraft.store` — `request_gate` / `approve_gate` / `reject_gate` write helpers.
- `kraft.api` — `POST /work-items/{id}/gates/{gate}/approve` and `.../reject`.
- Tests: template validation, hermetic gate walk, API endpoints.

### Out (later efforts)

- `retry_counters` table, per-hook caps, `<gate>_reject_loop` counters.
- Backward-motion coordinator: gate reject re-invoking the producing hook (`02` §7.2 entry point B). #1's reject is terminal — `gate_rejected` event + stays `needs_human`.
- Backward-motion coordinator: `verify` / `mr_checks` fix loops (`02` §7.2 entry point A).
- Pause / steer / resume, the `paused` status, `pending_steer_context` (`02` §10.2).
- Guidance gate / `plan_diverged` (`01` §8).
- Real planning, chain-review, local/MR review, CI poll, MR open, merge, human-review plugins.
- Chain review actually rewriting `chain_definition` (`01` §3.5) — the `chain_review` node runs its noop task and gates, nothing is spliced.
- Multi-repo / `work_item_repos` (federation, effort #6).

---

## 2. Design decisions

### A. Gate-wait state representation

A work item parked at a gate has `status = 'needs_human'`. The pending gate is **derived from the event log**: the most recent event of type `gate_requested` / `gate_approved` / `gate_rejected` for the work item is a `gate_requested` whose payload `gate` names the pending gate.

Rationale: `02` §4.1 pins the `work_items.status` enum to `active | paused | needs_human | completed` and states `needs_human` "covers both scheduled-gate waits and cap breaches". `02` §10.1 describes the UI running a "client-side awaiting-gate approval inference" — i.e. the design already expects the pending gate to be derived, not stored in a column.

Rejected alternatives: an `awaiting_gate` status value (contradicts the pinned enum); a `pending_gate` column on `work_items` (not in the `02` §4.1 schema; the event log is already authoritative).

### B. Resume after approve

`executor.run()` gains `start_index: int = 0`. On approve, the API handler:

1. Loads `chain_definition`, finds the node whose `gate_after == {gate}`, takes its index `g`.
2. Spawns `executor.run(..., start_index=g + 1)`.

`run()` skips `load_chain` when `start_index != 0` (the chain was already loaded at intake / first run). `current_node_id` is advanced by `_walk_node`'s `enter_node` call on the next node, so no `node_completed` is re-emitted for the gated node.

Rejected alternatives: a separate `resume_after_gate()` (duplicates `run()`'s tail); reusing the crash-recovery `resume()` (its `_reconcile_current_node` re-runs `complete_node`, double-emitting `node_completed` — the exact defect tracked in Kraft-gbt).

### C. Noop placeholder hooks

Nine hooks in `default.yaml` have no plugin: `on.spec.requested`, `on.plan.requested`, `on.chain.review_ready`, `on.review.local.run`, `on.mr.open`, `on.ci.poll`, `on.review.mr.run`, `on.human_review.requested`, `on.merge`.

Each is bound in `registry.yaml` as `{ kind: builtin, handler: noop }`. The `noop` handler writes a `worker_sessions` row, marks it `done`, emits `worker_session_started` / `worker_session_exited`, and returns `"done"` — no subprocess.

Rationale: `subprocess` bindings run with `cwd` = the work item's worktree (`executor._dispatch`). The `spec`, `plan`, and `chain_review` nodes execute **before** `env_setup`, so no worktree directory exists yet and `Popen(cwd=...)` would raise `FileNotFoundError`. A no-subprocess handler sidesteps this and better models "placeholder, no real work". One handler branch is less churn than a new adapter `kind`.

The noop bindings ship in the real `templates/registry.yaml` with a comment marking them placeholders to be replaced by their respective plugin efforts.

---

## 3. `default.yaml`

Verbatim from `01` §3.2:

```yaml
id: default
nodes:
  - id: spec
    tasks: [on.spec.requested]
    gate_after: spec_approval
  - id: plan
    tasks: [on.plan.requested]
    gate_after: plan_approval
  - id: chain_review
    tasks: [on.chain.review_ready]
    gate_after: chain_finalized
  - id: env_setup
    tasks: [on.env.prepare]
  - id: implementation
    tasks: [on.implementation.start]
  - id: verify
    tasks: [on.test.run, on.review.local.run]
  - id: open_mr
    tasks: [on.mr.open]
  - id: mr_checks
    tasks: [on.ci.poll, on.review.mr.run]
  - id: human_review
    tasks: [on.human_review.requested]
    gate_after: human_review_approval
  - id: merge
    tasks: [on.merge]
```

`quick-task.yaml` is unchanged. The "chain review fires only if the chain has both a `plan` node and a `chain_review` node" rule (`01` §3.5) needs **no executor logic** — it follows from template composition: `default.yaml` has both, `quick-task.yaml` has neither.

`verify` and `mr_checks` run their two tasks concurrently via the existing `asyncio.gather` in `_walk_node`. Under noop bindings both tasks return `done` immediately; the read-only concurrency rule (`01` §3.3) is not violated because noop tasks touch nothing.

---

## 4. `registry.yaml` additions

```yaml
  # --- placeholder bindings: replaced by their plugin efforts (03_plugin_adapters) ---
  on.spec.requested:         { kind: builtin, handler: noop }
  on.plan.requested:         { kind: builtin, handler: noop }
  on.chain.review_ready:     { kind: builtin, handler: noop }
  on.review.local.run:       { kind: builtin, handler: noop }
  on.mr.open:                { kind: builtin, handler: noop }
  on.ci.poll:                { kind: builtin, handler: noop }
  on.review.mr.run:          { kind: builtin, handler: noop }
  on.human_review.requested: { kind: builtin, handler: noop }
  on.merge:                  { kind: builtin, handler: noop }
```

`on.test.run` stays `{ kind: subprocess, command: [pytest, -q] }` — the `verify` node runs it for real against the worktree, same as `quick-task`.

`kraft.templates.load_registry` currently accepts `builtin` with any string `handler`. No loader change needed for the new bindings; the `noop` handler is resolved in `executor._dispatch`.

---

## 5. `gate_after` validation

`kraft.templates.load_templates` currently accepts any node shape with a string `id` and a list-of-strings `tasks`; `gate_after` is read but unvalidated.

Add: if any node's `gate_after` is neither `null`/absent nor one of `{spec_approval, plan_approval, chain_finalized, human_review_approval}`, the template is marked **invalid** (same mechanism as an unknown hook — excluded from the resolvable set, surfaced in `GET /health`).

Rationale: the approve/reject endpoints trust `gate_after` values from `chain_definition` to map a gate name to a node. A typo'd gate would otherwise produce a chain with an unreachable gate.

---

## 6. Executor changes

### 6.1 `_dispatch` — noop branch

```python
if kind == "builtin" and binding.get("handler") == "noop":
    return await _builtins.noop(db, run_dirs, hook_point=task_hook, **common)
```

Placed alongside the existing `handler == "env_setup"` branch.

### 6.2 `run()` — stop at a gate

Signature gains `start_index: int = 0`.

- `load_chain(nodes[0])` runs only when `start_index == 0`.
- The node loop iterates `nodes[start_index:]`.
- After a node completes cleanly, if `node["gate_after"]` is set:
  - `await db.write(store.request_gate(work_item_id, node["id"], gate))`
  - return `"awaiting_gate"` (the spawned task exits; the work item sits at `needs_human`).
- A node without `gate_after` proceeds as today.
- After the last node with no trailing gate: `mark_completed` + `beads.complete`, as today.

`materialize` already emits every node with a `gate_after` key (defaulting to `None`), so `node["gate_after"]` is always safe to read.

### 6.3 No change to `resume()`

Crash recovery is unchanged. A gated work item is `needs_human`, which `reattach` does not resume (`reattach` only resumes `status = 'active'`). Approval after a restart works because the approve handler rebuilds everything it needs from `chain_definition` + the event log and spawns a fresh `run(start_index=...)`.

---

## 7. Store helpers

```python
def request_gate(conn, work_item_id, node_id, gate) -> None:
    # UPDATE work_items SET status='needs_human', updated_at=? WHERE id=?
    # events.append(conn, work_item_id, "gate_requested", {"gate": gate, "node_id": node_id})

def approve_gate(conn, work_item_id, gate) -> None:
    # UPDATE work_items SET status='active', updated_at=? WHERE id=?
    # events.append(conn, work_item_id, "gate_approved", {"gate": gate})

def reject_gate(conn, work_item_id, gate, note) -> None:
    # UPDATE work_items SET updated_at=? WHERE id=?   (status stays 'needs_human')
    # events.append(conn, work_item_id, "gate_rejected", {"gate": gate, "note": note})
```

`events.type` is free-text TEXT with no CHECK constraint — no migration for the three new event types. `work_items.status` transitions stay within the existing CHECK set (`active`, `needs_human`, `completed`).

---

## 8. API endpoints

### `POST /work-items/{wid}/gates/{gate}/approve`

- 404 if the work item is unknown.
- 404 if `{gate}` is not one of the four known gates.
- 409 if the work item's pending gate (derived per §2.A) is not `{gate}` — covers "no gate pending", "different gate pending", "already approved".
- Otherwise: `await db.write(store.approve_gate(wid, gate))`, then compute `g` = index of the node with `gate_after == gate` in `chain_definition`, then `_spawn(run(..., start_index=g + 1))`.
- 200 with the refreshed work-item row.

### `POST /work-items/{wid}/gates/{gate}/reject`

- Body: `{ "note": string }` — `note` required (Pydantic model; missing → 422).
- Same 404 / 409 checks as approve.
- `await db.write(store.reject_gate(wid, gate, note))`. No executor spawn. Work item stays `needs_human`.
- 200 with the refreshed row.

### Pending-gate helper

```python
def _pending_gate(st, wid) -> str | None:
    # read events for wid where type in (gate_requested, gate_approved, gate_rejected),
    # ordered by seq; return payload["gate"] of the last one iff it is gate_requested,
    # else None.
```

Shared by both endpoints.

### `NewWorkItem` default

`chain_template` default stays `"quick-task"`. Callers opt into the full chain with `{"chain_template": "default"}`.

---

## 9. Error handling

| Situation | Behavior |
|---|---|
| Approve/reject unknown work item | 404 |
| Approve/reject unknown gate name | 404 |
| Approve/reject when that gate is not pending | 409 |
| Reject with no `note` | 422 (Pydantic) |
| Real task (e.g. `on.test.run`) fails in a non-gate node | existing `_walk_node` path → `needs_human`, walk stops |
| noop task | cannot fail (no subprocess) |
| Restart while gated | item is `needs_human`, not resumed; approve endpoint spawns `run()` from DB state |
| Approve gate, orchestrator crashes before `run()` re-spawns | item is `active` with `current_node_id` still the gated node and no live task; **known gap**, see §11 |

---

## 10. Testing

All hermetic (noop registry; `on.test.run` against the existing sample-repo fixture).

### `test_templates.py`

- `default.yaml` is in the valid set; 10 nodes; the four `gate_after` values present and correct.
- A template with `gate_after: bogus_gate` lands in the invalid set with a clear message.

### `test_gates.py` (new)

Driving `executor.run` in-process with the `default` template:

- Walk stops at `spec` — `status == needs_human`, last gate event is `gate_requested {gate: spec_approval}`, no worker session past the `spec` node.
- `store.approve_gate` + `run(start_index=1)` advances to and stops at `plan`.
- Approving all four gates in sequence drives the chain to `completed`; the bead is closed.
- `reject_gate` writes `gate_rejected` with the `note` in the payload; `status` stays `needs_human`; no further nodes run.

### `test_api.py` (extend)

- `POST /work-items {chain_template: "default"}` → poll events → `gate_requested` for `spec_approval`.
- `POST .../gates/spec_approval/approve` → 200 → next poll shows `spec` gate approved and `plan` node started.
- `POST .../gates/plan_approval/approve` while `spec_approval` is the pending gate → 409.
- `POST .../gates/spec_approval/reject` with `{}` (no note) → 422.
- `POST .../gates/spec_approval/reject` with `{note: "..."}` → 200, `gate_rejected` event carries the note, work item still `needs_human`.

### Regression

Every existing `quick-task` test stays green — `quick-task.yaml` has no gate nodes, so `run()`'s new gate branch is never taken.

---

## 11. Known gaps (documented, not fixed here)

- **Approve-then-crash window.** `store.approve_gate` sets `status = 'active'` and commits; if the orchestrator dies before `_spawn(run(...))` schedules, restart's `reattach` sees an `active` work item with no live worker session and `current_node_id` still on the gated node — and `reattach` only rebuilds tasks it can tie to a `worker_sessions` row. The item stalls silently. Fix belongs with a general "resume `active` work items with no live task" pass in the reattach/policy work (effort #2). File a bead. Tracked as Kraft-2v0.
- **Chain review is inert.** The `chain_review` node runs a noop and gates, but nothing rewrites `chain_definition` on approval (`01` §3.5). Real chain-review is its own effort.
- **`gate_rejected` is terminal.** No re-invocation of the producing hook, no counter. `02` §7.2 entry point B is effort #2.

---

## 12. Build order

1. `default.yaml` + `registry.yaml` noop bindings + `gate_after` validation + `noop` builtin handler + `_dispatch` branch. Test: `default` template resolves and walks to `completed` with **no** gate handling yet (temporarily treating `gate_after` as ignored) — or jump straight to step 2.
2. `store` gate helpers + `run(start_index=...)` + stop-at-gate. Test: `test_gates.py` in-process walk.
3. API approve/reject endpoints + `_pending_gate` helper. Test: `test_api.py` extensions.
4. File the "resume active work items with no live task" bead (§11 gap 1).
