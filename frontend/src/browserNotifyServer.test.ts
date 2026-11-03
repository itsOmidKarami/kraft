import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notifyServer, setEnabled } from "./browserNotify";

class FakeNotification {
  static permission: NotificationPermission = "granted";
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
  vi.stubGlobal("Notification", FakeNotification as unknown as typeof Notification);
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
  setEnabled(true);
});
afterEach(() => vi.unstubAllGlobals());

describe("notifyServer", () => {
  it("says what needs attention, once per signature", () => {
    notifyServer("aaa", 2);
    notifyServer("aaa", 2);
    expect(FakeNotification.instances).toHaveLength(1);
    const n = FakeNotification.instances[0]!;
    expect(n.title).toBe("Kraft's own chains need attention");
    expect(n.options.body).toBe("2 item(s). Run kraft admin doctor.");
    expect(n.options.tag).toBe("kraft-default");
    notifyServer("bbb", 1);
    expect(FakeNotification.instances).toHaveLength(2);
  });

  it.each([
    ["the device switch is off", () => setEnabled(false)],
    ["permission is not granted", () => { FakeNotification.permission = "denied"; }],
    ["the tab is visible", () => Object.defineProperty(document, "hidden", { value: false, configurable: true })],
    ["the board already showed this state", () => localStorage.setItem("kraft.kraftDefault.notified", "aaa")],
  ])("is silent when %s", (_why, arrange) => {
    arrange();
    notifyServer("aaa", 1);
    expect(FakeNotification.instances).toHaveLength(0);
  });
});
