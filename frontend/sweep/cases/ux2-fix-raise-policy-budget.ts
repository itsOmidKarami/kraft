import type { Page } from "@playwright/test";
import { NG_NOW } from "../ngItems";
import { ngItem, settle, type Case, type Ctx } from "../cellKit";

const LIMIT = { path: "", key: "budget_usd", value: 5, maximum: 25 };

/** The `capped` item stopped on a policy budget_usd: a budget stop that names its limit. */
function policyBudget(c: Ctx) {
  const b = (c.S.bundles as any)[c.S.ng.capped].item;
  b.stop = { ...b.stop, kind: "budget", reason: "budget_usd reached: $5.00 spent in the work item, cap $5.00.", limit: LIMIT };
  b.budget_cap = { cap_usd: 5, source: "policy", spent_usd: 5 };
}
const desktop = (c: Ctx, then?: (p: Page) => Promise<void>) => {
  policyBudget(c);
  return ngItem(c, "capped", { then: async (p) => { await p.getByRole("status").filter({ hasText: "budget_usd reached" }).waitFor({ timeout: 4000 }); if (then) await then(p); } });
};
const openEditor = async (p: Page) => { await p.getByRole("button", { name: "Raise cap" }).click(); await p.getByRole("dialog", { name: "Raise budget cap" }).waitFor({ timeout: 4000 }); await settle(p, 300); };

async function phone(c: Ctx, then?: (p: Page) => Promise<void>) {
  policyBudget(c);
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await c.page.goto(`/work-items/${c.S.ng.capped}`);
  await c.page.locator("main h1").first().waitFor({ timeout: 8000 });
  await settle(c.page, 700);
  if (then) { await then(c.page); await settle(c.page, 400); }
}

export const cells: Case[] = [
  // The banner and its dollar editor (desktop), for a stop a policy budget_usd made.
  { screen: "ng-item-budget-policy", variant: "banner", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => desktop(c) },
  { screen: "ng-item-budget-policy", variant: "editor", data: "default", widths: [768, 1280], shells: [{ mode: "light" }], run: (c) => desktop(c, openEditor) },
  // The phone's Raise budget lands on the dollar sheet, not the +$5 sheet.
  { screen: "ng-phone-item-budget-policy", variant: "sheet", data: "default", widths: [390], noLight: true, run: (c) => phone(c, async (p) => { await p.getByRole("button", { name: "Raise budget", exact: true }).click(); await p.getByRole("dialog", { name: "Raise the budget cap" }).waitFor({ timeout: 4000 }); }) },
];
