# Mobile App Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Kraft's frontend a real phone app shell — bottom tab bar, Search as its own screen, phone-usable Analytics and Settings, PWA install — without touching desktop layout or behavior.

**Architecture:** All changes are additive inside the frontend SPA (`frontend/src`): a new `BottomNav` component and `Search` view, `embedded`-mode support on the existing `SearchOverlay`, CSS-only reflows of `Analytics`/`Settings` scoped to the project's existing `@media (max-width: 640px)` phone block, and a static PWA manifest. No backend changes, no new npm dependencies — `react-router-dom` and `@phosphor-icons/react` are already installed.

**Tech Stack:** React 18, TypeScript, Vite, `react-router-dom` v6, `@phosphor-icons/react`, Vitest + Testing Library (unit), Playwright (phone e2e).

**Spec:** `docs/superpowers/specs/2026-09-10-mobile-app-shell-design.md`

## Global Constraints

- No service worker, no offline support (spec Non-goals).
- No mobile YAML/code editor — Templates and Steering pages stay read-only + "open on desktop" below 640px (spec Non-goals, §4).
- No new npm dependencies. Reuse `react-router-dom`, `@phosphor-icons/react`, both already in `frontend/package.json`.
- Zero change to desktop rendering or behavior. Every change is either inside the existing `@media (max-width: 640px)` block in `frontend/src/styles.css`, or gated by the existing `.phone-only`/`.desktop-only` classes.
- Touch targets ≥ 44px tall on any phone-only interactive element; the bottom nav's own tabs ≥ 56px (spec §1).
- **Cascade safety:** any new rule that hides/shows an element carrying `.desktop-only`/`.phone-only` must have specificity ≥ the rule(s) it overrides, not just later source position — `.app-nav .nav-link` is a 2-class selector and beats a bare `.desktop-only` (1-class) regardless of order. Verify new overrides the same way `frontend/src/styles.order.test.ts` already pins the diff-modal one, and extend that file rather than trusting position alone.
- Icon names are Phosphor's real exports, verified against `node_modules/@phosphor-icons/react/dist/csr/*.d.ts` in this repo: `SquaresFour`, `MagnifyingGlass`, `ChartBar`, `GearSix`.

---

### Task 1: Bottom tab bar + trimmed phone header

**Files:**
- Create: `frontend/src/components/BottomNav.tsx`
- Create: `frontend/src/components/BottomNav.test.tsx`
- Modify: `frontend/src/App.tsx:1-52` (imports, `Nav`, render)
- Modify: `frontend/src/styles.css` (new `.bottom-nav` rules + phone-block additions)

**Interfaces:**
- Produces: `export function BottomNav()` — a `<nav className="bottom-nav">` with four `NavLink`s to `/`, `/search`, `/analytics`, `/settings`. Task 2 relies on the `/search` route existing (added in Task 2, not here — the tab links to it regardless of whether the route exists yet, so task order 1 then 2 keeps the app working end to end).
- Consumes: nothing new. `react-router-dom`'s `NavLink`, already used the same way in `App.tsx`.

- [ ] **Step 1: Write the failing test for `BottomNav`**

```tsx
// frontend/src/components/BottomNav.test.tsx
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { BottomNav } from "./BottomNav";

describe("BottomNav", () => {
  it("renders one tab per top-level screen, Board matching only the root", () => {
    render(
      <MemoryRouter initialEntries={["/work-items/w1"]}>
        <BottomNav />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "primary" });
    const tabs = ["Board", "Search", "Analytics", "Settings"];
    for (const label of tabs) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    // On a work item's detail route, the Board tab must not read as active —
    // NavLink's default (non-`end`) match would otherwise treat "/" as a
    // prefix of every route and light Board up everywhere.
    expect(within(nav).getByRole("link", { name: "Board" })).not.toHaveClass("active");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/BottomNav.test.tsx`
Expected: FAIL — `Cannot find module './BottomNav'`

- [ ] **Step 3: Write `BottomNav`**

