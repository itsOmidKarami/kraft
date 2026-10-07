# Kraft's web UI

The single-page app Kraft serves at `/`: the board, a work item's page, the
review page, the template and settings screens, and a separate layout for
phones. This page is for someone changing it. For the product, see the
[docs site](https://itsomidkarami.github.io/kraft/); for the whole repository,
[CONTRIBUTING.md](../CONTRIBUTING.md).

Everything below was written from the code. Where a rule is enforced, the test
that enforces it is named: when this page and a test disagree, the test wins.

## Stack

- React 18, [react-router](https://reactrouter.com) 7 (`BrowserRouter`),
  [zustand](https://zustand.docs.pmnd.rs) 4 for state (`src/store.ts` holds the
  work items, sessions and events; a few areas keep a small store of their own),
  and [Vite](https://vite.dev) 8, in TypeScript.
- Plain CSS, one file per area, no CSS framework. Colour comes from generated
  tokens ([Theme](#theme-tokens)); icons are [lucide-react](https://lucide.dev).
- Tests: [vitest](https://vitest.dev) with jsdom and Testing Library for units,
  [Playwright](https://playwright.dev) for the browser. The Playwright version
  is pinned exactly in `package.json`, and CI's container image
  (`.github/workflows/test.yml`) has to move with it.
- Node 22. `npm ci` installs; from the repo root `just setup` does that and
  the Python side too.

## Running it

From the repo root:

```bash
just dev        # backend on :8766 (state in .dev/, agents faked) + Vite on :5173
just dev-seed   # fill it with work items in every state
just ui         # Vite alone, proxying /api to a backend you started with `just api`
```

Open <http://localhost:5173>. Vite proxies `/api` (including the event
websocket) to the backend, and every request the app makes is under `/api/`:
`src/vite.proxy.test.ts` fails if `api.ts` asks for anything outside it.

`npm run build` is `tsc -b && vite build` and writes `dist/`; that is the
typecheck CI runs, and vitest alone does not do it. `just test-ui` runs `tsc -b`
and then vitest, which is the same typecheck without the bundling.
The backend serves `dist/` from `KRAFT_FRONTEND_DIST`, and `just install`
bundles it into the installed package.

Two pages exist only for building the UI: `/_gallery` (the chain graph
components) and `/_tokens` (every colour token, in the current look). The dev
server has them; a release build does not, because `ng/App.tsx` drops their
imports unless `VITE_DEV_PAGES=1` is set.

## Layout of `src/`

```text
main.tsx          entry: fixes an old address, then loads ng/boot
api.ts            the typed REST client (every call under /api)
store.ts          zustand: work items, sessions and events, kept current by ws.ts
ws.ts             the event websocket, with reconnect backoff
deriveState.ts    leftovers of the previous UI's client-side state derivation;
statusGroups.ts   ng/ must not import either (its board and item contract tests)
types/            the API's payload shapes, written by hand to match the server
ng/               the UI itself; everything below is in here
testFixtures.ts   older shared test factories (see Testing)
```

The board's Needs you / Running / Not started / Done grouping is
`STATUS_GROUPS` in `ng/board/model.ts`. Both layouts read a work item's state
from the server's `display_status` and `stop` rather than deriving it.

`ng` is the name this UI went by while it was served under `/ng` next to its
predecessor. The predecessor is gone and the folder name stayed; treat `ng/`
as "the app".

Inside `ng/`, one folder per area, each owning its components, its CSS, its
pure model code and its tests:

| Folder | What it is |
|---|---|
| `shell/` | the frame: sidebar, header and crumbs, search overlay (⌘K), sign-in, first-run. `routes.ts` is the one list of pages that the sidebar, the crumbs, the search's "Go to" rows and the route tree all read |
| `board/` | the board, its side panel (peek), the composer, the archive, the new-item page (`/work-items/new`) |
| `item/` | a work item's page: header, state card, the chain and node panes, logs, documents |
| `graph/` | the chain and node graph components, shared by `item/` and `templates/`, and the dev gallery |
| `review/` | the review page: diff, file tree, threads, finishing a review |
| `templates/`, `library/`, `harnesses/` | Chains and Library (Templates), Repos and Harnesses (Settings): the screens that edit `config/` through the drafts API |
| `settings/` | Policy, Auto-intake, Notifications, Access, Appearance, About |
| `analytics/` | the Analytics page |
| `apply/` | the chip that offers a pending restart or reload once config is published |
| `ui/` | shared primitives: `Button`, `Dialog`, `Menu`, `Popover`, `Tabs`, `Toast`, and so on |
| `theme/` | the token file, the look (`applyTheme.ts`) and the token sheet |
| `phone/` | the phone layout: a separate app, below |

At the top of `ng/`, `boot.tsx` paints the cached look, probes the session
(`GET /health`, then the theme fetch), loads the store and opens the event
socket (`session.ts`) before first render. `http.ts` is the request helper that
keeps the status and body of a refusal, for pages that act on a 409 or 422;
`live.ts` fans live websocket frames out to pages that subscribe to one type.

A new desktop page adds a row to `ng/shell/routes.ts` with `built: true` and an
element to `BUILT` in `ng/App.tsx`; without them it renders a placeholder.
Routes with a parameter (a work item, its review) are declared in `App.tsx`
itself, and the phone declares its own in `phone/PhoneApp.tsx`.

## Phone and desktop

Phone is a different app, not a narrow desktop. `ng/App.tsx` calls
`usePhone()` (`ng/phone/usePhone.ts`), which follows `(max-width: 767px)` live,
and renders `PhoneApp` instead of the desktop route tree. Crossing 767px in
either direction swaps the shape and keeps the URL.

The two share data hooks, pure models and `ui/` primitives, never a pane. That
is enforced by `ng/phone/contract.test.ts`: it reads every non-test file under
`ng/phone/` and fails on a relative import that leaves `phone/` for anything
not on its `ALLOWED` list, or that reaches `deriveState`. A
module the phone genuinely should share gets a line in `ALLOWED` with the
reason, in the same change that imports it. The same file pins that the restart call is made only from the phone's More and
Access screens, each behind its own confirm.

Two more rules keep the phone's CSS apart (`ng/phone/css.test.ts`):

- every class a phone stylesheet styles starts with `ph-`, and no stylesheet
  outside `phone/` may name a `ph-` class;
- phone CSS never cuts text with `text-overflow`: identifiers wrap.

`phone/screens.ts` lists every phone screen and the taps that reach it from
the board, as data that a vitest walk (`phone/reach.test.tsx`) and the UI contract
(`e2e/contract/phone.spec.ts`) both read, so a new screen is added there. Phone tests stub `matchMedia`
themselves (see `phone/App.phone.test.tsx`).

## Theme tokens

Every colour in the app is a CSS custom property from
`ng/theme/theme.css`, and **that file is generated**. Do not edit it:

```bash
uv run python dev/gen_theme.py     # rewrites ng/theme/theme.css; commit the result
```

The generator computes, for each surface (`graphite`, `slate`, `ink`, `sand`,
`moss`) × mode (`dark`, `light`) × colour amount (`mono`, `subtle`, `full`),
the roles `--bg`, `--side`, `--surface`, `--surface-2`, `--line`, `--border`,
`--text`, `--text-sub`, `--text-muted`, `--text-faint`, `--stroke`, `--focus`,
`--selection`, the status colours `--ok --warn --bad --info`, and the diff and
code-scheme colours; and `--accent` for each accent (`none`, `blue`, `violet`,
`green`, `amber`, `rose`). `ng/theme/applyTheme.ts` writes the chosen look onto
`<html>` as `data-surface`, `data-accent`, `data-amount` and `data-mode` (plus
`data-code` for a named code scheme), which is what the generated selectors key
on. The look comes
from `theme.yaml` (the Appearance screen edits it) and is cached in
`localStorage` so the first paint is not another look.

To change a colour, change the generator, rerun it, and look at `/_tokens` in a
few looks. Two tests hold the result to contrast floors (4.5:1 for text, 3:1
for faint text, focus rings, strokes) in every combination:
`tests/test_theme_contrast.py` (which also checks the checked-in file is what
the generator writes) and `ng/theme/theme.contrast.test.ts`.

In your own CSS, use the tokens: `color: var(--text-sub)`. `ng/css.contract.test.ts`
fails on any colour literal outside `theme.css`: a hex value, `rgb()`/`hsl()`/
`oklch()` and the other colour functions, or a named colour (`white`, `red`,
`canvas`, ...). It reads `.css` files; use tokens in inline styles too, or
they escape the contrast tests.

## Breakpoints

One ladder, in `@media` queries only, enforced by the same test: `(max-width:
767px)`, `(max-width: 1023px)`, `(max-width: 1279px)` and `(max-height: 719px)`.
Any other query fails.

| Query | What changes |
|---|---|
| ≤ 767px | the phone app replaces the desktop one (`usePhone`) |
| ≤ 1023px | panes that dock beside a canvas overlay it instead (`useOverlay`); the board row drops columns |
| ≤ 1279px | the item header gives its draft chip up for the count on the Review button, and shortens its badge (`item/draft/draft.css`); the sidebar has no rule here, it is pinned at every width until a stored choice says otherwise (`shell/sidebarPref.ts`) |
| height ≤ 719px | the item page's facts card scrolls at 64px instead of growing |

## CSS rules that tests enforce

- **One owner per class.** The bundle's CSS is global, so a class may be styled
  at the head of a selector from only one top-level folder of `ng/`; `ui/` and
  `theme/` classes are shared (anyone may use them, only they style them).
  `ng/css.collision.test.ts` lists collisions. Each area prefixes its classes
  (`rv-` review, `lib-` library, `ph-` phone, ...) so this rarely bites.
- **`data-pan`** marks a pannable canvas and may appear only under `ng/graph/`.
- **`<main>`** is rendered only by the shell, the sign-in cards, the gallery and
  the phone frame; a page inside the shell must not render its own.
- **`data-allow-ellipsis`** marks a deliberate one-line cut whose whole text is in
  `title`. Nothing checks it today.

## Testing

Run `just test-ui` (typecheck, then vitest) before every push. A test lives
beside its source as `Name.test.ts(x)`. `src/test-setup.ts` adds jest-dom
matchers and defaults `matchMedia` to desktop; `vite.config.ts` sets jsdom.
Tests that read source files (the contract tests above) start with
`// @vitest-environment node`.

**Shared setup lives in the area's own testkit**, not in a global file. Each
area that needs one has a helper module beside its tests, named `testkit.tsx`,
`fixture.ts` or `testSupport.tsx` (never `*.test.*`, so vitest does not run it):

| Module | Provides |
|---|---|
| `ng/item/testkit.tsx` | `detail()` (a work item), `V1` (a chain), `stubFetch()` (answers `"METHOD /path"` from a map, records every call with its `query`, and refuses a write it has no answer for; `acceptWrites()` answers the ones a test expects), `inShell()` |
| `ng/settings/testkit.tsx` | `WithHeader`, standing in for the shell's header |
| `ng/library/fixture.ts`, `testSupport.tsx` | a library draft and the page mounted on it |
| `ng/harnesses/testkit.tsx` | the harnesses draft's tasks, problems and a fake server |
| `ng/phone/areas/testkit.tsx` | draft views and `mountAt()` for the phone's area screens |
| `ng/settings/policy/fixture.ts`, `ng/settings/intake/fixture.ts`, `ng/templates/repos/fixture.ts`, `ng/templates/draft/fixture.default.ts`, `ng/item/draft/fixtures.ts` and `testkit.tsx` | per-screen fixtures |

Reach for the nearest testkit first, and add a factory there the second time a
setup recurs. `src/testFixtures.ts` is the older shared set (`item()`,
`session()`, `NODES`) that a few tests still import; do not grow it, and do not
create a new top-level fixtures file. The general rules (the mutation check that
proves a test pins something, one behaviour pinned once, declaring a removed
test in the PR) are in [`docs/testing.md`](../docs/testing.md).

### Four layers, and when each is required

| Layer | What it is | Required when | Run |
|---|---|---|---|
| **vitest** | component and pure-logic tests in jsdom, with `fetch` stubbed | **Every change.** New behaviour gets a test beside it. CI's `frontend` job runs `npm run build` and `npm test` | `just test-ui` |
| **Playwright** (`e2e/`) | a real browser against a real orchestrator with the fake agent: the UI↔server contract, addresses, and layout jsdom cannot show (media queries, tap-target size, sideways overflow) | A change to what the page sends or reads, to an address, or to phone layout. CI's `playwright` job runs it on every code pull request | `just e2e-ci`; see [`e2e/README.md`](e2e/README.md) |
| **UI contract** (`e2e/contract/`) | the built SPA in a browser on a **mocked** `/api`: what a screen does (the sidebar's pin and reveal, Esc and focus order, the item header's cards, review, the document viewer, tooltips) and that every icon-only control has a name and a tooltip | A change to how a screen behaves, or a new icon-only control. CI's `ui contract` job runs it on every code pull request | `just ui-contract`; see [`e2e/contract/README.md`](e2e/contract/README.md) |

Playwright proves the contract and vitest proves component behaviour; do not
pin the same thing in both.

## Retaking the screenshots in `.github/assets/`

Ten images are shown, in eight places: the root README uses `board`, `gate`,
`mobile` and `analytics`; the docs home uses `board`, `gate`, `search`,
`analytics` and `mobile`; the first-work-item page `board` and `gate`; the web
UI reference (`docsite/content/5.reference/00.web-ui/`) `board`, `item`,
`review`, `analytics`, `chains` and `policy`; the phone guide
(`docsite/content/3.guides/1.day-to-day/8.kraft-on-a-phone.md`) `mobile`; and
the setup wizard guide (`docsite/content/3.guides/1.day-to-day/2.first-run.md`)
`setup`
(`docsite/public/assets` is a link to this folder).
Retake the ones whose screen changed, and all of them when a release is cut:

| File | Size | Shows |
|---|---|---|
| `board.png` | 1440×700 | the board, grouped by status |
| `gate.png` | 1440×700 | a work item at `spec_approval`: the review page with the gate's spec rendered |
| `search.png` | 1440×700 | Ctrl/⌘K with `caching` typed: an item and two documents, over the board |
| `item.png` | 1440×700 | the same item's page, with the `spec_approval` gate selected: the chain as a graph, and the inspector with the gate's files, open thread and Approve and Reject… |
| `review.png` | 1440×700 | the review page of that item on `calc.py`, with a comment being written on lines −2 to +2 (the removed line and its replacement), Must fix selected |
| `analytics.png` | 1440×660 | the Analytics page |
| `chains.png` | 1440×700 | Templates › Chains on the `default` chain, with the `spec_approval` gate selected and its settings in the inspector |
| `policy.png` | 1440×700 | Settings › Policy on its Limits section |
| `setup.png` | 1440×700 | the setup wizard's first step, on a home with no repo connected |
| `mobile.png` | 390×844 | the board in the phone layout |

All are the default look (graphite, dark, no accent), of a repo named `repo`
and the items `just dev-seed` files, plus more filed in step 3. `setup` is the
exception: it is of a second, empty home. `item` and `review` are of the same item as
`gate`, the one at `spec_approval`; its worktree's `calc.py` is `a - b` changed
to `a + b` by the fake agent, which is the diff `review` shows. To retake them:

1. Start a clean, seeded instance:

   ```bash
   (cd frontend && npm run build)
   just dev-reset && just api       # in one terminal: backend on :8766, serving frontend/dist
   just dev-seed                    # in another: four items, one at a gate
   ```

   The seed's two failing and slow items carry `KRAFT_FAIL` and `KRAFT_SLOW`
   in their titles, which a screenshot should not show. Abandon them
   (`kraft item abandon ID --yes`) and file the same work as clean titles with
   the marker in the description, which the fake agent reads too (below).

2. Give search something to find. It indexes only what git tracks under
   `.engineering/`, and the folder names the kind shown beside a hit
   (`specs/`, `plans/`). Write a spec to `.dev/repo/.engineering/specs/` and a
   plan to `.dev/repo/.engineering/plans/`, each a Markdown file with a
   `title:` in its front matter, commit them in the throwaway repo and reindex:

   ```bash
   git -C .dev/repo add .engineering && git -C .dev/repo commit -m docs
   KRAFT_HOME=$PWD/.dev KRAFT_PORT=8766 uv run kraft admin reindex --repo .dev/repo
   ```

3. Give the gate something to show. The seeded item's spec is the fake agent's:
   its front matter says `title: fake spec` (that becomes the heading) and its
   body `fake spec body`. Replace both with something real in
   `.dev/run/worktrees/<id>/.engineering/specs/<id>.md` and commit the change
   with `git -C .dev/run/worktrees/<id> commit -am "spec"`; `-a` rather than
   `add -A`, which would also stage the agent's `.engineering/sessions/` file.
   The item's id is in its address on the board. For `item`, which lists an
   open thread, add one with `kraft item comment <id> --body "why is this here?"`
   (a comment on no file is a thread on the whole change), against the same dev
   home: `KRAFT_HOME=$PWD/.dev KRAFT_PORT=8766 uv run kraft item comment ...`.
   The comment is a draft until a review is sent, which is how `item` and
   `review` show it ("1 open thread", "pending").

   File a few more, each as `KRAFT_HOME=$PWD/.dev KRAFT_PORT=8766 uv run kraft
   item create "title" --repo .dev/repo ...`: two with `--chain quick-task` and
   no `--autostart` (Not started), one more with `--autostart` on the default
   chain (a second gate), and a few with `--chain quick-task --autostart` that
   finish (Done). For Running, file two with `--chain quick-task --autostart
   --description KRAFT_SLOW`, which run for 15 seconds, and take `board` and
   `mobile` in that window (`ONLY=board,mobile`, below). `Rewrite the parser`
   with `--description KRAFT_FAIL` is the failed one; `Investigate the flaky
   import test` with `KRAFT_SLOW`, then `kraft item pause ID` while it runs, is
   the paused one. The Done group sorts the newest first, and the frame cuts
   it after one row: finish a clean item last, so an abandoned one is not the
   row that shows.
4. Save this as `frontend/e2e-shots/shot.mjs` (that folder is gitignored) and,
   with `npx playwright install chromium` done once, run it from `frontend/`
   as `GATED_ID=<id> node e2e-shots/shot.mjs`. `ONLY=board,mobile` takes just
   those; `setup` is taken on its own (`ONLY=setup`), against a second home
   with no repo, started from a folder that is not a checkout (the wizard
   offers the checkout it was started in as a path) and with its own copy of
   `config/`, as `just api` makes for `.dev`:

   ```js
   import { chromium } from "@playwright/test";

   const base = "http://127.0.0.1:8766";
   const out = process.env.OUT ?? "../.github/assets";
   const only = process.env.ONLY?.split(",");
   const browser = await chromium.launch();
   const desktop = { colorScheme: "dark", viewport: { width: 1440, height: 700 } };
   const phone = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true };

   async function shoot(name, context, go) {
     if (only && !only.includes(name)) return;
     const page = await (await browser.newContext(context)).newPage();
     await page.goto(base + "/");
     await go(page);
     await page.waitForLoadState("networkidle");
     await page.screenshot({ path: `${out}/${name}.png` }); // the viewport, not the full page
   }

   await shoot("board", desktop, (p) => p.waitForSelector(".board-row"));
   await shoot("analytics", { ...desktop, viewport: { width: 1440, height: 660 } }, (p) => p.goto(base + "/analytics"));
   await shoot("search", desktop, async (p) => {
     await p.waitForSelector(".board-row");
     await p.keyboard.press("Control+k"); // Meta+k on a Mac
     await p.keyboard.type("caching");
     await p.waitForTimeout(1000); // the results are debounced
   });
   await shoot("gate", desktop, (p) => p.goto(`${base}/work-items/${process.env.GATED_ID}/review?doc=1`));
   await shoot("item", desktop, (p) => p.goto(`${base}/work-items/${process.env.GATED_ID}?sel=spec_approval`));
   await shoot("review", desktop, async (p) => {
     await p.goto(`${base}/work-items/${process.env.GATED_ID}/review?file=calc.py`);
     await p.locator(".review-page").waitFor();
     const f = p.locator('[data-file="calc.py"]');
     const pick = (side, n) => f.getByRole("button", { name: `Pick ${side} line ${n}`, exact: true }).first();
     await pick("old", 2).click(); // the removed line ...
     await pick("new", 2).click({ modifiers: ["Shift"] }); // ... to the line that replaced it
     await pick("new", 2).hover();
     await p.locator(".rv-plus:visible").first().click(); // opens the composer: "Comment on lines -2 to +2"
     await p.getByRole("radio", { name: "Must fix", exact: true }).click();
     await p.getByRole("textbox", { name: "Comment" }).fill("Good catch. Add a test that pins it: add(2, 3) == 5.");
   });
   await shoot("chains", desktop, async (p) => {
     await p.goto(`${base}/templates/chains/default`);
     await p.getByText("spec_approval", { exact: true }).first().click(); // the gate's own settings in the inspector
   });
   await shoot("policy", desktop, (p) => p.goto(`${base}/settings/policy/limits`));
   await shoot("setup", desktop, (p) => p.getByText("Nothing on the board yet").waitFor()); // a home with no repo: ONLY=setup
   await shoot("mobile", phone, () => {});
   await browser.close();
   ```

5. Look at each PNG before committing it: that nothing in the frame is a path
   or a name of yours, and that its size is the table's.

   The `review` steps are the contract suite's range pick
   (`e2e/contract/review.spec.ts`) pointed at `calc.py`; check the composer
   header reads "Lines −2 to +2" before you save the frame. `?file=calc.py`
   shows that file alone; if your frame shows two columns, change the diff
   settings (Inline) before the shot.

The footer under the sidebar prints the instance's version. A checkout prints a
dev version (`2.0.0rc9.dev1+g…` on a tagged clone, `0.1.devN` in one without
tags; `uv run kraft --version` shows it), while the committed set was taken
from installed 1.5 release candidates (their footers read `v1.5.0rc10`, and
also showed the instance's address, which the footer no longer does). For the set that goes out
with a release, run that release's `kraft` against the seeded dev home rather
than the checkout; `dev_env` in the root `justfile` lists the variables a dev
instance sets, and `fixtures/bin` has to be first on `PATH` so the agents stay
fake. A pull request that changes only these PNGs counts as docs-only to CI.
The README and docs site link to them by file name, so keep the names.

## Other generated files

- `ng/theme/theme.css`, above.
- `src/kraft/templates/lucide_icons.txt`, the icon names a template's `icon:`
  is linted against, comes from the installed `lucide-react`: after bumping it,
  run `just icons`.
- `../vscode/schemas/` is generated from the Python config models (`just
  schemas`), not from anything here.
