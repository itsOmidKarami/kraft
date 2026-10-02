import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { Notify } from "../../../types";
import { NotificationChannel, NotificationsList } from "./Notifications";
import { mountAt, where } from "./testkit";

// Every test runs on a clock held at NOW (see beforeEach), so the last send
// reads "2m ago" however long the file has been running.
const NOW = Date.parse("2026-09-30T12:00:00Z");
const BASE: Notify = { enabled: true, url_set: true, base_url: "http://192.168.1.20:8765", events: ["gate_requested", "work_item_needs_human"], last_test: { at: new Date(NOW - 120_000).toISOString(), status: 200, ms: 184, error: null } };

function stubNotification(permission: NotificationPermission | null) {
  if (permission === null) return vi.stubGlobal("Notification", undefined);
  const shown: string[] = [];
  class N {
    static permission = permission;
    static requestPermission = vi.fn(async () => { N.permission = "granted"; return "granted" as NotificationPermission; });
    constructor(title: string) { shown.push(title); }
  }
  vi.stubGlobal("Notification", N);
  return { N, shown };
}
const serve = (over: Partial<Notify> = {}) => {
  const served = { ...BASE, ...over };
  vi.spyOn(api, "getNotify").mockResolvedValue(served);
  return vi.spyOn(api, "putNotify").mockImplementation(async (b) => ({ ...served, ...b, url_set: b.url === "" ? false : served.url_set || !!b.url }) as Notify);
};
const channel = (name: string, tab = "") => mountAt(<NotificationChannel />, `/settings/notifications/${name}${tab}`, "/settings/notifications/:channel");
beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Notifications list (O.2)", () => {
  it("lists the two channels, each active or inactive, and says progress events are not offered", async () => {
    stubNotification("granted");
    localStorage.setItem("kraft.browserNotify.enabled", "1");
    serve();
    mountAt(<NotificationsList />, "/settings/notifications", "/settings/notifications");
    const web = await screen.findByRole("link", { name: /^Webhook/ });
    expect(web).toHaveTextContent("active");
    expect(web).toHaveTextContent("gate_requested, work_item_needs_human");
    expect(web).toHaveAttribute("href", "/settings/notifications/webhook");
    expect(screen.getByRole("link", { name: /^Browser alerts/ })).toHaveTextContent("active");
    expect(screen.getByText(/Progress events are deliberately not offered/)).toBeInTheDocument();
  });

  it("a webhook with no URL is inactive even when enabled; browser alerts need the permission too", async () => {
    stubNotification("denied");
    localStorage.setItem("kraft.browserNotify.enabled", "1");
    serve({ url_set: false });
    mountAt(<NotificationsList />, "/settings/notifications", "/settings/notifications");
    expect(await screen.findByRole("link", { name: /^Webhook/ })).toHaveTextContent("inactive");
    expect(screen.getByRole("link", { name: /^Browser alerts/ })).toHaveTextContent("inactive");
  });
});

