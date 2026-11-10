# Kraft's web UI

The single-page app Kraft serves at `/`: the board, a work item's page, the
review page, the template and settings screens, and a separate layout for
phones. This page is for someone changing it. For the product, see the
[docs site](https://kraft-sdlc.dev/); for the whole repository,
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

### The dashboard demo

`demo/` runs this UI with no Kraft server, on the UI contract's scenario. `just demo` serves it at <http://localhost:5174/demo/>. How it works, the things-to-try badge and its tests are in [`DEMO.md`](DEMO.md).

## Layout of `src/`

```text
main.tsx          entry: fixes an old address, then loads ng/boot
api.ts            the typed REST client (every call under /api)
store.ts          zustand: work items, sessions and events, kept current by ws.ts
ws.ts             the event websocket, with reconnect backoff
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
not on its `ALLOWED` list. A module the phone genuinely should share gets a
line in `ALLOWED` with the reason, in the same change that imports it. The same
file pins that the restart call is made only from the phone's More and Access
screens, each behind its own confirm.

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
| **Demo smoke** (`e2e/demo/`) | the built dashboard demo in a browser: it boots, and each things-to-try Show me lands on its target | A change to `demo/`, or to a control a Show me rings (its name is in `demo/tries.ts`). CI's `ui contract` job runs it after the contract | `just demo-smoke`; see [`DEMO.md`](DEMO.md) |

Playwright proves the contract and vitest proves component behaviour; do not
pin the same thing in both.

## Retaking the screenshots in `.github/assets/`

`just docs-screenshots` retakes the web UI pictures; the table of every picture and where it is shown is in [`SCREENSHOTS.md`](SCREENSHOTS.md#retaking-the-screenshots-in-githubassets).

## Other generated files

- `ng/theme/theme.css`, above.
- `src/kraft/templates/lucide_icons.txt`, the icon names a template's `icon:`
  is linted against, comes from the installed `lucide-react`: after bumping it,
  run `just icons`.
- `../vscode/schemas/` is generated from the Python config models (`just
  schemas`), not from anything here.
