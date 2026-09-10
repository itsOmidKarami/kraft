# Mobile app shell

Date: 2026-09-10

## Problem

`Kraft-8mu.4` ("away from desk") shipped a phone-usable UI, but its own spec
scoped it deliberately narrow: make only what a notification links to work on
a phone — the board, a work item's detail/gate, the diff viewer — and leave
everything else, including the nav itself, desktop-shaped. That shows: the
same full desktop header (`Kraft · Analytics · Settings · ⌘K Search`) renders
on every phone screen, squeezed onto one wrapped line, and Settings/Analytics
are untouched below 640px beyond generic grid collapse.

This supersedes two of that spec's non-goals — "not every screen phone-shaped"
and "the browser is the app, not a native one" — in favor of a real app shell:
a persistent bottom tab bar and an installable PWA. It does not touch the
notification/webhook half of `Kraft-8mu.4`, which stands as shipped.

## Non-goals

- A service worker or offline support. Kraft is a live API against a running
  orchestrator; there is nothing useful to do disconnected, and a cache is a
  new invalidation problem for zero benefit here.
- A mobile YAML/code editor. The two Settings pages that edit raw template
  YAML (Templates, Steering) stay desktop-only, same pattern as the diff
  viewer's editor-launch buttons.
- Any change to desktop layout. Every change below is inside the existing
  `@media (max-width: 640px)` phone block or a new `.phone-only` element;
  desktop's current header and page bodies are untouched.
- Gesture navigation, swipe-between-tabs, or any native-app chrome beyond a
  bottom tab bar and a web manifest. This is a responsive web app that
  installs, not a rewrite in a native shell.

## 1. App shell

`App.tsx` renders one `<Nav>` header for every route today: brand, back-link,
health/conn badges, `Analytics`/`Settings` links, and the `⌘K Search` button,
all in one row that just wraps at phone width (`styles.css:898`,
`.app-nav { flex-wrap: wrap }`).

Split by viewport:

- **Header** (all widths, trimmed on phone): brand, back-link, health/conn
  badges. `Analytics`, `Settings`, and the `Search` button/`⌘K` hint are
  wrapped in `.desktop-only` and disappear on phone — that class already
  exists and is already used this way for `WorkItemDetail`'s steer/retry
  controls.
- **Bottom tab bar** (new `BottomNav.tsx`, `.phone-only`, `position: fixed;
  bottom: 0`): four `NavLink`s — Board, Search, Analytics, Settings — each an
  icon (`@phosphor-icons/react`, already a dependency) plus a label, ≥56px
  tall touch targets, active state via `aria-current` styling. Bottom padding
  includes `env(safe-area-inset-bottom)` for notch/home-indicator phones.
  `main` gets phone-only bottom padding equal to the bar's height so the last
  board row or settings field isn't hidden behind it.

## 2. Search becomes a screen

`SearchOverlay` today only opens as a modal, triggered by the header's
`Search` button or `⌘K` — neither exists on phone once the header is trimmed.
The bottom nav's Search tab routes to `/search`, which mounts `SearchOverlay`
inline as a page instead of a modal-over-board. Same component, same
`api.search` call; the only change is the mount point and that closing it
navigates back to `/` instead of unmounting a modal. Desktop keeps the button
+ `⌘K` + modal exactly as shipped.

## 3. Analytics phone view

`AnalyticsView`'s `.kpis` grid already collapses to 2 columns on phone
(`styles.css:925`). The two tables below it (`by-node`: 7 columns, `by-repo`:
5 columns, both CSS-grid `.node-row`/`.repo-row`, not `<table>`) do not — 7
columns has no readable width at 390px even wrapped.

Below 640px, `.node-row`/`.repo-row` switch from a grid row to a stacked card:
name/primary metric on top, the rest as labeled key:value pairs underneath,
each row becoming its own bordered block instead of a grid line. This is a
CSS-only reflow of the existing markup (`grid-template-columns: 1fr` +
`display: contents` won't carry labels, so each `<span>` needs a `data-label`
or a sibling text node picked up via `::before { content: attr(data-label) }`)
— no new component, no new data shape. If the column semantics don't survive
that reflow legibly for `by-node`'s 7 columns, the fallback is dropping the
two least-essential columns (`rounds`, `avg time`) on phone rather than adding
a second render path.

## 4. Settings phone view

`Settings` renders a `.board-sidebar` of 9 page links (`PAGES`) plus a routed
body. The sidebar already goes horizontal-wrap-row on phone (same rule as the
board's filter sidebar) — functional but cramped for 9 items, and doesn't
match the drill-down pattern the rest of the phone UI uses.

- **Phone nav**: the sidebar becomes a full-width stacked list (reusing
  `.facet-opt` styling, one row per page, chevron affordance) shown instead of
  the routed body when no page is selected; picking one shows that page with
  a back-to-list link. This mirrors the board → detail drill-down already in
  place, just one level added.
- **Light-edit pages** (Repos, Plugins, Intake, Policy, Appearance, Notify,
  Access) — forms, toggles, text fields. These already reflow to 1 column
  under the existing phone block; this design's work here is a pass for touch
  target size and `SaveRow` button placement, not new layout.
- **YAML editor pages** (Templates, Steering) — on phone, the page shows the
  current file's content read-only (already fetched for the desktop editor)
  with a `.desktop-only`-style notice: "Open on desktop to edit." No mobile
  code editor; editing raw YAML on a touch keyboard is out of scope.

## 5. PWA install

`frontend/public/manifest.json`: name, short_name, two icon sizes (192, 512),
`display: "standalone"`, `theme_color`/`background_color` matching the
existing dark palette (`styles.css` `--color-bg`/`--color-accent`).
`index.html` gets `<link rel="manifest">` and a `theme-color` `<meta>`. No
service worker — see Non-goals.

## 6. Testing

Extends `phone.visual.spec.ts`, which already exercises the 390×844 viewport
against a real running orchestrator — no new test infra:

- Bottom nav renders and is tappable from all four tabs, on both the board
  and a work item detail screen.
- The Search tab reaches `/search` and returns a result for a known item.
- Analytics renders `by-node`/`by-repo` as stacked cards with no horizontal
  overflow (`overflowsX`, the helper this spec file already defines).
- A Templates/Steering settings page shows the "open on desktop" notice and
  hides any edit control, same assertion pattern already used for
  `DocumentModal`'s editor-launch buttons in this file.

## Files touched

`App.tsx`, new `BottomNav.tsx`, `Analytics.tsx`, `Settings.tsx`,
`styles.css` (phone block additions), `frontend/public/manifest.json`,
`index.html`, `frontend/e2e/phone.visual.spec.ts`.
