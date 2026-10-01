import { expect } from "@playwright/test";
import { ng, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // ux2-W16 A: restart is explicit, behind a confirm, and the page waits for the server to come back.
  { name: "apply-restart", widths: [1280], keyboard: true, mock: { apply: "restart" }, start: ng("/settings/appearance"), steps: [
    { name: "chip-names-the-restart", run: async (p) => { await expect(p.getByRole("button", { name: "Restart needed, 1" })).toBeVisible(); } },
    { name: "popover-offers-restart", run: async (p) => { await p.getByRole("button", { name: "Restart needed, 1" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Restart Kraft" })).toBeVisible(); }, kbd: true },
    { name: "confirm-first", run: async (p) => {
      await p.getByRole("button", { name: "Restart Kraft" }).click(); await expect(p.getByRole("dialog", { name: "Restart Kraft?" })).toBeVisible();
      expect(await p.evaluate(() => performance.getEntriesByType("resource").filter((e) => e.name.includes("/api/apply/restart")).length)).toBe(0);
    } },
    { name: "restarting", run: async (p) => { await p.getByRole("button", { name: "Restart", exact: true }).click(); await expect(p.getByRole("dialog", { name: "Restarting Kraft…" })).toBeVisible(); } },
    { name: "back-and-cleared", run: async (p) => { await expect(p.getByRole("dialog")).toHaveCount(0, { timeout: 15000 }); await expect(p.getByRole("button", { name: /Restart needed/ })).toHaveCount(0); } },
  ] },
];
