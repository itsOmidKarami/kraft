import { ng, settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // W14: Templates › Harnesses. The draft's answers come from sweep/ngHarnesses.ts; the clock is already fixed.
  ...([
    ["floor", "floor", ""], ["harness", "floor", "?harness=claude"], ["harness-lane", "floor", "?harness=claude&lane=strong"],
    ["profile", "floor", "?profile=strong"], ["entry", "floor", "?profile=strong&lane=claude"],
  ] as const).map<Case>(([variant, scenario, q]) => ({
    screen: "ng-harnesses", variant, data: "default", widths: variant === "floor" ? [1280, 1920] : [1280], shells: [{ mode: "light" }], mock: { harnesses: scenario },
    run: (c) => ng(c, `/ng/templates/harnesses${q}`, {}),
  })),
  { screen: "ng-harnesses", variant: "floor-config", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "floor" }, run: async (c) => {
    await ng(c, "/ng/templates/harnesses", {});
    await c.page.getByRole("tab", { name: "Config" }).click(); await settle(c.page, 300);
  } },
  { screen: "ng-harnesses", variant: "problems", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "problems" }, run: (c) => ng(c, "/ng/templates/harnesses", {}) },
  { screen: "ng-harnesses", variant: "problems-profile", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "problems" }, run: (c) => ng(c, "/ng/templates/harnesses?profile=fast", {}) },
  { screen: "ng-harnesses", variant: "review", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { harnesses: "floor" }, run: async (c) => {
    await ng(c, "/ng/templates/harnesses?harness=cursor", {});
    await c.page.getByRole("radio", { name: "Available" }).click();
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await c.page.getByRole("tab", { name: "YAML diff" }).click();
    await c.page.getByText("policy.yaml", { exact: true }).first().waitFor(); await settle(c.page, 300);
  } },
  { screen: "ng-harnesses", variant: "review-problems", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "problems" }, run: async (c) => {
    await ng(c, "/ng/templates/harnesses", {});
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await c.page.getByRole("heading", { name: /^Draft · / }).waitFor(); await settle(c.page, 300);
  } },
  { screen: "ng-harnesses", variant: "yaml", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "floor" }, run: async (c) => {
    await ng(c, "/ng/templates/harnesses?profile=strong", {});
    await c.page.getByRole("button", { name: "Edit in YAML" }).click();
    await c.page.getByRole("textbox", { name: "harnesses.yaml, YAML" }).waitFor();
    await c.page.getByRole("tab", { name: "policy.yaml" }).click(); await settle(c.page, 300);
  } },
  { screen: "ng-harnesses", variant: "empty", data: "default", widths: [1280], mock: { harnesses: "empty" }, run: (c) => ng(c, "/ng/templates/harnesses", {}) },
];
