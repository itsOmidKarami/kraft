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
