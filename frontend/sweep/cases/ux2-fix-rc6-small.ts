import { ngItem, settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // Kraft-9d8b2.51: an ended item's review is read only: "Read only" in the bar, no file comment button, no Finish review.
  { screen: "ng-review-readonly", variant: "ended", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngItem(c, "done", { tail: "/review" });
    await c.page.getByRole("group", { name: /^Lines of / }).first().waitFor({ timeout: 6000 }); await settle(c.page, 300);
  } },
  // Kraft-9d8b2.52: an item filed with no chain names the chain it runs in the pane heading.
  { screen: "ng-item-unnamed-chain", variant: "running", data: "default", widths: [1280], run: async (c) => {
    (c.S.bundles[c.S.ng.running].item as { chain_template: string | null }).chain_template = null;
    await ngItem(c, "running");
  } },
];
