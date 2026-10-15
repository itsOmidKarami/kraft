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
    expect(webhook.getByText("Decision waiting")).toBeInTheDocument();
    expect(webhook.getByText("Stopped for you")).toBeInTheDocument();
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
    expect(webhook.getByRole("button", { name: /Send a test/ })).toHaveAccessibleDescription("Set a webhook URL first.");
    expect(webhook.getByText("Set a webhook URL first.")).toHaveClass("is-quiet");
  });

  it("says inactive in the pane too, when the switch is on but the channel cannot send", async () => {
    stubNotification("granted");
    localStorage.setItem("kraft.browserNotify.enabled", "1");
    setup({ url_set: false, last_test: null });
    await open("This browser");
    const browser = screen.getByRole("complementary", { name: "This browser pane" });
    await userEvent.click(within(browser).getByRole("switch", { name: "Alerts on this device" }));
    expect(within(browser).getByRole("switch", { name: "Alerts on this device" })).not.toBeChecked();
    expect(within(browser).getByText("inactive")).toBeInTheDocument();
    expect(within(browser).queryByText("active")).toBeNull();
    await userEvent.click(within(browser).getByRole("button", { name: "Notifications" }));
    await open("Webhook");
    const webhook = screen.getByRole("complementary", { name: "Webhook pane" });
    expect(within(webhook).getByRole("switch", { name: "Webhook notifications" })).toBeChecked();
    expect(within(webhook).getByText("inactive")).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole("switch", { name: "A decision is waiting (webhook)" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls.map((c) => c[0])).toEqual([{ enabled: false }, { events: ["work_item_needs_human"] }]);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("saves the URL and the link back as each field is left, with Enter too, and has no Save button", async () => {
    stubNotification("default");
    const put = setup();
    await open("Webhook");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://ntfy.sh/topic");
    expect(put).not.toHaveBeenCalled();
    const linkBack = screen.getByLabelText("Link back");
    await userEvent.click(linkBack);
    await waitFor(() => expect(put).toHaveBeenCalledWith({ url: "https://ntfy.sh/topic" }));
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("");
    await userEvent.clear(linkBack);
    await userEvent.type(linkBack, "http://10.0.0.5:8765{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ base_url: "http://10.0.0.5:8765" }));
    expect(put).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByLabelText("Webhook URL"));
    await userEvent.click(linkBack);
    expect(put).toHaveBeenCalledTimes(2);
  });

  it("refuses a URL that is not http(s) before sending it, and names why on the field", async () => {
    stubNotification("default");
    const put = setup();
    await open("Webhook");
    await userEvent.type(screen.getByLabelText("Webhook URL"), "ntfy{Enter}");
    expect(put).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Webhook URL")).toBeInvalid();
    expect(screen.getByLabelText("Webhook URL")).toHaveAccessibleDescription("Start it with http:// or https://");
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("ntfy");
  });

  it("shows the server's refusal inline and keeps what was typed", async () => {
    stubNotification("default");
    const put = setup();
    put.mockRejectedValueOnce(new Error("url: host not allowed"));
    await open("Webhook");
    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://bad.example{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("url: host not allowed");
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("https://bad.example");
  });

  it("reads the typed, unsaved URL in the test's reason, and not 'set a URL' again", async () => {
    stubNotification("default");
    setup({ url_set: false, last_test: null });
    await open("Webhook");
    const pane = screen.getByRole("complementary", { name: "Webhook pane" });
    expect(within(pane).getByRole("button", { name: /Send a test/ })).toHaveAccessibleDescription("Set a webhook URL first.");
    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://ntfy.sh/topic");
    expect(within(pane).getByRole("button", { name: /Send a test/ })).toHaveAccessibleDescription("Press Enter to save the URL first.");
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

  it("asks for browser permission only from its own button, which comes before the switch", async () => {
    const { N } = stubNotification("default") as { N: { requestPermission: ReturnType<typeof vi.fn> } };
    setup();
    await open("This browser");
    const allow = screen.getByRole("button", { name: "Allow notifications" });
    const sw = screen.getByRole("switch", { name: "Alerts on this device" });
    expect(allow.compareDocumentPosition(sw) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sw).toBeDisabled();
    expect(sw).toHaveAccessibleDescription(/Allow notifications.*asks? once|has not been asked/);
    expect(within(screen.getByRole("complementary", { name: "This browser pane" })).queryByText("Allow notifications first.")).toBeNull();
    expect(within(card("This browser")).getByText("Allow notifications first.")).toHaveClass("is-quiet");
    expect(N.requestPermission).not.toHaveBeenCalled();
    await userEvent.click(allow);
    expect(N.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Allow notifications" })).toBeNull());
    expect(screen.getByText("Notifications are allowed")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Alerts on this device" })).toBeChecked();
    expect(within(card("This browser")).getByText("active")).toBeInTheDocument();
  });

  it("leaves the channel off when the browser does not grant permission after Allow", async () => {
    const { N } = stubNotification("default") as { N: { permission: string; requestPermission: ReturnType<typeof vi.fn> } };
    N.requestPermission.mockImplementation(async () => { N.permission = "denied"; return "denied"; });
    setup();
    await open("This browser");
    await userEvent.click(screen.getByRole("button", { name: "Allow notifications" }));
    await screen.findByText("Notifications are blocked");
    expect(browserNotify.isEnabled()).toBe(false);
    expect(within(card("This browser")).queryByText("active")).toBeNull();
  });

  it("offers Try again when the settings fail to load, and loads them on the retry", async () => {
    const get = vi.spyOn(api, "getNotify").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(BASE);
    render(<NotifyPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "This browser" })).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("gives a blocked browser its steps and a Check again, with the switch disabled and no retry prompt", async () => {
    const { N } = stubNotification("denied") as { N: { permission: string; requestPermission: ReturnType<typeof vi.fn> } };
    setup();
    await open("This browser");
    const pane = screen.getByRole("complementary", { name: "This browser pane" });
    expect(within(pane).getByText("Notifications are blocked")).toBeInTheDocument();
    expect(within(pane).getByText(/Open the site settings/)).toBeInTheDocument();
    expect(within(card("This browser")).getByText("blocked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow notifications" })).toBeNull();
    expect(within(pane).getByRole("switch", { name: "Alerts on this device" })).toBeDisabled();
    expect(within(card("This browser")).getByRole("button", { name: /Send a test/ })).toBeDisabled();
    N.permission = "granted";
    await userEvent.click(within(pane).getByRole("button", { name: "Check again" }));
    expect(within(pane).getByRole("switch", { name: "Alerts on this device" })).toBeEnabled();
    expect(N.requestPermission).not.toHaveBeenCalled();
  });

  it("says an unsupported browser can't show notifications, with no switch to look off", async () => {
    stubNotification(null);
    setup();
    await open("This browser");
    const pane = screen.getByRole("complementary", { name: "This browser pane" });
    expect(within(pane).getByText("This browser can't show notifications")).toBeInTheDocument();
    expect(within(pane).queryByRole("switch")).toBeNull();
    expect(within(pane).queryByRole("button", { name: /Send a test|Allow notifications|Check again/ })).toBeNull();
  });

  it("keeps the browser's own events apart from the webhook's, in this browser", async () => {
    stubNotification("granted");
    const put = setup();
    await open("This browser");
    await userEvent.click(screen.getByRole("switch", { name: "A decision is waiting (this browser)" }));
    expect(browserNotify.enabledEvents()).toEqual(["work_item_needs_human", "work_item_storage_held"]);
    expect(within(card("This browser")).queryByText("Decision waiting")).toBeNull();
    expect(within(card("Webhook")).getByText("Decision waiting")).toBeInTheDocument();
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

  it("links the preview to a work item id in Kraft's own format, 8 base32 characters", async () => {
    stubNotification("default");
    setup();
    const pane = await screen.findByRole("complementary", { name: "notifications pane" });
    expect(within(pane).getByRole("img", { name: /a decision is waiting/ })).toHaveTextContent(/http:\/\/192\.168\.1\.20:8765\/work-items\/[0-9abcdefghjkmnpqrstvwxyz]{8}$/);
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