```tsx
// frontend/src/components/BottomNav.tsx
import { NavLink } from "react-router-dom";
import { ChartBar, GearSix, MagnifyingGlass, SquaresFour } from "@phosphor-icons/react";

/**
 * The phone app shell's tab bar (mobile app shell design, §1). Desktop keeps
 * the header's Analytics/Settings links and Search button (`App.tsx`'s `Nav`,
 * now `.desktop-only`); this is the phone equivalent, fixed to the bottom.
 */
const TABS: { to: string; label: string; icon: typeof SquaresFour; end?: boolean }[] = [
  { to: "/", label: "Board", icon: SquaresFour, end: true },
  { to: "/search", label: "Search", icon: MagnifyingGlass },
  { to: "/analytics", label: "Analytics", icon: ChartBar },
  { to: "/settings", label: "Settings", icon: GearSix },
];

export function BottomNav() {
  return (
    <nav className="bottom-nav" aria-label="primary">
      {TABS.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className="bottom-nav-tab">
          <Icon size={22} />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/components/BottomNav.test.tsx`
Expected: PASS

- [ ] **Step 5: Wire `BottomNav` into `App.tsx` and trim the phone header**

In `frontend/src/App.tsx`, add the import next to the other component imports (after the `IntakeModal` import):

```tsx
import { BottomNav } from "./components/BottomNav";
```

Replace the `Nav` function's `Analytics`/`Settings`/`Search` controls (lines 33–43 of the current file) so they carry `desktop-only`:

```tsx
      <NavLink to="/analytics" className="nav-link desktop-only">
        Analytics
      </NavLink>
      <NavLink to="/settings" className="nav-link desktop-only">
        Settings
      </NavLink>
      <button className="btn btn-secondary desktop-only" onClick={onSearch}>
        <MagnifyingGlass size={14} />
        Search
        <span className="kbd">⌘K</span>
      </button>
```

Render `BottomNav` once, as a sibling of `<main>` inside the router (after the closing `</main>` tag, before the `{search && ...}` overlay block):

```tsx
      <main>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
          <Route path="/analytics" element={<AnalyticsView />} />
          <Route path="/settings/*" element={<Settings />} />
        </Routes>
      </main>
      <BottomNav />
      {search && <SearchOverlay onClose={() => setSearch(false)} />}
```

- [ ] **Step 6: Add the CSS — base rules and the cascade-safe phone overrides**

Add near the `.app-nav` block in `frontend/src/styles.css` (after the existing header rules, before the `/* ══ board (design 2a) ══` comment at line 219):

```css
/* ══ bottom nav (mobile app shell) ═══════════════════════════════════════ */
.bottom-nav {
  display: none; /* the phone block below turns this on */
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 30;
  justify-content: space-around;
  background: var(--color-bg); border-top: 1px solid var(--color-divider);
  padding-bottom: env(safe-area-inset-bottom);
}
.bottom-nav-tab {
  flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px;
  min-height: 56px; padding: 8px 4px;
  font-size: 10px; text-decoration: none; color: var(--color-neutral-500);
}
.bottom-nav-tab.active { color: var(--color-accent); }
```

Add to the existing `@media (max-width: 640px)` phone block (the one starting at `/* ══ phone (design 1n) ══...` — append these lines just before its closing `}`):

```css
  .bottom-nav { display: flex; }
  main { padding-bottom: calc(56px + env(safe-area-inset-bottom)); }
  /* `.app-nav .nav-link` / `.app-nav .btn` are 2-class selectors
     (specificity 0,2,0) and beat a bare `.desktop-only` (0,1,0) regardless of
     source order — these re-qualify with `.desktop-only` so the header's
     Analytics/Settings/Search controls actually disappear on phone. */
  .app-nav .nav-link.desktop-only,
  .app-nav .btn.desktop-only { display: none; }
```

- [ ] **Step 7: Pin the cascade fix with a regression test**

In `frontend/src/styles.order.test.ts`, add a new `describe` block (same file, same pattern as the existing specificity test):

```ts
describe("styles.css specificity — phone header", () => {
  it("re-qualifies .desktop-only so it outranks .app-nav .nav-link / .app-nav .btn", () => {
    const css = readFileSync(join(here, "styles.css"), "utf-8");
    expect(css).toMatch(/\.app-nav \.nav-link\.desktop-only,\s*\n\s*\.app-nav \.btn\.desktop-only \{ display: none; \}/);
  });
});
```

