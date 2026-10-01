import { ng, settle, type Case } from "../cellKit";

const at = (url: string, ready: string, mock: Case["mock"]): Pick<Case, "mock" | "run"> => ({
  mock,
  run: async (c) => { await ng(c, url, {}, { side: "pinned" }); await c.page.locator(ready).first().waitFor({ timeout: 8000 }); await settle(c.page, 400); },
});

// Kraft-9d8b2.42 (R76): the areas load with their pane on its rail (the designs' paneOpen:false).
export const cells: Case[] = [
  { screen: "repos-first", variant: "list", data: "default", widths: [1280], shells: [{ mode: "light" }], ...at("/templates/repos", ".rp-row", { areas: "default" }) },
  { screen: "intake-first", variant: "default", data: "default", widths: [1280], shells: [{ mode: "light" }], ...at("/settings/auto-intake", ".ink-card", { areas: "default" }) },
  { screen: "harnesses-first", variant: "floor", data: "default", widths: [1280], shells: [{ mode: "light" }], ...at("/templates/harnesses", ".hn-lane", { harnesses: "floor" }) },
];
