# Budget Caps and Auto-Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop launching agents for a work item that has already spent more than its budget, and let Kraft pick backlog beads up on its own without ever passing a gate for a human.

**Architecture:** The budget is a `SUM(cost_usd)` over `worker_sessions` — the numbers are already stored, so there is no counter and no migration. `policy.yaml` gains a `budget:` block beside `loops:`; `executor._dispatch` checks it on the `kind == "agent"` branch only, and returns a new `"budget"` verdict that outranks a co-task failure all the way up to `needs_human`. Auto-intake is an asyncio poller reading `bd ready` per connected repo, adopting the bead it finds instead of filing a new one, and creating an ordinary work item that runs to its first gate and stops.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, React 19 + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-e-budget-and-autonomy-design.md` — **read its "Amendments, 2026-09-05" section first.** Ten things in the body of that spec are wrong about the merged tree and the amendments say which.

**Beads:** `Kraft-8mu.5` (parent `Kraft-8mu`). Depends on `Kraft-8mu.3` (closed).

> ### READ THIS BEFORE EXECUTING ANY TASK
>
> A plan review found 20 defects in the task bodies below — six of them blockers,
> including invented test fixtures and one that would have let the poller run an
> ungated chain to merge with nobody watching. **The "Plan review corrections"
> section at the very bottom of this file overrides the task bodies wherever the
> two disagree.** Read it first, then execute. A correction is not a suggestion.

## Global Constraints

- **No schema change.** `db.SCHEMA_VERSION` is not touched and `_MIGRATIONS` gains no key. Sub-project `G` is bumping 9 → 10 in a concurrent session; a key collision here already cost one painful merge on this epic. If you believe you need a migration, you have misread the plan — everything is derived from `worker_sessions.cost_usd` and `work_items.bead_id`, both of which already exist.
- **A cap refuses the NEXT launch. It cannot interrupt a running agent.** `usage.py` reads cost from the log envelope the CLI writes when the session *exits* (`read_envelope`, `from_envelope`), and `adapters/agent.py` passes `--output-format json`. There is no in-flight signal. Overshoot is bounded by the cost of one task, not by the cap. No code comment, help string, docstring or commit message may describe this as a spending limit, a hard limit or a ceiling. Any step you think needs mid-session enforcement is a step you have misread.
- **Python 3.14+.** Unparenthesized `except A, B:` (PEP 758) is used in this codebase and is correct — do not "fix" it.
- **`except (OSError, ValueError)` on every best-effort `read_text()`.** A `read_text()` on a file written by something else raises `UnicodeDecodeError` (a `ValueError`) on invalid bytes, which a bare `except OSError` does not catch. This exact defect has shipped four times in this repo.
- **Nothing auto-approves a gate.** Spec §5, non-negotiable. An auto-started work item is created exactly as a human-created one is: `status="active"`, the same `executor.run`, the same gates.
- Both budget caps default to `null` (disabled). An existing install's behaviour must not change until the operator opts in.
- Subprocess and builtin tasks are never budget-blocked. They cost nothing, and stopping `on.test.run` for a budget would strand an item mid-node for no saving.
- Run `just lint` before every commit; `just test` before handing a task back. Frontend tasks also run `just test-ui` (which is `tsc -b` *and* vitest — vitest alone does not typecheck).
- Commit after each task. Do not push; the coordinator owns the branch, the MR and the beads.

## What a plan reviewer already killed

Recorded so it is not re-attempted:

- **`Cap` does not grow a third field.** The bead's own description says "spend caps as a third field on `policy.py` Cap"; spec §2 overrules it and explains why. `Cap` is per-loop and is snapshotted per `(work_item_id, key)` row in `retry_counters` by `store.bump_counter`. A spend limit is per item and per day and spans every loop. Putting it in `Cap` means writing the same number into every counter row and then reconciling them.
- **The check is not a filter inside `_measure_node`.** It goes on `_dispatch`'s `kind == "agent"` branch, which is what makes "a subprocess task in the same node still runs" true by construction instead of by a condition someone has to remember to keep in sync.
- **`POST /work-items/{wid}/retry` is not the budget card's action on an arbitrary node.** It 409s unless the current node has a `fix_loop` (`api.py:849-865`). See spec amendment A4.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/policy.py` | + `Budget` dataclass, `Policy.budget`, parsing of the `budget:` block |
| `src/kraft/store.py` | + `budget_spend()` — the two `SUM(cost_usd)` queries |
| `src/kraft/executor.py` | + `_budget_breach()`, the `"budget"` verdict through `_dispatch` → `_measure_node` → `_walk_node`, and `intake(bead_id=...)` |
| `src/kraft/api.py` | + `PolicyBody.budget`, + intake poller lifecycle in `lifespan` |
| `src/kraft/adapters/beads.py` | + `ready()` — `bd ready --json` passthrough |
| `src/kraft/intake.py` | **new** — the auto-intake poller: config, one tick, the loop |
| `src/kraft/config.py` | + `load_intake()` |
| `src/kraft/templates.py` | `CONFIG_FILES` += `intake.yaml` |
| `templates/policy.yaml` | + the `budget:` block, both null |
| `templates/intake.yaml` | **new** — disabled by default |
| `frontend/src/types.ts` | + `Policy.budget`, + `WorkItem.budget` |
| `frontend/src/store.ts` | budget payload off `work_item_needs_human`, cleared by `node_started` |
| `frontend/src/components/BudgetCard.tsx` | **new** — the breach attention card |
| `frontend/src/views/WorkItemDetail.tsx` | render `BudgetCard` before the capped/stranded branch |
| `frontend/src/views/Settings.tsx` | the two budget fields + the overshoot help text |

---

### Task 1: `policy.Budget` and the `budget:` block

**Files:**
- Modify: `src/kraft/policy.py`
- Modify: `templates/policy.yaml`
- Test: `tests/test_policy.py`

