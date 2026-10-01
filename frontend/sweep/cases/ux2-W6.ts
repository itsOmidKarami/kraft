import type { Page } from "@playwright/test";
import { NG_NOW } from "../ngItems";
import { ng, settle, type Case, type Ctx } from "../cellKit";

/** The board (ux2-W6), served its own fixtures (`mock: { ngBoard }`), the clock fixed at NG_NOW so ages read the same every run. */
async function ngBoard(c: Ctx, opts: { tail?: string; side?: "pinned" | "rail"; then?: (p: Page) => Promise<void> } = {}) {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await ng(c, `/${opts.tail ?? ""}`, {}, { side: opts.side ?? "pinned" });
  if (opts.then) { await opts.then(c.page); await settle(c.page, 400); }
}
/** The composer with its repos, chains and first dry run in. */
const composerReady = async (p: Page) => { await p.getByText(/nodes run ·/).waitFor(); await p.waitForTimeout(400); };
const composerFilled = async (p: Page) => {
  await composerReady(p);
  await p.getByRole("textbox", { name: "Title" }).fill("Design the caching layer for document search");
  await p.getByRole("textbox", { name: "Brief" }).fill("Cache embeddings by content hash; invalidate on reindex.");
  await p.getByRole("button", { name: "+ spec" }).click();
  const box = p.getByRole("textbox", { name: /Search specs/ });
  await box.fill("docs/specs/doc-search-cache.md"); await p.waitForTimeout(300); await box.press("Enter");
  await p.getByText(/of 15 nodes run/).waitFor(); await p.waitForTimeout(500);
};
/** The draft item page from a typed URL (its query carries the draft), titled with a spec or empty. */
const ngDraft = async (c: Ctx, titled: boolean, then?: (p: Page) => Promise<void>) => {
  const q = new URLSearchParams({ repo: "/Users/dev/code/kraft-plugins", chain: "default", ...(titled ? { title: "Design the caching layer for document search", spec: "docs/specs/doc-search-cache.md" } : {}) });
  await ngBoard(c, { tail: `work-items/new?${q}`, then: async (p) => { await p.getByText(/nodes run ·/).first().waitFor(); await p.waitForTimeout(500); if (then) await then(p); } });
};
/** A board row per peek variant, by the start of its title (ngBoard.ts). */
const PEEK_ROWS = {
  "needs-gate": "Design the caching layer", question: "Add rate limit headers", capped: "Fix flaky retry test", failed: "Retry on 429",
  paused: "Trim the review prompts", running: "Bump the VS Code", "not-started": "Spike: stream logs", done: "Release notes for 0.14", cancelled: "Rename the harness profiles",
} as const;
/** Click a board row and wait for its peek. */
const peekRow = (title: string) => async (p: Page) => {
  await p.getByRole("button", { name: new RegExp(`^${title}`) }).click();
  await p.locator(".pane .pane-tabs").waitFor();
};
/** Check board rows by the start of their titles. */
const checkRows = (titles: string[]) => async (p: Page) => {
  for (const t of titles) await p.getByRole("checkbox", { name: new RegExp(`^Select ${t}`) }).check();
};

