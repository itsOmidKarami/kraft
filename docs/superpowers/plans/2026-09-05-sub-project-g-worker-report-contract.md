# Worker Report Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent say "done, but I have doubts" or "I was never told X" instead of forcing both into `done` or `failed` — and make each of those land somewhere a human can act on.

**Architecture:** Two values join the `worker_sessions.status` CHECK. `done_with_concerns` advances the chain exactly like `done` and surfaces its text at the next gate. `needs_context` stops the item and routes the agent's question through the steer channel — which requires widening two guards that today accept only `paused`. Fix cycles additionally carry the previous attempt's result file by path.

**Tech Stack:** Python 3.14, SQLite, pytest; React 18 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-g-worker-report-contract-design.md`

**Beads:** `Kraft-8mu.7`. Model escalation is `Kraft-8mu.7.1`, blocked by nothing now that C has landed, and **not** in this plan.

## Global Constraints

- Python 3.14+. Unparenthesized `except A, B:` (PEP 758) is used here and is correct.
- **An unrecognized status still resolves to `failed`.** The conservative default is correct and must survive.
- `tests/test_fix_loop.py` and `tests/test_findings_loop.py` are sub-project F's backward-compatibility guarantees. They stay green.
- `done_with_concerns` changes **no** control flow. Every place a status is compared must treat it as advancing.
- No new table.
- Run `just lint` before each commit.

## What the plan review found, recorded so it is not re-made

The first draft was unexecutable. Five blockers:

1. **The `needs_context` answer path did not exist.** `/steer` and `/resume` 409 unless `status == "paused"`; the stop sets `needs_human`; `/retry` refuses nodes without a `fix_loop`. On a plain node the stop was a dead end. The spec has been corrected and the fix is to widen the two guards.
2. **Nothing made an agent capable of emitting the new statuses.** `adapters/agent.py`'s injected context never mentions them, and `tests/support/fake_agent.py` hard-codes `{"status": "done"}` with no knob — so the feature was inert *and* untestable.
3. **The migration's column list omitted `created_at`**, which is `NOT NULL`, so the rebuild's `INSERT ... SELECT` would have failed on the first existing row. It also dropped `idx_worker_sessions_status` without recreating it.
4. **`chain_env` and `loop_env` did not exist**, and `renderDetail` takes no arguments.
5. **The `_ADVANCING` sweep was scoped to `executor.py`** and missed `store.py`'s `mark_sessions_capped_out` and `_reconcile_current_node`'s `all(... == "done")`.

Also corrected: `plan_diverged` is documented but **not implemented**, so it is a precedent in prose only; and the retry counter is bumped *before* the fix dispatch, so the no-cycle carve-out is only meaningful for a `needs_context` from a **measuring** task.

---

### Task 1: The statuses, end to end in the data layer

**Files:**
- Modify: `src/kraft/db.py` (`SCHEMA_VERSION`, `worker_sessions` DDL, `_MIGRATIONS`)
- Modify: `src/kraft/adapters/subprocess.py` (`_resolve_result_file`, plus the field readers)
- Test: `tests/test_db.py`, `tests/test_adapters_subprocess.py`

**Interfaces:**
- `worker_sessions.status` accepts `done_with_concerns` and `needs_context`.
- `subprocess._resolve_result_file` returns either when the result file names it.
- `subprocess.read_concerns(path) -> str | None`, `subprocess.read_question(path) -> str | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py` imports **only `db`** today — add `store` if you use it, or avoid it.

The migration test must actually run the migration. `db.migrate` on a fresh connection takes the `version == 0` path and executes `SCHEMA_SQL` directly, so a test that connects and migrates never touches `_MIGRATIONS` at all. Use the file's existing `_build_old_db(conn, 9, replace=(...))` helper — `test_migrate_v4_to_v5_rebuilds_work_items_for_the_paused_status` is the shape to copy. Build a v9 database whose `worker_sessions` CHECK lacks the two new values, insert a row with values in **every** column, migrate, and assert: `user_version == db.SCHEMA_VERSION`, the row survived with each field intact, and the two new statuses are now accepted.

Add the negative case too — `tests/test_db.py`'s existing `test_status_check_constraints` only covers `work_items`, so nothing would notice if the `worker_sessions` CHECK were dropped entirely.

And assert `idx_worker_sessions_status` still exists after the migration.

`tests/test_adapters_subprocess.py` uses `from kraft.adapters import subprocess as sp` and function-local `from kraft.adapters.subprocess import _resolve` — there is no `_subprocess` name in that file. Follow its actual imports.

```python
def test_result_file_can_report_done_with_concerns(tmp_path):
def test_result_file_can_report_needs_context(tmp_path):
def test_unknown_status_still_resolves_to_failed(tmp_path):
    """Regression guard — passes before this task too."""