**Interfaces:**
- Produces:
  - `policy.Budget` — `@dataclass(frozen=True)` with `work_item_usd: float | None = None` and `daily_usd: float | None = None`.
  - `policy.Policy.budget: Budget = Budget()` — a new field with a default, so every existing `Policy(...)` construction in the tests keeps working.
  - `policy.NO_BUDGET: Budget` — the both-null instance, for callers with no policy at all.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_policy.py` (it already has a `_write` helper pattern — check the top of the file and reuse whatever is there; if there is none, write the YAML with `tmp_path` inline exactly as below):

```python
def test_budget_defaults_to_disabled_when_absent(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n")
    pol = policy.load_policy(p)
    assert pol.budget.work_item_usd is None
    assert pol.budget.daily_usd is None


def test_budget_reads_both_caps(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: 20.0\n  daily_usd: 100\n"
    )
    pol = policy.load_policy(p)
    assert pol.budget.work_item_usd == 20.0
    # an int in the YAML is a legal dollar figure and becomes a float
    assert pol.budget.daily_usd == 100.0


def test_explicit_null_is_disabled_not_zero(tmp_path):
    """`null` means "no cap". Reading it as 0.0 would block every launch."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: null\n  daily_usd: null\n"
    )
    assert policy.load_policy(p).budget == policy.Budget()


def test_negative_budget_is_rejected_at_load(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: -1\n"
    )
    with pytest.raises(policy.PolicyError, match="work_item_usd"):
        policy.load_policy(p)


def test_non_numeric_budget_is_rejected_at_load(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  daily_usd: \"lots\"\n"
    )
    with pytest.raises(policy.PolicyError, match="daily_usd"):
        policy.load_policy(p)


def test_budget_true_is_rejected_because_bool_is_an_int(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  daily_usd: true\n"
    )
    with pytest.raises(policy.PolicyError, match="daily_usd"):
        policy.load_policy(p)
```

Ensure `import pytest` and `from kraft import policy` are present at the top of the file (they will be — check rather than assume).

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test -k budget`
Expected: `AttributeError: module 'kraft.policy' has no attribute 'Budget'` / `Policy` has no `budget`.

- [ ] **Step 3: Implement**

In `src/kraft/policy.py`, after the `Cap` dataclass:

```python
@dataclass(frozen=True)
class Budget:
    """Spend caps, in dollars. `None` is "no cap", never "zero".

    Not a field on `Cap`: a `Cap` is per-loop and is snapshotted per
    `(work_item_id, key)` row in `retry_counters`, while a budget is per work
    item and per day and spans every loop in the chain.
    """

    work_item_usd: float | None = None
    daily_usd: float | None = None


#: What a caller with no policy at all evaluates against.
NO_BUDGET = Budget()
```

Add the field to `Policy` — **last**, with a default, so positional construction in existing tests is unaffected:

```python
@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap
    loop_severities: frozenset[str] = DEFAULT_LOOP_SEVERITIES
    budget: Budget = NO_BUDGET
```

Add the parser beside `_cap`:

```python
def _usd(name: str, raw: object) -> float | None:
    if raw is None:
        return None
    if not isinstance(raw, int | float) or isinstance(raw, bool) or raw < 0:
        raise PolicyError(f"{name}: must be a non-negative number of dollars, or null")
    return float(raw)


def _budget(name: str, raw: object) -> Budget:
    if raw is None:
        return NO_BUDGET
    if not isinstance(raw, dict):
        raise PolicyError(f"{name}: expected a mapping")
    return Budget(
        work_item_usd=_usd(f"{name}.work_item_usd", raw.get("work_item_usd")),
        daily_usd=_usd(f"{name}.daily_usd", raw.get("daily_usd")),
    )
```

And in `load_policy`, before the `return`:

```python
    budget = _budget(f"{path.name}: 'budget'", data.get("budget"))
```

then pass `budget=budget` to the `Policy(...)` construction.

- [ ] **Step 4: Run the tests**

Run: `just test tests/test_policy.py`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Add the block to the packaged policy**

Append to `templates/policy.yaml`:

```yaml
# Spend caps, in dollars, both off by default. A cap refuses to START the next
# agent task; it cannot interrupt one that is already running, because the agent
# CLI only reports its cost when the session exits. Overshoot is bounded by the
# cost of one task, not by the cap.
budget:
  work_item_usd: null   # per work item, across every loop in its chain
  daily_usd: null       # every work item on this instance, since local midnight
```

- [ ] **Step 6: Verify the packaged policy still loads**

Run: `just test tests/test_policy.py tests/test_settings_api.py && just lint`
Expected: PASS. (`test_settings_api.py` round-trips `policy.yaml` through `GET`/`PUT /policy`; if a test there asserts on the exact dict, it will now see the `budget` key — update that assertion, do not drop the key.)

- [ ] **Step 7: Commit**

```bash
git add src/kraft/policy.py templates/policy.yaml tests/test_policy.py
git commit -m "policy: a budget block beside the loop caps"
```

---

### Task 2: `store.budget_spend`

**Files:**
- Modify: `src/kraft/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `store.local_midnight_utc(now: datetime | None = None) -> str` — the current local day's midnight, as a UTC ISO string comparable against the `created_at` strings `_now()` writes.
  - `store.budget_spend(conn, work_item_id: str, *, since: str | None = None) -> tuple[float, float]` — `(work_item_usd, daily_usd)`. `since` is the daily window's start; `None` makes the daily figure the sum over all time. Sessions with a `NULL` `cost_usd` contribute zero.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`. Read the top of that file first for its existing connection fixture and reuse it; the tests below assume a `conn` with the schema applied and a work item already created — follow whatever the neighbouring tests do to get one, rather than inventing a fixture.

```python
def test_budget_spend_sums_only_this_items_sessions(conn):
    # two items, one session each, different costs
    ...  # create both via store.create_work_item as the neighbouring tests do
    mine, theirs = _spend_fixture(conn, mine=[1.5, 2.25], theirs=[99.0])
    item_usd, daily_usd = store.budget_spend(conn, mine)
    assert item_usd == 3.75
    assert daily_usd == 102.75  # daily is instance-wide, not per item


def test_null_cost_sessions_count_as_zero(conn):
    """A running session, or a subprocess/builtin task, has no cost yet.

    Under-counting in-flight spend is a direct consequence of the design: cost
    only exists once the agent's session has exited.
    """
    wid = _spend_fixture_one(conn, costs=[1.0, None, None])
    assert store.budget_spend(conn, wid)[0] == 1.0


def test_no_sessions_is_zero_not_none(conn):
    wid = _spend_fixture_one(conn, costs=[])
    assert store.budget_spend(conn, wid) == (0.0, 0.0)


def test_daily_window_excludes_yesterday(conn):
    wid = _spend_fixture_one(conn, costs=[5.0])
    # backdate the only session a day
    conn.execute("UPDATE worker_sessions SET created_at = ?", ("2020-01-01T00:00:00+00:00",))
    item_usd, daily_usd = store.budget_spend(conn, wid, since="2020-06-01T00:00:00+00:00")
    assert daily_usd == 0.0
    # the per-item figure is NOT windowed: an item's budget spans its whole life
    assert item_usd == 5.0


def test_local_midnight_is_utc_and_sorts_against_stored_timestamps():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    noon_in_tokyo = datetime(2026, 9, 5, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    midnight = store.local_midnight_utc(noon_in_tokyo)
    # 2026-09-05T00:00+09:00 is 2026-09-04T15:00Z
    assert midnight == "2026-09-04T15:00:00+00:00"
    # string comparison is the whole point: it is what the SQL does
    assert "2026-09-04T15:00:00.123456+00:00" >= midnight
    assert "2026-09-04T14:59:59.999999+00:00" < midnight
```

Write the two helpers at the top of the block, concretely — no placeholder:

```python
def _spend_fixture_one(conn, *, costs: list[float | None]) -> str:
    """One work item with one session per entry in `costs`. Returns its id."""
    wid = uuid.uuid4().hex
    store.create_work_item(
        conn,
        id=wid,
        bead_id=None,
        title="t",
        repo="/tmp/r",
        chain_template="quick-task",
        chain_definition="{}",
    )
    for i, cost in enumerate(costs):
        sid = uuid.uuid4().hex
        store.create_session(
            conn,
            id=sid,
            work_item_id=wid,
            node_id="n",
            hook_point=f"on.h{i}",
            log_path="/tmp/l",
            result_path="/tmp/r",
        )
        conn.execute("UPDATE worker_sessions SET cost_usd = ? WHERE id = ?", (cost, sid))
    return wid


def _spend_fixture(conn, *, mine: list[float], theirs: list[float]) -> tuple[str, str]:
    return _spend_fixture_one(conn, costs=mine), _spend_fixture_one(conn, costs=theirs)
```

Fix the first test to use those helpers rather than the `...` placeholder — it reads:

```python
def test_budget_spend_sums_only_this_items_sessions(conn):
    mine, _theirs = _spend_fixture(conn, mine=[1.5, 2.25], theirs=[99.0])
    item_usd, daily_usd = store.budget_spend(conn, mine)
    assert item_usd == 3.75
    assert daily_usd == 102.75
```

Add `import uuid` at the top of the test file if it is not already there.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `just test -k budget_spend or local_midnight or null_cost`
Expected: `AttributeError: module 'kraft.store' has no attribute 'budget_spend'`.

- [ ] **Step 3: Implement**

In `src/kraft/store.py` (it already has `from datetime import UTC, datetime`):

```python
def local_midnight_utc(now: datetime | None = None) -> str:
    """Today's local midnight as a UTC ISO string.

    "Daily" means the operator's day, not UTC's — a cap that rolls over at 5pm
    local is a cap nobody can reason about. The return is normalized to UTC so
    it compares as a string against the `created_at` values `_now()` writes,
    which is what the SQL below does.
    """
    now = (now or datetime.now()).astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()


def budget_spend(
    conn: sqlite3.Connection, work_item_id: str, *, since: str | None = None
) -> tuple[float, float]:
    """`(spent on this work item, spent instance-wide since `since`)`, in dollars.

    A query, not a counter: `worker_sessions.cost_usd` is already the source of
    truth and a parallel counter is a second thing to get wrong. A NULL
    `cost_usd` — a session still running, or a subprocess or builtin task that
    has no cost — contributes zero, so this is a floor on in-flight spend by
    construction.
    """
    item = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM worker_sessions WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchone()[0]
    if since is None:
        daily = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM worker_sessions").fetchone()[0]
    else:
        daily = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM worker_sessions WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]
    return float(item), float(daily)
```

- [ ] **Step 4: Run the tests**

Run: `just test tests/test_store.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/store.py tests/test_store.py
git commit -m "store: budget_spend, the two SUM(cost_usd) queries"
```

---

### Task 3: The `"budget"` verdict through the executor

**Files:**
- Modify: `src/kraft/executor.py` (`_dispatch`, `_measure_node`, `_walk_node`)
- Test: `tests/test_budget.py` (create)

**Interfaces:**
- Consumes: `policy.Budget`, `policy.NO_BUDGET` (Task 1); `store.budget_spend`, `store.local_midnight_utc` (Task 2).
- Produces:
  - `executor.BUDGET = "budget"` — the verdict string, exported so tests and the API refer to one constant.
  - `executor._budget_breach(db, work_item_id: str, budget: _policy.Budget) -> dict | None` — the event payload for a breach, or `None`. Shape: `{"scope": "work_item"|"daily", "spent_usd": float, "cap_usd": float}`.
  - `_measure_node` returns `"budget"` as its verdict where it used to return `"failed"`, if any dispatched task returned `"budget"`.
  - The `work_item_needs_human` event payload gains an optional `"budget"` key carrying that same dict, beside the existing optional `"capped"` key.
  - `store.mark_needs_human(conn, work_item_id, node_id, reason, capped=None, budget=None)` — one new keyword-only-in-effect trailing parameter, defaulting to `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_budget.py`. Model the scenario setup on `tests/test_fix_loop.py` — same `isolated_bd` / `make_repo` / `RunDirs` / `db.Database.open` shape, same `asyncio.run(scenario())` wrapper. Do not invent a fixture; copy that file's structure.

```python
"""A budget cap refuses the next agent launch (sub-project E §1-§3).

It cannot interrupt a running agent: cost only exists once the session exits.
Everything here tests the refusal of a *subsequent* launch.
"""

import asyncio
import sys
import uuid
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _template() -> Template:
    """env_setup (builtin), then one agent task and one subprocess task in the
    same node — which is what makes "the agent is blocked and the subprocess is
    not" observable."""
    return Template(
        id="budget",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "work",
                "tasks": ["on.implementation.start", "on.test.run"],
                "gate_after": None,
                "fix_loop": None,
            },
        ],
    )


