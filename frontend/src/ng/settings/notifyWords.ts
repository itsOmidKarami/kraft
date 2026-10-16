import type { Notify } from "../../types";

/** What Settings › Notifications says on both layouts: the channels' names, whether each is on, and the permission lines. */
export type Permission = NotificationPermission | "unsupported";
export const permissionNow = (): Permission => (typeof Notification === "undefined" ? "unsupported" : Notification.permission);

export const PERMISSION_TEXT: Record<Permission, string> = {
  granted: "Allowed in this browser.",
  default: "This browser has not been asked yet.",
  denied: "Blocked in the browser's site settings. Allow notifications for this site there to use it.",
  unsupported: "This browser has no notifications.",
};
export const PERMISSION_SHORT: Record<Permission, string> = { granted: "allowed", default: "not asked yet", denied: "blocked", unsupported: "not supported" };

/** The browser pane opens on this: what the permission is, and the one thing to do about it. `steps` is for a blocked one. */
export const PERMISSION_LEAD: Record<Permission, { title: string; text: string; steps?: string[] }> = {
  granted: { title: "Notifications are allowed", text: "This browser can show Kraft's alerts." },
  default: { title: "Allow notifications", text: "This browser has not been asked yet. It asks once, when you press the button." },
  denied: {
    title: "Notifications are blocked",
    text: "Kraft cannot ask again. Unblock it in this browser:",
    steps: ["Open the site settings from the icon beside the address.", "Set Notifications to Allow.", "Come back and check again."],
  },
  unsupported: { title: "This browser can't show notifications", text: "Use the webhook, or open Kraft in a browser that can." },
};
export const ALLOW = "Allow notifications";
export const CHECK_AGAIN = "Check again";
/** Why the browser's own controls are disabled: the one line for the switch and the test, on both layouts. */
export const BROWSER_WHY: Record<Permission, string> = { granted: "", default: "Allow notifications first.", denied: "Unblock notifications first.", unsupported: "Not supported in this browser." };
export const BROWSER_OFF = "Turn on alerts on this device first.";
/** An event's words with a capital, for a row that leads with its meaning. */
export const eventMeaning = (label: string): string => label.charAt(0).toUpperCase() + label.slice(1);

export const CHANNELS = { webhook: "Webhook", browser: "This browser" } as const;
export type Channel = keyof typeof CHANNELS;

/** The state word under a channel, and the one on the status row. */
export const channelState = (active: boolean): "active" | "inactive" => (active ? "active" : "inactive");
export const webhookActive = (n: Pick<Notify, "enabled" | "url_set">): boolean => n.enabled && n.url_set;
export const browserActive = (on: boolean, permission: Permission): boolean => on && permission === "granted";

export const CLEAR_URL = "Clear URL";
export const CLEAR_URL_TITLE = "Clear the webhook URL?";
export const CLEAR_URL_TEXT = "Removes the stored webhook and stops all sends.";
export const PROGRESS_NOTE = "Progress events are deliberately not offered. A notifier that fires on progress gets muted, and a muted channel is the same as no channel at all.";
/** An event id in words, for a chip or a list: the one map both layouts read. An id it does not know reads as itself. */
export const EVENT_WORDS: Record<string, string> = { gate_requested: "Decision waiting", work_item_needs_human: "Stopped for you", work_item_storage_held: "Storage held" };
export const eventWords = (id: string): string => EVENT_WORDS[id] ?? id;
export const evLine = (ids: string[]): string => (ids.length ? ids.map(eventWords).join(", ") : "nothing");

/** The address a preview notification links to: the configured link back, else the address this page was opened at. */
export const previewAddress = (link: string): string => (link || location.origin).replace(/\/$/, "");

/** What a failed load says on both layouts, over its retry button. */
export const RETRY = "Try again";
export const toggled = (ids: string[], id: string): string[] => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);
