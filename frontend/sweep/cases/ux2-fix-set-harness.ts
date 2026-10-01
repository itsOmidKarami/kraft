import { ng, settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // Kraft-9d8b2.15: a harness's own fields are editable; one is changed so the fields draw amber.
  { screen: "harnesses", variant: "harness-edited", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { harnesses: "floor" }, run: async (c) => {
    await ng(c, "/templates/harnesses?harness=claude-sandbox", {});
    await c.page.getByRole("switch", { name: "Disable claude-sandbox" }).click();
    await c.page.getByRole("radio", { name: "max" }).click();
    await c.page.getByRole("switch", { name: "Enable claude-sandbox" }).waitFor(); await settle(c.page, 300);
  } },
];