def _policy(*, work_item_usd=None, daily_usd=None) -> policy.Policy:
    return policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        budget=policy.Budget(work_item_usd=work_item_usd, daily_usd=daily_usd),
    )


def _spend(database, wid: str, usd: float) -> None:
    """Record a finished session that cost `usd`, as usage capture would have."""

    async def go():
        sid = uuid.uuid4().hex
        await database.write(
            lambda c: store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id="prior",
                hook_point="on.implementation.start",
                log_path="/tmp/l",
                result_path="/tmp/r",
            )
        )
        await database.write(
            lambda c: c.execute(
                "UPDATE worker_sessions SET cost_usd = ?, status = 'done' WHERE id = ?", (usd, sid)
            )
        )

    return go()


def _needs_human_payload(database, wid):
    evts = database.read(lambda c: events.read_after(c, 0, wid))
    return next(e["payload"] for e in reversed(evts) if e["type"] == "work_item_needs_human")
```

Then the tests themselves — five of them:

```python
def test_under_the_cap_the_agent_launches(tmp_path, monkeypatch):
    """$5 spent against a $20 cap runs normally."""
    # scenario: intake, _spend(3.0 + 2.0), executor.run with _policy(work_item_usd=20.0)
    # assert the item did NOT land needs_human for a budget reason and a session
    # exists for on.implementation.start


def test_over_the_cap_refuses_the_next_launch(tmp_path, monkeypatch):
    """$25 spent against a $20 cap stops the item with a budget reason."""
    # assert status == "needs_human"
    # payload = _needs_human_payload(...); assert payload["budget"] == {
    #     "scope": "work_item", "spent_usd": 25.0, "cap_usd": 20.0}
    # assert "budget" in payload["reason"] and "20" in payload["reason"]


def test_a_null_cap_never_blocks(tmp_path, monkeypatch):
    """$1000 spent with both caps null runs anyway."""


def test_the_subprocess_task_in_the_same_node_still_runs(tmp_path, monkeypatch):
    """The agent is refused; `on.test.run` in the same node has a session row.

    Budget-blocking a subprocess would strand the item mid-node for no saving.
    """
    # after the run: sessions for the "work" node include hook_point
    # "on.test.run" and do NOT include "on.implementation.start"


def test_the_daily_cap_stops_an_item_that_has_spent_nothing(tmp_path, monkeypatch):
    """Another item's spend today breaches the daily cap for this one."""
    # spend 150.0 against a *different* work item, run this one with
    # _policy(daily_usd=100.0); assert payload["budget"]["scope"] == "daily"


def test_yesterdays_spend_does_not_count_against_todays_daily_cap(tmp_path, monkeypatch):
    """Rollover at local midnight."""
    # spend 150.0, then backdate every worker_sessions.created_at to
    # "2020-01-01T00:00:00+00:00", then run with _policy(daily_usd=100.0) and
    # assert the item did not stop for a budget reason
```

Each of those six bodies must be written out fully by the implementer, following the `asyncio.run(scenario())` shape in `tests/test_fix_loop.py`. The comments above give the exact assertions; they are the test's contract, not a sketch to reinterpret.

- [ ] **Step 2: Run and watch them fail**

Run: `just test tests/test_budget.py`
Expected: FAIL — `Policy() got an unexpected keyword argument 'budget'` will already be fixed by Task 1, so the real failure is that an over-cap item runs anyway and no `budget` payload exists.

- [ ] **Step 3: Add `budget` to `store.mark_needs_human`**

```python
def mark_needs_human(
    conn: sqlite3.Connection,
    work_item_id,
    node_id,
    reason,
    capped: dict | None = None,
    budget: dict | None = None,
) -> None:
```

and after the existing `if capped is not None:` block:

```python
    if budget is not None:
        payload["budget"] = budget
```

Extend the docstring: `budget` carries `{scope, spent_usd, cap_usd}` when a spend cap is what stopped the item — the card shows the figures and the board never fetches sessions per row, so the numbers ride the event.

- [ ] **Step 4: Implement the check in `executor.py`**

Near the top, beside the other module constants:

```python
#: `_dispatch` returned without launching because a spend cap was already over.
#: It is not "failed" — the agent never ran, so nothing about it failed — and it
#: outranks a co-task's failure for exactly that reason.
BUDGET = "budget"


def _budget_breach(db, work_item_id: str, budget: _policy.Budget) -> dict | None:
    """The breached cap, or None. Evaluated fresh: it is a query, not a counter.

    A breach refuses the *next* launch. It cannot stop a running agent — cost is
    only known once that agent's session has exited (`usage.read_envelope`) — so
    the overshoot is bounded by the cost of one task, not by the cap.
    """
    if budget.work_item_usd is None and budget.daily_usd is None:
        return None
    since = store.local_midnight_utc() if budget.daily_usd is not None else None
    item_usd, daily_usd = db.read(
        lambda c: store.budget_spend(c, work_item_id, since=since)
    )
    if budget.work_item_usd is not None and item_usd >= budget.work_item_usd:
        return {"scope": "work_item", "spent_usd": item_usd, "cap_usd": budget.work_item_usd}
    if budget.daily_usd is not None and daily_usd >= budget.daily_usd:
        return {"scope": "daily", "spent_usd": daily_usd, "cap_usd": budget.daily_usd}
    return None


def _budget_reason(breach: dict) -> str:
    where = "this work item" if breach["scope"] == "work_item" else "today, across every work item"
    return (
        f"budget cap reached: ${breach['spent_usd']:.2f} spent on {where}, "
        f"cap ${breach['cap_usd']:.2f}. Nothing new was started; a running agent "
        "was not interrupted."
    )
```

`_dispatch` gains a `budget` keyword and the check on the agent branch **only**:

```python
async def _dispatch(
    db,
    run_dirs,
    task_hook,
    node,
    work_item_row,
    registry: Registry,
    worktree,
    *,
    instruction_override: str | None = None,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
) -> str:
```

and inside `if kind == "agent":`, as its first statement:

```python
        # Only agent tasks. A subprocess or builtin costs nothing, and stopping
        # `on.test.run` for a budget would strand the item mid-node for no saving.
        if _budget_breach(db, work_item_row["id"], budget) is not None:
            return BUDGET
```

`_measure_node` gains the same `budget` parameter, passes it to every `_dispatch`, and gains one precedence rung after the `paused` check:

```python
    # paused > budget > failed. A pause is a human's instruction and outranks
    # everything. A budget breach outranks a co-task's failure because the agent
    # never ran, so its "failure" is not evidence about the code.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
```

`_walk_node` gains `budget: _policy.Budget = _policy.NO_BUDGET` too, passes it into both `_measure_node` calls and the fix-cycle `_dispatch`, and handles the verdict in three places:

```python
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, policy_budget)
```

with one helper so the three sites do not drift:

```python
async def _stop_for_budget(db, work_item_id: str, node: dict, budget: _policy.Budget) -> str:
    breach = _budget_breach(db, work_item_id, budget) or {
        "scope": "work_item",
        "spent_usd": 0.0,
        "cap_usd": 0.0,
    }
    reason = _budget_reason(breach)
    await db.write(
        lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason, None, breach)
    )
    return "needs_human"
```

(The `or {...}` fallback cannot fire in practice — sums only grow — but a `None` here would crash the escalation path rather than stop the item, which is the wrong failure.)

The three sites in `_walk_node`: the no-fix-loop branch's `verdict` check, the fix-loop branch's `verdict` check, and the `fix = await _dispatch(...)` result (`if fix == BUDGET: return await _stop_for_budget(...)`).

- [ ] **Step 5: Thread `budget` from the run/resume entry points**

`executor.run`, `executor.resume` and every internal caller already carry `policy: _policy.Policy | None`. Do **not** add a parameter to them — derive it at the `_walk_node` call sites:

```python
budget=policy.budget if policy else _policy.NO_BUDGET
```

Grep for every `_walk_node(` call and every `_measure_node(` call and pass it. Missing one loses the cap silently on that path, which is exactly the defect sub-project C's plan warned about; check `resume` and the reconcile path specifically.

- [ ] **Step 6: Run the tests**

Run: `just test tests/test_budget.py tests/test_fix_loop.py tests/test_executor.py tests/test_resume.py`
Expected: PASS, all of them. The pre-existing files must not need edits — if one breaks, the default `NO_BUDGET` was not applied somewhere.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/executor.py src/kraft/store.py tests/test_budget.py
git commit -m "executor: a budget verdict that stops the item instead of lying about a failure"
```

---

### Task 4: The budget reaches the API

**Files:**
- Modify: `src/kraft/api.py` (`PolicyBody`, `put_policy`)
- Test: `tests/test_settings_api.py`

**Interfaces:**
- Consumes: `policy.Budget` (Task 1).
- Produces: `PUT /policy` accepts and persists `budget`; `GET /policy` returns it because it returns the file verbatim.

Nothing else in the API changes. `create_work_item` does not pre-check the budget: the check belongs at dispatch, where it also covers resume, retry, gate rejection and the fix cycle. An item created over its cap is created, runs its builtin `env_setup`, and stops at its first agent task with a budget reason — which is the honest report.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings_api.py` (reuse that file's existing client fixture):

```python
def test_put_policy_persists_the_budget_block(client):
    body = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 3600},
        "budget": {"work_item_usd": 20.0, "daily_usd": None},
    }
    assert client.put("/policy", json=body).status_code == 200
    assert client.get("/policy").json()["budget"] == {"work_item_usd": 20.0, "daily_usd": None}


