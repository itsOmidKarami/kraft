import { ngItem, type Case } from "../cellKit";

export const cells: Case[] = [
  // Kraft-9d8b2.28: paused with no agent task to steer, so the card offers plain Resume only.
  { screen: "ng-item", variant: "paused-not-steerable", data: "default", widths: [1280], run: async (c) => {
    const it = c.S.bundles[c.S.ng.paused].item;
    it.steerable = false;
    try { await ngItem(c, "paused"); } finally { it.steerable = true; }
  } },
];
