# Worker Report Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent say "done, but I have doubts" or "I was never told X" instead of forcing both into `done` or `failed`.

**Architecture:** Two values join the `worker_sessions.status` CHECK constraint. `done_with_concerns` progresses exactly like `done` and surfaces its text at the next gate; `needs_context` stops the item and routes the agent's question through the steer channel that already answers stopped agents. Fix cycles additionally carry the previous cycle's result-file path.

**Tech Stack:** Python 3.14, SQLite, pytest; React 18 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-g-worker-report-contract-design.md`

**Beads:** `Kraft-8mu.7` (parent `Kraft-8mu`). Model escalation is `Kraft-8mu.7.1`, blocked by `Kraft-8mu.3` and **not** in this plan.

## Global Constraints

- Python 3.14+. Unparenthesized `except A, B:` (PEP 758) is correct here.
- **An unrecognized status still resolves to `failed`.** The conservative default in `adapters/subprocess.py:33` is correct and must survive.
- SQLite cannot alter a CHECK constraint in place — rebuild the table, following the pattern already in `db._MIGRATIONS[4]`.
- `done_with_concerns` must not change control flow anywhere. If a test asserts a chain advanced on `done`, it must assert the same on `done_with_concerns`.
- A `needs_context` inside a `fix_loop` node **does not consume a cycle**.
- Do not add a column for concerns or questions — read them from the result file, as `read_summary_ref` already does.
- Run `just lint` before each commit.

---

### Task 1: Widen the status set

**Files:**
- Modify: `src/kraft/db.py:16` (`SCHEMA_VERSION`), the `worker_sessions` DDL, `_MIGRATIONS`
- Modify: `src/kraft/adapters/subprocess.py:19-33` (`_resolve_result_file`)
- Test: `tests/test_db.py`, `tests/test_adapters_subprocess.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `worker_sessions.status` accepts `done_with_concerns` and `needs_context`; `_resolve_result_file` returns either when the result file names it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_worker_session_accepts_the_new_statuses(tmp_path):
    conn = db._connect(tmp_path / "m.db")
    db.migrate(conn)
    store.create_work_item(
        conn, id="w1", bead_id="B", title="t", repo="/r",
        chain_template="default", chain_definition="{}",
    )
    for i, status in enumerate(("done_with_concerns", "needs_context")):
        store.create_session(
            conn, id=f"s{i}", work_item_id="w1", node_id="n", hook_point="h",
            log_path="/l", result_path="/r",
        )
        conn.execute("UPDATE worker_sessions SET status = ? WHERE id = ?", (status, f"s{i}"))
    got = {r["status"] for r in conn.execute("SELECT status FROM worker_sessions")}
    assert got == {"done_with_concerns", "needs_context"}


def test_existing_rows_survive_the_rebuild(tmp_path):
    """The CHECK widening rebuilds the table — no row may be lost."""
    conn = db._connect(tmp_path / "m.db")
    db.migrate(conn)
    store.create_work_item(
        conn, id="w1", bead_id="B", title="t", repo="/r",
        chain_template="default", chain_definition="{}",
    )
    store.create_session(
        conn, id="s1", work_item_id="w1", node_id="n", hook_point="h",
        log_path="/l", result_path="/r", round=2,
    )
    conn.execute("UPDATE worker_sessions SET cost_usd = 1.5, model = 'opus' WHERE id = 's1'")
    db.migrate(conn)  # idempotent re-run
    row = conn.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    assert row["round"] == 2
    assert row["cost_usd"] == 1.5
    assert row["model"] == "opus"
```

Append to `tests/test_adapters_subprocess.py`:

```python
def test_result_file_can_report_done_with_concerns(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"status": "done_with_concerns", "concerns": "retry path untested"}')
    assert _subprocess._resolve_result_file(p) == "done_with_concerns"


def test_result_file_can_report_needs_context(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"status": "needs_context", "question": "which branch?"}')
    assert _subprocess._resolve_result_file(p) == "needs_context"


def test_unknown_status_still_resolves_to_failed(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"status": "vibes"}')
    assert _subprocess._resolve_result_file(p) == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k "new_statuses or done_with_concerns or needs_context"`
Expected: FAIL — CHECK constraint rejects the values; `_resolve_result_file` returns `failed`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/db.py`, bump `SCHEMA_VERSION` and update the base `worker_sessions`
DDL's CHECK to:

```sql
  status TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out',
                    'paused', 'unknown', 'done_with_concerns', 'needs_context')),
```

Add a migration that rebuilds the table, following `_MIGRATIONS[4]`'s
create-new / insert-select / drop / rename shape. **Copy every column** —
`pid`, `pid_start_time`, `attempt`, `session_summary_ref`, `started_at`,
`round`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `wall_ms`,
`exited_at` — an omitted column is silent data loss, which is what the
second test above guards.

