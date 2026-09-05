# Sub-project E: budget caps and auto-intake

Date: 2026-09-04
Beads: Kraft-8mu.5 (parent Kraft-8mu); blocked by Kraft-8mu.3

## Problem

Kraft measures spend it cannot limit. `worker_sessions` carries `cost_usd`,
`tokens_in`, `tokens_out` and `model` per session; `analytics.py` reports totals
by node, repo and week. `policy.yaml` bounds attempts and wall-clock and says
nothing about money. A fix loop that burns its three attempts on an expensive
model costs whatever it costs, and Kraft's only defence is that it eventually
runs out of attempts.

Separately, work only starts when a human types it in. `bd ready` already knows
what is available; Kraft cannot pick any of it up.

The two are one sub-project, in that order, because auto-intake without a budget
ceiling is the single most expensive mistake this codebase could ship.

**Blocked by `Kraft-8mu.3`**: unattended starts are only defensible once a hook
can be denied tools it does not need.

## Non-goals

- Provider billing integration. Kraft reads what the agent CLI reports and never
  calls a billing API.
- Forecasting a run's cost before it starts.
- Auto-intake from anywhere but `bd ready`.
- Auto-approving gates. §5, and it is not negotiable.

## 1. The constraint that shapes everything

`usage.py:91` reads cost from the agent's log envelope, and
`adapters/agent.py` passes `--output-format json`, which the CLI writes **when
the session exits**. There is no in-flight cost signal.

Therefore: **a budget cap refuses to start the next task. It cannot stop a
running one.**

A $20 cap means "do not launch anything new once the item has passed $20", and
the overshoot is bounded by the cost of one task, not by the cap. The spec says
this in those words, the Settings screen says it in the field's help text, and
no release note calls it a spending limit. Promising a ceiling the mechanism
cannot deliver is how a user ends up with a bill and a reasonable complaint.

Bounding the overshoot further would mean parsing streaming output for
incremental usage — a per-CLI format, in a codebase that has just finished
isolating one CLI's flags behind `PROFILES`. Not now; the hook is the same
`resolve_invocation` seam if it becomes necessary.

## 2. Cap shape

Budget is *not* a third field on `policy.Cap`. `Cap` is per-loop and is stored
per `(work_item_id, key)` in `retry_counters` alongside `cap_attempts` and
`cap_wall_s`; a spend limit is per work item and per day, spanning every loop.
Forcing it into that row means writing the same limit into every counter and
reconciling them.

`policy.yaml` gains a sibling to `loops`:

```yaml
budget:
  work_item_usd: 20.0     # null disables
  daily_usd: 100.0        # null disables
```

Both default to null on upgrade, so an existing install's behaviour does not
change until the user opts in.

Evaluation is a query, not a counter — `worker_sessions.cost_usd` is already the
source of truth, and a parallel counter is a second thing to get wrong:

- work item: `SUM(cost_usd) WHERE work_item_id = ?`
- daily: `SUM(cost_usd) WHERE created_at >= <local midnight>`

Sessions with a null `cost_usd` — a still-running task, or a subprocess or
builtin task that has no cost — contribute zero. Under-counting in-flight spend
is a direct consequence of §1 and needs no separate handling.

The check runs in `policy.py` immediately before an agent-kind task is launched.
Subprocess and builtin tasks are never blocked: they cost nothing, and stopping
`on.test.run` because of a budget would strand an item mid-node for no saving.

## 3. What a breach does

It reuses the escalation path that already exists rather than inventing a
terminal state. The work item goes to `needs_human`, the reason distinguishes a
budget breach from an attempts or wall-clock breach, and `CappedCard.tsx` gains
a case that shows spend against the cap and offers the same "clear and re-run"
action as `POST /work-items/{wid}/retry`.

That endpoint already takes steer text and clears a breached counter. For a
budget breach it clears nothing — the money is spent, the sum will not go down —
so retrying past a budget breach requires raising the cap or clearing it, and
the card says so. A retry button that silently re-breaches on the next launch is
a loop with a human in it.

A `daily_usd` breach stops every item, so its card wording is instance-level, not
item-level.

## 4. Auto-intake

A poller, off by default, in `$KRAFT_HOME/templates/intake.yaml`:

```yaml
enabled: false
interval_s: 300
max_concurrent: 1
repos: []              # empty means every enabled repo
priority_ceiling: 2    # only P2 and below start unattended
```