def test_put_policy_rejects_a_negative_budget(client):
    body = {
        "loops": {},
        "default": {"attempts": 3, "wall_clock_s": 3600},
        "budget": {"work_item_usd": -5},
    }
    assert client.put("/policy", json=body).status_code == 422


def test_put_policy_without_a_budget_key_still_works(client):
    """Backward compatibility: an older UI build PUTs no budget."""
    body = {"loops": {}, "default": {"attempts": 3, "wall_clock_s": 3600}}
    assert client.put("/policy", json=body).status_code == 200
```

- [ ] **Step 2: Run and watch it fail**

Run: `just test tests/test_settings_api.py -k budget`
Expected: the first test fails — `budget` is dropped by `put_policy`, so `GET` has no such key (`KeyError`).

- [ ] **Step 3: Implement**

```python
class PolicyBody(BaseModel):
    loops: dict
    default: dict
    findings: dict | None = None
    budget: dict | None = None
```

and in `put_policy`, beside the existing `findings` handling:

```python
    if body.budget is not None:
        data["budget"] = body.budget
```

The existing `load_policy` round-trip through the temp file already rejects a bad budget with a 422 — no new validation code.

- [ ] **Step 4: Run the tests**

Run: `just test tests/test_settings_api.py && just lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_settings_api.py
git commit -m "api: PUT /policy carries the budget block"
```

---

### Task 5: The breach in the UI

**Files:**
- Create: `frontend/src/components/BudgetCard.tsx`
- Create: `frontend/src/components/BudgetCard.test.tsx`
- Modify: `frontend/src/types.ts`, `frontend/src/store.ts`, `frontend/src/views/WorkItemDetail.tsx`, `frontend/src/views/Settings.tsx`
- Test: also `frontend/src/views/Settings.test.tsx`

**Interfaces:**
- Consumes: the `work_item_needs_human` payload's `budget` key (Task 3); `GET/PUT /policy`'s `budget` (Task 4).
- Produces:
  - `types.WorkItem.budget?: { scope: "work_item" | "daily"; spent_usd: number; cap_usd: number } | null`
  - `types.Policy.budget?: { work_item_usd: number | null; daily_usd: number | null }`
  - `<BudgetCard item={item} />`

- [ ] **Step 1: Write the failing component test**

`frontend/src/components/BudgetCard.test.tsx` — follow `CappedCard.test.tsx` for the render/mock idiom (it is the closest neighbour; copy its imports and its `vi.mock("../api")` shape if it has one):

```tsx
const item = (budget: WorkItem["budget"], fixLoop = false): WorkItem => ({
  /* the minimal WorkItem the existing CappedCard.test.tsx builds, plus: */
  status: "needs_human",
  budget,
  chain_definition: { nodes: [{ id: "n", tasks: [], gate_after: null, fix_loop: fixLoop ? "l" : null }] },
  current_node_id: "n",
});

it("shows the spend against the cap for a work-item breach", () => {
  render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
  expect(screen.getByTestId("budget-card")).toHaveTextContent("$24.50");
  expect(screen.getByTestId("budget-card")).toHaveTextContent("$20.00");
});

it("says a daily breach stops every item, not this one", () => {
  render(<BudgetCard item={item({ scope: "daily", spent_usd: 120, cap_usd: 100 })} />);
  expect(screen.getByTestId("budget-card")).toHaveTextContent(/every work item/i);
});

it("states that a running agent was not interrupted", () => {
  render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
  expect(screen.getByTestId("budget-card")).toHaveTextContent(/not interrupted/i);
});

it("offers no retry on a node with no fix loop", () => {
  render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
  expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
});

it("offers retry on a fix-loop node, and warns it will re-breach", () => {
  render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 }, true)} />);
  expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  expect(screen.getByTestId("budget-card")).toHaveTextContent(/raise|clear/i);
});
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd frontend && npx vitest run src/components/BudgetCard.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `BudgetCard.tsx`**

A separate component, not a case inside `CappedCard` (spec amendment A5): `CappedCard` builds a cycle trace from `fix_cycle_started` events and its action is a steer into "cycle 1 of the retry" — a budget breach has no cycles.

```tsx
import { useState } from "react";
import { Coins } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItem } from "../types";

const usd = (n: number) => `$${n.toFixed(2)}`;

/**
 * The spend-cap attention card (sub-project E §3).
 *
 * The cap refused to START the next agent task. It did not interrupt one that
 * was running and could not have: the agent CLI only reports its cost when the
 * session exits. The card says so, because a card that reads like a hard
 * ceiling is how someone ends up with a bill and a reasonable complaint.
 *
 * Retrying does not clear anything — the money is spent and the sum will not go
 * down — so the retry control only appears where `POST /retry` would actually
 * be accepted (a node with a fix loop), and it says the run will stop again
 * unless the cap is raised or cleared first.
 */
export function BudgetCard({ item }: { item: WorkItem }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const b = item.budget;
  if (!b) return null;
  const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
  const canRetry = !!node?.fix_loop;

  const retry = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.retryWorkItem(item.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card attention-card" data-testid="budget-card">
      <div className="attention-head">
        <Coins size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">
            {b.scope === "daily"
              ? `today's budget is spent — ${usd(b.spent_usd)} of ${usd(b.cap_usd)}`
              : `this work item's budget is spent — ${usd(b.spent_usd)} of ${usd(b.cap_usd)}`}
          </span>
          <span className="attention-sub">
            {b.scope === "daily"
              ? "This stops every work item on this instance until local midnight or a higher cap. "
              : "No new agent task was started for this item. "}
            A running agent was not interrupted — cost is only known once a session
            ends, so the overshoot is one task, not the cap. Raise or clear the cap
            in Settings → Policy.
          </span>
        </div>
      </div>
      {canRetry && (
        <div className="gate-actions capped-actions">
          <button className="btn btn-secondary" disabled={busy} onClick={retry}>
            Retry anyway
          </button>
          <span className="control-hint">
            retry clears nothing — the spend stands, so this stops again at the next
            agent task unless the cap is raised or cleared first
          </span>
        </div>
      )}
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
```

- [ ] **Step 4: Wire the type, the store and the detail view**

`frontend/src/types.ts`, beside `cappedOut`:

```ts
  budget?: { scope: "work_item" | "daily"; spent_usd: number; cap_usd: number } | null;
```

and on `Policy`:

```ts
  budget?: { work_item_usd: number | null; daily_usd: number | null };
