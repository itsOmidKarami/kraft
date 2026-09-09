---
work_item_ids: [f13dccef78ed45fbbbffbed06d171484]
node_id: human_review
hook_point: on.human_review.requested
kind: review_briefs
title: Bead bookkeeping is one-way — Kraft now closes the sub-beads it opens
---

## What changed

One commit (`ba3a047`), four files of source, 322 lines total.

- **`src/kraft/executor.py`**: new `_extract_beads()` regex-scans a work
  item's description for `Kraft-xxxx` ids at intake time and stores them as
  `implements_beads`. A new `_close_beads(db, row, bd_cwd)` helper replaces
  three near-identical inline try/except blocks (in `run` and twice in
  `resume`) that only ever closed `row['bead_id']`. It now also closes every
  id in `implements_beads`, and — if `bead_id` is still `None` (bd was down
  at intake) — backfills one via a late `beads.intake()` call and persists it
  to the row before closing. Each close/backfill failure is caught and
  logged individually; one bad bead can't block the others or fail
  completion.
- **`src/kraft/db.py` / `store.py`**: new `implements_beads TEXT` column
  (schema v15→v16, migration included), threaded through
  `create_work_item`, and a new `store.set_bead_id()` for the backfill path.
- **`tests/conftest.py`**: the autouse fixture now also monkeypatches `HOME`
  to a tmp path, closing a hole where a test with no app fixture and no
  explicit `bd_cwd` could reach the operator's real `~/.beads` via bd's
  hardcoded global fallback.
- **`tests/test_bead_bookkeeping.py`** (new) + **`tests/test_db.py`**: one
  test per bead in scope — sub-bead extraction/closing, late backfill for a
  bd-was-down item, the `HOME` isolation, and the schema migration.

## Bead-by-bead disposition (per the agreed spec)

- **Kraft-p8q1** — code: sub-beads named in a description are now closed
  alongside the tracking bead.
- **Kraft-dr3n** — code: an item that never got a bead at intake gets one
  backfilled at completion instead of staying bead-less forever.
- **Kraft-t5g** — code: tests no longer touch the operator's real
  `~/.beads`.
- **Kraft-ikze** — **no source change**, by design. The bead itself already
  carries a comment recording the decision: enforcement of "don't `bd
  close`" belongs in the future permission-prompt-tool handler (Kraft-oor),
  not in this work. This is intentional scope-narrowing agreed with the user
  before implementation, not an omission.

## Verification

- `uv run pytest tests/test_bead_bookkeeping.py tests/test_db.py -q` — 32
  passed.
- `just lint` — ruff check and format both clean.
- Did not run the full suite (per standing guidance: it's ~14 min; ran the
  targeted files instead).

## What to look at

- `_extract_beads` matches on a bare regex over the description text
  (`Kraft-[a-z0-9]+`), not a bd dependency edge — this is deliberately the
  "stopgap" convention the bead itself calls out (bullet-form ids), not the
  fuller `bd dep add` design also discussed on Kraft-p8q1. Worth confirming
  that's still the intended scope for this MR rather than the edge-based
  version.
- The late-backfill path in `_close_beads` calls `beads.intake()` again at
  completion time for a bead-less item — this creates a *new* bead rather
  than searching for one that might already exist by title. That matches
  Kraft-dr3n's stated options but is worth a second look if duplicate-bead
  creation is a concern in practice.
- No new bead was filed for the Kraft-oor enforcement follow-up beyond what
  Kraft-ikze's own comment already tracks — nothing further to file here.

## Unsure about

- Nothing outstanding; both spec docs referenced in the work item's
  description live in a different worktree path than this one and weren't
  re-read here, but the prior session's commit message and the beads'
  comment trails corroborate the design as implemented.
