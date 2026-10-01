import { ng, settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // W3: the graph components' gallery, fed by fixtures.
  { screen: "ng-gallery", variant: "default", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], fullPage: true, run: (c) => ng(c, "/ng/_gallery", {}) },
  { screen: "ng-gallery", variant: "mono", data: "default", widths: [1280], fullPage: true, run: (c) => ng(c, "/ng/_gallery", { colour_amount: "mono" }) },
  { screen: "ng-gallery", variant: "full", data: "default", widths: [1280], fullPage: true, run: (c) => ng(c, "/ng/_gallery", { accent: "violet", colour_amount: "full" }) },
  // Under 1024 the pane overlays the canvas (R7).
  { screen: "ng-gallery", variant: "overlay", data: "default", widths: [768], fullPage: true, run: (c) => ng(c, "/ng/_gallery", {}) },
  // R6 by keyboard: Tab lands on the workbench's current node, → moves to the next, Enter opens its pane with the ring on it.
  { screen: "ng-gallery", variant: "keyboard", data: "default", widths: [1280], run: async (c) => {
    await ng(c, "/ng/_gallery", {});
    const bench = c.page.locator('section[aria-labelledby="g-pane"] [role="group"]').first();
    await bench.scrollIntoViewIfNeeded();
    await c.page.keyboard.press("Shift");
    await bench.locator('.graph-node[tabindex="0"]').focus();
    await c.page.keyboard.press("ArrowRight");
    await c.page.keyboard.press("Enter");
    await settle(c.page, 300);
  } },
];
