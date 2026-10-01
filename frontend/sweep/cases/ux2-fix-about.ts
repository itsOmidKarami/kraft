import { NG_NOW } from "../ngItems";
import { ng, settle, type Case } from "../cellKit";

const instance = (scanMinutesAgo: number | null, errors: string[]): Case["run"] => async (c) => {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  const index = c.S.settings.health.index!;
  index.last_scan_at = scanMinutesAgo === null ? null : new Date(Date.parse(NG_NOW) - scanMinutesAgo * 60_000).toISOString();
  index.errors = errors;
  await ng(c, "/settings/about", {});
  await settle(c.page, 300);
};

// Kraft-9d8b2.42 (R76) and .18: About draws the run directory, the process (pid, uptime) and the search index from /health.
export const cells: Case[] = [
  { screen: "about-instance", variant: "scanned", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { update: "available" }, run: instance(2, []) },
  { screen: "about-instance", variant: "scan-errors", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { update: "available" }, run: instance(null, ["/src/platform: unreadable", "/src/product: unreadable"]) },
];
