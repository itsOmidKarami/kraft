import type { Page } from "@playwright/test";
import type { DisplayState, Scenario } from "./fixtures";
import type { MockOptions } from "./mockApi";
import { NG_NOW } from "./ngItems";

/** What every sweep/cases/<wave>.flows.ts imports; interactions.spec.ts imports it too. */

// `kbd` / `keyboard`: the step is driven from the keyboard, so the focus-ring
// check judges whatever holds focus, not only a :focus-visible element.
export type Step = { name: string; run: (p: Page, S: Scenario) => Promise<void>; wait?: number; kbd?: true };
export interface Flow { name: string; data?: "default" | "long"; state?: DisplayState; widths: number[]; start: (p: Page, S: Scenario) => Promise<void>; steps: Step[]; keyboard?: true; mock?: MockOptions }

export const settle = (p: Page, ms = 400) => p.waitForTimeout(ms);
// UX V2 shell flows. Assertions throw inside a step, which the manifest records as that step's error, so flow-completes fails on them.
export const ng = (url: string) => async (p: Page) => { await p.goto(url); await p.locator("main h1").first().waitFor({ timeout: 8000 }); await settle(p, 600); };
// ux2-W5: the item page for one of ngItems.ts's scenarios, the clock fixed.
export const ngItem = (sc: string) => async (p: Page, S: Scenario) => { await p.clock.setFixedTime(new Date(NG_NOW)); await ng(`/work-items/${S.ng[sc]}`)(p); };
// W10: the Chains editor on the mock's real draft answers.
export const chains = (key: string) => async (p: Page) => { await p.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned")); await p.goto(`/templates/chains/${key}`); await p.locator(".canvas").first().waitFor({ timeout: 8000 }); await settle(p, 700); };