describe("Webhook channel (O.2)", () => {
  it("Overview shows the last send and what a notification looks like, with the link back", async () => {
    stubNotification("default");
    serve();
    channel("webhook");
    expect(await screen.findByRole("tab", { name: "Overview", selected: true })).toBeInTheDocument();
    expect(screen.getByText(/delivered 2m ago · 200 · 184 ms/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Preview: Add retry budget to the intake poller\. a decision is waiting/ })).toHaveTextContent(/http:\/\/192\.168\.1\.20:8765\/work-items\/[0-9a-f]{32}$/);
  });

  it("each control sends only its own key", async () => {
    stubNotification("default");
    const put = serve();
    channel("webhook", "?tab=config");
    await userEvent.click(await screen.findByRole("switch", { name: /enabled/ }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ enabled: false }));
    await userEvent.click(screen.getByRole("switch", { name: /gate_requested/ }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ events: ["work_item_needs_human"] }));
    await userEvent.click(screen.getByRole("button", { name: /^link back/ }));
    const box = screen.getByLabelText("Link back", { selector: "input" });
    await userEvent.clear(box);
    await userEvent.type(box, "http://10.0.0.2:8765{Enter}");
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ base_url: "http://10.0.0.2:8765" }));
    expect(put).toHaveBeenCalledTimes(3);
  });

  it("the URL is masked: a password field, never rendered back, saved by the sheet's Set", async () => {
    stubNotification("default");
    const put = serve({ url_set: false });
    channel("webhook", "?tab=config");
    const row = await screen.findByRole("button", { name: /^webhook URL/ });
    expect(row).toHaveTextContent("not set");
    await userEvent.click(row);
    const box = screen.getByLabelText("Webhook URL", { selector: "input" });
    expect(box).toHaveAttribute("type", "password");
    await userEvent.type(box, "https://ntfy.sh/s3cret-token{Enter}");
    await waitFor(() => expect(put).toHaveBeenCalledWith({ url: "https://ntfy.sh/s3cret-token" }));
    expect(await screen.findByRole("button", { name: /^webhook URL/ })).toHaveTextContent("•••••••• set");
    expect(document.body.textContent).not.toContain("s3cret-token");
  });

  it("Remove URL asks first, then sends an empty url", async () => {
    stubNotification("default");
    const put = serve();
    channel("webhook", "?tab=config");
    await userEvent.click(await screen.findByRole("button", { name: "Remove URL" }));
    expect(put).not.toHaveBeenCalled();
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove URL" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ url: "" }));
  });

  it("a refused write says why under its row, and the switch stays where the server has it", async () => {
    stubNotification("default");
    serve();
    vi.spyOn(api, "putNotify").mockRejectedValue(new Error("the webhook host is not allowed"));
    channel("webhook", "?tab=config");
    const sw = await screen.findByRole("switch", { name: /enabled/ });
    await userEvent.click(sw);
    expect(await screen.findByRole("alert")).toHaveTextContent("the webhook host is not allowed");
    expect(screen.getByRole("switch", { name: /enabled/ })).toBeChecked();
  });

  it("Send a test posts once and shows the result; it says what to do first when it cannot", async () => {
    stubNotification("default");
    serve();
    const test = vi.spyOn(api, "testNotify").mockResolvedValue({ at: new Date().toISOString(), status: 502, ms: 40, error: "bad gateway" });
    channel("webhook", "?tab=config");
    await userEvent.click(await screen.findByRole("button", { name: /^Send a test/ }));
    await waitFor(() => expect(test).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: /^Send a test/ })).toHaveTextContent("failed just now: bad gateway");
  });

  it("Send a test is disabled with the reason when the webhook is off or has no URL", async () => {
    stubNotification("default");
    serve({ url_set: false });
    channel("webhook", "?tab=config");
    const row = await screen.findByRole("button", { name: /^Send a test/ });
    expect(row).toBeDisabled();
    expect(row).toHaveTextContent("Turn on the webhook and set a URL first.");
  });

  it("YAML shows the effective values and never the URL", async () => {
    stubNotification("default");
    serve();
    channel("webhook", "?yaml=1");
    const text = (await screen.findByText(/^enabled: true/)).textContent!;
    expect(text).toContain(`url: "(set, never shown)"`);
    expect(text).toContain("events: [gate_requested, work_item_needs_human]");
  });

  it("an unknown channel says so", async () => {
    stubNotification("default");
    serve();
    channel("pager");
    expect(await screen.findByText("There is no channel pager.")).toBeInTheDocument();
  });

  it("the tab is in the address", async () => {
    stubNotification("default");
    serve();
    channel("webhook");
    await userEvent.click(await screen.findByRole("tab", { name: "Config" }));
    expect(where()).toBe("/settings/notifications/webhook?tab=config");
  });
});

describe("Browser alerts channel (O.2)", () => {
  it("never asks for permission by itself: only the button does, once", async () => {
    const { N } = stubNotification("default") as { N: { requestPermission: ReturnType<typeof vi.fn> } };
    serve();
    channel("browser", "?tab=config");
    const ask = await screen.findByRole("button", { name: /^Allow notifications/ });
    expect(N.requestPermission).not.toHaveBeenCalled();
    await userEvent.click(ask);
    await waitFor(() => expect(N.requestPermission).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Allow notifications/ })).toBeNull());
  });

  it("states what each permission means", async () => {
    for (const [perm, text] of [["granted", /Allowed in this browser/], ["denied", /Blocked in the browser's site settings/], ["default", /has not been asked yet/]] as const) {
      stubNotification(perm);
      serve();
      const { unmount } = (() => { channel("browser"); return { unmount: () => document.body.replaceChildren() }; })();
      expect(await screen.findByText(text)).toBeInTheDocument();
      unmount();
    }
  });

  it("a denied permission leaves the test disabled and never asks", async () => {
    const { N } = stubNotification("denied") as { N: { requestPermission: ReturnType<typeof vi.fn> } };
    serve();
    channel("browser", "?tab=config");
    expect(await screen.findByRole("button", { name: /^Send a test/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /^Allow notifications/ })).toBeNull();
    expect(N.requestPermission).not.toHaveBeenCalled();
  });

  it("with no Notification at all, says so and disables the switch", async () => {
    stubNotification(null);
    serve();
    channel("browser", "?tab=config");
    expect(await screen.findByRole("switch", { name: /Alerts on this device/ })).toBeDisabled();
    expect(screen.getByText(/This browser has no notifications/)).toBeInTheDocument();
  });

  it("the switch is kept in this browser only, and a granted test shows an alert", async () => {
    const { shown } = stubNotification("granted") as { shown: string[] };
    const put = serve();
    channel("browser", "?tab=config");
    await userEvent.click(await screen.findByRole("switch", { name: /Alerts on this device/ }));
    expect(localStorage.getItem("kraft.browserNotify.enabled")).toBe("1");
    await userEvent.click(screen.getByRole("button", { name: /^Send a test/ }));
    expect(shown).toEqual(["Test alert"]);
    expect(put).not.toHaveBeenCalled();
  });
});
