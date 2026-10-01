import { ng, settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // Kraft-9d8b2.17: the lanes canvas scrolled to its end, the pane docked: every lane shows whole, left of the pane.
  { screen: "ng-harnesses-scrolled", variant: "floor", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "floor" }, run: async (c) => {
    await ng(c, "/ng/templates/harnesses", {});
    await c.page.evaluate(() => { const el = document.querySelector(".hn-canvas") as HTMLElement; el.scrollLeft = el.scrollWidth; });
    await settle(c.page, 300);
  } },
];
