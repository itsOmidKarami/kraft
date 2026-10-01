import { ng, settle, type Case } from "../cellKit";
import { NG_NOW } from "../ngItems";

const open = (permission: string, then: (c: Parameters<Case["run"]>[0]) => Promise<void>): Case["run"] => async (c) => {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await c.page.addInitScript((p) => { (window as any).Notification = class { static permission = p; static requestPermission = async () => p; }; }, permission);
  await ng(c, "/settings/notifications", {});
  await then(c);
  await settle(c.page, 300);
};

// Kraft-9d8b2.42 (R76): Notifications as AreaNotifications draws it: channel cards, the selected one in the pane.
export const cells: Case[] = [
  { screen: "notifications-pane", variant: "webhook-config", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("default", async (c) => { await c.page.getByRole("button", { name: "Webhook", exact: true }).click(); }) },
  { screen: "notifications-pane", variant: "webhook-overview", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("default", async (c) => { await c.page.getByRole("button", { name: "Webhook", exact: true }).click(); await c.page.getByRole("tab", { name: "Overview" }).click(); }) },
  { screen: "notifications-pane", variant: "webhook-dirty", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("default", async (c) => { await c.page.getByRole("button", { name: "Webhook", exact: true }).click(); await c.page.getByLabel("Webhook URL").fill("https://ntfy.sh/kraft"); }) },
  { screen: "notifications-pane", variant: "browser-config", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("granted", async (c) => { await c.page.getByRole("button", { name: "This browser", exact: true }).click(); }) },
  { screen: "notifications-pane", variant: "browser-denied", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("denied", async (c) => { await c.page.getByRole("button", { name: "This browser", exact: true }).click(); }) },
  { screen: "notifications-pane", variant: "yaml", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("default", async (c) => { await c.page.getByRole("button", { name: "YAML", exact: true }).first().click(); }) },
  { screen: "notifications-pane", variant: "collapsed", data: "default", widths: [1280], shells: [{ mode: "light" }], mock: { apply: "none" }, run: open("default", async (c) => { await c.page.getByRole("button", { name: "Collapse pane" }).click(); }) },
];
