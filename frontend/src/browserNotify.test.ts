import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KraftEvent } from "./types";
import { isEnabled, maybeNotify, requestPermission, setEnabled } from "./browserNotify";

const ev = (type: string, over: Partial<KraftEvent> = {}): KraftEvent =>
  ({ seq: 1, work_item_id: "w1", type, payload: {}, created_at: "2026-09-14T00:00:00Z", ...over }) as KraftEvent;

class FakeNotification {
  static permission: NotificationPermission = "granted";
  static requestPermission = vi.fn(async () => FakeNotification.permission);
  static instances: FakeNotification[] = [];
  onclick: (() => void) | null = null;
  constructor(
    public title: string,
    public options: NotificationOptions,
  ) {
    FakeNotification.instances.push(this);
  }
}

beforeEach(() => {
  localStorage.clear();
  FakeNotification.permission = "granted";
  FakeNotification.instances = [];
  FakeNotification.requestPermission.mockClear();
  vi.stubGlobal("Notification", FakeNotification as unknown as typeof Notification);
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("isEnabled/setEnabled", () => {
  it("defaults to disabled and persists via localStorage", () => {
    expect(isEnabled()).toBe(false);
    setEnabled(true);
    expect(isEnabled()).toBe(true);
    setEnabled(false);
    expect(isEnabled()).toBe(false);
  });
});

describe("requestPermission", () => {
  it("delegates to Notification.requestPermission", async () => {
    await requestPermission();
    expect(FakeNotification.requestPermission).toHaveBeenCalled();
  });
});

describe("maybeNotify", () => {
  it("no-ops when disabled", () => {
    setEnabled(false);
    maybeNotify(ev("gate_requested"), "My Item");
    expect(FakeNotification.instances).toHaveLength(0);
  });

  it("no-ops when the tab is visible", () => {
    setEnabled(true);
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    maybeNotify(ev("gate_requested"), "My Item");
    expect(FakeNotification.instances).toHaveLength(0);
  });

  it("no-ops when permission is not granted", () => {
    setEnabled(true);
    FakeNotification.permission = "denied";
    maybeNotify(ev("gate_requested"), "My Item");
    expect(FakeNotification.instances).toHaveLength(0);
  });

  it("no-ops for non-matching event types", () => {
    setEnabled(true);
    maybeNotify(ev("node_started"), "My Item");
    expect(FakeNotification.instances).toHaveLength(0);
  });

  it("fires for gate_requested when enabled, hidden, and granted", () => {
    setEnabled(true);
    maybeNotify(ev("gate_requested", { work_item_id: "w42" }), "My Item");
    expect(FakeNotification.instances).toHaveLength(1);
    const n = FakeNotification.instances[0];
    expect(n.title).toBe("My Item");
    expect(n.options.tag).toBe("w42");
    expect(n.options.body).toContain("decision is waiting");
    expect(n.options.icon).toBe("/icon.svg");
  });

  it("fires for work_item_needs_human", () => {
    setEnabled(true);
    maybeNotify(ev("work_item_needs_human"), "My Item");
    expect(FakeNotification.instances).toHaveLength(1);
  });
});
