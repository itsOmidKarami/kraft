import { expect, type Page } from "@playwright/test";
import type { Scenario } from "./mocks/fixtures";
import { withBeads, withCapLimit, withEscalationThread, withFailedTests, withFixRoundOutcome, withReviewer, withScopes, withStop, withTestResult } from "./mocks/fixtures";
import { app, away, contract, dragPaneEdge, focusedName, item, pause, type Row } from "./kit";

/** The item page: the side pane, the header's card menu, the log viewer, the chain graph, a gate's pane, and what a stop says. */

const appLog = async (p: Page, data: "default" | "long" = "default", sel = "verification.review.code_review") => {
  await app(p, item("running", `/nodes/verification?sel=${sel}&tab=log`), { data, ready: (pg) => pg.getByLabel("Log lines").first().waitFor() });
  return p.getByLabel("Log lines").first();
};
/** The log's scroll box: the lines' own element or the nearest ancestor that scrolls (the pre can grow and leave the scrolling to its pane). `to` scrolls it there first. */
const scrollerOf = (l: ReturnType<Page["locator"]>, to?: number) => l.evaluate((el, to) => {
  let n: HTMLElement | null = el as HTMLElement;
  while (n && !(n.scrollHeight > n.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(n).overflowY))) n = n.parentElement;
  const box = n ?? (el as HTMLElement);
  if (to !== undefined) { box.scrollTop = to; box.dispatchEvent(new Event("scroll")); }
  return { top: Math.round(box.scrollTop), client: box.clientHeight, height: box.scrollHeight, cls: box.className };
}, to);

const appPoint = (loc: ReturnType<Page["locator"]>) => loc.evaluateAll((els) => {
  for (const el of els) {
    const r = el.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
    const hit = document.elementFromPoint(x, y);
    if (hit && el.contains(hit)) return { x, y, label: el.getAttribute("aria-label") };
  }
  return null;
});
/** What the page shows as selected: the URL and the number of selected-looking elements. */
const selection = (p: Page) => p.evaluate(() => ({ url: location.pathname + location.search, selected: document.querySelectorAll('[aria-pressed="true"].graph-node, .graph-node.is-selected, .graph-node[aria-current], .strip-node.is-selected, [data-selected="true"]').length }));

async function clickNodeByMouse(p: Page, nodes: ReturnType<Page["locator"]>) {
  const before = await selection(p);
  const pt = await appPoint(nodes);
  expect(pt, "a node whose centre is visible and not covered").not.toBeNull();
  await p.mouse.click(pt!.x, pt!.y); await pause(p, 600);
  const after = await selection(p);
  expect(JSON.stringify(after), `clicking "${pt!.label}" at ${pt!.x},${pt!.y} changed nothing`).not.toBe(JSON.stringify(before));
}