- [ ] **Step 8: Run the full frontend unit suite**

Run: `cd frontend && npx vitest run`
Expected: PASS (existing `App.test.tsx` tests — "opens the search overlay from the header button" etc. — are unaffected: the button is still in the DOM, jsdom does not evaluate `@media`, `getByRole` still finds it)

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/BottomNav.tsx frontend/src/components/BottomNav.test.tsx \
        frontend/src/App.tsx frontend/src/styles.css frontend/src/styles.order.test.ts
git commit -m "feat(mobile): add phone bottom tab bar, trim phone header"
```

---

### Task 2: Search as a phone screen

**Files:**
- Create: `frontend/src/views/Search.tsx`
- Create: `frontend/src/views/Search.test.tsx`
- Modify: `frontend/src/components/SearchOverlay.tsx` (add `embedded` mode)
- Modify: `frontend/src/components/SearchOverlay.test.tsx` (cover the new mode)
- Modify: `frontend/src/App.tsx` (add the `/search` route)
- Modify: `frontend/src/styles.css` (`.search-page` layout)

**Interfaces:**
- Consumes: `BottomNav`'s `/search` tab from Task 1 (already links there; this task makes the link resolve to something).
- Produces: `SearchOverlay` gains an optional `embedded?: boolean` prop (default `false` — existing callers, `App.tsx`'s `⌘K`/button-triggered overlay, are unaffected). `export function SearchView()` in `views/Search.tsx`, mounted at `/search`.

- [ ] **Step 1: Write the failing test for embedded mode**

```tsx
// frontend/src/components/SearchOverlay.test.tsx — add this case to the
// existing describe block (the file already mocks `api.search` etc. in a
// shared `beforeEach`; reuse that setup, do not duplicate it)
it("renders as a plain page with no backdrop or esc control when embedded", () => {
  render(<SearchOverlay embedded onClose={() => {}} />);
  expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
  expect(screen.getByRole("searchbox")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "esc" })).toBeNull();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/SearchOverlay.test.tsx -t embedded`
Expected: FAIL — `embedded` is not a known prop / the dialog role is still present (TypeScript will also flag the unknown prop if run through `tsc`, but `vitest run` alone will show the role assertions failing)

- [ ] **Step 3: Add `embedded` mode to `SearchOverlay`**

In `frontend/src/components/SearchOverlay.tsx`, change the signature:

```tsx
export function SearchOverlay({
  onClose,
  embedded = false,
}: {
  onClose: () => void;
  embedded?: boolean;
}) {
```

Replace the final `return` block (currently `<div className="dialog-backdrop" ...><div className="dialog search-overlay elev-lg">...</div>{openDoc && <DocumentModal .../>}</div>`) with:

```tsx
  const content = (
    <div className={embedded ? "search-page" : "dialog search-overlay elev-lg"}>
      <div className="search-bar">
        <MagnifyingGlass size={16} className="search-icon" />
        <input
          ref={inputRef}
          type="search"
          aria-label="search"
          placeholder="Search…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="seg search-mode">
          {MODES.map((m) => (
            <label key={m} className="seg-opt">
              <input
                type="radio"
                name="search-mode"
                checked={mode === m}
                onChange={() => setMode(m)}
              />
              {m}
            </label>
          ))}
        </span>
        {!embedded && (
          <button type="button" className="btn btn-ghost search-esc" onClick={onClose}>
            esc
          </button>
        )}
      </div>

      <div className="search-facets">
        <span className="tag tag-neutral">source: {sourceKind || "any"}</span>
        <span className="tag tag-neutral">kind: {kind || "any"}</span>
        <span className="tag tag-neutral">repo: {repo || "any"}</span>
        <button
          type="button"
          className="btn btn-ghost search-advanced"
          aria-expanded={advanced}
          onClick={() => setAdvanced((v) => !v)}
        >
          advanced
        </button>
        <span className="search-count">
          {results.length} {results.length === 1 ? "result" : "results"} · lagging index, not
          live state
        </span>
      </div>

      {advanced && (
        <div className="search-filters">
          <label>
            source_kind
            <select
              aria-label="source_kind"
              value={sourceKind}
              onChange={(e) => setSourceKind(e.target.value)}
            >
              <option value="">any</option>
              <option value="artifact">artifact</option>
              <option value="session_summary">session_summary</option>
            </select>
          </label>
          <label>
            kind
            <input
              className="input"
              aria-label="kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            />
          </label>
          <label>
            repo
            <select aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
              <option value="">any repo</option>
              {repos.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {body}

      <div className="bead-strip">
        <CirclesThree size={15} className="bead-icon" />
        <span className="bead-label">Beads · live via hub</span>
        {beads.length === 0 ? (
          <span className="bead-hit">no bead matches</span>
        ) : (
          beads.slice(0, 2).map((b) => (
            <span key={b.id} className="bead-hit">
              <code>{b.id}</code> {b.title}
              {b.status === "closed" && <span className="tag tag-neutral">closed</span>}
            </span>
          ))
        )}
      </div>
    </div>
  );

  return (
    <>
      {embedded ? (
        content
      ) : (
        <div
          className="dialog-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Search"
          {...backdropProps(onClose)}
        >
          {content}
        </div>
      )}
      {openDoc && <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} onNavigate={onClose} />}
    </>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/SearchOverlay.test.tsx`
Expected: PASS — both the new embedded case and every pre-existing non-embedded test (unchanged behavior, `embedded` defaults `false`)

- [ ] **Step 5: Write the failing test for `SearchView`**

```tsx
// frontend/src/views/Search.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import { SearchView } from "./Search";

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
  vi.spyOn(api, "search").mockResolvedValue({ results: [] });
  vi.spyOn(api, "searchBeads").mockResolvedValue({ beads: [] });
});

describe("SearchView", () => {
  it("mounts SearchOverlay embedded, with no dialog role", () => {
    render(
      <MemoryRouter>
        <SearchView />
      </MemoryRouter>,
    );
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/views/Search.test.tsx`
Expected: FAIL — `Cannot find module './Search'`

- [ ] **Step 7: Write `SearchView`**

```tsx
// frontend/src/views/Search.tsx
import { useNavigate } from "react-router-dom";
import { SearchOverlay } from "../components/SearchOverlay";

/**
 * Search as a full screen (mobile app shell design, §2). The Search bottom-nav
 * tab routes here on phone; desktop still reaches `SearchOverlay` as a modal
 * via the header button / ⌘K in `App.tsx`. Same component, same API calls —
 * only the mount point differs.
 */
export function SearchView() {
  const navigate = useNavigate();
  return <SearchOverlay embedded onClose={() => navigate("/")} />;
}
```

- [ ] **Step 8: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/views/Search.test.tsx`
Expected: PASS

- [ ] **Step 9: Add the route in `App.tsx`**

```tsx
import { SearchView } from "./views/Search";
```

```tsx
          <Route path="/settings/*" element={<Settings />} />
          <Route path="/search" element={<SearchView />} />
```

- [ ] **Step 10: Add `.search-page` layout CSS**

In `frontend/src/styles.css`, near the existing `.search-overlay` rule (around line 693):

```css
.search-page { padding: 20px 16px; display: flex; flex-direction: column; gap: 0; }
```

- [ ] **Step 11: Run the full frontend unit suite**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add frontend/src/components/SearchOverlay.tsx frontend/src/components/SearchOverlay.test.tsx \
        frontend/src/views/Search.tsx frontend/src/views/Search.test.tsx \
        frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(mobile): mount Search as a routed phone screen"
```

---

### Task 3: Analytics phone card reflow

**Files:**
- Modify: `frontend/src/views/Analytics.tsx` (`data-label` attributes on `.node-row`/`.repo-row` spans)
- Modify: `frontend/src/views/Analytics.test.tsx` (assert the attributes)
- Modify: `frontend/src/styles.css` (phone-block card CSS)

**Interfaces:**
- Independent of Tasks 1, 2, 4, 5 — touches only `Analytics.tsx` and shared CSS.
- Produces: every `.node-row`/`.repo-row` data span carries `data-label`, which the phone CSS reads via `content: attr(data-label)`. `Task 6`'s e2e addition depends on this.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/Analytics.test.tsx` (reuse the file's existing `report`/`renderView` fixtures):

```tsx
it("labels every by-node and by-repo cell for the phone card reflow", async () => {
  renderView();
  await screen.findByText("implementation");
  const nodeRow = screen.getByText("implementation").closest(".node-row")!;
  for (const label of ["node", "cost share", "runs", "avg time", "tokens", "cost", "rounds"]) {
    expect(nodeRow.querySelector(`[data-label="${label}"]`)).not.toBeNull();
  }
  const repoRow = screen.getByText("repo-a").closest(".repo-row")!;
  for (const label of ["repo", "items", "MRs", "tokens", "cost"]) {
    expect(repoRow.querySelector(`[data-label="${label}"]`)).not.toBeNull();
  }
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/views/Analytics.test.tsx -t "phone card"`
Expected: FAIL — no elements have `data-label`

- [ ] **Step 3: Add `data-label` to the by-node and by-repo rows**

In `frontend/src/views/Analytics.tsx`, replace the `by_node` map body:

```tsx
                {report.by_node.map((n) => (
                  <div key={n.node} className="node-row" data-node={n.node}>
                    <span className="node-name" data-label="node">
                      {n.node}
                    </span>
                    <span className="share" data-label="cost share">
                      <span style={{ width: `${(n.cost_usd / topCost) * 100}%` }} />
                    </span>
                    <span className="num" data-label="runs">
                      {n.runs}
                    </span>
                    <span className="num" data-label="avg time">
                      {elapsed(n.avg_ms)}
                    </span>
                    <span className="num" data-label="tokens">
                      {tokens(n.tokens)}
                    </span>
                    <span className="num strong" data-label="cost">
                      {usd(n.cost_usd, n.cost_complete)}
                    </span>
                    <span className="num" data-label="rounds">
                      {n.rounds}
                    </span>
                  </div>
                ))}
```

And the `by_repo` map body:

```tsx
                {report.by_repo.map((r) => (
                  <div key={r.repo} className="repo-row" data-repo={r.repo}>
                    <span title={r.repo} data-label="repo">
                      {repoName(r.repo)}
                    </span>
                    <span className="num" data-label="items">
                      {r.items}
                    </span>
                    <span className="num" data-label="MRs">
                      {r.mrs}
                    </span>
                    <span className="num" data-label="tokens">
                      {tokens(r.tokens)}
                    </span>
                    <span className="num strong" data-label="cost">
                      {usd(r.cost_usd, r.cost_complete)}
                    </span>
                  </div>
                ))}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/views/Analytics.test.tsx`
Expected: PASS

- [ ] **Step 5: Add the phone card CSS**

Append inside the existing `@media (max-width: 640px)` phone block in `frontend/src/styles.css` (the same block Task 1 touched), after the `.kpis` line:

```css
  /* by-node is 7 columns, by-repo is 5 — no readable width at 390px even
     wrapped. Stack each row into a card; `data-label` (added on the JSX
     spans) supplies the per-field caption via ::before, so this is a CSS-only
     reflow of markup that already carries every value it needs. */
  .node-head, .repo-head { display: none; }
  .node-row, .repo-row { grid-template-columns: 1fr; gap: 4px; padding: 12px 0; }
  .node-row > span, .repo-row > span {
    display: flex; justify-content: space-between; align-items: center; text-align: left;
  }
  .node-row > span::before, .repo-row > span::before {
    content: attr(data-label);
    font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--color-text-muted);
  }
  .node-row .node-name, .repo-row span[data-label="repo"] {
    font-size: 13px; font-weight: 500; color: var(--color-text);
  }
  .node-row .node-name::before, .repo-row span[data-label="repo"]::before { content: none; }
  /* the cost-share bar reads as noise once it's one of several stacked
     key:value lines instead of a column beside them */
  .node-row .share { display: none; }
```

This is a new rule set (no pre-existing `.node-row`/`.repo-row` phone override to worry about beating on specificity — the only other phone-block mention, at the `.row, .doc-row, ... .node-row, .repo-row` selector, only sets a `background` gradient, not `grid-template-columns`, so there's no conflict to order around).

- [ ] **Step 6: Run the full frontend unit suite**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Analytics.tsx frontend/src/views/Analytics.test.tsx frontend/src/styles.css
git commit -m "feat(mobile): reflow Analytics tables into phone cards"
```

---

### Task 4: Settings phone nav list + YAML-editor desktop notice

**Files:**
- Modify: `frontend/src/views/Settings.tsx` (`TemplatesPage`, `SteeringPage`)
- Modify: `frontend/src/views/Settings.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Independent of Tasks 1–3, 5.
- Produces: on phone, `.settings .board-sidebar` renders as a full-width stacked list instead of wrapped chips (pure CSS, no markup change — `.settings .board-sidebar` is a 2-class selector, already higher specificity than the generic 1-class `.board-sidebar` phone rule, so no ordering trap here). `TemplatesPage`/`SteeringPage` gain a `.phone-only` read-only `<pre>` + "Open on desktop to edit" notice, matching the existing `WorkItemDetail.tsx` pattern (`<p className="phone-only open-on-desktop">...`).

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/Settings.test.tsx` (reuse the file's `renderAt` helper and existing `beforeEach` mocks):

```tsx
describe("Settings · phone (mobile app shell)", () => {
  it("Templates hides the editable textarea behind desktop-only and shows a read-only notice", async () => {
    renderAt("/settings/templates");
    await screen.findByText(/nodes · validated/i);
    expect(screen.getByLabelText("template nodes")).toHaveClass("desktop-only");
    expect(screen.getByText(/open on desktop to edit/i)).toBeInTheDocument();
    expect(screen.getByText(/open on desktop to edit/i)).toHaveClass("phone-only");
  });

  it("Steering hides the editable textarea the same way once a file is selected", async () => {
    renderAt("/settings/steering");
    await userEvent.click(await screen.findByText("house-style"));
    expect(await screen.findByLabelText("steering body")).toHaveClass("desktop-only");
    expect(screen.getByText(/open on desktop to edit/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/views/Settings.test.tsx -t "mobile app shell"`
Expected: FAIL — neither textarea carries `desktop-only`, no "open on desktop" text exists on these pages

- [ ] **Step 3: Update `TemplatesPage`**

In `frontend/src/views/Settings.tsx`, in `TemplatesPage`'s `template-draft` block, change the `<textarea>` and the trailing `save-row`:

```tsx
          <textarea
            id="template-nodes"
            aria-label="template nodes"
            className="input mono template-yaml desktop-only"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <pre className="template-readout phone-only">{draft}</pre>
          <p className="phone-only open-on-desktop">Open on desktop to edit.</p>
```

(leave `{!parsed && ...}`, `<ChainBar .../>`, `{showDiff && ...}`, `{report && ...}` exactly as they are — the visualization and validation report stay visible on phone, only editing is desktop-only)

```tsx
          <div className="save-row desktop-only">
            <button className="btn btn-primary" disabled={busy || !parsed} onClick={save}>
              <Check size={14} />
              Save
            </button>
            <button className="btn btn-secondary" disabled={!parsed} onClick={check}>
              Validate
            </button>
            <button className="btn btn-secondary" onClick={() => setShowDiff((v) => !v)}>
              Changes
            </button>
            <button className="btn btn-ghost" onClick={() => setDraft(original)}>
              Discard
            </button>
            <span className="save-hint">
              {message ?? `writes ${current?.id ?? "the template"}.yaml · validator re-runs`}
            </span>
          </div>
```

- [ ] **Step 4: Update `SteeringPage`**

In the same file, `SteeringPage`'s `selected === null ? ... : (...)` branch — change the `<textarea>` and its `save-row`:

```tsx
              <textarea
                id="steering-body"
                aria-label="steering body"
                className="input mono template-yaml desktop-only"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              <pre className="template-readout phone-only">{draft}</pre>
              <p className="phone-only open-on-desktop">Open on desktop to edit.</p>
              {showDiff && <DraftDiff before={loaded} after={draft} />}
              <div className="save-row desktop-only">
                <button
                  className="btn btn-primary"
                  disabled={busy || draft === loaded}
                  onClick={save}
                >
                  <Check size={14} />
                  Save
                </button>
                <button className="btn btn-secondary" onClick={() => setShowDiff((v) => !v)}>
                  Changes
                </button>
                <button
                  className="btn btn-ghost"
                  disabled={busy || draft === loaded}
                  onClick={() => setDraft(loaded)}
                >
                  Discard
                </button>
                <button className="btn btn-ghost" disabled={busy} onClick={remove}>
                  Delete
                </button>
                <span className="save-hint">
                  {message ??
                    `${draftBytes} B · counts toward the ${list.max_bytes} B assembled ` +
                      `budget of any repo or hook that references this file`}
                </span>
              </div>
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/views/Settings.test.tsx`
Expected: PASS

- [ ] **Step 6: Add the CSS**

In `frontend/src/styles.css`, near `textarea.template-yaml { min-height: 420px; ... }` (line 825):

```css
.template-readout {
  white-space: pre-wrap; overflow-wrap: anywhere;
  font-family: ui-monospace, Menlo, monospace; font-size: 12px; line-height: 1.5;
  background: var(--color-neutral-900); border-radius: 8px; padding: 12px;
  max-height: 50vh; overflow-y: auto; margin: 0;
}
```

Append inside the existing phone `@media (max-width: 640px)` block (Task 1's block):

```css
  /* 9 settings pages wrapped as chips is cramped; `.settings .board-sidebar`
     (2-class selector) already outranks the generic 1-class `.board-sidebar`
     phone rule above on specificity alone, so this needs no ordering care. */
  .settings .board-sidebar { flex-direction: column; flex-wrap: nowrap; }
  .settings .board-sidebar .facet-opt { min-height: 44px; font-size: 14px; }
```

- [ ] **Step 7: Run the full frontend unit suite**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/views/Settings.test.tsx frontend/src/styles.css
git commit -m "feat(mobile): stack Settings nav on phone, gate YAML editors to desktop"
```

---

### Task 5: PWA install (manifest + icon)

**Files:**
- Create: `frontend/public/manifest.json`
- Create: `frontend/public/icon.svg`
- Create: `frontend/src/manifest.test.ts`
- Modify: `frontend/index.html`

**Interfaces:**
- Independent of every other task. `frontend/public/` is Vite's default static-asset root (already configured by `vite build`'s defaults — no `vite.config.ts` change needed; files under `public/` are copied to `dist/` root as-is).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/manifest.test.ts
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

describe("PWA manifest", () => {
  it("declares a standalone, installable app with an icon", () => {
    const manifest = JSON.parse(
      readFileSync(join(here, "..", "public", "manifest.json"), "utf-8"),
    );
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/");
    expect(manifest.name).toBe("Kraft");
    expect(manifest.icons.length).toBeGreaterThan(0);
    expect(manifest.icons[0].src).toBe("/icon.svg");
  });

  it("index.html links the manifest, the icon, and a theme-color", () => {
    const html = readFileSync(join(here, "..", "index.html"), "utf-8");
    expect(html).toContain('<link rel="manifest" href="/manifest.json" />');
    expect(html).toContain('<link rel="icon" href="/icon.svg" type="image/svg+xml" />');
    expect(html).toMatch(/<meta name="theme-color" content="#161826" \/>/);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/manifest.test.ts`
Expected: FAIL — `frontend/public/manifest.json` does not exist (ENOENT)

- [ ] **Step 3: Create the icon**

```svg
<!-- frontend/public/icon.svg -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#161826"/>
  <text x="256" y="340" text-anchor="middle"
        font-family="ui-sans-serif, system-ui, sans-serif" font-size="280" font-weight="600"
        fill="#9184d9">K</text>
</svg>
```

(`#161826`/`#9184d9` are Nocturne's default `--color-bg`/`--color-accent` from `frontend/src/nocturne.css` — the manifest is static and can't follow a user's chosen palette from Settings → Appearance, so it uses the shipped default)

- [ ] **Step 4: Create the manifest**

```json
{
  "name": "Kraft",
  "short_name": "Kraft",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#161826",
  "theme_color": "#161826",
  "icons": [
    { "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable" }
  ]
}
```

- [ ] **Step 5: Link both from `index.html`**

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="manifest" href="/manifest.json" />
    <link rel="icon" href="/icon.svg" type="image/svg+xml" />
    <meta name="theme-color" content="#161826" />
    <title>Kraft</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/manifest.test.ts`
Expected: PASS

- [ ] **Step 7: Run the full frontend unit suite**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/public/manifest.json frontend/public/icon.svg frontend/src/manifest.test.ts frontend/index.html
git commit -m "feat(mobile): add PWA manifest and icon"
```

---

### Task 6: Extend the phone e2e visual spec

**Files:**
- Modify: `frontend/e2e/phone.visual.spec.ts`

**Interfaces:**
- Consumes: the bottom nav (`.bottom-nav`, `.bottom-nav-tab`, Task 1), the `/search` route (Task 2), the Analytics card `data-label` reflow (Task 3), the Settings desktop-only notice (Task 4). Run this task last.

- [ ] **Step 1: Add the bottom-nav test**

Append to `frontend/e2e/phone.visual.spec.ts`:

```ts
test("the bottom nav reaches every screen and highlights the active tab", async ({ page }) => {
  await createItem(page, "phone shell", "default");
  const nav = page.getByRole("navigation", { name: "primary" });
  await expect(nav).toBeVisible();

  // every tab meets the 44px touch-target floor
  for (const label of ["Board", "Search", "Analytics", "Settings"]) {
    const box = await nav.getByRole("link", { name: label }).boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }

  await nav.getByRole("link", { name: "Analytics" }).click();
  await expect(page).toHaveURL(/\/analytics$/);
  await expect(nav.getByRole("link", { name: "Analytics" })).toHaveClass(/active/);

  await nav.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings/);

  await nav.getByRole("link", { name: "Board" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(nav.getByRole("link", { name: "Board" })).toHaveClass(/active/);

  await expect(overflowsX(page.locator("body"))).resolves.toBe(false);
});

test("Search opens as a full screen from the bottom nav, not a modal", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "primary" }).getByRole("link", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/search$/);
  expect(await page.getByRole("dialog", { name: "Search" }).count()).toBe(0);
  await page.getByRole("searchbox").fill("reconnect backoff");
  await expect(page.locator(".search-result-title", { hasText: "WS transport design" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-07-search.png`, fullPage: true });
});

test("Analytics stacks by-node and by-repo into cards with no horizontal overflow", async ({ page }) => {
  await createItem(page, "phone analytics", "quick-task");
  await expect(page.getByText(/completed|needs you/i).first()).toBeVisible({ timeout: 60_000 });
  await page.goto("/analytics");
  await expect(page.locator(".node-row").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-08-analytics.png`, fullPage: true });
  expect(await overflowsX(page.locator(".analytics-body"))).toBe(false);
  // the head row is redundant once every cell carries its own label
  await expect(page.locator(".node-head")).toBeHidden();
});

test("Templates and Steering show the open-on-desktop notice instead of an editable textarea", async ({ page }) => {
  await page.goto("/settings/templates");
  await expect(page.getByText(/open on desktop to edit/i)).toBeVisible();
  await expect(page.getByLabel("template nodes")).toBeHidden();

  await page.goto("/settings/steering");
  const first = page.locator(".template-list .facet-opt").first();
  if ((await first.count()) > 0) {
    await first.click();
    await expect(page.getByText(/open on desktop to edit/i)).toBeVisible();
    await expect(page.getByLabel("steering body")).toBeHidden();
  }
  await page.screenshot({ path: `${SHOTS}/phone-09-settings.png`, fullPage: true });
});
```

- [ ] **Step 2: Run the extended spec against a live fixture server**

```bash
cd frontend && npm run build
uv run python e2e/serve.py   # note the printed KRAFT_E2E_REPO and port
# in a second terminal:
cd frontend && KRAFT_E2E_REPO=<printed path> npx playwright test phone.visual.spec.ts
```

Expected: all tests pass, including the 5 pre-existing ones; `frontend/e2e-shots/phone-07-search.png`, `phone-08-analytics.png`, `phone-09-settings.png` are written and worth a manual look.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/phone.visual.spec.ts
git commit -m "test(mobile): extend the phone e2e spec for the app shell"
```