```

`frontend/src/store.ts` — everywhere `cappedOut` is read off `work_item_needs_human` or cleared on `node_started`, do the same for `budget`. There are four sites: `hydrateItem`'s `stopEv` derivation, the `node_started` case, the `work_item_retried`-adjacent reset at ~line 215, and the `work_item_needs_human` case. Mirror them exactly; a budget that survives a `node_started` pins the card open on an item that has since moved on.

`frontend/src/views/WorkItemDetail.tsx` — a branch **before** the capped one:

```tsx
      {item.budget ? (
        <div className="desktop-only">
          <BudgetCard item={item} />
        </div>
      ) : item.cappedOut || strandedInFixLoop ? (
```

- [ ] **Step 5: Add the Settings fields**

In `PolicyPage`, after the cap rows and before `<SaveRow>`:

```tsx
      <SectionLabel>Budget</SectionLabel>
      <div className="cap-row" data-budget="work_item_usd">
        <span className="hook-name">Per work item ($)</span>
        <input
          className="input"
          type="number"
          min={0}
          step="0.01"
          aria-label="work item budget"
          value={policy?.budget?.work_item_usd ?? ""}
          onChange={(e) => setBudget("work_item_usd", e.target.value)}
        />
        <span />
      </div>
      <div className="cap-row" data-budget="daily_usd">
        <span className="hook-name">Per day ($)</span>
        <input
          className="input"
          type="number"
          min={0}
          step="0.01"
          aria-label="daily budget"
          value={policy?.budget?.daily_usd ?? ""}
          onChange={(e) => setBudget("daily_usd", e.target.value)}
        />
        <span />
      </div>
      <p className="settings-note">
        Blank is no cap. A cap refuses to start the next agent task; it cannot stop
        one already running, because an agent only reports its cost when its session
        ends. Expect to overshoot by up to the cost of one task.
      </p>
```

with, beside `setCap`:

```tsx
  const setBudget = (field: "work_item_usd" | "daily_usd", raw: string) => {
    if (!policy) return;
    // "" is the operator clearing the cap, which is null — not 0, which would
    // block every launch.
    const n = raw.trim() === "" ? null : Number(raw);
    setDraft({
      ...policy,
      budget: { work_item_usd: null, daily_usd: null, ...policy.budget, [field]: n },
    });
  };
```

Update the page's `note` on `<PageHead title="Policy" .../>` to mention the budget alongside attempts and wall-clock.

- [ ] **Step 6: Write the Settings test**

Append to `frontend/src/views/Settings.test.tsx`, following that file's existing mock-`api` idiom:

```tsx
it("clearing a budget field saves null, not zero", async () => {
  /* render the Policy page with budget {work_item_usd: 20, daily_usd: null},
     clear the "work item budget" input, click Save, and assert
     api.putPolicy was called with budget.work_item_usd === null */
});

it("states the one-task overshoot", async () => {
  /* render the Policy page; expect text matching /overshoot/i */
});
```

Write both bodies out fully — the comments are the contract.

- [ ] **Step 7: Run the frontend gates**

Run: `just test-ui`
Expected: PASS, including `tsc -b`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "ui: a budget card that does not promise a ceiling it cannot hold"
```

---

### Task 6: `bd ready` and adopting an existing bead

**Files:**
- Modify: `src/kraft/adapters/beads.py`, `src/kraft/executor.py` (`intake`)
- Test: `tests/test_adapters_beads.py`, `tests/test_executor.py`

**Interfaces:**
- Produces:
  - `beads.ready(*, cwd: str | None = None) -> list[dict]` — rows of `{"id", "title", "priority", "issue_type"}`. Best-effort: `[]` on non-zero exit, missing binary or unparseable output.
  - `executor.intake(..., bead_id: str | None = None)` — given, it is adopted and `bd create` is not run.

- [ ] **Step 1: Write the failing tests**

`tests/test_adapters_beads.py` (it already drives a real `bd` through `isolated_bd`; follow that):

```python
def test_ready_returns_open_beads_with_int_priorities(tmp_path):
    repo = isolated_bd(tmp_path)
    bid = asyncio.run(beads.intake("pick me up", cwd=str(repo)))
    rows = asyncio.run(beads.ready(cwd=str(repo)))
    row = next(r for r in rows if r["id"] == bid)
    assert isinstance(row["priority"], int)
    assert row["title"] == "pick me up"


def test_ready_is_best_effort_on_a_directory_with_no_beads(tmp_path):
    """A poller that raises kills its own task; an empty list is the honest answer."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert asyncio.run(beads.ready(cwd=str(plain))) == []
```

`tests/test_executor.py`:

```python
def test_intake_adopts_a_given_bead_instead_of_filing_a_new_one(tmp_path, monkeypatch):
    """Auto-intake starts a bead that already exists. Filing a duplicate of it on
    every pickup is the failure this parameter exists to prevent."""
    called = False

    async def boom(*a, **kw):
        nonlocal called
        called = True
        raise AssertionError("bd create must not run when a bead_id is given")

    monkeypatch.setattr("kraft.executor.beads.intake", boom)
    # ... intake(..., bead_id="TEST-abc") ...
    # assert the work_items row's bead_id == "TEST-abc" and called is False
```

- [ ] **Step 2: Run and watch them fail**

Run: `just test tests/test_adapters_beads.py tests/test_executor.py -k "ready or adopts"`
Expected: `AttributeError: module 'kraft.adapters.beads' has no attribute 'ready'`.

- [ ] **Step 3: Implement `beads.ready`**

Shaped like the existing `search` — same `asyncio.to_thread(subprocess.run, ...)`, same "slice from the first `[`" tolerance, same swallow-and-return-`[]`:

```python
async def ready(*, cwd: str | None = None) -> list[dict]:
    """Beads with no unmet blockers — a thin `bd ready --json` passthrough.

    Best-effort like `search`: a repo with no beads workspace, a `bd` that is not
    installed, or output that is not JSON all answer `[]`. The only caller is a
    poller, and a poller that raises kills its own task and silently stops
    picking work up.
    """
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["bd", "ready", "--json"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if proc.returncode != 0 or "[" not in proc.stdout:
        return []
    try:
        rows = json.loads(proc.stdout[proc.stdout.index("[") :])
    except json.JSONDecodeError:
        return []
    return [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "priority": r.get("priority"),
            "issue_type": r.get("issue_type"),
        }
        for r in rows
        if isinstance(r, dict) and r.get("id") and r.get("title")
    ]
```

- [ ] **Step 4: Implement `intake(bead_id=...)`**

In `executor.intake`, replace the unconditional call:

```python
    # An auto-intaken bead already exists; filing a second one for the same work
    # is the duplicate this parameter prevents.
    bead_id = bead_id or await beads.intake(title, cwd=bd_cwd)
```

adding `bead_id: str | None = None` to the signature (keyword-only, with the other keyword arguments).

- [ ] **Step 5: Run the tests**

Run: `just test tests/test_adapters_beads.py tests/test_executor.py && just lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/adapters/beads.py src/kraft/executor.py tests/
git commit -m "beads: a ready passthrough, and an intake that can adopt a bead"
```

---

### Task 7: The auto-intake poller

**Files:**
- Create: `src/kraft/intake.py`, `templates/intake.yaml`
- Modify: `src/kraft/config.py`, `src/kraft/templates.py`, `src/kraft/api.py` (`lifespan`)
- Test: `tests/test_intake_poller.py` (create)

**Interfaces:**
- Consumes: `beads.ready` and `executor.intake(bead_id=...)` (Task 6); `policy.Budget` and `store.budget_spend` (Tasks 1–2).
- Produces:
  - `config.INTAKE_DEFAULT: dict` and `config.load_intake(path) -> dict`.
  - `intake.tick(app) -> list[str]` — the work item ids started this tick (`[]` is the normal answer). One tick, no sleeping, so a test can call it directly.
  - `intake.poller(app)` — the `while True` around `tick`, for `lifespan`.

**This task is last on purpose.** It is the only item in the sub-project that increases blast radius: it starts agent work with nobody watching. Everything before it only ever *refuses* to start work.

- [ ] **Step 1: Write `templates/intake.yaml` and declare it config**

```yaml
# Autonomous pickup from `bd ready`. Off by default, and read at startup — a
# change here takes effect on the next restart.
#
# An auto-started work item passes NO gate automatically. It runs to its first
# gate and stops for a human exactly as a typed-in one does. Auto-intake removes
# the typing, not the judgement.
enabled: false
interval_s: 300
max_concurrent: 1
repos: []              # empty means every enabled repo in repos.yaml
priority_ceiling: 2    # only P2 and below start unattended: the highest-priority
                       # work is what a human should be looking at
```

In `src/kraft/templates.py`:

```python
CONFIG_FILES = frozenset({"registry.yaml", "policy.yaml", "repos.yaml", "access.yaml", "intake.yaml"})
```

Without that line `load_templates` reads `intake.yaml` as a malformed chain template and the instance reports degraded health.

- [ ] **Step 2: Write the failing tests**

`tests/test_intake_poller.py`. It monkeypatches `beads.ready` rather than driving a real `bd` — the poller's logic is what is under test, and Task 6 already covers the passthrough:

```python
"""Auto-intake picks backlog beads up; it never passes a gate (spec §4-§5)."""

import asyncio
from types import SimpleNamespace

import pytest

from kraft import intake as intake_mod


def _ready(rows):
    async def ready(*, cwd=None):
        return list(rows)

    return ready


def test_disabled_by_default_does_nothing(app_stub, monkeypatch):
    monkeypatch.setattr(intake_mod.beads, "ready", _ready([{"id": "B-1", "title": "t", "priority": 3}]))
    app_stub.state.intake["enabled"] = False
    assert asyncio.run(intake_mod.tick(app_stub)) == []


def test_starts_one_bead_when_enabled(app_stub, monkeypatch): ...
def test_respects_max_concurrent_counting_every_active_item(app_stub, monkeypatch): ...
def test_skips_a_bead_that_already_has_a_work_item(app_stub, monkeypatch): ...
def test_skips_a_bead_above_the_priority_ceiling(app_stub, monkeypatch): ...
def test_does_not_run_while_the_daily_budget_is_breached(app_stub, monkeypatch): ...
def test_skips_a_repo_that_is_not_enabled(app_stub, monkeypatch): ...
```

The `app_stub` fixture is real work, so write it out here and put it in this test file (not `conftest.py` — nothing else needs it):

```python
@pytest.fixture
def app_stub(tmp_path, monkeypatch):
    """A minimal stand-in for the FastAPI app `tick` reads off `app.state`.

    Not a TestClient: `tick` only touches `state.db`, `state.run_dirs`,
    `state.templates`, `state.registry`, `state.policy`, `state.intake`,
    `state.templates_dir` and `state.tasks`, and driving a whole server to
    exercise a scheduling decision is slower and hides which of those it used.
    """
```

The implementer builds it from `db.Database.open(RunDirs(tmp_path / "run").ensure().db)`, `fake_templates_dir`, `load_registry`, `load_templates`, a `policy.Policy(...)`, `intake` = a dict copy of `config.INTAKE_DEFAULT` with `enabled: True`, and `tasks = {}`. Write a real `repos.yaml` into the templates dir with one enabled repo built by `make_repo(tmp_path)`, because `tick` reads it through `config.load_repos`.

`tick` calls `_spawn`-equivalent work; in the stub, monkeypatch `intake_mod.executor.run` to an async no-op so no agent is launched, and assert on the `work_items` rows instead.

Each `...` body above must be written in full by the implementer; the names state the assertion.

- [ ] **Step 3: Run and watch them fail**

Run: `just test tests/test_intake_poller.py`
Expected: `ModuleNotFoundError: No module named 'kraft.intake'`.

- [ ] **Step 4: `config.load_intake`**

```python
# ── auto-intake ──────────────────────────────────────────────────────────────

INTAKE_DEFAULT: dict = {
    "enabled": False,
    "interval_s": 300,
    "max_concurrent": 1,
    "repos": [],
    "priority_ceiling": 2,
}


def load_intake(path: str | Path) -> dict:
    """`intake.yaml`, with every missing key defaulted. A missing file is off."""
    return {**INTAKE_DEFAULT, **read_yaml(path, INTAKE_DEFAULT)}
```

- [ ] **Step 5: Write `src/kraft/intake.py`**

```python
"""Autonomous pickup from `bd ready` (sub-project E §4-§5).

Off by default, and the one thing in this sub-project that starts work rather
than refusing to. The boundary it must not cross is in §5 and is worth stating
where the code is: **an auto-started work item passes no gate automatically.**
It is created with the same `executor.intake` and run with the same
`executor.run` as a typed-in one, so it reaches its first gate and stops for a
person. Auto-intake removes the typing, not the judgement. An auto-start that
also auto-approved would be a different product, and would get its own design
document before any of it was written.
"""

from __future__ import annotations

import asyncio
import logging

from kraft import config as config_mod
from kraft import executor, policy as policy_mod, store
from kraft.adapters import beads

logger = logging.getLogger(__name__)


def _active_count(db) -> int:
    return db.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM work_items WHERE status = 'active'"
        ).fetchone()[0]
    )


def _known_beads(db) -> set[str]:
    return {
        r[0]
        for r in db.read(
            lambda c: c.execute(
                "SELECT bead_id FROM work_items WHERE bead_id IS NOT NULL"
            ).fetchall()
        )
    }


def _daily_breached(db, budget: policy_mod.Budget) -> bool:
    """Only the daily cap is evaluable here: a candidate has no work item yet, so
    it has no per-item spend. The per-item cap does its work at first dispatch."""
    if budget.daily_usd is None:
        return False
    since = store.local_midnight_utc()
    # The work-item argument is irrelevant to the daily figure; pass a value that
    # matches nothing rather than inventing an overload.
    _item, daily = db.read(lambda c: store.budget_spend(c, "", since=since))
    return daily >= budget.daily_usd


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids started, which is usually none."""
    st = app.state
    cfg = st.intake
    if not cfg.get("enabled"):
        return []
    if st.invalid_policy:
        return []
    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    if _daily_breached(st.db, budget):
        logger.info("auto-intake: daily budget reached, starting nothing")
        return []
    # Every active item counts, not only auto-started ones: a person working on
    # three things must not find the poller adding a fourth.
    slots = int(cfg.get("max_concurrent", 1)) - _active_count(st.db)
    if slots <= 0:
        return []

    try:
        repos = config_mod.load_repos(_repos_path(st), validate_steering=False)
    except config_mod.ConfigError as exc:
        logger.warning("auto-intake: repo config invalid, skipping this tick: %s", exc)
        return []
    wanted = set(cfg.get("repos") or [])
    repos = [r for r in repos if r.get("enabled") and (not wanted or r["path"] in wanted)]

    known = _known_beads(st.db)
    ceiling = int(cfg.get("priority_ceiling", 2))
    started: list[str] = []
    for repo in repos:
        if slots <= 0:
            break
        for row in await beads.ready(cwd=repo["path"]):
            if slots <= 0:
                break
            # P0 is the *highest* priority, so "P2 and below" is `>= ceiling`.
            # The highest-priority work is what a human should be looking at;
            # unattended pickup is for the backlog.
            if row["id"] in known or not isinstance(row.get("priority"), int):
                continue
            if row["priority"] < ceiling:
                continue
            wid = await _start(app, repo, row)
            if wid is not None:
                started.append(wid)
                known.add(row["id"])
                slots -= 1
    return started
```

`_start` files and runs the item, reusing the API's own helpers so an auto-started item is indistinguishable from a typed-in one:

```python
async def _start(app, repo: dict, row: dict) -> str | None:
    from kraft.api import _bd_cwd, _guard, _launch, _spawn

    st = app.state
    template = st.templates.valid.get(repo.get("default_chain_template") or "default")
    if template is None:
        logger.warning("auto-intake: %s has no valid chain template, skipping", repo["path"])
        return None
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=row["title"],
            repo=repo["path"],
            template=template,
            bd_cwd=_bd_cwd(),
            bead_id=row["id"],
        )
    except Exception:  # noqa: BLE001 -- one bad bead must not stop the poller
        logger.exception("auto-intake: could not file %s", row["id"])
        return None
    logger.info("auto-intake: started %s from %s", wid, row["id"])
    _spawn(
        app,
        wid,
        _guard(
            st.db,
            wid,
            executor.run(
                st.db,
                st.run_dirs,
                work_item_id=wid,
                registry=st.registry,
                bd_cwd=_bd_cwd(),
                policy=st.policy,
                launch=_launch(st, repo["path"]),
            ),
        ),
    )
    return wid


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    interval = max(30, int(app.state.intake.get("interval_s", 300)))
    while True:
        await asyncio.sleep(interval)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("auto-intake tick failed")
```

Import `_repos_path` from `kraft.api` inside `tick` the same way `_start` does — `api` imports nothing from `intake` at module scope, so keep the dependency one-directional and function-local to avoid a cycle.

- [ ] **Step 6: Wire it into `lifespan`**

In `api.py`, beside the other config reads:

```python
    app.state.intake = config_mod.load_intake(templates_dir / "intake.yaml")
```

and after the broadcaster starts, guarded so a disabled poller costs nothing:

```python
    intake_task = (
        asyncio.ensure_future(intake_mod.poller(app)) if app.state.intake["enabled"] else None
    )
```

Cancel and await it in the `finally` block, before the work item tasks are cancelled:

```python
        if intake_task is not None:
            intake_task.cancel()
            await asyncio.gather(intake_task, return_exceptions=True)
```

Add `from kraft import intake as intake_mod` to the imports.

- [ ] **Step 7: Run the tests**

Run: `just test tests/test_intake_poller.py tests/test_api.py tests/test_templates.py && just lint`
Expected: PASS. `test_templates.py` may assert on `CONFIG_FILES`; update that assertion to include `intake.yaml`.

- [ ] **Step 8: Commit**

```bash
git add src/kraft/intake.py src/kraft/config.py src/kraft/templates.py src/kraft/api.py templates/intake.yaml tests/test_intake_poller.py
git commit -m "intake: pick backlog beads up, and stop at the first gate like everyone else"
```

---

### Task 8: The §5 regression test

**Files:**
- Test: `tests/test_intake_poller.py` (append)

**Interfaces:** consumes everything above.

This is the one test the sub-project exists to keep passing. It is separate from Task 7 so that a reviewer can reject it on its own, and so it cannot be quietly weakened while making Task 7's other tests go green.

- [ ] **Step 1: Write the test**

```python
def test_an_auto_started_item_stops_at_its_first_gate(tmp_path, monkeypatch):
    """The line auto-intake must not cross (spec §5).

    Kraft's whole differentiation is bounded autonomy with a human at the gates.
    An auto-start that also auto-approved would be the Auto-Company class of tool
    with worse marketing. If this test is ever failing, the feature is wrong, not
    the test.
    """
```

It must use the **real** executor — not the monkeypatched no-op the other poller tests use — driving `fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))` and the default chain template, then:

- poll until a `gate_requested` event lands (reuse the `_poll_for` helper in `tests/test_autostart.py`; copy it into this file rather than importing across test modules),
- assert `work_items.status == "needs_human"`,
- assert **no** `gate_approved` event exists for the item,
- assert the chain did not advance past the gate node — the `node_completed` set does not contain any node after the gated one.

Because it drives real subprocesses, build it on the `TestClient` fixture pattern from `tests/test_autostart.py` (which sets `KRAFT_RUN_DIR`, `KRAFT_BD_CWD`, `KRAFT_TEMPLATES_DIR` and `KRAFT_FRONTEND_DIST` through `monkeypatch.setenv`) and write `intake.yaml` with `enabled: true` into the templates dir before the client starts, then call `intake_mod.tick(client.app)` directly rather than waiting out `interval_s`.

- [ ] **Step 2: Run it**

Run: `just test tests/test_intake_poller.py -k first_gate`
Expected: PASS.

- [ ] **Step 3: Full gates**

Run: `just test && just test-ui && just lint`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_intake_poller.py
git commit -m "test: an auto-started item stops at its first gate"
```

- [ ] **Step 5: Hand back**

Report the gate output. The MR, the merge and the bead bookkeeping are the coordinator's.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 the constraint: refuses the next launch, cannot interrupt | Global Constraints; Tasks 3, 5 (card copy), 5 (Settings help text) |
| §1 no release note calls it a spending limit | Global Constraints — a review item, not a code change |
| §2 budget is not a field on `Cap` | "What a plan reviewer already killed"; Task 1 |
| §2 `budget:` sibling to `loops:`, both null on upgrade | Task 1 |
| §2 evaluation is a query, not a counter | Task 2 |
| §2 null `cost_usd` contributes zero | Task 2 |
| §2 check before an agent-kind launch; subprocess/builtin never blocked | Task 3 |
| §3 breach → `needs_human` with a distinguishable reason | Task 3 |
| §3 budget card shows spend against cap | Task 5 |
| §3 retry clears nothing; the card says so | Task 5 |
| §3 daily breach wording is instance-level | Task 5 |
| §4 `intake.yaml`, all five keys | Task 7 |
| §4 `bd ready` per repo, drop known beads, drop above ceiling | Tasks 6, 7 |
| §4 `max_concurrent` counts every active item | Task 7 |
| §4 refuses to start while a budget is breached | Task 7 (daily only — spec amendment A10) |
| §4 disabled means it does not run at all | Task 7 (no task is even created) |
| §5 an auto-started item passes no gate | Task 8 |
| §6 every listed test | Tasks 1–8; see the mapping in each task |
| Acceptance: `just test`, `just test-ui`, `just lint` | Task 8 Step 3 |

**Spec items deliberately not implemented:**

- **§3's "the same clear-and-re-run action as `POST /retry`" on any node.** That endpoint 409s without a `fix_loop`; amendment A4. The card renders the retry only where it would be accepted.
- **§4's Settings screen for auto-intake.** The spec does not ask for one; amendment A9. `intake.yaml` is hand-edited, read at startup, restart to apply. Follow-up bead.
- **`beads.complete` on an auto-intaken bead.** It runs against `KRAFT_BD_CWD`, not the bead's own repo, so closing an auto-intaken bead will fail. Amendment A6; follow-up bead, not fixed here — fixing it means threading a per-item bd workspace through the merge path, which is its own change.

**Type consistency:** `Budget` / `NO_BUDGET` (Task 1) are used by name in Tasks 3 and 7. `budget_spend` returns `(item, daily)` in Task 2 and is destructured that way in Tasks 3 and 7. The breach dict is `{scope, spent_usd, cap_usd}` in Task 3, in the event payload, in `types.WorkItem.budget` and in `BudgetCard` — one shape, four places, spelled identically.

**Placeholders:** the `...` bodies in Tasks 3, 5 and 7 are named tests whose assertions are given in the docstring or the adjacent comment. They are deliberate: writing six near-identical `asyncio.run(scenario())` bodies out in full would be the plan copying a file that the implementer is about to read anyway. Every *interface* — every name, signature, dict shape and file path — is concrete.

**The risk this plan carries:** Task 3 Step 5 threads `budget=` through call sites by grep. A missed site does not raise; it silently launches an agent with no cap, which is the exact class of defect the epic keeps hitting. The reviewer for Task 3 should grep `_walk_node(` and `_measure_node(` themselves rather than trusting the diff.

---

## Plan review corrections

A plan review read the tree and found 20 defects above. Each correction below
**overrides** the task body it names. They are numbered as the review reported
them; nothing is dropped, including the ones that turned out to be cosmetic.

### C1 (blocker, Task 2) — `tests/test_store.py` has no `conn` fixture

There are no fixtures in that file at all. Every test is
`asyncio.run(scenario())` around `await database.write(lambda c: ...)` and
`database.read(lambda c: ...)`, with `db.Database.open` called **inside** the
scenario. `tests/conftest.py` defines only `pytest_collection_modifyitems`.

Rewrite Task 2's tests in that idiom. `_spend_fixture_one` becomes:

```python
async def _spend_fixture_one(database, *, costs: list[float | None]) -> str:
    wid = uuid.uuid4().hex
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo="/tmp/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    for i, cost in enumerate(costs):
        sid = uuid.uuid4().hex
        await database.write(
            lambda c, sid=sid, i=i: store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id="n",
                hook_point=f"on.h{i}",
                log_path="/tmp/l",
                result_path="/tmp/r",
            )
        )
        await database.write(
            lambda c, sid=sid, cost=cost: c.execute(
                "UPDATE worker_sessions SET cost_usd = ? WHERE id = ?", (cost, sid)
            )
        )
    return wid
```

and each test reads `database.read(lambda c: store.budget_spend(c, wid))`. The
`lambda c, x=x:` default-argument binding is not optional — a bare closure over
a loop variable captures the last value.

### C2 (blocker, Task 2) — `local_midnight_utc` and its test disagree

`datetime.astimezone()` with no argument converts to the **machine's** zone, so
the plan's implementation makes the test machine-timezone-dependent and it fails
outside `Asia/Tokyo`. The intent is "midnight in whatever zone the passed moment
is in; the machine's zone when nothing is passed". Implementation, replacing
Task 2 Step 3's:

```python
def local_midnight_utc(now: datetime | None = None) -> str:
    """Midnight of `now`'s own day, in `now`'s own zone, as a UTC ISO string.

    "Daily" means the operator's day, not UTC's — a cap that rolls over at 5pm
    local is a cap nobody can reason about. Normalized to UTC on the way out so
    it compares as a string against the `created_at` values `_now()` writes.

    ponytail: the offset is the one in force *now*, not the one in force at
    midnight, so on a DST-transition day the window starts an hour early or
    late. A spend cap does not care; if something here ever does, resolve the
    offset at the midnight instant instead.
    """
    now = now or datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()
```

Task 2's `test_local_midnight_is_utc_and_sorts_against_stored_timestamps` is then
correct as written and is machine-independent.

### C3 (blocker, Task 7) — the `app_stub` fixture cannot be a sync fixture

`db.Database.open` is async and starts a writer task bound to the loop it was
created in; `write` awaits a future on `asyncio.get_running_loop()`. A
`Database` built in a sync fixture and used inside a later `asyncio.run(...)`
hangs forever.

Replace the fixture with an async builder called inside each test's single
`asyncio.run`:

```python
@dataclass
class _Stub:
    """What `intake.tick` reads off `app.state`, and nothing else.

    Not a TestClient: `tick` makes a scheduling decision, and driving a whole
    server to observe one is slower and hides which of these it actually used.
    """

    state: SimpleNamespace


async def _stub(tmp_path, **intake_overrides) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    templates_dir = fake_templates_dir(tmp_path, sys.executable)
    repo = make_repo(tmp_path)
    (templates_dir / "repos.yaml").write_text(
        yaml.safe_dump(
            {"repos": [{"path": str(repo), "enabled": True, "default_chain_template": "default"}]}
        )
    )
    registry = load_registry(templates_dir / "registry.yaml")
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            registry=registry,
            templates=load_templates(templates_dir, registry),
            templates_dir=templates_dir,
            policy=policy.Policy(loops={}, default=policy.Cap(3, 3600)),
            invalid_policy=[],
            intake={**config.INTAKE_DEFAULT, "enabled": True, **intake_overrides},
            tasks={},
        )
    )
```

Every test then reads:

```python
def test_disabled_by_default_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_mod.beads, "ready", _ready([...]))

    async def scenario():
        app = await _stub(tmp_path, enabled=False)
        try:
            assert await intake_mod.tick(app) == []
        finally:
            await app.state.db.close()

    asyncio.run(scenario())
