import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · auto-intake", () => {
  it("saves the whole poller config in one PUT and reports it applied", async () => {
    const put = vi.spyOn(api, "putIntake").mockImplementation(async (body) => body);
    renderAt("/settings/intake");
    await screen.findByRole("heading", { name: "Auto-intake" });

    // nothing is sent until Save: the poller restarts on write, so a
    // half-edited form must not bounce it once per keystroke.
    await userEvent.click(screen.getByRole("switch", { name: "Auto-intake" }));
    const interval = screen.getByLabelText("poll interval");
    await userEvent.clear(interval);
    await userEvent.type(interval, "60");
    await userEvent.click(await screen.findByRole("checkbox", { name: /repo-a/ }));
    expect(put).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][0]).toEqual({
      enabled: true,
      interval_s: 60,
      max_concurrent: 1,
      priority_ceiling: 2,
      repos: ["/repo-a"],
    });
    expect(await screen.findByText("saved")).toBeInTheDocument();
  });

  it("shows the server's rejection rather than pretending the save landed", async () => {
    vi.spyOn(api, "putIntake").mockRejectedValue(new Error("interval_s: too small"));
    renderAt("/settings/intake");
    await screen.findByRole("heading", { name: "Auto-intake" });
    await userEvent.click(screen.getByRole("switch", { name: "Auto-intake" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("interval_s: too small")).toBeInTheDocument();
  });
});