def test_read_concerns_and_read_question(tmp_path):
def test_the_field_readers_tolerate_a_broken_file(tmp_path):
    """Missing file, non-JSON, non-mapping, absent key, and non-UTF-8 bytes.

    UnicodeDecodeError is a ValueError, so `except OSError` does not catch it.
    That exact clause has been wrong three times in this codebase already.
    """
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_db.py tests/test_adapters_subprocess.py -v`
Expected: the CHECK and reader tests FAIL. `test_unknown_status_still_resolves_to_failed` passes already — it is a regression guard, and Step 2 not turning it red is correct.

- [ ] **Step 3: Write the implementation**

`SCHEMA_VERSION` 9 → **10**; the new migration is keyed **9** (`_MIGRATIONS[8]` is A's `base_ref`). Note the dict's keys are not in source order — append the key, not the text.

Update the base `worker_sessions` CHECK to:

```
'pending','running','done','failed','capped_out','paused','unknown',
'done_with_concerns','needs_context'
```

The migration rebuilds the table following `_MIGRATIONS[4]`'s create-new / insert-select / drop / rename shape. **All twenty columns**, in this order — this list is exhaustive, not illustrative:

```
id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
result_path, status, attempt, session_summary_ref, created_at, started_at,
round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at
```

`created_at` is `NOT NULL`; omitting it fails on the first existing row. Recreate `CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)` — it dies with the dropped table, and nothing in the suite compares indexes, so its loss would ship silently.

In `adapters/subprocess.py`, widen the accepted set:

```python
_AGENT_STATUSES = ("done", "failed", "done_with_concerns", "needs_context")
```

keeping the `else "failed"` — an agent inventing a status is a broken agent, and guessing at its intent is worse than failing the session.

`read_summary_ref` is already 90% of the two new readers. Factor one `_read_str_field(path, key)` and let all three call it, catching `OSError, ValueError` so a non-UTF-8 result file cannot escape as a raw `UnicodeDecodeError`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_db.py tests/test_adapters_subprocess.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/kraft/db.py src/kraft/adapters/subprocess.py tests/test_db.py tests/test_adapters_subprocess.py
git commit -m "feat: done_with_concerns and needs_context reach the data layer"
```

---

### Task 2: A fake agent that can report them

**Files:**
- Modify: `tests/support/fake_agent.py`
- Modify: `fixtures/fake-claude.sh`
- Test: exercised by Tasks 3-5

**Why first:** every remaining task needs an agent that can return the new statuses, per cycle. Today `fake_agent.py` writes `{"status": "done"}` with no knob. Without this task the rest of the plan has nothing to test against — the same "harness that cannot exist" failure that killed sub-project F's first draft.

- [ ] **Step 1: Add the knob**

`KRAFT_FAKE_AGENT_STATUS` sets the status the fake reports, and `KRAFT_FAKE_AGENT_CONCERNS` / `KRAFT_FAKE_AGENT_QUESTION` set the matching field. Default stays `done`, so every existing test is unaffected.