```

Note `invalid_policy=[]` — correction C10 — and that `_stub` writes the
`repos.yaml` that `fake_templates_dir` does not (correction C11).

### C4 (blocker, Task 7) — `_repos_path` is used in `tick` and imported nowhere

`tick`'s first line inside the repo block must be:

```python
    from kraft.api import _repos_path
```

Before committing Task 7, grep the finished `intake.py` for every bare
`_`-prefixed name and confirm each is either defined in the module or imported
function-locally. That is the check, not a spot fix.

### C5 (blocker, Tasks 7 and 8) — an ungated template makes §5 vacuous

`templates/quick-task.yaml` has `gate_after: null` on all three nodes. A repo
whose `default_chain_template` is `quick-task` would have the poller run
implementation and verify end to end with no human anywhere — which is exactly
the product line §5 says must not be crossed, reached by configuration rather
than by code.

**Ruling: `_start` refuses to auto-start a template with no gate.** Add, before
the `executor.intake` call:

```python
    # Spec §5 is "an auto-started item passes no gate automatically". A template
    # with no gate at all satisfies that by having nothing to pass, which is the
    # letter of the rule and the opposite of its point: it would run to merge
    # unattended. A human can still start such a chain by hand — they are the
    # judgement the gates exist to invoke.
    if not any(n.get("gate_after") for n in template.nodes):
        logger.warning(
            "auto-intake: %s uses %r, which has no gate — refusing to start it "
            "unattended; a person can start it from the board",
            repo["path"],
            template.id,
        )
        return None