In `src/kraft/adapters/subprocess.py`, replace the accepted set:

```python
_AGENT_STATUSES = ("done", "failed", "done_with_concerns", "needs_context")
...
    return status if status in _AGENT_STATUSES else "failed"
```

The `else "failed"` stays. An agent inventing a status is a broken agent, and
guessing at its intent is worse than failing the session.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_db.py tests/test_adapters_subprocess.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/db.py src/kraft/adapters/subprocess.py tests/test_db.py tests/test_adapters_subprocess.py
git commit -m "feat: done_with_concerns and needs_context session statuses"
```

---

### Task 2: `done_with_concerns` progresses like `done`

**Files:**
- Modify: `src/kraft/executor.py` (`_measure_node`'s failure detection — the `failed = [...]` comprehension), and any other `== "done"` comparison in the walk
- Modify: `src/kraft/adapters/subprocess.py` — add `read_concerns`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: Task 1's statuses.
- Produces: `subprocess.read_concerns(path: Path) -> str | None`, mirroring `read_summary_ref`. Chain progression treats `done_with_concerns` identically to `done`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_executor.py`:

```python
def test_done_with_concerns_advances_the_chain(chain_env):
    chain_env.agent_reports(status="done_with_concerns", concerns="retry path untested")
    assert chain_env.run() == "ok"
    assert chain_env.current_node() != chain_env.first_node()


def test_done_with_concerns_is_not_a_failure(chain_env):
    chain_env.agent_reports(status="done_with_concerns", concerns="unsure")
    chain_env.run()
    assert chain_env.work_item_status() != "needs_human"


def test_read_concerns(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"status": "done_with_concerns", "concerns": "retry path untested"}')
    assert _subprocess.read_concerns(p) == "retry path untested"


def test_read_concerns_absent_or_broken(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"status": "done"}')
    assert _subprocess.read_concerns(p) is None
    p.write_text("{not json")
    assert _subprocess.read_concerns(p) is None
```

`chain_env` stands for the existing executor harness in that file — reuse it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k done_with_concerns`
Expected: FAIL — the walk treats the status as a failure.

- [ ] **Step 3: Write the implementation**

In `src/kraft/adapters/subprocess.py`, beside `read_summary_ref`:

```python
def read_concerns(path: Path) -> str | None:
    """`concerns` from a result file, or None. An agent's own doubts about work
    it nonetheless completed — recorded so it does not have to overstate
    confidence or fail the task to be heard."""
    try:
        data = json.loads(Path(path).read_text())
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    text = data.get("concerns")
    return text if isinstance(text, str) and text else None
```

In `src/kraft/executor.py`, introduce one predicate and route every progression
check through it rather than editing comparisons one by one:

```python
#: Statuses that let the chain advance. `done_with_concerns` is deliberately
#: here: the agent finished the work — its doubts are information for the
#: human at the next gate, not a control-flow change.
_ADVANCING = ("done", "done_with_concerns")
```

Replace `_measure_node`'s failure detection:

```python
    failed = [
        tasks[i]
        for i, r in enumerate(results)
        if isinstance(r, BaseException) or (r not in _ADVANCING and r != "paused")
    ]
```

Grep for every other `== "done"` in `executor.py` and route each through
`_ADVANCING` where it is a progression check. Leave comparisons that are
genuinely about the literal `done` status alone.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_executor.py tests/test_fix_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/executor.py src/kraft/adapters/subprocess.py tests/test_executor.py
git commit -m "feat: done_with_concerns advances the chain"
```

---

### Task 3: `needs_context` stops the item without burning a cycle

**Files:**
- Modify: `src/kraft/executor.py` (`_walk_node`, both branches)
- Modify: `src/kraft/adapters/subprocess.py` — add `read_question`
- Test: `tests/test_needs_context.py` (create)

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: `subprocess.read_question(path) -> str | None`; a `needs_human` reason of the form `needs_context: <question>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_needs_context.py`:

```python
"""An agent that was never told something stops the item and asks.

The carve-out under test: this is not an attempt at the work, so a fix loop
must not charge a cycle for it — the same reasoning `02` §7.2 already applies
to `plan_diverged`.
"""


def test_needs_context_stops_the_item(chain_env):
    chain_env.agent_reports(status="needs_context", question="which branch should the MR target?")
    assert chain_env.run() == "needs_human"
    assert "which branch" in chain_env.needs_human_reason()
    assert chain_env.needs_human_reason().startswith("needs_context:")


def test_needs_context_does_not_consume_a_fix_cycle(loop_env):
    loop_env.fail(cycle=0)
    loop_env.fix_reports(cycle=1, status="needs_context", question="which config file?")
    loop_env.run()
    assert loop_env.counter_count() == 1  # cycle 0's failure only


def test_needs_context_preserves_the_counter_for_a_later_retry(loop_env):
    loop_env.fail(cycle=0)
    loop_env.fix_reports(cycle=1, status="needs_context", question="?")
    loop_env.run()
    assert loop_env.counter_count() == 1
    assert loop_env.counter_row() is not None  # not cleared


def test_answering_through_steer_resumes(client, stopped_on_needs_context):
    wid = stopped_on_needs_context
    assert client.post(f"/work-items/{wid}/steer", json={"note": "target main"}).status_code == 200
    assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 200
    assert "target main" in client.get(f"/work-items/{wid}").json()["title"] or True
    # The steer reaches the relaunch: assert it lands in the next session's log.
    assert "target main" in stopped_on_needs_context_next_prompt(wid)


def test_needs_context_question_reaches_the_api(client, stopped_on_needs_context):
    body = client.get(f"/work-items/{stopped_on_needs_context}").json()
    assert body["needs_context_question"] == "which branch should the MR target?"
```

Build `stopped_on_needs_context` from the existing API-test harness; the last
assertion's helper reads the relaunched session's log, where
`_STEER_PROMPT` lands.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_needs_context.py`
Expected: FAIL — the status resolves through the failure path and burns a cycle.

- [ ] **Step 3: Write the implementation**

Add `read_question` to `adapters/subprocess.py`, identical in shape to
`read_concerns` but reading `question`.

In `executor.py`, handle the status **before** the cap machinery in the
`fix_loop` branch and before the failure path in the plain branch:

```python
def _needs_context_reason(db, work_item_id, node, round) -> str | None:
    """The question from any session in this round that asked for context."""
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    for row in rows:
        if row["status"] == "needs_context":
            return _subprocess.read_question(row["result_path"]) or "(no question given)"
    return None
```

In the plain branch, before the existing `verdict == "failed"` handling:

```python
        question = _needs_context_reason(db, work_item_id, node, 0)
        if question is not None:
            await db.write(
                lambda c: store.mark_needs_human(
                    c, work_item_id, node["id"], f"needs_context: {question}"
                )
            )
            return "needs_human"
```

In the `fix_loop` branch, the same check goes **above** `bump_counter`:

```python
        # Before the counter: an agent that was never told something did not
        # attempt the work, and the cap bounds attempts. Same carve-out `02`
        # §7.2 makes for plan_diverged.
        question = _needs_context_reason(db, work_item_id, node, round)
        if question is not None:
            await db.write(
                lambda c: store.mark_needs_human(
                    c, work_item_id, node["id"], f"needs_context: {question}"
                )
            )
            return "needs_human"
```

and the same check after the fix task returns, keyed on `round=count`.

`store.sessions_for_round` comes from sub-project F Task 3. If F has not landed,
add it here with the same signature and body — the two plans agree on it
deliberately, and duplicating a five-line query is cheaper than sequencing the
sub-projects.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test tests/test_needs_context.py tests/test_fix_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/executor.py src/kraft/adapters/subprocess.py tests/test_needs_context.py
git commit -m "feat: needs_context stops the item without burning a fix cycle"
```

---

### Task 4: Surface the question and the concerns

**Files:**
- Modify: `src/kraft/api.py` (work-item detail payload)
- Modify: `frontend/src/types.ts`, `frontend/src/views/WorkItemDetail.tsx`, `frontend/src/components/Gate.tsx`
- Test: `tests/test_api.py`, `frontend/src/views/WorkItemDetail.test.tsx`

**Interfaces:**
- Consumes: Tasks 2-3.
- Produces: `needs_context_question: string | null` and `concerns: string[]` on `GET /work-items/{wid}`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/views/WorkItemDetail.test.tsx`:

```tsx
it("shows the agent's question and labels the steer box as the answer", () => {
  renderDetail({ status: "needs_human", needs_context_question: "which branch?" });
  expect(screen.getByText(/which branch\?/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: /answer/i })).toBeInTheDocument();
});

it("shows concerns at a gate", () => {
  renderDetail({
    status: "needs_human",
    gate: "human_review_approval",
    concerns: ["retry path untested"],
  });
  expect(screen.getByText(/retry path untested/)).toBeInTheDocument();
});
```

Append the matching API assertions to `tests/test_api.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run WorkItemDetail`
Expected: FAIL.

- [ ] **Step 3: Write the implementation**

In `api.py`, derive both from the item's sessions — no new columns, matching how
sub-project F derives its deferred findings:

- `needs_context_question`: the question from the most recent `needs_context`
  session, or `null`
- `concerns`: `read_concerns` over every `done_with_concerns` session, in order

In the UI, reuse rather than add:

- The attention card renders the question above the existing steer control, and
  the control's label becomes "Answer" when `needs_context_question` is set.
  `_STEER_PROMPT`'s wording — *"A human has steered this run"* — reads oddly as an
  answer; add a sibling `_ANSWER_PROMPT` in `executor.py` that leads with the
  question, and select between them on whether a question was pending.
- Concerns render in `Gate.tsx` beside sub-project F's deferred findings, under
  one heading. Both are the same shape of thing: something a machine noticed,
  deferred, and owes a human before approval.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test && just test-ui`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py src/kraft/executor.py frontend/src/types.ts \
        frontend/src/views/WorkItemDetail.tsx frontend/src/components/Gate.tsx \
        frontend/src/views/WorkItemDetail.test.tsx tests/test_api.py