For per-cycle scripting, follow `tests/support/fake_reviewer.py`, which F added for exactly this problem: a JSON plan file plus a sidecar invocation counter, because `run_task` passes no round number into the child. Reuse that mechanism rather than inventing a second one — a `KRAFT_FAKE_AGENT_PLAN` pointing at a list of per-invocation `{"status": ..., "question": ...}` entries.

`fixtures/fake-claude.sh` is the dev-instance fake and is a different shape: its failure branch is `exit 3` and it writes the result file near the end. Thread a `status` variable through rather than adding title tokens — and note the existing `KRAFT_SLOW` comment, which records that the title reaches the script twice, so a token would match twice.

- [ ] **Step 2: Prove the knob works**

One test per fake: set the env, run a launch, assert `worker_sessions.status` is what you asked for. These must fail before Task 1 (the CHECK rejects the value) and pass after.

- [ ] **Step 3: Commit**

```bash
git add tests/support/fake_agent.py fixtures/fake-claude.sh tests/
git commit -m "test: the fakes can report the new statuses, per invocation"
```

---

### Task 3: `done_with_concerns` advances the chain

**Files:**
- Modify: `src/kraft/executor.py`, `src/kraft/store.py`
- Modify: `frontend/src/types.ts`
- Test: `tests/test_executor.py`, `tests/test_store.py`

**Interfaces:** `executor._ADVANCING = ("done", "done_with_concerns")`, used at every progression check.

- [ ] **Step 1: Write the failing tests**

`tests/test_executor.py` has **no fixtures** — it is `async def scenario()` bodies driven by `asyncio.run`, using `isolated_bd` / `make_repo` / `fake_registry` from `tests/support/harness.py`. Follow that shape. Note `executor.run` returns `"completed"` / `"needs_human"` / `"paused"` / `"awaiting_gate"` — never `"ok"`; only `_walk_node` returns `"ok"`.

```python
def test_done_with_concerns_advances_the_chain(tmp_path, monkeypatch):
    # executor.run(...) == "completed", and the node completed
def test_done_with_concerns_is_not_capped_out_by_a_sibling_breach(tmp_path, monkeypatch):
    # store.mark_sessions_capped_out must not overwrite it
def test_reconcile_accepts_a_done_with_concerns_session(tmp_path, monkeypatch):
    # a resume over a node whose only session ended done_with_concerns
    # must not report "did not resolve cleanly"
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Write the implementation**

```python
#: Statuses that let the chain advance. `done_with_concerns` is deliberately
#: here: the agent finished the work — its doubts are information for the human
#: at the next gate, not a control-flow change.
_ADVANCING = ("done", "done_with_concerns")
```

Three call sites, and the sweep must leave `executor.py`:

- `_measure_node`'s failure detection. Today it is `isinstance(r, BaseException) or r == "failed"` — **keep that shape** and add `needs_context` to the failure set (`done_with_concerns` needs no change here: it was never in that set and stays out, which is exactly the behaviour wanted), rather than inverting to `r not in _ADVANCING`. Inverting makes any unexpected return a node failure, which is a silent policy change this task did not ask for.
- `_reconcile_current_node`'s `all(r["status"] == "done" for r in final)` — must widen, or a crash-resume over a `done_with_concerns` node reports "did not resolve cleanly".
- `store.mark_sessions_capped_out`'s `AND status NOT IN ('done', 'capped_out')` — must widen, or a concerns session on a capping loop is overwritten to `capped_out` and its text disappears.

`frontend/src/types.ts`'s `SessionStatus` is a closed union — add both values, or the API returning them fails `tsc`.

- [ ] **Step 4: Run tests to verify they pass**

Run the full `just test` and `just test-ui` — this task touches shared comparisons.

- [ ] **Step 5: Commit**

---

### Task 4: `needs_context` stops the item, and can be answered

**Files:**
- Modify: `src/kraft/executor.py`, `src/kraft/api.py`
- Test: `tests/test_needs_context.py` (create)

**Interfaces:** a `needs_human` reason of the form `needs_context: <question>`; `/steer` and `/resume` accept such an item.

- [ ] **Step 1: Write the failing tests**

```python
def test_needs_context_stops_the_item_with_the_question_in_the_reason(...):
def test_needs_context_from_a_measuring_task_does_not_consume_a_cycle(...):
    """The counter is bumped before the fix dispatch, so only a measuring-task
    needs_context can avoid consuming one. Assert the counter across two cycles,
    not a single call — a one-cycle assertion passes with the carve-out removed."""
