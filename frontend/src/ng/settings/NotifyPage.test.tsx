import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as browserNotify from "../../browserNotify";
import type { Notify } from "../../types";
import { NotifyPage } from "./NotifyPage";

const BASE: Notify = { enabled: true, url_set: true, base_url: "http://192.168.1.20:8765", events: ["gate_requested", "work_item_needs_human"], last_test: { at: new Date(Date.now() - 120_000).toISOString(), status: 200, ms: 184, error: null } };

function setup(over: Partial<Notify> = {}) {
  const served = { ...BASE, ...over };
  vi.spyOn(api, "getNotify").mockResolvedValue(served);
  const put = vi.spyOn(api, "putNotify").mockImplementation(async (b) => ({ ...served, ...b, url_set: b.url === "" ? false : served.url_set || !!b.url }) as Notify);
  render(<NotifyPage />);
  return put;
}

function stubNotification(permission: NotificationPermission | null) {
  if (permission === null) return vi.stubGlobal("Notification", undefined);
  const shown: { title: string; opts?: NotificationOptions }[] = [];
  class N {
    static permission = permission;
    static requestPermission = vi.fn(async () => {
      N.permission = "granted";
      return "granted" as NotificationPermission;
    });
    constructor(title: string, opts?: NotificationOptions) {
      shown.push({ title, opts });
    }
  }
  vi.stubGlobal("Notification", N);
  return { N, shown };
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ng NotifyPage", () => {
  it("sends only the key each control changes", async () => {
    stubNotification("default");
    const put = setup();
    await screen.findByRole("heading", { name: "Notifications" });
    await userEvent.click(screen.getByRole("switch", { name: "Webhook notifications" }));
    await userEvent.click(screen.getByRole("switch", { name: "gate_requested" }));
    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://ntfy.sh/topic{Enter}");
    const link = screen.getByLabelText("Link back");
    await userEvent.clear(link);
    await userEvent.type(link, "http://10.0.0.5:8765");
    await userEvent.tab();
    await waitFor(() => expect(put).toHaveBeenCalledTimes(4));
    expect(put.mock.calls.map((c) => c[0])).toEqual([{ enabled: false }, { events: ["work_item_needs_human"] }, { url: "https://ntfy.sh/topic" }, { base_url: "http://10.0.0.5:8765" }]);
  });

  it("puts the switch back and shows the reason when the server refuses", async () => {
    stubNotification("default");
    const put = setup({ enabled: false, url_set: false });
    put.mockRejectedValueOnce(new Error("set a webhook URL before enabling notifications"));
    await userEvent.click(await screen.findByRole("switch", { name: "Webhook notifications" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("set a webhook URL before enabling");
    expect(screen.getByRole("switch", { name: "Webhook notifications" })).toHaveAttribute("aria-checked", "false");
  });

  it("asks for browser permission only from its own button", async () => {
    const { N } = stubNotification("default") as { N: { requestPermission: ReturnType<typeof vi.fn> } };
    setup();
    await userEvent.click(await screen.findByRole("switch", { name: "Alerts on this device" }));
    expect(browserNotify.isEnabled()).toBe(true);
    expect(N.requestPermission).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Allow notifications" }));
    expect(N.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Allow notifications" })).toBeNull());
    expect(screen.getByText(/Allowed in this browser/)).toBeInTheDocument();
  });

  it("explains a denied browser and offers no retry; a missing one disables the switch", async () => {
    stubNotification("denied");
    setup();
    expect(await screen.findByText(/Blocked in the browser's site settings/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow notifications" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Send a test" })[1]).toBeDisabled();
  });

  it("disables browser alerts where there are no notifications", async () => {
    stubNotification(null);
    setup();
    expect(await screen.findByRole("switch", { name: "Alerts on this device" })).toBeDisabled();
    expect(screen.getByText(/no notifications/)).toBeInTheDocument();
  });

  it("shows a test alert on this device when allowed", async () => {
    const { shown } = stubNotification("granted") as { shown: { title: string }[] };
    setup();
    const tests = await screen.findAllByRole("button", { name: "Send a test" });
    await userEvent.click(tests[1]);
    expect(shown).toHaveLength(1);
    expect(shown[0].title).toBe("Test alert");
  });

  it("maps the webhook test to delivered, or failed with its reason", async () => {
    stubNotification("default");
    setup();
    expect(await screen.findByText(/last delivered 2m ago · 200 · 184 ms/)).toBeInTheDocument();
    vi.spyOn(api, "testNotify").mockResolvedValue({ at: new Date().toISOString(), status: null, ms: null, error: "connection refused" });
    await userEvent.click(screen.getAllByRole("button", { name: "Send a test" })[0]);
    expect(await screen.findByText(/failed: connection refused/)).toBeInTheDocument();
  });

  it("builds the preview with the same function the real notification uses", async () => {
    stubNotification("default");
    const text = vi.spyOn(browserNotify, "notificationText");
    setup();
    await screen.findByRole("heading", { name: "Preview" });
    expect(text).toHaveBeenCalledWith(browserNotify.NOTIFY_EVENTS[0], expect.any(String));
    await userEvent.click(screen.getByRole("radio", { name: "work_item_needs_human" }));
    expect(screen.getByRole("img", { name: /stopped — gate wait or cap breach/ })).toBeInTheDocument();
  });

  it("clears the webhook URL only after a confirmation", async () => {
    stubNotification("default");
    const put = setup();
    await userEvent.click(await screen.findByRole("button", { name: "Clear URL" }));
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(screen.getAllByRole("button", { name: "Clear URL" })[1]);
    await waitFor(() => expect(put).toHaveBeenCalledWith({ url: "" }));
  });
});
