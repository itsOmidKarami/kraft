import type { KraftEvent } from "./types";

/** The two states Kraft is blocked on a person. Anything else gets muted
 *  within a week, and a muted channel is the same as no channel. Shared
 *  between here and NotifyPage.tsx's webhook "what notifies" section. */
export const NOTIFY_EVENTS: { id: string; label: string }[] = [
  { id: "gate_requested", label: "a decision is waiting" },
  { id: "work_item_needs_human", label: "stopped — gate wait or cap breach" },
];

const STORAGE_KEY = "kraft.browserNotify.enabled";

/** Per-device by design, not synced — someone with Kraft open on a phone
 *  and a laptop wants each device to independently notify only when *that*
 *  device's tab is hidden, not a shared on/off switch. */
export function isEnabled(): boolean {
  return localStorage.getItem(STORAGE_KEY) === "1";
}

export function setEnabled(next: boolean): void {
  localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
}

export async function requestPermission(): Promise<NotificationPermission> {
  if (typeof Notification === "undefined") return "denied";
  return Notification.requestPermission();
}

export function maybeNotify(ev: KraftEvent, title: string): void {
  if (!isEnabled()) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (!document.hidden) return;
  const event = NOTIFY_EVENTS.find((e) => e.id === ev.type);
  if (!event) return;

  const n = new Notification(title, { body: event.label, tag: ev.work_item_id, icon: "/icon.svg" });
  n.onclick = () => {
    window.focus();
    location.href = "/work-items/" + ev.work_item_id;
  };
}