def test_steer_accepts_a_needs_context_stop(...):
    # POST /steer -> 200 (not 409). Body model is {"text": ...}, not {"note": ...}
def test_resume_accepts_a_needs_context_stop(...):
def test_steer_still_409s_on_a_running_item(...):
    """The guard widened, it did not disappear."""
def test_the_answer_reaches_the_next_launch(...):
    # assert on the argv log, via KRAFT_FAKE_AGENT_ARGV_LOG
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Write the implementation**

A helper that finds the question from the round's sessions. **Take the latest row
per hook point**, not the first match — `store.sessions_for_round` returns every
session ever stamped with that round, and a resume or `/retry` re-enters at round
0 with stale rows sitting there. F's `_collect_findings` documents this exact
hazard; a first-match scan would let one historical `needs_context` re-stop the
item on every subsequent pass, permanently.

**Take the latest-row-per-hook-point idea from `_collect_findings` and NOT its
filter.** `_collect_findings` deliberately restricts to `row["hook_point"] in
node["tasks"]`, to keep the fix agent's own result out of the cycle's findings.
This helper needs the opposite: the fix task shares the measuring pass's `round`,
so including `on.implementation.start` is precisely how a fix task's own
`needs_context` surfaces, one iteration later. Copying the filter would silently
drop the case the feature exists for.

**Both branches of `_walk_node`, not just the fix loop.** This is the gap that
would have made the feature useless in practice: `on.implementation.start` is the
only agent-kind hook, and in **both** shipped templates it runs as a plain,
non-loop node — `implementation` in `default.yaml`, and the sole implementation
task in `quick-task.yaml`, which has no fix-loop node at all. The likeliest place
an agent asks for missing context is exactly the branch a fix-loop-only check
would miss, and the acceptance criterion would have failed there silently.

Write one helper and call it from both:

- **Plain branch** (`if not key:`): before the existing `verdict == "failed"`
  handling, which today produces the generic `task failed in node …` reason.
- **Fix-loop branch**: **after** the `enters_loop` early return and **before**
  `bump_counter`. Below the early return because a clean node must not be
  re-stopped by a stale row; above `bump_counter` because that is what the
  carve-out means.

In `api.py`, widen both guards from `status != "paused"` to also accept a `needs_human` item whose stop reason is a `needs_context`. Keep the 409 for everything else — the guard exists to stop steering a *running* item and that still holds.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

---

### Task 5: Tell the agent, and carry the previous attempt

**Files:**
- Modify: `src/kraft/adapters/agent.py` (`_CTX`), `src/kraft/executor.py`
- Test: `tests/test_adapters_agent.py`, `tests/test_findings_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_injected_context_names_the_statuses_and_fields(...):
    # done_with_concerns, needs_context, concerns, question all appear
def test_the_context_asks_for_one_question_per_stop(...):
    """Spec §5: needs_context costs a full stop and relaunch, so an agent that
    needs three facts must ask for all three at once."""
def test_fix_cycle_two_names_the_previous_result_file(...):
    # assert the path is present; do NOT assert ".json" not in prompts[0] —
    # F's findings block can contain a .json file path for unrelated reasons
def test_the_path_is_passed_not_the_contents(...):
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Write the implementation**

Extend `_CTX` with a short paragraph naming the four statuses, the two optional fields, and the one-question-per-stop instruction. This is the outbound half — without it the vocabulary is inert, because nothing else tells an agent the statuses exist.

For the handoff, resolve the previous round's `on.implementation.start` session — filter by hook point in **code**, not only in prose, since `sessions_for_round` returns the re-measure sessions too. Pass the path, and the `session_summary_ref` if the row has one (spec §4 asks for both). Hand over the **path, not the contents**: pasting the file costs tokens on every cycle of every work item, and the agent can already read files.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

---

### Task 6: Surface it

**Files:**
- Modify: `src/kraft/adapters/subprocess.py`, `src/kraft/store.py` (write the event)
- Modify: `src/kraft/api.py`, `frontend/src/views/WorkItemDetail.tsx`, `frontend/src/components/Gate.tsx`, `frontend/src/types.ts`
- Test: `tests/test_api.py`, `tests/test_adapters_subprocess.py`, `frontend/src/views/WorkItemDetail.test.tsx`

- [ ] **Step 1: Write the failing tests**

`renderDetail` takes **no arguments** — state is injected by `setup(over)`. Follow the file's actual shape.

```tsx
it("shows the agent's question and an answer box on a needs_context stop", ...)
it("shows concerns at the review gate", ...)
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Write the implementation**

