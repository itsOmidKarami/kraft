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
export const evLine = (ids: string[]): string => (ids.length ? ids.join(", ") : "nothing");
export const toggled = (ids: string[], id: string): string[] => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);