const ROWS: Row[] = [
  // Work item
  {
    name: "work item: Esc from the page collapses the side pane",
    run: async (p) => { await app(p, item("running")); const b = p.getByRole("button", { name: "Collapse pane" }); await expect(b).toBeVisible(); await p.locator("main h1").first().click(); await p.keyboard.press("Escape"); await pause(p); await expect(b).toBeHidden(); },
  },
  {
    name: "work item: Esc in a node view, pane closed, goes back to the chain",
    run: async (p) => { await app(p, item("running", "/nodes/verification")); await p.getByRole("button", { name: "Collapse pane" }).click(); await pause(p); await p.keyboard.press("Escape"); await pause(p); expect(p.url()).not.toContain("/nodes/"); },
  },
  {
    name: "work item: the side pane's width, once dragged, persists across a reload",
    run: async (p) => { await app(p, item("running")); const h = p.getByRole("separator", { name: "Resize pane" }); const [x0, x1, x2] = await dragPaneEdge(p, h, async () => { await p.reload(); await p.locator("main h1").first().waitFor(); await pause(p); }); expect(x1).toBeLessThan(x0 - 40); expect(Math.abs(x2 - x1)).toBeLessThan(4); },
  },

  {
    name: "item header: a renamed title saves on blur [decided]",
    run: async (p) => {
      let body: any = null;
      await app(p, item("running"), { routes: (pg) => pg.route(/\/api\/work-items\/[0-9a-f]+$/, async (r) => { if (r.request().method() === "PATCH") { body = r.request().postDataJSON(); return r.fulfill({ status: 200, contentType: "application/json", body: "{}" }); } return r.fallback(); }) });
      await p.locator(".item-title-btn").click(); const box = p.getByRole("textbox", { name: "Title" }); await expect(box).toBeFocused({ timeout: 2000 });
      await box.fill("Renamed by the sweep"); await p.mouse.click(700, 500); await pause(p);
      await expect(box).toBeHidden(); expect(body?.title).toBe("Renamed by the sweep");
    },
  },
  ...(["Pause", "Escalate", "Mark complete", "Cancel"] as const).map((act): Row => ({
    name: `item header: ${act} opens as a card anchored under the main button, not a modal [decided]`,
    run: async (p) => {
      // Escalate only where /escalate takes it (#504): not on a running item, so its card is shot on the failed one.
      await app(p, item(act === "Escalate" ? "failed" : "running"));
      const main = (await p.locator(".item-main").boundingBox())!;
      { await p.locator(".item-main-action").hover(); await pause(p, 400); await p.getByRole("menuitem", { name: new RegExp(`^${act}`) }).click(); }
      await pause(p, 400);
      const card = p.getByRole("dialog").last(); await expect(card).toBeVisible();
      expect(await card.getAttribute("aria-modal"), "a card, not a modal").not.toBe("true");
      const b = (await card.boundingBox())!;
      expect(b.y, "just under the main button").toBeGreaterThan(main.y + main.height - 2); expect(b.y).toBeLessThan(main.y + main.height + 24);
      expect(b.width).toBeGreaterThan(330); expect(b.width).toBeLessThan(390);
      expect(await p.locator(".dialog-backdrop, [class*=scrim]").filter({ visible: true }).count(), "no backdrop").toBe(0);
    },
  })),
  {
    name: "item header: one main button; hover opens its menu over it, leaving closes it [decided]",
    run: async (p) => { await app(p, item("running")); const btn = (await p.locator(".item-main").boundingBox())!; expect(btn.width).toBeGreaterThanOrEqual(118); await p.locator(".item-main-action").hover(); await pause(p, 400); const menu = p.getByRole("menu", { name: "Item actions" }); await expect(menu).toBeVisible(); const m = (await menu.boundingBox())!; expect(Math.abs(m.y - btn.y)).toBeLessThan(4); expect(Math.abs(m.width - btn.width)).toBeLessThan(4); await away(p); await expect(menu).toBeHidden(); },
  },
  {
    name: "item header: clicking ▾ opens the menu; clicking the label runs the action [decided]",
    run: async (p) => { await app(p, item("running")); await p.getByRole("button", { name: "More actions" }).click({ force: true }); await pause(p, 300); await expect(p.getByRole("menu", { name: "Item actions" })).toBeVisible(); await expect(p.getByRole("menuitem", { name: /^Mark complete/ })).toBeVisible(); await app(p, item("running")); await p.locator(".item-main-action").click(); await pause(p, 400); await expect(p.getByRole("dialog", { name: "Pause this item?" })).toBeVisible(); },
  },
  {
    name: "item header: a running item's menu has no Escalate (the server refuses it); a failed item's has it [decided]",
    run: async (p) => {
      await app(p, item("running")); await p.locator(".item-main-action").hover(); await pause(p, 400);
      await expect(p.getByRole("menuitem", { name: /^Mark complete/ })).toBeVisible();
      await expect(p.getByRole("menuitem", { name: /^Escalate/ })).toHaveCount(0);
      await app(p, item("failed")); await p.locator(".item-main-action").hover(); await pause(p, 400);
      await expect(p.getByRole("menuitem", { name: /^Escalate/ })).toBeVisible();
    },
  },
  {
    name: "item header: the diff line counts open review threads (H6)",
    run: async (p) => { await app(p, item("needs-gate")); await expect(p.getByText(/\d+ files? \+\d+ −\d+ · \d+ open threads?/).or(p.getByText(/\d+ open threads?/)).first()).toBeVisible({ timeout: 3000 }); },
  },

  // Logs viewer
  {
    name: "logs: a log opens scrolled to its newest line",
    run: async (p) => { const box = await appLog(p, "long"); await pause(p); const m = await scrollerOf(box); expect(m.height, "nothing scrolls").toBeGreaterThan(m.client); expect(m.top + m.client, JSON.stringify(m)).toBeGreaterThanOrEqual(m.height - 2); },
  },
  {
    name: "logs: scrolled up by the reader, a running log stays put when it is read again",
    run: async (p) => {
      // code_review is the running task, so Log.tsx reads its log again every 3s.
      const box = await appLog(p, "long");
      expect((await scrollerOf(box)).height).toBeGreaterThan((await scrollerOf(box)).client + 100);
      await scrollerOf(box, 0);
      await pause(p, 3600);
      expect((await scrollerOf(box)).top).toBeLessThan(20);
      expect(await box.evaluate((el) => el.scrollTop)).toBeLessThan(20);
    },
  },
  {
    name: "logs: full screen opens and Esc closes it",
    run: async (p) => { await appLog(p); await p.getByRole("button", { name: /full screen/ }).click(); await expect(p.getByRole("dialog")).toBeVisible(); await p.keyboard.press("Escape"); await pause(p); await expect(p.getByRole("dialog")).toBeHidden(); },
  },
  {
    name: "logs: full screen covers the whole viewport [decided]",
    run: async (p) => { await appLog(p); await p.getByRole("button", { name: /full screen/ }).click(); const b = (await p.getByRole("dialog").boundingBox())!; expect(b.x).toBeLessThanOrEqual(1); expect(b.y).toBeLessThanOrEqual(1); expect(b.width).toBeGreaterThanOrEqual(1278); expect(b.height).toBeGreaterThanOrEqual(798); const long = await p.getByRole("dialog").evaluate((el) => el.scrollWidth <= el.clientWidth + 1); expect(long, "nothing runs off the right edge").toBe(true); },
  },
  {
    name: "item: the ⋮ menu has no View run log [decided]",
    run: async (p) => { await app(p, item("running")); await p.getByRole("button", { name: "Item menu" }).click(); await pause(p, 300); await expect(p.getByRole("menuitem").first()).toBeVisible(); await expect(p.getByRole("menuitem", { name: /run log/i })).toHaveCount(0); },
  },
  {
    name: "logs: closing full screen returns focus to the full-screen button",
    run: async (p) => { await appLog(p); await p.getByRole("button", { name: /full screen/ }).click(); await expect(p.getByRole("dialog")).toBeVisible(); await p.keyboard.press("Escape"); await pause(p); expect(await focusedName(p)).toMatch(/full screen/); },
  },
  {
    name: "logs: a task that has not run shows a Log tab saying there is no log yet",
    run: async (p) => { await app(p, item("running", "/nodes/verification?sel=verification.review.automated_review&tab=log")); await expect(p.getByRole("tab", { name: "Log" }).or(p.getByRole("button", { name: "Log", exact: true })).first()).toBeVisible({ timeout: 2000 }); await expect(p.getByText(/No log (yet|for this attempt)/)).toBeVisible(); },
  },

  {
    name: "gate: the gate pane offers Approve and Reject side by side",
    run: async (p) => { await app(p, item("needs-gate", "/nodes/final_review")); await expect(p.getByRole("button", { name: /^Approve/ }).first()).toBeVisible(); await expect(p.getByRole("button", { name: /^Reject/ }).first()).toBeVisible({ timeout: 2000 }); },
  },

  // V2.1
  {
    name: "escalate: the card's Escalate stays off until the note says what to look at [V2.1]",
    run: async (p) => {
      await app(p, item("failed"));
      await p.locator(".item-main-action").hover(); await pause(p, 400);
      await p.getByRole("menuitem", { name: /^Escalate/ }).click(); await pause(p, 400);
      const card = p.getByRole("dialog").last();
      const go = card.getByRole("button", { name: /^Escalate$/ });
      await expect(go).toBeDisabled({ timeout: 2000 });
      await card.getByRole("textbox").first().fill("look at the race"); await pause(p, 300);
      await expect(go).toBeEnabled();
    },
  },
  {
    name: "logs: source chips (sys, stdout, agent, tool), follow and copy head the log [V2.1]",
    run: async (p) => {
      await app(p, item("running", "/nodes/verification?sel=verification.review.code_review&tab=log"), { ready: (pg) => pg.getByLabel("Log lines").first().waitFor() });
      for (const c of ["sys", "stdout", "agent", "tool", "copy"]) await expect(p.getByRole("button", { name: c, exact: true }).first()).toBeVisible();
      await expect(p.getByText("follow", { exact: true }).first()).toBeVisible();
    },
  },

  {
    name: "graph: a mouse click on a visible node of the item page's chain selects it",
    run: async (p) => { await app(p, item("running")); await clickNodeByMouse(p, p.locator(".graph-node")); },
  },
  {
    name: "graph: a mouse click on a visible node of the Chains editor's canvas selects it",
    run: async (p) => { await app(p, "/templates/chains/default"); await pause(p, 600); await clickNodeByMouse(p, p.locator(".graph-node")); },
  },
  {
    name: "graph: a mouse click on a visible node of the new-item draft page selects it",
    run: async (p) => {
      await app(p, `/work-items/new?${new URLSearchParams({ repo: "/Users/dev/code/kraft-plugins", chain: "default", title: "Design the caching layer" })}`, { ready: (pg) => pg.getByText(/nodes run ·/).first().waitFor() });
      await pause(p, 500); await clickNodeByMouse(p, p.locator(".graph-node"));
    },
  },
  {
    name: "gate: clicking a gate's auto_review opens its own task pane with attempts and Log, and no Skip or Retry",
    run: async (p) => {
      await app(p, item("needs-gate", "/nodes/final_review"), { tweak: withReviewer });
      await p.getByRole("button", { name: /auto_review/ }).first().click(); await pause(p, 600);
      expect(p.url()).toContain("sel=final_review.auto_review");
      await expect(p.getByRole("tab", { name: "Log" }).first()).toBeVisible();
      await expect(p.getByRole("button", { name: /^(Skip|Retry)/ })).toHaveCount(0);
    },
  },
  // What a card or a stop says
  {
    name: "item header: Mark complete says what stops and what stays, and names beads only when the item has some [decided]",
    run: async (p) => {
      const open = async (tweak?: (S: Scenario) => void) => {
        await app(p, item("running"), { tweak }); await p.locator(".item-main-action").hover(); await pause(p, 400);
        await p.getByRole("menuitem", { name: /^Mark complete/ }).click(); await pause(p, 500);
        return p.getByRole("dialog").last();
      };
      const card = await open();
      await expect(card.locator("dt", { hasText: /^stops now$/ })).toBeVisible();
      await expect(card.locator("dt", { hasText: /^keeps$/ })).toBeVisible();
      await expect(card.locator("dt", { hasText: /^beads$/ })).toHaveCount(0);
      await expect((await open(withBeads("Kraft-1", "Kraft-2"))).locator("dd", { hasText: "2 stay open" })).toBeVisible();
    },
  },
  {
    name: "item header: the Escalate card says what \"Start a new thread\" changes, from the threads the item has [decided]",
    run: async (p) => {
      await app(p, item("failed"), { tweak: withEscalationThread(2) });
      await p.locator(".item-main-action").hover(); await pause(p, 400);
      await p.getByRole("menuitem", { name: /^Escalate/ }).click(); await pause(p, 400);
      const card = p.getByRole("dialog").last();
      await expect(card.getByText("continues thread 1 (turn 3)", { exact: false })).toBeVisible();
      await card.getByRole("checkbox", { name: /new thread/i }).check();
      await expect(card.getByText("starts thread 2", { exact: false })).toBeVisible();
    },
  },
  {
    name: "item page: a link opens a node on the pass it names, and the pickers keep the pass and the round in the address [decided]",
    run: async (p) => {
      const query = () => new URL(p.url()).search;
      // The node ran twice; a link from a phone names the earlier pass.
      await app(p, item("running", "/nodes/verification?pass=1"), { tweak: withScopes });
      await expect(p.getByRole("button", { name: /^pass 1 of 2$/ })).toBeVisible();
      await p.getByRole("button", { name: /latest ↩/ }).click();
      await expect(p.getByRole("button", { name: "pass 2 of 2 · latest" })).toBeVisible();
      expect(query()).toBe("");
      await p.getByRole("button", { name: /^round \d/ }).click();
      await p.getByRole("menuitemradio", { name: /^Round 1/ }).click();
      await expect.poll(query).toBe("?round=1");
      // Picking is not a step in history: Back leaves the node, it does not walk the picks.
      await p.goBack();
      await expect.poll(() => new URL(p.url()).pathname).not.toMatch(/nodes\/verification/);
    },
  },
  {
    name: "item page: a link opens a node on the round it names [decided]",
    run: async (p) => {
      await app(p, item("running", "/nodes/verification?round=1"), { tweak: withScopes });
      await expect(p.getByRole("button", { name: /^round 1( of \d+)?$/ })).toBeVisible();
      await p.getByRole("button", { name: /latest ↩/ }).click();
      await expect(p.getByRole("button", { name: /^round 2.* · latest$/ })).toBeVisible();
      expect(new URL(p.url()).search).toBe("");
    },
  },
  {
    name: "item page: Recent says whether a fix round committed a change [decided]",
    run: async (p) => {
      await app(p, item("running"), { tweak: withFixRoundOutcome(true) });
      await expect(p.getByText("fix loop round 2 · the fix committed a change")).toBeVisible();
      await app(p, item("running"), { tweak: withFixRoundOutcome(false) });
      await expect(p.getByText("fix loop round 2 · the fix changed nothing")).toBeVisible();
      await app(p, item("running"), { tweak: withFixRoundOutcome(undefined) });
      await expect(p.getByText(/^verification · fix loop round 2$/)).toBeVisible();
    },
  },
  {
    name: "item page: a finished item's Recent reads its gate and its end once each (CG-4)",
    run: async (p) => {
      await app(p, item("done"));
      await expect(p.getByText("final_review · approved by you")).toBeVisible();
      await expect(p.getByText(/^final_review finished/)).toHaveCount(0);
      await expect(p.getByText(/^release finished/)).toHaveCount(1);
    },
  },
  {
    name: "gate: the gate pane reports the test result, naming the red scopes [decided]",
    run: async (p) => {
      await app(p, item("needs-gate", "?sel=final_review"), { tweak: withTestResult(3, 0) });
      await expect(p.getByText("✓ 3 scopes")).toBeVisible();
      await app(p, item("needs-gate", "?sel=final_review"), { tweak: withTestResult(3, 1) });
      await expect(p.getByText(/✗ 1 of 3 scopes/)).toBeVisible();
      await expect(p.getByRole("link", { name: "tests/s0" })).toBeVisible();
    },
  },
  {
    name: "stop: a refused forge login says which command to run; a plain failure does not [decided]",
    run: async (p) => {
      await app(p, item("failed"), { tweak: withStop("infra", { cause: "forge_auth" }) });
      await expect(p.getByText("gh auth login")).toBeVisible();
      await expect(p.getByRole("button", { name: /^Retry/ }).first()).toBeVisible();
      await app(p, item("failed"), { tweak: withStop("infra", { cause: "stranded" }) });
      await expect(p.getByText("gh auth login")).toHaveCount(0);
    },
  },
  {
    name: "stop: the failed card says \"tests passing\" only when every scope passed [decided]",
    run: async (p) => {
      await app(p, item("failed"), { tweak: withFailedTests(3, 0) });
      await expect(p.getByText(/tests passing/)).toBeVisible();
      await app(p, item("failed"), { tweak: withFailedTests(3, 1) });
      await expect(p.getByText(/tests passing/)).toHaveCount(0);
    },
  },
  {
    name: "stop: a time-capped item names its limit and offers Raise cap [decided]",
    run: async (p) => {
      await app(p, item("capped"), { tweak: withCapLimit });
      await expect(p.getByText("Running time hit its 8h cap.")).toBeVisible();
      await expect(p.getByRole("button", { name: "Raise cap" }).first()).toBeVisible();
    },
  },
];

contract(ROWS);