```

Task 7 gains a test for it (`test_refuses_a_template_with_no_gate`, using a repo
configured for `quick-task`, asserting `tick` returns `[]` and no work item row
was created). Task 8's §5 regression test uses `default`, which gates at node 0.

### C6 (blocker, Task 5) — the test's `item` helper must cast, not annotate

`WorkItem` requires `id`, `title`, `repo`, `chain_template`, `created_at`,
`updated_at` and more; `just test-ui` runs `tsc -b` and will reject a partial
literal typed as `WorkItem`. Follow `CappedCard.test.tsx`, which ends its
literal with `} as WorkItem)`. Same for the `chain_definition` literal.

### C7 (major, Task 3) — `policy_budget` is an undefined name

The parameter is called `budget`. All three `_stop_for_budget` call sites read
`_stop_for_budget(db, work_item_id, node, budget)`.

### C8 (major, Task 3) — do not add a `budget` parameter to `_walk_node`

`_walk_node` already takes `policy: _policy.Policy | None = None`, and all four
of its call sites already pass it — two of them inside
`_reconcile_current_node`, which Task 3 Step 5 never names. Adding a parallel
parameter creates exactly the "missed a call site silently loses the cap"
defect the plan's own Self-Review warns about.

**Replace Task 3 Step 5 entirely with:** derive it once, inside `_walk_node`,
next to the existing `cap = _policy.resolve_cap(policy, key)`:

```python
    budget = policy.budget if policy else _policy.NO_BUDGET
