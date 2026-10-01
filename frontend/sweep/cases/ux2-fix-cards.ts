import { ng, settle, type Case } from "../cellKit";

// Kraft-9d8b2.42 (R76) item 5: Access as AreaAccess draws it, each section a card.
export const cells: Case[] = [
  { screen: "access-cards", variant: "add-host", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: async (c) => { await ng(c, "/settings/access", {}); await c.page.getByRole("button", { name: "add", exact: true }).click(); await settle(c.page, 300); } },
  { screen: "access-cards", variant: "password-edit", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: async (c) => { await ng(c, "/settings/access", {}); await c.page.getByRole("button", { name: "Change", exact: true }).click(); await settle(c.page, 300); } },
];
