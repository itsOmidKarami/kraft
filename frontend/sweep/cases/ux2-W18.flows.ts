import { expect } from "@playwright/test";
import { settle, type Flow } from "../flowKit";

// ux2-W18: addresses from before the cutover land on their pages. The new UI
// was served under a prefix, kept by main.tsx's redirect for bookmarks; the
// shipped UI's settings pages and item hashes are aliases (ng/shell/aliases.tsx).
const landed = async (p: import("@playwright/test").Page) => { await p.locator("main h1").first().waitFor({ timeout: 8000 }); await settle(p, 400); };

export const flows: Flow[] = [
  { name: "old-addresses", widths: [1280], start: async (p) => { await p.goto("/"); await landed(p); }, steps: [
    { name: "prefixed-bookmark", run: async (p, S) => { await p.goto(`/ng/work-items/${S.ng.running}?sel=verification`); await expect(p).toHaveURL(new RegExp(`/work-items/${S.ng.running}\\?sel=verification$`)); await landed(p); } },
    { name: "shipped-settings-page", run: async (p) => { await p.goto("/settings/chains"); await expect(p).toHaveURL(/\/templates\/chains/); await p.locator(".canvas, .tpl-note").first().waitFor({ timeout: 8000 }); await settle(p, 400); } },
    { name: "shipped-item-hash", run: async (p, S) => { await p.goto(`/work-items/${S.ng.running}#node=verification&tab=tasks`); await expect(p).toHaveURL(new RegExp(`/work-items/${S.ng.running}/nodes/verification$`)); await landed(p); } },
  ] },
];
