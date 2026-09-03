# Playwright end-to-end

One spec (`chain.spec.ts`): open the Board, create a `quick-task` work item
against a sample repo with a failing test, watch the chain run to
`work_item_completed` in the UI, then confirm the Board card shows `completed`.

This is **manual / non-blocking**, mirroring the gated Python e2e
(`pytest -m e2e`, `KRAFT_E2E=1`). It is not part of `npm test` or the blocking
`frontend` CI job.

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
tracker, and the fake agent (`KRAFT_FAKE_AGENT=fix`, which flips `calc.py`
`a - b` → `a + b` so pytest passes). No `claude` binary needed.

```bash
uv run python frontend/e2e/serve.py
```

Env it sets for the child `python -m kraft`:

| var | value |
| --- | --- |
| `KRAFT_PORT` | `8765` (override via `KRAFT_PORT`) |
| `KRAFT_RUN_DIR` | `<tmp>/run` |
| `KRAFT_TEMPLATES_DIR` | fake templates dir (quick-task + default + registry + policy) |
| `KRAFT_BD_CWD` | isolated `bd` tracker repo |
| `KRAFT_FRONTEND_DIST` | `frontend/dist` |
| `KRAFT_FAKE_AGENT` | `fix` |

It polls `http://127.0.0.1:8765/health` until `200`, then prints:

```
  server up on http://127.0.0.1:8765  (temp: /tmp/kraft-e2e-XXXX)
  KRAFT_E2E_REPO=/tmp/kraft-e2e-XXXX/sample
```

Leave it running. Ctrl-C tears it down.

### 3. Run the spec

In a second terminal, with the repo path printed above:

```bash
cd frontend && KRAFT_E2E_REPO=/tmp/kraft-e2e-XXXX/sample npx playwright test
```

Optional: `KRAFT_E2E_BASE=http://127.0.0.1:<port>` if you changed `KRAFT_PORT`.

Expected: 1 passed — the work item reaches `work_item_completed` in the
timeline and the Board card status badge reads `completed`.
