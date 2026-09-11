import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings → Notifications", () => {
  it("never renders the webhook URL back into the DOM", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: true,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    expect(await screen.findByText(/a webhook URL is set/i)).toBeInTheDocument();
    const field = screen.getByLabelText(/webhook url/i) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(field).toHaveAttribute("type", "password");
  });

  it("saves a URL without re-sending it on the next unrelated save", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    const put = vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");

    const field = (await screen.findByLabelText(/webhook url/i)) as HTMLInputElement;
    await userEvent.type(field, "https://ntfy.sh/my-topic");
    const channelSection = field.closest("section") as HTMLElement;
    await userEvent.click(within(channelSection).getByRole("button", { name: /^save$/i }));
    expect(put).toHaveBeenNthCalledWith(1, { url: "https://ntfy.sh/my-topic" });
    expect(field.value).toBe("");

    // unrelated save: toggling an event switch must not resend the (now
    // cleared) URL draft.
    await userEvent.click(await screen.findByRole("switch", { name: "gate_requested" }));
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls[1][0]).not.toHaveProperty("url");
    expect(put.mock.calls[1][0]).toEqual({
      events: ["work_item_needs_human"],
    });
  });

  it("offers clearing the URL as an explicit action", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    const put = vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");
    await userEvent.click(await screen.findByRole("button", { name: /clear/i }));
    expect(put).toHaveBeenCalledWith({ url: "" });
  });

  it("commits both changes when two different event switches are toggled back to back", async () => {
    // A fake backend with a network-like gap: `putNotify` commits quickly,
    // `getNotify` (re-fetched by `reload`) reads it back more slowly. That
    // gap is what the bug depended on — if `busy` clears as soon as the PUT
    // resolves, without waiting for the slower re-fetch to land, a second
    // click in that window reads the still-stale `notify.events` closure
    // and silently drops the first change. If `busy` only clears once
    // `reload()` has actually landed, that window cannot exist.
    const backend = {
      enabled: false,
      url_set: false,
      base_url: null as string | null,
      events: [] as string[],
    };
    vi.spyOn(api, "getNotify").mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ ...backend }), 40)),
    );
    const put = vi.spyOn(api, "putNotify").mockImplementation(
      (body) =>
        new Promise((resolve) =>
          setTimeout(() => {
            Object.assign(backend, body);
            resolve({ ...backend });
          }, 5),
        ),
    );
    renderAt("/settings/notify");

    const gateSwitch = await screen.findByRole("switch", { name: "gate_requested" });
    const humanSwitch = await screen.findByRole("switch", { name: "work_item_needs_human" });

    await userEvent.click(gateSwitch);
    // As soon as the switch is clickable again, click the other one — in
    // the broken version this lands after the PUT but before the re-fetch.
    await waitFor(() => expect(gateSwitch).not.toBeDisabled());
    await userEvent.click(humanSwitch);
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));

    const secondCall = put.mock.calls[1][0] as { events: string[] };
    expect(secondCall.events).toHaveLength(2);
    expect(secondCall.events).toEqual(
      expect.arrayContaining(["gate_requested", "work_item_needs_human"]),
    );
  });

  it("shows a failed enable next to the switch it belongs to, not next to an unrelated save row", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: false,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    vi.spyOn(api, "putNotify").mockRejectedValue(
      new Error("set a webhook URL before enabling notifications"),
    );
    renderAt("/settings/notify");

    const toggle = await screen.findByRole("switch", { name: /notifications/i });
    await userEvent.click(toggle);

    const channelRow = toggle.closest(".save-row") as HTMLElement;
    expect(
      await within(channelRow).findByText(/set a webhook url before enabling notifications/i),
    ).toBeInTheDocument();

    // The original bug report's actual claim: the message must not also
    // show up on the two unrelated SaveRows, which did nothing.
    const channelSection = toggle.closest("section") as HTMLElement;
    const urlRow = within(channelSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(within(urlRow).queryByText(/set a webhook url before enabling notifications/i)).toBeNull();

    const baseSection = screen.getByLabelText(/base url/i).closest("section") as HTMLElement;
    const baseRow = within(baseSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(within(baseRow).queryByText(/set a webhook url before enabling notifications/i)).toBeNull();
  });

  it("shows a failed event-switch save beside that switch, not beside the Channel switch", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: true,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    vi.spyOn(api, "putNotify").mockRejectedValue(new Error("could not save events"));
    renderAt("/settings/notify");

    const eventSwitch = await screen.findByRole("switch", { name: "gate_requested" });
    await userEvent.click(eventSwitch);

    const eventRow = eventSwitch.closest(".save-row") as HTMLElement;
    expect(await within(eventRow).findByText(/could not save events/i)).toBeInTheDocument();

    const channelToggle = screen.getByRole("switch", { name: /notifications/i });
    const channelRow = channelToggle.closest(".save-row") as HTMLElement;
    expect(within(channelRow).queryByText(/could not save events/i)).toBeNull();
    // the Channel switch's own on/off hint must be untouched by someone
    // else's error
    expect(within(channelRow).getByText(/on — Kraft will POST when a run stops/i)).toBeInTheDocument();
  });

  it("shows a successful save's confirmation only on the row that saved", async () => {
    vi.spyOn(api, "getNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: null,
      events: ["gate_requested", "work_item_needs_human"],
    });
    vi.spyOn(api, "putNotify").mockResolvedValue({
      enabled: false,
      url_set: true,
      base_url: "http://192.168.1.20:8765",
      events: ["gate_requested", "work_item_needs_human"],
    });
    renderAt("/settings/notify");

    const baseField = (await screen.findByLabelText(/base url/i)) as HTMLInputElement;
    await userEvent.type(baseField, "http://192.168.1.20:8765");
    const baseSection = baseField.closest("section") as HTMLElement;
    await userEvent.click(within(baseSection).getByRole("button", { name: /^save$/i }));

    const baseRow = within(baseSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(await within(baseRow).findByText(/^saved$/i)).toBeInTheDocument();

    // the unrelated Channel hint and Webhook URL SaveRow must still show
    // their own defaults, not "saved" leaking over from the Base URL save.
    const channelToggle = screen.getByRole("switch", { name: /notifications/i });
    const channelRow = channelToggle.closest(".save-row") as HTMLElement;
    expect(within(channelRow).queryByText(/^saved$/i)).toBeNull();
    expect(within(channelRow).getByText(/off — no outbound traffic/i)).toBeInTheDocument();

    const channelSection = channelToggle.closest("section") as HTMLElement;
    const urlRow = within(channelSection)
      .getByRole("button", { name: /^save$/i })
      .closest(".save-row") as HTMLElement;
    expect(within(urlRow).queryByText(/^saved$/i)).toBeNull();
  });
});
