import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as browserNotify from "../../browserNotify";
import type { Notify } from "../../types";
import { HeaderActionsHost } from "../shell/HeaderActions";
import { NotifyPage } from "./NotifyPage";

// Every test runs on a clock held at NOW (see beforeEach), so the last send
// reads "2m ago" however long the file has been running.
const NOW = Date.parse("2026-09-30T12:00:00Z");
const BASE: Notify = { enabled: true, url_set: true, base_url: "http://192.168.1.20:8765", events: ["gate_requested", "work_item_needs_human"], last_test: { at: new Date(NOW - 120_000).toISOString(), status: 200, ms: 184, error: null } };

function Page() {
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <>
      <div ref={setHost} />
      <HeaderActionsHost.Provider value={host}><NotifyPage /></HeaderActionsHost.Provider>
    </>
  );
}

function setup(over: Partial<Notify> = {}) {
  const served = { ...BASE, ...over };
  vi.spyOn(api, "getNotify").mockResolvedValue(served);
  const put = vi.spyOn(api, "putNotify").mockImplementation(async (b) => ({ ...served, ...b, url_set: b.url === "" ? false : served.url_set || !!b.url }) as Notify);
  render(<Page />);
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

const card = (name: string) => screen.getByRole("button", { name }).closest(".nt-card") as HTMLElement;
const open = async (name: string) => userEvent.click(await screen.findByRole("button", { name }));

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ng NotifyPage", () => {
  it("draws each channel as a card: status, event chips, last sent, a test button", async () => {
    stubNotification("default");
    setup();
    await screen.findByRole("heading", { name: "Notifications" });
    const webhook = within(card("Webhook"));
    expect(webhook.getByText("active")).toBeInTheDocument();
    expect(webhook.getByText("gate_requested")).toBeInTheDocument();
    expect(webhook.getByText("work_item_needs_human")).toBeInTheDocument();
    expect(webhook.getByText(/last sent 2m ago · 200 OK/)).toBeInTheDocument();
    expect(webhook.getByRole("button", { name: /Send a test/ })).toBeEnabled();
    const browser = within(card("This browser"));
    expect(browser.getByText("inactive")).toBeInTheDocument();
    expect(browser.getByText("not asked yet")).toBeInTheDocument();
    expect(browser.getByRole("button", { name: /Send a test/ })).toBeDisabled();
  });

  it("calls a webhook with no URL inactive, however its switch stands, and cannot test it", async () => {
    stubNotification("default");
    setup({ url_set: false, last_test: null });
    const webhook = within(await screen.findByRole("button", { name: "Webhook" }).then(() => card("Webhook")));
    expect(webhook.getByText("inactive")).toBeInTheDocument();
    expect(webhook.getByText("nothing can be sent")).toBeInTheDocument();
    expect(webhook.getByRole("button", { name: /Send a test/ })).toBeDisabled();
  });

  it("opens a card in the pane on Config, and the floor on Overview", async () => {
    stubNotification("default");
    setup();
    const floor = await screen.findByRole("complementary", { name: "notifications pane" });
    expect(within(floor).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "YAML"]);
    await open("Webhook");
    const pane = screen.getByRole("complementary", { name: "Webhook pane" });
    expect(within(pane).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Config", "YAML"]);
    expect(within(pane).getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(within(pane).getByRole("button", { name: "Notifications" }));
    await open("This browser");
    expect(within(screen.getByRole("complementary", { name: "This browser pane" })).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Config"]);
    await userEvent.click(within(screen.getByRole("complementary", { name: "This browser pane" })).getByRole("button", { name: "Notifications" }));
    expect(screen.getByRole("complementary", { name: "notifications pane" })).toBeInTheDocument();
  });

  it("sends only the key each control changes", async () => {
    stubNotification("default");
    const put = setup();
    await open("Webhook");
    await userEvent.click(screen.getByRole("switch", { name: "Webhook notifications" }));
    await userEvent.click(screen.getByRole("switch", { name: "gate_requested (webhook)" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls.map((c) => c[0])).toEqual([{ enabled: false }, { events: ["work_item_needs_human"] }]);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("sends the typed URL and link back only on Save, and Discard sends nothing", async () => {
    stubNotification("default");
    const put = setup();
    await open("Webhook");
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://ntfy.sh/topic");
    const linkBack = screen.getByLabelText("Link back");
    await userEvent.clear(linkBack);
    await userEvent.type(linkBack, "http://10.0.0.5:8765");
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(save);
    await waitFor(() => expect(put).toHaveBeenCalledWith({ url: "https://ntfy.sh/topic", base_url: "http://10.0.0.5:8765" }));
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://other.example/x");
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("");
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("shows the server's refusal inline and keeps what was typed", async () => {
    stubNotification("default");
    const put = setup();
    put.mockRejectedValueOnce(new Error("url: must be an http(s) URL"));
    await open("Webhook");
    await userEvent.type(screen.getByLabelText("Webhook URL"), "ntfy");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("url: must be an http(s) URL");
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("ntfy");
  });

  it("puts the switch back and shows the reason when the server refuses it", async () => {
    stubNotification("default");
    const put = setup({ enabled: false, url_set: false });
    put.mockRejectedValueOnce(new Error("set a webhook URL before enabling notifications"));
    await open("Webhook");
    await userEvent.click(screen.getByRole("switch", { name: "Webhook notifications" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("set a webhook URL before enabling");
    expect(screen.getByRole("switch", { name: "Webhook notifications" })).toHaveAttribute("aria-checked", "false");
  });

  it("shows notify.yaml from the state under the header's YAML button, never the URL", async () => {
    stubNotification("default");
    setup();
    await screen.findByRole("heading", { name: "Notifications" });
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    const yaml = screen.getByLabelText("notify.yaml");
    expect(yaml.textContent).toBe("enabled: true\n# webhook_url: 0600, never shown\nbase_url: http://192.168.1.20:8765\nevents:\n  - gate_requested\n  - work_item_needs_human");
    expect(screen.getByRole("button", { name: "YAML" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.queryByLabelText("notify.yaml")).toBeNull();
  });

  it("says no URL is set in the YAML when none is", async () => {
    stubNotification("default");
    setup({ url_set: false, enabled: false, base_url: null, events: [] });
    await userEvent.click(await screen.findByRole("button", { name: "YAML" }));
    expect(screen.getByLabelText("notify.yaml").textContent).toBe('enabled: false\n# webhook_url: not set\nbase_url: ""\nevents:');
  });

  it("asks for browser permission only from its own button", async () => {
    const { N } = stubNotification("default") as { N: { requestPermission: ReturnType<typeof vi.fn> } };
    setup();
    await open("This browser");
    await userEvent.click(screen.getByRole("switch", { name: "Alerts on this device" }));
    expect(browserNotify.isEnabled()).toBe(true);
    expect(N.requestPermission).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Allow notifications" }));
    expect(N.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Allow notifications" })).toBeNull());
    expect(screen.getByText(/Allowed in this browser/)).toBeInTheDocument();
    expect(within(card("This browser")).getByText("active")).toBeInTheDocument();
  });

  it("explains a denied browser and offers no retry; a missing one disables the switch", async () => {
    stubNotification("denied");
    setup();
    await open("This browser");
    expect(within(screen.getByRole("complementary", { name: "This browser pane" })).getByText(/Blocked in the browser's site settings/)).toBeInTheDocument();
    expect(within(card("This browser")).getByText("blocked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow notifications" })).toBeNull();
    expect(within(card("This browser")).getByRole("button", { name: /Send a test/ })).toBeDisabled();
  });

  it("disables browser alerts where there are no notifications", async () => {
    stubNotification(null);
    setup();
    await open("This browser");
    expect(screen.getByRole("switch", { name: "Alerts on this device" })).toBeDisabled();
    expect(within(screen.getByRole("complementary", { name: "This browser pane" })).getByText(/no notifications/)).toBeInTheDocument();
  });

  it("keeps the browser's own events apart from the webhook's, in this browser", async () => {
    stubNotification("granted");
    const put = setup();
    await open("This browser");
    await userEvent.click(screen.getByRole("switch", { name: "gate_requested (this browser)" }));
    expect(browserNotify.enabledEvents()).toEqual(["work_item_needs_human"]);
    expect(within(card("This browser")).queryByText("gate_requested")).toBeNull();
    expect(within(card("Webhook")).getByText("gate_requested")).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  it("shows a test alert on this device when allowed", async () => {
    const { shown } = stubNotification("granted") as { shown: { title: string }[] };
    setup();
    await open("This browser");
    await userEvent.click(screen.getByRole("switch", { name: "Alerts on this device" }));
    await userEvent.click(within(card("This browser")).getByRole("button", { name: /Send a test/ }));
    expect(shown.map((s) => s.title)).toEqual(["Test alert"]);
    expect(within(card("This browser")).getByText(/shown just now/)).toBeInTheDocument();
    expect(within(card("Webhook")).queryByText(/shown just now/)).toBeNull();
  });

  it("maps the webhook test to delivered, or failed with its reason", async () => {
    stubNotification("default");
    setup();
    await open("Webhook");
    expect(await screen.findByText(/last delivered 2m ago · 200 · 184 ms/)).toBeInTheDocument();
    vi.spyOn(api, "testNotify").mockResolvedValue({ at: new Date().toISOString(), status: null, ms: null, error: "connection refused" });
    await userEvent.click(within(card("Webhook")).getByRole("button", { name: /Send a test/ }));
    expect(await screen.findByText(/last attempt just now, failed: connection refused/)).toBeInTheDocument();
    expect(within(card("Webhook")).getByText(/failed/)).toBeInTheDocument();
    expect(within(card("This browser")).queryByText(/failed/)).toBeNull();
  });

  it("builds the preview with the same function the real notification uses", async () => {
    stubNotification("default");
    const text = vi.spyOn(browserNotify, "notificationText");
    setup();
    const pane = await screen.findByRole("complementary", { name: "notifications pane" });
    expect(text).toHaveBeenCalledWith(browserNotify.NOTIFY_EVENTS[0], expect.any(String));
    expect(within(pane).getByRole("img", { name: /a decision is waiting/ })).toBeInTheDocument();
  });

  it("links the preview to a work item id in Kraft's own format, 32 hex characters", async () => {
    stubNotification("default");
    setup();
    const pane = await screen.findByRole("complementary", { name: "notifications pane" });
    expect(within(pane).getByRole("img", { name: /a decision is waiting/ })).toHaveTextContent(/http:\/\/192\.168\.1\.20:8765\/work-items\/[0-9a-f]{32}$/);
  });

  it("clears the webhook URL only after a confirmation", async () => {
    stubNotification("default");
    const put = setup();
    await open("Webhook");
    await userEvent.click(screen.getByRole("button", { name: "Clear URL" }));
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(screen.getAllByRole("button", { name: "Clear URL" })[1]);
    await waitFor(() => expect(put).toHaveBeenCalledWith({ url: "" }));
  });
});