Each tick: run `bd ready` per repo through the existing beads adapter, drop
beads that already have a work item, drop anything above `priority_ceiling`,
and start work items up to `max_concurrent` *active* items — counting all active
items, not only auto-started ones, so a human working on three things does not
find the poller adding a fourth.

It refuses to start anything when a budget cap is currently breached, and when
`enabled` is false it does not run at all.

`priority_ceiling` deserves its inversion: the highest-priority work is the work
a human should be looking at. Unattended pickup is for the backlog.

## 5. The line auto-intake must not cross

**An auto-started work item passes no gate automatically.**

It runs to its first gate and stops in `needs_human`, exactly as a
human-created item does. Auto-intake removes the typing, not the judgement.

This is the thesis boundary. Kraft's whole differentiation against the
Auto-Company class of tool is bounded autonomy with a human at the gates; an
auto-start that also auto-approves is that tool, with worse marketing. If a
future request is "let it merge without me", it is a different product decision
and gets its own document.

Paired with `Kraft-8mu.4`: an auto-started item that reaches a gate notifies.
Without notifications, auto-intake produces work that silently queues up behind
gates nobody knows are there — which is why `B` should land first in practice
even though only `C` blocks this formally.

## 6. Testing

- budget: under cap launches; over cap does not; a null cap never blocks
- an agent-kind task is blocked and a subprocess task in the same node is not
- null `cost_usd` sessions count as zero
- daily rollover at local midnight
- breach lands `needs_human` with a budget reason and renders the budget card
- retry past a budget breach without raising the cap re-breaches at the next
  launch and does not loop
- auto-intake: disabled by default; respects `max_concurrent` counting all
  active items; skips beads that already have a work item; skips above the
  priority ceiling; does not run while a budget is breached
- **an auto-started item stops at its first gate** — the §5 regression test

## Acceptance

- A work item that exceeds its budget stops with a clear reason and an accurate
  spend figure.
- The Settings help text states the one-task overshoot.
- With auto-intake enabled, a P3 bead becomes a running work item without a
  human, and stops at its first gate.
- `just test`, `just test-ui`, `just lint` pass.

---

## Amendments, 2026-09-05 — reality after `C` landed

Re-read against the merged tree at `ae2619f`. Sub-project `C` (agent invocation
contract) is in; `Kraft-8mu.3` is closed. Nine things this spec assumed have
moved, or were never true.

### A1. No schema migration. At all.

Everything a budget needs is already stored: `worker_sessions.cost_usd`
(`db.py` schema, added by `_MIGRATIONS[3]`) and `work_items.bead_id`. Both caps
are `SUM` queries and auto-intake's "already has a work item" test is a
`bead_id` lookup. `SCHEMA_VERSION` stays where sub-project `G` leaves it and
`_MIGRATIONS` gains no key. This is a hard constraint on the plan, not a
preference: `G` is bumping 9 → 10 concurrently.

### A2. §2's "the check runs in `policy.py`" is imprecise

`policy.py` is pure and does no I/O — `load_policy` reads one YAML file and
`check()` is arithmetic. The budget splits three ways along the seams that
already exist:

- `policy.Budget` + parsing of the `budget:` block, in `policy.py`.
- `store.budget_spend(conn, work_item_id, since)` — the two `SUM(cost_usd)`
  queries, in `store.py` with every other query.
- the call, in `executor._dispatch`, on the `kind == "agent"` branch. That is
  what makes "an agent-kind task is blocked and a subprocess task in the same
  node is not" true by construction rather than by a filter someone has to
  remember.

**Amended in implementation:** an earlier draft of this section said "and
nowhere else", and the shipped code has three call sites, not one. The other two
do not weaken the invariant:

- `executor._walk_node`, immediately before `store.bump_counter` in the fix
  loop. The `_dispatch` check alone fires too late there — the counter is
  bumped and `fix_cycle_started` appended before the fix task is dispatched, so
  a refusal would spend a fix-loop attempt and leave a phantom cycle for an
  agent that never ran, which `_last_measurement` then reads back as "no
  progress". It guards a fix *agent* dispatch, so subprocess tasks are still
  never blocked. The `_dispatch` check stays as the backstop.
- `intake._daily_breached`, for the poller, per A10 below. No task is being
  dispatched there at all — it decides whether to file a work item.

### A3. A breach needs a verdict that survives `_measure_node`

`_dispatch` returns a status string; `_measure_node` folds those into
`("ok"|"failed"|"paused", failed, excs)`; `_walk_node` maps that to
`needs_human`. A budget breach that returns `"failed"` is indistinguishable
from a task that ran and failed, and would be reported as one.

