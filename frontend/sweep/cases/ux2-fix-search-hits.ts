import { searchFor } from "../fixtures";
import { NG_NOW } from "../ngItems";
import { ng, ngItem, settle, type Case, type Ctx } from "../cellKit";

/** ⌘K, a query, then the first document row, answered with a document that belongs to no work item (links: []). */
async function freeDocument(c: Ctx) {
  const all = Object.values(c.S.docs).flat();
  await c.page.route("**/api/search*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...searchFor("cache", all, c.S.variant), results: searchFor("cache", all, c.S.variant).results.map((x) => ({ ...x, links: [] })) }) }));
  await ng(c, "/ng/settings/policy", {}, { side: "pinned" });
  await c.page.keyboard.press("Control+k");
  await c.page.getByRole("combobox", { name: /search/i }).fill("cache");
  await c.page.getByRole("option").first().waitFor({ timeout: 4000 });
  await c.page.getByRole("listbox").locator('[data-section="docs"] [role="option"]').first().click();
  await c.page.getByRole("dialog").waitFor({ timeout: 4000 });
  await c.page.getByText("Findings").first().waitFor({ timeout: 4000 });
  await settle(c.page, 400);
}

export const cells: Case[] = [
  // The ⌘K document hit with no work item: the document in a dialog over the page.
  { screen: "ng-search-doc", variant: "free", data: "default", widths: [1280], shells: [{ mode: "light" }], run: freeDocument },
  { screen: "ng-search-doc", variant: "free", data: "default", widths: [768, 1024], run: freeDocument },
  { screen: "ng-search-doc", variant: "free-long", data: "long", widths: [1280], run: freeDocument },

  // The ⌘K document hit with a work item: the item page with that document open (`?doc=`).
  ...([["default", "default", [1280], [{ mode: "light" } as const]], ["default", "default", [768, 1024], undefined], ["long", "long", [1280], undefined]] as const).map<Case>(([variant, data, widths, shells]) => ({
    screen: "ng-item-doc", variant: variant === "long" ? "long" : "open", data, widths: [...widths], shells: shells ? [...shells] : undefined,
    run: async (c) => {
      const id = c.S.ng.running;
      const d = Object.values(c.S.docs).flat()[0];
      c.S.docs[id] = [{ ...d, work_item_id: id, node_id: "verification", hook_point: "on.test.run", attempt: 1 }];
      await ngItem(c, "running", { tail: `?doc=${encodeURIComponent(d.document_id)}` });
      await c.page.getByRole("dialog").waitFor({ timeout: 4000 });
      await c.page.getByText("Findings").first().waitFor({ timeout: 4000 });
    },
  })),

  // The ⌘K bead hit: the draft item page with the bead's title and "Implements <id>".
  ...([[1280, [{ mode: "light" } as const]], [768, undefined]] as const).map<Case>(([w, shells]) => ({
    screen: "ng-draft-item", variant: "bead", data: "default", widths: [w], shells: shells ? [...shells] : undefined, mock: { ngBoard: true },
    run: async (c) => {
      await c.page.clock.setFixedTime(new Date(NG_NOW));
      const q = new URLSearchParams({ repo: "/Users/dev/code/kraft-plugins", chain: "default", title: "Gate card findings list pushes the split off-screen", bead: "kraft-a4js" });
      await ng(c, `/ng/work-items/new?${q}`, {}, { side: "pinned" });
      await c.page.getByText(/nodes run ·/).first().waitFor();
      await c.page.getByText("kraft-a4js").waitFor();
      await settle(c.page, 500);
    },
  })),
];