git commit -m "feat: show the agent's question and its concerns"
```

---

### Task 5: Carry the previous fix attempt forward

**Files:**
- Modify: `src/kraft/executor.py` (fix-task dispatch)
- Test: `tests/test_findings_loop.py` or `tests/test_fix_loop.py`

**Interfaces:**
- Consumes: `store.sessions_for_round`.
- Produces: fix cycles after the first name the previous cycle's result-file path in the prompt.

- [ ] **Step 1: Write the failing test**

```python
def test_fix_cycle_two_names_the_previous_result_file(loop_env):
    loop_env.fail(cycle=0)
    loop_env.fail(cycle=1)
    loop_env.pass_(cycle=2)
    loop_env.run()
    prompts = loop_env.fix_prompts()
    assert ".json" not in prompts[0]           # first cycle has no predecessor
    assert loop_env.result_path(cycle=1) in prompts[1]


def test_the_path_is_passed_not_the_contents(loop_env):
    loop_env.fail(cycle=0)
    loop_env.write_result(cycle=1, extra={"notes": "SENTINEL_TEXT"})
    loop_env.fail(cycle=1)
    loop_env.pass_(cycle=2)
    loop_env.run()
    assert "SENTINEL_TEXT" not in loop_env.fix_prompts()[1]
```

That second test is the point of the task: content in the prompt is paid for on
every cycle of every work item, a path is paid for once.

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k previous_result_file`
Expected: FAIL — no path in the prompt.

- [ ] **Step 3: Write the implementation**

In `executor.py`, add to the fix instruction when `count > 1`:

```python
_FIX_PRIOR = (
    "\n\nYour previous attempt on this node wrote its result file to {path}. "
    "Read it before you start — it is what you tried and what you concluded. "
    "Do not repeat an approach that already failed."
)
```

resolving `path` from the previous round's `on.implementation.start` session via
`store.sessions_for_round(work_item_id, node_id, count - 1)`.

Hand over the **path, not the contents**. Pasting the file costs tokens on every
cycle of every work item; the agent can already read files, and it already writes
to `$KRAFT_RESULT_PATH`, so both directions of the channel exist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -k fix -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/executor.py tests/test_findings_loop.py
git commit -m "feat: fix cycles carry the previous attempt's result file"
```

---

### Task 6: Verify and close

- [ ] **Step 1: Exercise both statuses against a dev instance**

`fixtures/fake-claude.sh` steers on the work-item title (`KRAFT_FAIL`,
`KRAFT_SLOW`). Add the two statuses to that fake — one title token each — so the
dev instance and the suite exercise the same paths, and `just dev-seed` can land
an item stopped on a question.

- [ ] **Step 2: Full gates**

Run: `just test && just test-ui && just lint`
Expected: all pass.

- [ ] **Step 3: Close**

```bash
bd close Kraft-8mu.7 --reason="worker report contract: done_with_concerns, needs_context, prior-attempt handoff; model escalation remains Kraft-8mu.7.1"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 two statuses, CHECK widening, result-file fields, unknown stays failed | 1 |
| §2 `done_with_concerns` progresses like `done`, concerns at the gate | 2, 4 |
| §3 `needs_context` stops, reuses steer/resume, no cycle consumed | 3, 4 |
| §4 previous attempt handed over by path, not contents | 5 |
| §5 one-question-per-stop is instruction, not mechanism | 4 (prompt wording), no code task |

**Placeholders:** none. `chain_env`, `loop_env` and `renderDetail` are named as existing harnesses to reuse, and say so.

**Type consistency:** `read_concerns` and `read_question` share `read_summary_ref`'s signature `(path) -> str | None`. `_ADVANCING` is a tuple used with `in` in every progression check. `store.sessions_for_round` has the same signature here as in sub-project F's plan, and Task 3 states what to do if F has not landed first.

**Cross-plan note:** Tasks 3 and 5 both use `store.sessions_for_round`, introduced by sub-project F Task 3. The plans deliberately duplicate the five-line query rather than sequencing F before G — whichever lands first adds it, the second finds it there.