So `_dispatch` returns a new `"budget"` verdict, `_measure_node` gains a
precedence rung — **paused > budget > failed** — and both of `_walk_node`'s
branches, plus the fix-cycle `_dispatch` call site inside the loop, map it to
`needs_human` with a budget reason. Precedence: a pause is a human's
instruction and outranks everything; a budget breach outranks a co-task's
failure because the agent never ran and its "failure" is not evidence of
anything.

### A4. `POST /retry` cannot be the budget card's action, and §3 overstated it

`retry_work_item` (`api.py:849`) raises 409 unless the current node has a
`fix_loop`. A budget breach can land on **any** agent-kind node. §3's "offers
the same clear-and-re-run action" is therefore only available on a fix-loop
node.

Amended: the budget card states the spend, the cap and which cap, and points at
Settings → Policy. It renders the retry control **only** when the current node
has a fix loop — the same condition `WorkItemDetail.strandedInFixLoop` already
computes. On any other node the item is stopped exactly as a
`task failed in node X` stop is stopped today, which is a pre-existing product
limit and not this sub-project's to fix.

### A5. A separate `BudgetCard`, not a case inside `CappedCard`

§3 says `CappedCard.tsx` gains a case. `CappedCard` is a fix-loop artifact end
to end: it builds a cycle trace from `fix_cycle_started` events, measures the
loop's span, and its one action is a steer note that "goes into cycle 1 of the
retry". A budget breach has no cycles, no loop span, and its steer text is not
the point. A `budget` case would be a second component wearing the first one's
name.

`BudgetCard.tsx` instead, reusing the `.attention-card` markup and CSS.
`WorkItemDetail` gains a branch **before** the `cappedOut || strandedInFixLoop`
one, so a budget breach inside a fix loop reads as a budget breach.

### A6. Auto-intake must adopt an existing bead, and `executor.intake` cannot

`executor.intake` calls `beads.intake(title)` unconditionally, which runs
`bd create` and files a **new** bead. Auto-intake starting a bead from
`bd ready` would file a duplicate of it on every pickup.

`executor.intake` gains `bead_id: str | None = None`: given, it is adopted and
`bd create` is not run. Nothing else changes.

Note in passing: manual intake's beads land in `KRAFT_BD_CWD`, a single tracker
for the instance, while an auto-intaken bead lives in its own repo's `.beads`.
The `bead_id` string is stored as-is either way; nothing in Kraft resolves a
bead id back to a workspace, so the two coexist. `beads.complete` runs against
`KRAFT_BD_CWD` and so would fail to close an auto-intaken bead — filed as a
follow-up bead, not fixed here.

### A7. `bd ready` has no adapter

`adapters/beads.py` has `intake`, `complete` and `search`. §4 needs a fourth:
`ready(cwd) -> list[dict]`, a `bd ready --json` passthrough shaped like
`search` — best-effort, returning `[]` on a non-zero exit or unparseable
output, because a poller that raises kills its own task.

Verified against the real CLI: `bd ready --json` prints a JSON array whose
objects carry `id`, `title`, `status`, `priority` (**int, 0–4**), `issue_type`.
So `priority_ceiling: 2` keeps rows with `priority >= 2` — the inversion §4
calls out, in the direction the data actually goes.

### A8. `intake.yaml` must be declared a config file

`templates.CONFIG_FILES` is the set of files in `templates/` that
`load_templates` skips. A new `intake.yaml` that is not in it is read as a
malformed chain template and shows up as degraded instance health. Add it there
and to the packaged `templates/` (which `cli.seed_home` copies into
`$KRAFT_HOME` on first run, and `just install` bundles).

### A9. No Settings screen for auto-intake; there is one for budget

§4 specifies a file and no UI, and this amendment does not invent one:
`intake.yaml` is hand-edited and read at startup, so enabling the poller needs
a restart. Filed as a follow-up bead.

The budget **does** get UI, because §1 requires it to: "the Settings screen says
it in the field's help text". Settings → Policy gains the two fields and the
one-task-overshoot sentence.

### A10. What auto-intake means by "a budget cap is currently breached"

§4 says the poller refuses to start anything while a budget is breached. Only
`daily_usd` can be evaluated before an item exists — `work_item_usd` is
per-item and a candidate has no spend. So: the poller checks the daily cap
only, and the per-item cap does its own work at that item's first dispatch.
