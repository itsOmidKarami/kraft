# Test suite tiering — kill the per-test `bd init`

**Status:** design agreed
**Date:** 2026-09-04
**Bead:** Kraft-0fr

---

## Problem

`uv run pytest -m "not e2e"` takes 4–7 min; the "fast" tier
(`-m "not e2e and not slow"`, 155 tests) still takes **3:07**. Measured with
`--durations=20`: the 20 slowest tests are all 3.5–5.0s and every one of them
calls `tests/support/harness.isolated_bd()`, which runs a real `bd init`
(Dolt spin-up, ~3.6s) into a throwaway repo. ~40 tests do this. That single
call is ~140s of the 188s fast-tier wall clock.

Pure-logic tests (`test_db`, `test_events`, `test_store`, `test_templates`,
`test_paths`, …) already run in <1s combined. The problem is entirely the
`bd init` fan-out.

## Root cause

`isolated_bd()` pays full `bd init` per call. But:

- `kraft.adapters.beads` only ever runs `bd create` and `bd close <id>`.
- Tests only ever run `bd show <id> --json` on ids they just created.
- **No test** runs `bd list`, `bd ready`, `bd count`, or asserts on
  cross-issue state (grep confirmed).

So every test needs *a working bd workspace*, not a *pristine* one. Issue ids
are globally unique within a workspace regardless of prefix, so even a shared
workspace would be correct — but per-test isolation is free if we copy a
pre-initialised template.

## Change

### 1. `isolated_bd()` — init once, copy per test

Build one bd workspace per test-run process, lazily, on first `isolated_bd()`
call; cache it in a module global; `shutil.rmtree` it via `atexit`. Each
`isolated_bd(tmp_path)` call then `copytree`s that template to
`tmp_path/tracker` and returns it.

Measured: template `copytree` = 0.075s; a full create→show→close cycle in a
copied workspace = ~1.15s (down from ~3.6s). Expected fast tier: **3:07 → ~1:10**.

No call-site changes — `isolated_bd(tmp_path)` keeps its signature. ~15-line
diff in `tests/support/harness.py`.

Copied workspace validated at a new path: `bd create`/`show`/`close` all work,
`.git` carried along verbatim is fine (bd doesn't read git history for these
commands; no test asserts tracker git state).

### 2. CI job split

One slow test remains legitimately slow: `test_reattach_adopts_running_agent`
has a hard 40s `KRAFT_FAKE_CLAUDE_DELAY`. `slow`-marked tests
(`test_api_reattach`, `test_api_shutdown`, `test_ws` real-uvicorn,
`test_index_triggers`) stay in the tens-of-seconds range regardless of the
`bd` fix. Keep them out of the blocking feedback loop.

`.gitlab-ci.yml`: split `lint-and-test` into two jobs in the same stage
(run in parallel):

- **`lint-and-test`** (blocking): `uv run pytest -m "not e2e and not slow"`
  plus the ruff steps. ~1–2 min.
- **`slow-tests`** (blocking, parallel, `needs: []`): `uv run pytest -m "slow"`.
  Shares the same `before_script` (git + bd install). Still gates merge, but
  doesn't hold up the fast signal.

`e2e` stays excluded everywhere (unchanged); `frontend` / `frontend-e2e`
unchanged.

### 3. Marker taxonomy (docs only)

`pyproject.toml` already declares `e2e` and `slow`. Update the `slow`
description to name the new tiering contract:

```
"slow: excluded from the blocking PR gate; run in the parallel slow-tests job (subprocess servers, real uvicorn, multi-second sleeps)",
```

No new markers. A `unit`/`integration` split was considered and dropped
(YAGNI) — once `bd init` is gone the fast tier is ~70s, fast enough that a
third tier earns nothing.

## Out of scope

- bd shared-server / `--global` daemon (removes Dolt-per-command cost too, but
  adds daemon lifecycle to the test harness — not worth it at ~1.15s/test).
- Converting `isolated_bd` to a pytest fixture (touches ~30 call sites for no
  behaviour gain over the module-global cache).
- Reducing the 40s fake-claude delay in the reattach test (that delay is the
  thing under test).

## Verification

1. `time uv run pytest -m "not e2e and not slow"` — expect < 90s, 155 passed.
2. `uv run pytest -m "slow"` — passes.
3. `uv run pytest -m "not e2e"` — full suite still green (no test lost a marker
   it needed).
4. CI: both `lint-and-test` and `slow-tests` green on the MR.