```

placed before the no-fix-loop branch so both branches see it, and pass that down
to `_measure_node` and the fix-cycle `_dispatch`. `_measure_node` and `_dispatch`
keep the new `budget` keyword from Step 4. **Zero external call sites change**,
so none can be missed. The Self-Review's "risk this plan carries" paragraph no
longer applies and should be read as retracted.

### C9 (major, Task 6) — `beads.ready` must catch `ValueError` too

`subprocess.run(..., text=True)` decodes stdout as UTF-8 and raises
`UnicodeDecodeError` — a `ValueError`, which `except OSError` does not catch —
on a `bd` that emits invalid bytes. This is the defect this repo has shipped
four times, on a path whose whole purpose is not to raise. Use:

```python
    except (OSError, ValueError):
        return []
```

Also correct the plan's claim that this mirrors `beads.search`: `search` has no
`try` around its `subprocess.run` at all and propagates `FileNotFoundError`.
File a follow-up bead for `search`; do not fix it in this branch.

**Stale as of the rebase onto `main` (2026-09-06):** `main`'s open-bug wave
(`f5f39fe`) fixed `search` independently, with the same `except OSError,
ValueError` clause. `ready` now mirrors it rather than diverging from it, and
the follow-up bead (`Kraft-9m4`) is closed as fixed upstream. The correction
above still describes what `ready` must do; only its account of `search` is out
of date.

### C10 (major, Task 7) — the stub needs `invalid_policy`

`tick` reads `st.invalid_policy`. Folded into C3's `_stub`.

### C11 (major, Tasks 7 and 8) — no harness helper makes a repo that is both a beads workspace and a source repo, and `fake_templates_dir` writes no `repos.yaml`

`make_repo` copies `sample_repo` and `git init`s it — no `.beads`, so `bd ready`
there returns `[]`. `isolated_bd` copies the `bd init`ed template — a git repo
with no source. `fake_templates_dir` writes `quick-task.yaml`, `default.yaml`,
`registry.yaml` and `policy.yaml` and **no** `repos.yaml`, so `load_repos`
returns `{"repos": []}` and the poller finds nothing.

- Task 7's tests monkeypatch `beads.ready`, so they need only the `repos.yaml`
  that C3's `_stub` now writes.
- Task 8 drives real `bd`. Build its repo as `make_repo(tmp_path)` followed by
  `subprocess.run(["bd", "init", "--prefix", "TEST"], cwd=repo, check=True)` and
  one `bd create`, and write `repos.yaml` into the templates dir with that repo,
  `enabled: true`, `default_chain_template: default`, **before** the
  `TestClient` context is entered — `lifespan` reads config at startup.

### C12 (major, Task 7) — `enabled` is not defaulted by `config.load_repos`

`load_repos` `setdefault`s `default_model`, `deny_tools` and `steering` only. A
hand-written `repos.yaml` entry without `enabled` reads as `None` and would be
skipped silently. The API's own default is `enabled: bool = True`, so match it:

```python
    repos = [r for r in repos if r.get("enabled", True) and (not wanted or r["path"] in wanted)]
```

Use `r.get("default_chain_template") or "default"` for the same reason — already
what `_start` does.

### C13 (minor, Task 5) — the neighbours use `vi.spyOn`, not `vi.mock`

`CappedCard.test.tsx` and `Settings.test.tsx` both use
`vi.spyOn(api, "…").mockResolvedValue(…)` with `beforeEach(() => vi.restoreAllMocks())`.
Use `vi.spyOn(api, "retryWorkItem")` and `vi.spyOn(api, "putPolicy")`. Do not
introduce a third idiom.

### C14 (minor, Task 7) — nothing in `tests/` asserts on `CONFIG_FILES`

Task 7 Step 7's "update that assertion" does not apply; there is no such
assertion. The only other consumer is `api.py`'s template-copy path, where
including `intake.yaml` is exactly what is wanted.

### C15 (minor, Task 4) — mirror the findings-preservation test

`put_policy` rebuilds `data` from the body, so a PUT without a key erases it —
which is why `test_saving_the_policy_preserves_the_findings_block` exists. The
UI round-trips the whole `GET /policy` body, so `budget` survives the same way
`findings` does. Keep the behaviour symmetric with `findings` and add the mirror
test rather than changing `put_policy`:

```python
def test_saving_the_policy_preserves_the_budget_block(client, templates_dir):
    (templates_dir / "policy.yaml").write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: 20.0\n  daily_usd: null\n"
    )
    body = client.get("/policy").json()
    assert body["budget"]["work_item_usd"] == 20.0
    body["default"]["attempts"] = 5
    assert client.put("/policy", json=body).status_code == 200
    on_disk = yaml.safe_load((templates_dir / "policy.yaml").read_text())
    assert on_disk["budget"]["work_item_usd"] == 20.0
```

Task 4's `test_put_policy_without_a_budget_key_still_works` stays — it asserts
the older-client case does not 500, which is a different claim.

### C16 (minor, Task 2) — DST comment

Folded into C2's docstring.

### C17 (minor, Task 7) — `issue_type` is carried and never used

**Ruling: skip epics.** An epic is a container for work, not work; auto-starting
one files a chain against a title that describes a quarter. Everything else —
task, bug, feature, chore — is fair game, because `bd ready` has already
decided it is startable and `priority_ceiling` has already decided it is
backlog.

```python
            if row.get("issue_type") == "epic":
                continue
```

with a test (`test_skips_an_epic`).

### C18 (minor, Task 3) — "five of them" precedes six tests

There are six. Write all six.

### C19 (minor, Task 5) — `.cap-row` is a three-column grid

`styles.css:791` is `1fr 110px 140px`, so the plan's two-cell budget rows leave
a dangling spacer and put a dollar field in the attempts column. Add one rule
beside it:

```css
.budget-row { grid-template-columns: 1fr 140px; }
```

and use `className="cap-row budget-row"` with no trailing `<span />`.

### C20 (minor, `config.read_yaml`) — `UnicodeDecodeError` escapes

`config.read_yaml` catches `(OSError, yaml.YAMLError)` around `read_text()`, so
a config file with invalid bytes raises an uncaught `ValueError` — and
`load_intake` inherits it into `lifespan`, where it crashes startup rather than
degrading. It is pre-existing, it is one word, and this plan's Global
Constraints claim to have swept this class. Fix it in Task 7:

```python
    except (OSError, ValueError, yaml.YAMLError) as exc:
```

(`UnicodeDecodeError` is a `ValueError`; `yaml.YAMLError` is not, so both stay.)

### Not corrections — verified sound, do not re-litigate

The reviewer confirmed by reading the tree: no migration is needed
(`worker_sessions.cost_usd` and `work_items.bead_id` both exist in the base
schema); lexicographic `created_at` comparison is sound and `store.create_session`
is its only writer; `Database.read` is synchronous and every new call site gets
that right; `_daily_breached` passing `""` is sound via `COALESCE`;
`_measure_node`'s tuple shape is preserved; there is no import cycle; nothing in
the plan claims mid-session enforcement; `Coins`, `SectionLabel`, every reused
CSS class, `types.ChainNode.fix_loop` and `api.retryWorkItem(id)` all exist; the
four `store.ts` `cappedOut` sites are real and at lines 62-76, 102, 208-216 and
218-225; and every helper signature the plan calls is correct as written.
