import { settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // UX V2 under /ng. At 390 the phone redirect lands on the shipped board; the entry's `url` records where.
  { screen: "ng-shell", variant: "board-stub", data: "default", widths: [1280, 390], run: async (c) => { await c.page.goto("/ng"); await c.page.locator('h1, [data-testid="board-card"], .board-row').first().waitFor({ timeout: 8000 }); await settle(c.page); } },
];
