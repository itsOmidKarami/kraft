# Playwright end-to-end

Eight specs, all against one running orchestrator. Playwright proves the
UI↔server contract and real-browser layout; component behaviour (Escape
handling, active nav tabs, which controls a phone hides) is vitest's. Shared
helpers — `REPO`, `REPO_NAME`, `connectRepo`, `createItem` — live in
`fixtures.ts`.

| spec | what it drives |
| --- | --- |
| `chain.spec.ts` | create a `quick-task` item against a sample repo with a failing test, watch it reach `work_item_completed`, open the linked session summary |
| `planning.spec.ts` | a `default`-chain item reaching `spec_approval`, "Review spec" → Documents tab, rejecting the gate (re-runs the spec node, returns to the same gate), approving into `plan_approval`, and "Review plan" |
| `lifecycle.spec.ts` | pause / steer / resume a running agent, and a deep link answered by the SPA fallback |
| `regression.spec.ts` | every Settings page's write path (repos, chains, policy, steering, access, notify, appearance) |
| `search.spec.ts` | Ctrl-K → a real index hit → the document viewer; the advanced kind filter |
| `board-responsive.spec.ts` | the peek overlays the board without moving a row |
| `phone.visual.spec.ts` | the phone contract at 390x844: no sideways scroll, 44px touch targets, the reject textarea's 16px floor (under it, mobile Safari zooms on focus and never zooms back), a **real** diff wrapping. Writes screenshots to `frontend/e2e-shots/`. jsdom has no viewport, so this is the only place the media queries are real |
| `attachments.visual.spec.ts` | intake from an existing spec/plan: the type-to-search picker, the `from spec+plan` badge, the "attached at intake" tag. Also writes screenshots |

`lifecycle.spec.ts` slows its agent with `KRAFT_SLOW` in the title so there is
something to pause. One server serves every spec, so tests run one at a time
(`workers: 1`, set in `playwright.config.ts`).

CI runs this suite as the `playwright` job in `.github/workflows/test.yml`. To
run it locally in one command, use `just e2e-ci` from the repo root: it builds
the SPA, starts `serve.py`, runs the suite, and tears the server down. The
steps below do the same by hand.

## Prerequisites

- `chromium` installed for Playwright: `cd frontend && npx playwright install chromium`
- Python deps synced: `uv sync` (from the repo root)

## Run it

All commands from the **repo root** unless noted.

### 1. Build the SPA

```bash
cd frontend && npm run build   # produces frontend/dist/
```

### 2. Start the orchestrator

`frontend/e2e/serve.py` boots `python -m kraft` against hermetic fixtures
(`tests/support/harness.py`): a fresh sample git repo, an isolated `bd`
tracker, and the fake agent (`fixtures/fake-claude.sh`, `KRAFT_FAKE_CLAUDE=fix`,
which flips `calc.py` `a - b` → `a + b` so pytest passes, and also honours the
`artifact:` contract on the two planning hooks). No `claude` binary needed.

```bash
uv run python frontend/e2e/serve.py
```

Env it sets for the child `python -m kraft`:

| var | value |
| --- | --- |
| `KRAFT_PORT` | ephemeral by default; pin one explicitly via `KRAFT_PORT` |
| `KRAFT_RUN_DIR` | `<tmp>/run` |
| `KRAFT_TEMPLATES_DIR` | fake templates dir (the V1 library, its chains, harness profiles and policy) |
| `KRAFT_BD_CWD` | isolated `bd` tracker repo |
| `KRAFT_FRONTEND_DIST` | `frontend/dist` |
| `KRAFT_FAKE_CLAUDE` | `fix` |
| `KRAFT_INDEX_REPOS` | the sample repo, seeded with two `.engineering/` documents so search has something to find |
| `KRAFT_HOME` | `<tmp>` — pinned, never inherited: the V1 `fake` harness overlay is written to `$KRAFT_HOME/templates/harnesses`, which is the only place the daemon reads it from |

It polls the port it picked (or the one you pinned) until `/api/health` answers 200, then prints:

```
  server up on http://127.0.0.1:54321  (temp: /tmp/kraft-e2e-XXXX)
  KRAFT_E2E_REPO=/tmp/kraft-e2e-XXXX/sample
  KRAFT_E2E_BASE=http://127.0.0.1:54321
```

Leave it running. Ctrl-C tears it down.

### 3. Run the spec

In a second terminal, with the values printed above:

```bash
cd frontend && KRAFT_E2E_REPO=/tmp/kraft-e2e-XXXX/sample KRAFT_E2E_BASE=http://127.0.0.1:54321 npx playwright test
```

`KRAFT_E2E_BASE` is required. The config throws rather than default to the
installed daemon's `8765`, so a missing fixture server can't point the suite
at your real `~/.kraft`.

`npm run e2e` (or `just e2e`) runs the same `playwright test` command. Add
`KRAFT_E2E_TIMEOUT_SCALE=3` to triple every timeout on a slow machine. Pass a
file to run one spec, for example `npx playwright test e2e/chain.spec.ts`.

To verify: the run ends with every test passed and none failed. `chain.spec.ts`
passing means a work item reached `work_item_completed` in the timeline and
moved to the Done group on the Board.