`GET /work-items/{wid}` gains `needs_context_question: string | null` and `concerns: string[]`.

**Derive concerns from an event, not from disk** — and note that *writing* that
event is part of this task, not something an earlier one already did. Task 1
gives you `read_concerns`/`read_question`, but nothing calls them, and the
migration deliberately adds no `concerns` column, so an event is the only channel
the text has.

`adapters/subprocess.run_task` already reads `read_summary_ref` off the result
file just before calling `store.session_exited`. Do the same there: read the
concerns and the question, and append an event carrying them alongside the
terminal status. Add `src/kraft/adapters/subprocess.py` and `src/kraft/store.py`
to this task's Files list.

Reading `result_path` off disk per session inside `GET /work-items/{wid}` — which
is what "match how F does it" would look like if you skipped the write side — is
a synchronous file read per session in a request handler.

The UI renders the question with an answer box for a `needs_context` stop — `PausedCard` renders only for `paused` today, and a `needs_human` item without a fix loop falls to a control row whose Steer button is hard-disabled, so this is a new affordance, not a relabel.

Concerns render at the review gate beside F's deferred findings. F already passes `deferred` to `<Gate>` scoped to `human_review_approval`; extend that panel rather than adding a second one, and update `Gate.test.tsx` with it.

- [ ] **Step 4: Run the full gates**

- [ ] **Step 5: Commit**

---

### Task 7: Verify

- [ ] **Step 1:** `just test && just test-ui && just lint`.
- [ ] **Step 2:** confirm `tests/test_fix_loop.py` and `tests/test_findings_loop.py` are unedited: `git diff --stat <base> -- tests/test_fix_loop.py tests/test_findings_loop.py` is empty.
- [ ] **Step 3:** report the gate numbers. Bead bookkeeping, the MR and the merge are the coordinator's.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 two statuses, CHECK widening, result-file fields, unknown stays `failed` | 1 |
| §1 the agent is told the statuses exist | 5 |
| §2 `done_with_concerns` advances, concerns at the gate | 3, 6 |
| §2 concerns survive a sibling cap breach and a crash-resume | 3 |
| §3 `needs_context` stops; the answer path works; no cycle consumed | 4 |
| §4 previous attempt by path plus its summary ref | 5 |
| §5 one question per stop | 5 |
| §6 model escalation | out of scope — `Kraft-8mu.7.1` |

**Not covered, and named rather than hidden:** spec §2's "an item that reaches `merge` without passing a gate carries its concerns into the completion event". Task 6 stamps concerns into an event at session exit, which makes them recoverable from the timeline — but nothing adds them to `work_item_completed` specifically. Deferred deliberately; the timeline carries the information either way.

**Placeholders:** none. Task 2 exists because the fakes cannot report the new statuses, rather than assuming a harness. Every test that names a helper names one that exists, with its real signature.

**The risk this plan carries:** Task 3 widens three status comparisons in two modules, and `done_with_concerns` is supposed to change no control flow. A missed comparison does not raise — it silently treats a finished session as unfinished. The three are enumerated above and each has its own test.
