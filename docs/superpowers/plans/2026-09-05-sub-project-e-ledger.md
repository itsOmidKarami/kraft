# Sub-project E — SDD ledger

Bead: `Kraft-8mu.5`. Branch: `sub-e-budget-autonomy`.
Plan: `2026-09-05-sub-project-e-budget-and-autonomy.md`.
Spec: `../specs/2026-09-04-sub-project-e-budget-and-autonomy-design.md`.

Every ruling and every deferred finding lands here **and** as a bead. Nothing
lives only in a conversation.

## Rulings

- **Ruling: the budget is not a third field on `policy.Cap` — the bead's own description says it is, spec §2 says it is not, and the spec wins — cost if wrong: the same limit is written into every `retry_counters` row and has to be reconciled across loops, which is a rewrite of `bump_counter` and of the retry path.**
- **Ruling: no schema migration, everything is derived from `worker_sessions.cost_usd` and `work_items.bead_id` — cost if wrong: none here; the point is that `_MIGRATIONS` gains no key and cannot collide with sub-project G's concurrent 9 → 10 bump, which already cost this epic one painful merge.**
- **Ruling: the check lives on `_dispatch`'s `kind == "agent"` branch, not in `policy.py` as spec §2 words it — `policy.py` is pure and does no I/O — cost if wrong: a filter somewhere upstream that someone has to remember to keep in sync, instead of "subprocess tasks are never blocked" being true by construction.**
- **Ruling: a breach returns a new `"budget"` verdict rather than reusing `"failed"` — cost if wrong: a budget stop is reported to the operator as a task that ran and failed, and the fix-loop counter burns an attempt for an agent that never launched.**
- **Ruling: `BudgetCard.tsx` is a new component, not a case inside `CappedCard` as spec §3 says — `CappedCard` is a fix-loop artifact end to end (cycle trace, loop span, "goes into cycle 1 of the retry") — cost if wrong: one more file, and two attention cards sharing CSS instead of one.**
- **Ruling: the budget card offers retry only on a node with a `fix_loop`, because `POST /work-items/{wid}/retry` 409s on any other node — spec §3 overstated the endpoint's reach — cost if wrong: a button that always 409s, on the one screen whose job is to tell the operator what to do next.**
- **Ruling: auto-intake refuses to start a template with no gate at all (`quick-task` has none) — cost if wrong: this is the blocker; without it a repo configured for `quick-task` has the poller run implementation, verify and merge unattended, which is precisely the §5 line, crossed by configuration rather than by code.**
- **Ruling: auto-intake skips beads whose `issue_type` is `epic` — cost if wrong: a chain filed against a title that describes a quarter of work.**
- **Ruling: auto-intake's "a budget cap is currently breached" means the daily cap only — a candidate has no work item and therefore no per-item spend — cost if wrong: nothing; the per-item cap does its own work at that item's first dispatch.**
- **Ruling: no Settings screen for `intake.yaml`; hand-edited, read at startup, restart to apply — spec §4 specifies a file and no UI — cost if wrong: an operator edits YAML and restarts to turn the poller on. Filed as `Kraft-8mu.5.1`.**
- **Ruling: `put_policy` keeps its existing rebuild-from-body behaviour and `budget` is preserved the same way `findings` is (the UI round-trips the whole `GET` body), rather than changing the endpoint to merge with the on-disk file — cost if wrong: a non-UI client that PUTs a partial policy erases the budget block, which is already true of `findings` and is covered by a mirror test.**
- **Ruling: Task 2's `test_daily_window_excludes_yesterday` ships without a demonstrated RED phase — the reviewer's Important finding was that the implementer's RED command did not match that test's name — no fix round was dispatched — cost if wrong: if that test is tautological, the daily-window rollover is unguarded. The reviewer read the test itself and confirmed it exercises both the windowed and unwindowed branches against a real backdated row, and a RED phase cannot be regenerated for code that now exists without reverting it.**
- **Ruling: Task 3's `_spend` test helper, as the plan wrote it, was a plain `def` returning an un-awaited coroutine, so every over-cap test would have recorded no spend and passed vacuously — the implementer was directed to make it `async def` and await it — cost if wrong: none; this is strictly a correction, and the reviewer independently confirmed all six call sites now await it.**

## Deferred findings

Each also filed as a bead — the bead is the source of truth, this is the index.

| Finding | Bead |
|---|---|
| No Settings screen for auto-intake; `intake.yaml` needs a restart | `Kraft-8mu.5.1` |
| `beads.complete` runs against `KRAFT_BD_CWD`, so an auto-intaken bead (which lives in its own repo's `.beads`) cannot be closed | `Kraft-8mu.5.2` |
| `beads.search` has no `try` around its `subprocess.run` and propagates `FileNotFoundError` where every caller treats it as best-effort | `Kraft-9m4` — **closed, fixed upstream**: `main`'s open-bug wave (`f5f39fe`) landed the same `except OSError, ValueError` while this branch was in flight. Found on rebasing. |

## Plan review

Dispatched before any code, as required. 20 findings, 6 blockers — including
the ungated-template hole above, an invented `conn` fixture in `tests/test_store.py`,
a `Database` lifecycle the stub fixture could not satisfy, and a
machine-timezone-dependent assertion. All 20 are answered in the plan's
"Plan review corrections" section, which overrides the task bodies.

## Task log

Filled in as tasks land.
