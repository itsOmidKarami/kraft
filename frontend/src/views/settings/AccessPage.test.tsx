import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { access, renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · access (5e)", () => {
  it("switches the bind and sets a password", async () => {
    const put = vi.spyOn(api, "putAccess").mockResolvedValue({ ...access, bind: "0.0.0.0" });
    renderAt("/settings/access");
    await screen.findByRole("heading", { name: "Access" });

    await userEvent.click(screen.getByRole("radio", { name: /0\.0\.0\.0/ }));
    expect(put).toHaveBeenCalledWith({ bind: "0.0.0.0" });

    await userEvent.type(screen.getByLabelText("Set a password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenLastCalledWith({ password: "hunter2" });
  });

  it("lists sessions and revokes one", async () => {
    vi.spyOn(api, "getAuthSessions").mockResolvedValue({
      sessions: [
        {
          id: "abc",
          label: "Firefox",
          ip: "192.168.1.9",
          created_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
          expires_at: new Date().toISOString(),
          current: true,
        },
      ],
    });
    const revoke = vi.spyOn(api, "revokeSession").mockResolvedValue(undefined);
    renderAt("/settings/access");
    expect(await screen.findByText("Firefox")).toBeInTheDocument();
    expect(screen.getByText("current")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^session /i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Revoke" }));
    // this is the session doing the asking — confirm says so before it lands
    expect(revoke).not.toHaveBeenCalled();
    expect(screen.getByText(/signs you out here/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: "Revoke" }));
    expect(revoke).toHaveBeenCalledWith("abc");
  });

  it("reports a save only once the new state has been read back", async () => {
    // Same network-like gap as the templates page: `putAccess` commits
    // quickly, `getAccess` reads it back more slowly. "saved" is the signal
    // that the page is settled, so it must not appear while the page still
    // renders pre-save state.
    const backend = { ...access };
    vi.spyOn(api, "getAccess").mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ ...backend }), 40)),
    );
    vi.spyOn(api, "putAccess").mockImplementation(
      (body) =>
        new Promise((resolve) =>
          setTimeout(() => {
            Object.assign(backend, body, { password_set: true });
            resolve({ ...backend });
          }, 5),
        ),
    );
    renderAt("/settings/access");

    await userEvent.type(await screen.findByLabelText("Set a password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("saved");
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });
});
