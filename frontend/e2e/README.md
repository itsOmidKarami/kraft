# Playwright end-to-end

Four specs, all against one running orchestrator:

| spec | what it drives |
| --- | --- |
| `chain.spec.ts` | create a `quick-task` item against a sample repo with a failing test, watch it reach `work_item_completed`, open the linked session summary |
| `lifecycle.spec.ts` | the human-in-the-loop controls: gate approve, gate reject → re-plan, pause / steer / resume |
| `regression.spec.ts` | every Settings page (repos, templates, plugins, policy, access), Analytics, the search overlay |
| `search.spec.ts` | the search overlay in detail: filters, document viewer, Escape handling |
| `phone.visual.spec.ts` | sub-project B's phone contract at a 390x844 viewport: the board, the gate, the reject textarea's 16px floor (under it, mobile Safari zooms on focus and never zooms back), and the diff viewer wrapping a **real** diff. Writes screenshots to `frontend/e2e-shots/`. jsdom has no viewport, so the unit tests can only assert class boundaries and stylesheet source order — this is the only place the media queries are real |
| `attachments.visual.spec.ts` | intake from an existing spec/plan: the type-to-search picker, the struck-through chain preview, the `from spec+plan` badge, the "attached at intake" tag. Writes screenshots to `frontend/e2e-shots/` (gitignored) — it asserts little and is meant to be looked at |

`lifecycle.spec.ts` slows the implementation hook through `PUT /registry` so
there is something to pause, and puts it back afterwards — so these run one at
a time (`workers: 1`, already set in `playwright.config.ts`).

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