export const cells: Case[] = [
  // ux2-W6: the board, its own fixtures (ngBoard.ts).
  { screen: "board", variant: "default", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }, { short: true }], mock: { ngBoard: true }, run: (c) => ngBoard(c) },
  ...(["long", "many"] as const).map<Case>((d) => ({ screen: "board", variant: d, data: d, widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c) })),
  // No items, but a connected repo: the four empty groups, not first-run.
  { screen: "board", variant: "empty", data: "default", widths: [1280], mock: { ngBoard: "empty" }, run: (c) => ngBoard(c) },
  // The header's and filter bar's menus, opened as a person would.
  ...([["repo-menu", /all repos/], ["chain-menu", /^Chain/], ["group-menu", /^Group/], ["sort-menu", /^Sort/]] as const).map<Case>(([v, name]) => ({
    screen: "board", variant: v, data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngBoard(c, { then: async (p) => { await p.getByRole("button", { name }).click(); await p.getByRole("menu").waitFor(); } }),
  })),
  // C: no repo connected (first-run), the first read still running, the server gone after boot.
  { screen: "board", variant: "fresh", data: "empty", widths: [1280], mock: { ngBoard: "empty" }, run: (c) => ngBoard(c) },
  { screen: "board", variant: "loading", data: "default", widths: [1280], mock: { ngBoard: true, boardState: "loading" }, run: (c) => ngBoard(c) },
  { screen: "board", variant: "offline", data: "default", widths: [1280], mock: { ngBoard: true, boardState: "offline" }, run: (c) => ngBoard(c, { then: async (p) => { await p.getByText("OFFLINE").waitFor(); } }) },
  // D: selection in every group, the bulk Cancel's inline confirm, and a partial answer.
  { screen: "board", variant: "selected", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { then: checkRows(["Bump the VS Code", "Fix flaky retry", "Remove the legacy poller"]) }) },
  { screen: "board", variant: "bulk-cancel-confirm", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { then: async (p) => {
    await checkRows(["Bump the VS Code", "Fix flaky retry"])(p);
    await p.getByRole("button", { name: "Cancel 2…" }).click();
    await p.getByRole("textbox", { name: /Reason/ }).fill("Superseded by kraft-cb61");
  } }) },
  { screen: "board", variant: "bulk-results", data: "default", widths: [1280], mock: { ngBoard: true, bulkFail: ["kraft-2c77"] }, run: (c) => ngBoard(c, { then: async (p) => {
    await checkRows(["Bump the VS Code", "Lint fan-out"])(p);
    await p.getByRole("button", { name: "‖ Pause 2" }).click();
    await p.getByText("1 of 2 items paused").waitFor();
  } }) },
  // E: the peek on one row of each kind, its other tabs, the overlay under 1024 and a short window.
  ...Object.entries(PEEK_ROWS).map<Case>(([v, title]) => ({
    screen: "board-peek", variant: v, data: "default", widths: [1280], ...(v === "needs-gate" ? { shells: [{ short: true }] } : {}), mock: { ngBoard: true },
    run: (c) => ngBoard(c, { then: peekRow(title) }),
  })),
  ...(["activity", "config"] as const).map<Case>((tab) => ({
    screen: "board-peek", variant: tab, data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngBoard(c, { then: async (p) => { await peekRow(PEEK_ROWS.capped)(p); await p.getByRole("tab", { name: tab === "activity" ? "Activity" : "Config" }).click(); } }),
  })),
  { screen: "board-peek", variant: "running", data: "default", widths: [768], mock: { ngBoard: true }, run: (c) => ngBoard(c, { side: "rail", then: peekRow(PEEK_ROWS.running) }) },
  // F: the composer at the top of the board, as it fills, its chain menu, the attach search, the discard ask, a refused attachment.
  { screen: "new-item", variant: "composer", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: composerReady }) },
  { screen: "new-item", variant: "filled", data: "default", widths: [1024, 1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: composerFilled }) },
  { screen: "new-item", variant: "chain-menu", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => { await composerReady(p); await p.getByRole("button", { name: /^default/ }).click(); await p.getByRole("menu").waitFor(); } }) },
  { screen: "new-item", variant: "attach", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => { await composerReady(p); await p.getByRole("button", { name: "+ spec" }).click(); await p.getByRole("textbox", { name: /Search specs/ }).fill("cache"); await p.waitForTimeout(500); } }) },
  { screen: "new-item", variant: "discard", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => { await composerFilled(p); await p.keyboard.press("Escape"); await p.getByText(/Discard this draft/).waitFor(); } }) },
  { screen: "new-item", variant: "error", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => {
    await composerFilled(p);
    await p.getByRole("button", { name: "+ plan" }).click();
    const box = p.getByRole("textbox", { name: /Search plans/ });
    await box.fill("docs/plans/missing.md"); await p.waitForTimeout(300); await box.press("Enter");
    await p.getByText(/attachment not found/).waitFor();
  } }) },
  // G: the draft item page, empty and titled with a spec, a node and a covered node, Config, YAML, members, the discard ask.
  { screen: "draft-item", variant: "default", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngDraft(c, false) },
  { screen: "draft-item", variant: "titled", data: "default", widths: [1024, 1280], shells: [{ short: true }], mock: { ngBoard: true }, run: (c) => ngDraft(c, true) },
  ...([["node", /^verification,/], ["node-covered", /^spec,/]] as const).map<Case>(([v, name]) => ({
    screen: "draft-item", variant: v, data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngDraft(c, true, async (p) => { await p.getByRole("button", { name }).click(); }),
  })),
  ...(["Config", "YAML"] as const).map<Case>((tab) => ({
    screen: "draft-item", variant: tab.toLowerCase(), data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngDraft(c, true, async (p) => { await p.getByRole("tab", { name: new RegExp(`^${tab}`) }).click(); await p.waitForTimeout(400); }),
  })),
  { screen: "draft-item", variant: "members", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngDraft(c, true, async (p) => {
    await p.getByRole("button", { name: "+ members" }).click();
    await p.getByRole("button", { name: /plugins\/kraft-lite/ }).click();
  }) },
  { screen: "draft-item", variant: "discard", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngDraft(c, true, async (p) => { await p.keyboard.press("Escape"); await p.getByRole("alertdialog").waitFor(); }) },
  // H: the archived view, with rows, empty, and two checked.
  { screen: "archived", variant: "default", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "archived" }) },
  { screen: "archived", variant: "empty", data: "default", widths: [1280], mock: { ngBoard: "empty" }, run: (c) => ngBoard(c, { tail: "archived" }) },
  { screen: "archived", variant: "selected", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "archived", then: checkRows(["Trim the default chain", "Drop the v0 webhook"]) }) },
  { screen: "board", variant: "group-repo", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?group=repo" }) },
  { screen: "board", variant: "filtered", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?q=docs&chain=docs_only" }) },
];
