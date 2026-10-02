# Playwright end-to-end

Nine specs, all against one running orchestrator. Playwright proves the
UI↔server contract and real-browser layout; component behaviour (Escape
handling, which controls a state offers, keyboard paths) is vitest's. Shared
helpers (`REPO`, `REPO_NAME`, `connectRepo`, `openComposer`, `createItem`,
`publish`, `agentRunning`, `eventCount`) live in `fixtures.ts`.

| spec | what it drives |
| --- | --- |
| `chain.spec.ts` | create a `quick-task` item from the board's composer, watch the header reach DONE, open the implement task's log and its session summary, find the item in the board's Done group |
| `planning.spec.ts` | a `default` item at `spec_approval`: read the spec from the gate, reject it with a note (the spec node re-runs and the gate comes back), approve into `plan_approval`, read the plan |
| `lifecycle.spec.ts` | pause a running agent (`KRAFT_SLOW`), resume it with a steer, see it finish; a deep link to an item loads it |
| `regression.spec.ts` | each area's write path: connect a repo and publish, publish a chain change, a library component's and a harness's links into Chains, publish a policy cap and the intake interval, Access's port, Notifications' "Send a test", Appearance's density and open-in after a reload |
| `search.spec.ts` | Ctrl-K finds an indexed document and opens it; the kind filter narrows documents |
| `board-responsive.spec.ts` | the peek opens without reflowing a row: docked at 1440 and 1100, overlaid at 900 (R7) |
| `attachments.visual.spec.ts` | the composer's spec and plan picker against the real index, the chain it trims, the item that results. Writes screenshots to `frontend/e2e-shots/` |
| `phone.visual.spec.ts` | the phone at 390x844: the board, an item at a gate, its reject composer and a real diff, each with nothing scrolling sideways, every tap target at least 44px and the note at 16px (under it mobile Safari zooms on focus and never zooms back). jsdom has no viewport, so this is the only place the phone's media queries are real. Writes screenshots |
| `addresses.spec.ts` | addresses from before the cutover still open their page: a bookmark from the new UI's old prefix, `/settings/chains`, an item's `#node=` hash; and each phone-only address (`/more`, one harness, profile, channel or schedule), and `/archived`, widened from 390px to 1024px in one tab, lands on a desktop page, never Not found |

What the shipped UI's specs drove that has no page in this UI is not driven
here: the item page's Timeline tab (no V2 equivalent, kickoff §4.4), and the
`/settings/steering` address, which is an alias the router test pins
(`src/ng/shell/aliases.test.tsx`).

Three waits read the API because the page has nothing to show them by: a
rejected gate re-runs its node in under a second and comes back looking the
same (`eventCount`), a paused item takes a steer only once the server says
it is steerable, and a resume in the first seconds after a pause is refused
while the walk unwinds (the spec sends it again).

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
passing means a work item's header reached DONE and the item sits in the
board's Done group.
