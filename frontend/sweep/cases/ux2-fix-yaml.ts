import { ng, settle, type Case } from "../cellKit";

const open = (c: Parameters<Case["run"]>[0]) => c.page.getByRole("button", { name: "YAML", exact: true }).first().click();

// Kraft-9d8b2.42 (R76): the header YAML button on Access and Appearance, and Appearance's Overview pane.
export const cells: Case[] = [
  { screen: "access-yaml", variant: "lan", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: async (c) => { await ng(c, "/settings/access", {}); await open(c); await settle(c.page, 300); } },
  { screen: "access-yaml", variant: "restart-pending", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "restart" }, run: async (c) => {
    Object.assign(c.S.settings.access, { port: 9100 });
    c.S.settings.health.bind = c.S.settings.access.bind;
    await ng(c, "/settings/access", {}); await open(c); await settle(c.page, 300);
  } },
  { screen: "appearance-yaml", variant: "yaml", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => { await ng(c, "/settings/appearance", { surface: "slate", accent: "blue" }); await open(c); await settle(c.page, 300); } },
  { screen: "appearance-yaml", variant: "overview", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => { await ng(c, "/settings/appearance", { surface: "slate", accent: "blue" }); await c.page.getByRole("button", { name: "Expand appearance" }).click(); await settle(c.page, 300); } },
];
