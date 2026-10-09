import type { KraftEvent } from "./types";
import type { EventType } from "./types/vocab.generated";

/** The two states Kraft is blocked on a person. Anything else gets muted
 *  within a week, and a muted channel is the same as no channel. Shared
 *  between here and NotifyPage.tsx's webhook "what notifies" section. */
export const NOTIFY_EVENTS: { id: EventType; label: string }[] = [
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

const EVENTS_KEY = "kraft.browserNotify.events";

/** The events this browser alerts for: both until it is told otherwise, like its switch, kept in this browser only. */
export function enabledEvents(): string[] {
  try {
    const stored = JSON.parse(localStorage.getItem(EVENTS_KEY) ?? "null");
    if (Array.isArray(stored)) return NOTIFY_EVENTS.map((e) => e.id).filter((id) => stored.includes(id));
  } catch {
    // An unreadable value is the default.
  }
  return NOTIFY_EVENTS.map((e) => e.id);
}

export function setEvents(ids: string[]): void {
  localStorage.setItem(EVENTS_KEY, JSON.stringify(ids));
}

export async function requestPermission(): Promise<NotificationPermission> {
  if (typeof Notification === "undefined") return "denied";
  return Notification.requestPermission();
}

/** What a notification says for `event`: the work item's title over the event's own words. */
export function notificationText(event: { label: string }, title: string): { title: string; body: string } {
  return { title, body: event.label };
}

export function maybeNotify(ev: KraftEvent, title: string): void {
  if (!isEnabled()) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (!document.hidden) return;
  const event = NOTIFY_EVENTS.find((e) => e.id === ev.type);
  if (!event || !enabledEvents().includes(event.id)) return;

  const text = notificationText(event, title);
  const n = new Notification(text.title, { body: text.body, tag: ev.work_item_id, icon: "/icon.svg" });
  n.onclick = () => {
    window.focus();
    location.href = "/work-items/" + ev.work_item_id;
  };
}
