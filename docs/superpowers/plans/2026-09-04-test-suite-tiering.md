# Test suite tiering — Implementation Plan

**Goal:** Cut the fast test tier from 3:07 to ~1:10 by eliminating the per-test
`bd init`, and keep the handful of genuinely-slow tests off the blocking CI
feedback loop.

**Spec:** `docs/superpowers/specs/2026-09-04-test-suite-tiering-design.md`

**Bead:** Kraft-0fr

**Tech:** no new deps. `shutil.copytree`, `atexit`, module-global cache in the
test harness; GitLab CI YAML.

## Global constraints

- No product-code changes. Only `tests/support/harness.py`, `.gitlab-ci.yml`,
  `pyproject.toml`.
- `isolated_bd(tmp_path)` keeps its exact signature and return type
  (`Path` to the tracker repo) — no call-site edits.
- `uv run ruff check .` and `uv run ruff format --check .` clean before commit.
- Full `uv run pytest -m "not e2e"` stays green — no test may lose a marker it
  relies on.

---

## Task 1 — Template-copy in `isolated_bd()`

- [ ] Add a module-level `_bd_template()` helper in `tests/support/harness.py`:
      lazily create one workspace via `tempfile.mkdtemp`, `git init` + config,
      `bd init --prefix TEST`; cache in a module global; register
      `atexit` cleanup (`shutil.rmtree(..., ignore_errors=True)`).
- [ ] Rewrite `isolated_bd(tmp_path)` to `shutil.copytree(_bd_template(),
      tmp_path / "tracker")` and return that path. Drop the per-call
      `git init` / `bd init`.
- [ ] Verify: `time uv run pytest -m "not e2e and not slow"` — expect 155
      passed in < 90s (was 187s).
- [ ] Verify: `uv run pytest tests/test_adapters_beads.py tests/test_executor.py
      tests/test_resume.py -q` green (heaviest bd consumers).
- [ ] `ruff check` + `ruff format --check` clean.
- [ ] Commit.

## Task 2 — CI job split

- [ ] `.gitlab-ci.yml`: rename nothing; change `lint-and-test` script's
      pytest line to `uv run pytest -m "not e2e and not slow"`.
- [ ] Add a `slow-tests` job in the same stage: `needs: []`, reuse the
      `before_script` (extract the git+bd install into a YAML anchor to avoid
      duplication), `script: uv run pytest -m "slow"`, same `after_script`
      cache prune.
- [ ] Verify locally: `uv run pytest -m "slow"` passes;
      `uv run pytest -m "not e2e and not slow"` passes.
- [ ] Commit.

## Task 3 — Marker doc

- [ ] `pyproject.toml`: update the `slow` marker description to state the
      tiering contract (excluded from blocking gate, runs in parallel
      `slow-tests` job).
- [ ] Full run `uv run pytest -m "not e2e"` green.
- [ ] `ruff` clean. Commit.

## Task 4 — MR

- [ ] Push branch, open MR to `main`, set auto-merge, monitor pipeline.
- [ ] On merge: update local `main`, close Kraft-0fr, clean up worktree.
