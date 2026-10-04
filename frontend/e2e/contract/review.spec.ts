import { expect, type Page } from "@playwright/test";
import { withProducer } from "./mocks/fixtures";
import { app, contract, editorsRoute, focusedName, item, pause, type Row } from "./kit";

/** Review and the document viewer: the gate review, Approve's lock, Esc and focus, Open in editor, search matches. */

const appDoc = async (p: Page, editors = true) => {
  await app(p, item("needs-gate", "?sel=final_review"), editors ? { routes: editorsRoute } : {});
  await p.getByRole("button", { name: /^Read / }).first().click();
  await p.getByRole("dialog").waitFor();
};
const appSearchDoc = async (p: Page, routes: (p: Page) => Promise<unknown> = editorsRoute) => {
  await app(p, "/settings/policy", { routes });
  await p.keyboard.press("ControlOrMeta+k"); await p.getByRole("combobox", { name: /search/i }).fill("cache");
  await p.getByRole("listbox").locator('[data-section="docs"] [role="option"]').first().click(); await p.getByRole("dialog").waitFor(); await pause(p, 600);
};

const ROWS: Row[] = [
  {
    name: "review: Approve stays locked while must-fix threads are open [decided]",
    run: async (p) => { await app(p, item("needs-gate", "/review"), { ready: (pg) => pg.locator(".review-page").waitFor() }); await expect(p.getByText(/must[- ]fix/i).first()).toBeVisible(); await expect(p.getByRole("button", { name: /^Approve/ }).first()).toBeDisabled(); },
  },

  // Document viewer
  {
    name: "doc viewer: Esc closes it",
    run: async (p) => { await appDoc(p); await p.keyboard.press("Escape"); await pause(p); await expect(p.getByRole("dialog")).toBeHidden(); },
  },
  {
    name: "doc viewer: a click outside it closes it",
    run: async (p) => { await appDoc(p); await p.mouse.click(40, 400); await pause(p); await expect(p.getByRole("dialog")).toBeHidden(); },
  },
  {
    name: "doc viewer: closing it returns focus to the button that opened it",
    run: async (p) => { await appDoc(p); await p.keyboard.press("Escape"); await pause(p); expect(await focusedName(p)).toMatch(/^.?\s*Read /); },
  },
  {
    name: "doc viewer: opening it moves focus into it",
    run: async (p) => { await appDoc(p); const inside = await p.evaluate(() => !!document.activeElement?.closest("[role=dialog]")); expect(inside).toBe(true); },
  },
  {
    name: "doc viewer: it opens as a panel from the right edge, the page still visible beside it",
    run: async (p) => { await appDoc(p); const b = (await p.getByRole("dialog").boundingBox())!; expect(b.x + b.width).toBeGreaterThan(1270); expect(b.x).toBeGreaterThan(400); },
  },
  {
    name: "doc viewer: full screen fills the window and reads in a ~760px column [decided]",
    run: async (p) => { await appDoc(p); await p.getByRole("dialog").getByRole("button", { name: /full screen/i }).click({ timeout: 2000 }); await pause(p); const b = (await p.getByRole("dialog").boundingBox())!; expect(b.width).toBeGreaterThan(1200); expect(b.height).toBeGreaterThan(780); const c = (await p.locator(".dv-body > *").first().boundingBox())!; expect(c.width).toBeGreaterThan(680); expect(c.width).toBeLessThan(800); },
  },
  {
    name: "doc viewer: closing full screen and opening again is a drawer [decided]",
    run: async (p) => { await appDoc(p); await p.getByRole("dialog").getByRole("button", { name: /full screen/i }).click(); await pause(p); await p.keyboard.press("Escape"); await pause(p); await p.getByRole("button", { name: /^Read / }).first().click(); await pause(p); const b = (await p.getByRole("dialog").boundingBox())!; expect(b.width).toBeLessThan(700); },
  },
  {
    name: "doc viewer: one Open in editor button; its menu lists only the installed editors [decided]",
    run: async (p) => {
      // GET /editors (mocked): VS Code (default) and Cursor installed, plus the system opener. An indexed document (from search).
      await appSearchDoc(p); const d = p.getByRole("dialog");
      await expect(d.getByRole("button", { name: "Open in editor" })).toHaveCount(1, { timeout: 3000 });
      await expect(d.getByRole("button", { name: "Open in editor" })).toBeEnabled();
      await d.getByRole("button", { name: "Other editors" }).click(); await pause(p, 300);
      await expect(p.getByRole("menuitem")).toHaveText(["Cursor", "System default"]);
    },
  },
  {
    name: "doc viewer: a gate's document (opened from the gate) offers Open in editor too",
    run: async (p) => { await appDoc(p); await expect(p.getByRole("dialog").getByRole("button", { name: "Open in editor" })).toBeVisible({ timeout: 3000 }); },
  },
  {
    name: "doc viewer: with no editor installed, Open in editor is disabled and says so [decided]",
    run: async (p) => {
      await appSearchDoc(p, (pg) => pg.route("**/api/editors", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ available: [], system: false, default: null }) })));
      const b = p.getByRole("dialog").getByRole("button", { name: "Open in editor" });
      await expect(b).toBeDisabled(); await expect(b).toHaveAttribute("title", "No editor found on this machine");
    },
  },
  {
    name: "doc viewer: opened from search, a bar shows the query and n of N matches [decided]",
    run: async (p) => { await app(p, "/settings/policy", { routes: editorsRoute }); await p.keyboard.press("ControlOrMeta+k"); await p.getByRole("combobox", { name: /search/i }).fill("cache"); await p.getByRole("listbox").locator('[data-section="docs"] [role="option"]').first().click(); const bar = p.getByRole("group", { name: "Search matches" }); await expect(bar).toBeVisible({ timeout: 4000 }); await expect(bar).toContainText("from search"); await expect(bar.getByRole("status")).toHaveText(/\d+ of \d+|matched in the title|no match in the text/); expect(p.url()).toMatch(/[?&]q=cache/); },
  },
  {
    name: "settings: Appearance has a default editor, listing the installed editors [decided]",
    run: async (p) => { await app(p, "/settings/appearance", { routes: editorsRoute }); await expect(p.getByText(/Default editor/i).first()).toBeVisible({ timeout: 3000 }); },
  },

  {
    name: "review: Esc closes the review and returns to the item",
    run: async (p) => { await app(p, item("needs-gate", "/review"), { ready: (pg) => pg.locator(".review-page").waitFor() }); await p.keyboard.press("Escape"); await pause(p); expect(p.url()).not.toContain("/review"); },
  },
  {
    name: "review: the board's Review to approve opens the gate's review",
    run: async (p) => { await app(p, "/"); await p.getByRole("button", { name: "Review to approve" }).first().click(); await pause(p, 800); expect(p.url()).toMatch(/\/review/); await expect(p.getByRole("button", { name: /^Approve/ }).first()).toBeVisible(); },
  },

  {
    name: "review: at a gate Request changes is a rejection, and the Finish review dialog says where it goes [decided]",
    run: async (p) => {
      await app(p, item("needs-gate", "/review?gate=final_review&doc=1"), { ready: (pg) => pg.locator(".review-page").waitFor({ timeout: 8000 }) });
      await expect(p.getByRole("button", { name: /^Reject/ }), "no Reject button in the review header").toHaveCount(0);
      await p.getByRole("button", { name: "Request changes" }).first().click();
      const d = p.getByRole("dialog");
      await expect(d.getByText("Reject final_review · goes back to implementation")).toBeVisible({ timeout: 2000 });
      await expect(d.getByRole("button", { name: "Reject to implementation" })).toBeVisible();
    },
  },
  {
    name: "doc viewer: a gate's document says who wrote it [decided]",
    run: async (p) => {
      await app(p, item("needs-gate", "?sel=final_review"), { routes: editorsRoute, tweak: withProducer });
      await p.getByRole("button", { name: /^Read / }).first().click(); await p.getByRole("dialog").waitFor();
      await expect(p.getByRole("dialog").getByText(/written by/).first()).toBeVisible({ timeout: 3000 });
    },
  },
  {
    name: "doc viewer: Open in editor and Copy path are both there on a gate's document [decided]",
    run: async (p) => {
      await app(p, item("needs-gate", "?sel=final_review"), { routes: editorsRoute });
      await p.getByRole("button", { name: /^Read / }).first().click(); await p.getByRole("dialog").waitFor();
      const d = p.getByRole("dialog");
      await expect(d.getByRole("button", { name: /^Open in/ }).first()).toBeVisible({ timeout: 3000 });
      await d.getByRole("button", { name: "Other editors" }).click(); await pause(p, 300);
      await expect(p.getByRole("menuitem", { name: /Copy path/ }).or(p.getByText("Copy path", { exact: true })).first()).toBeVisible({ timeout: 2000 });
    },
  },
  {
    name: "review: dragging the range's last handle extends the range and the open composer keeps its text and follows",
    run: async (p) => {
      await app(p, item("needs-gate", "/review"), { ready: (pg) => pg.locator(".review-page").waitFor({ timeout: 8000 }) });
      const f = p.locator('[data-file="kraft/progress.py"]');
      const num = (side: string, n: number) => f.getByRole("button", { name: `Pick ${side} line ${n}`, exact: true }).first();
      await num("new", 2).click(); await num("new", 7).click({ modifiers: ["Shift"] }); await num("new", 7).hover(); await pause(p, 200);
      await p.locator(".rv-plus:visible").first().click();
      const box = p.getByRole("group", { name: /^Comment:/ }).first();
      await expect(box).toHaveAttribute("aria-label", "Comment: Lines +2 to +7");
      await p.getByRole("textbox", { name: "Comment" }).first().fill("kept");
      const g = (await p.locator('[data-tip="Drag to change the last line"]').first().boundingBox())!, t = (await num("new", 8).boundingBox())!;
      await p.mouse.move(g.x + g.width / 2, g.y + g.height / 2); await p.mouse.down(); await p.mouse.move(t.x + 4, t.y + t.height / 2, { steps: 6 }); await p.mouse.up(); await pause(p, 300);
      await expect(box).toHaveAttribute("aria-label", "Comment: Lines +2 to +8");
      await expect(p.getByRole("textbox", { name: "Comment" }).first()).toHaveValue("kept");
      await expect(p.getByRole("button", { name: /edit (the )?(start|lines|range)/i })).toHaveCount(0);
    },
  },
];

contract(ROWS);
