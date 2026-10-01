import { expect, type Page, type Request } from "@playwright/test";
import { settle, type Flow } from "../flowKit";
import { NG_NOW } from "../ngItems";

const seen = (p: Page) => { const s: string[] = []; p.on("request", (r: Request) => { if (r.method() !== "GET") s.push(`${r.method()} ${new URL(r.url()).pathname.replace(/^\/api/, "")}`); }); return s; };

export const flows: Flow[] = [
  // The phone's Raise budget on a policy budget_usd stop: dollars, PATCH the policy, then retry, never /budget/raise.
  { name: "phone-raise-policy-budget", widths: [390], start: async (p, S) => {
    const b = S.bundles[S.ng.capped].item;
    b.stop = { ...b.stop, kind: "budget", reason: "budget_usd reached: $5.00 spent in the work item, cap $5.00.", limit: { path: "", key: "budget_usd", value: 5, maximum: 25 } };
    await p.clock.setFixedTime(new Date(NG_NOW));
    await p.goto(`/work-items/${S.ng.capped}`);
    await p.locator("main h1").first().waitFor({ timeout: 8000 });
    await settle(p, 600);
  }, steps: [
    { name: "raise-budget-opens-the-dollar-sheet", run: async (p) => { await p.getByRole("button", { name: "Raise budget", exact: true }).click(); await expect(p.getByRole("dialog", { name: "Raise the budget cap" })).toBeVisible(); await expect(p.getByRole("button", { name: /\+\$5/ })).toHaveCount(0); } },
    { name: "above-the-maximum-is-refused-in-place", run: async (p) => { const box = p.getByRole("textbox", { name: "Raise the budget cap" }); await box.fill("30"); await p.getByRole("button", { name: "Save & retry" }).click(); await expect(p.getByRole("alert")).toContainText("The policy maximum is $25."); } },
    { name: "a-figure-patches-the-policy-then-retries", run: async (p) => {
      const sent = seen(p);
      await p.getByRole("textbox", { name: "Raise the budget cap" }).fill("12.5");
      await p.getByRole("button", { name: "Save & retry" }).click();
      await expect.poll(() => sent.filter((s) => !s.includes("/budget/raise")).length).toBeGreaterThanOrEqual(2);
      expect(sent.some((s) => s.includes("/budget/raise"))).toBe(false);
      expect(sent[0]).toMatch(/^PATCH \/work-items\//);
      expect(sent[1]).toMatch(/^POST \/work-items\/.+\/retry$/);
    } },
  ] },
];
