import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { hooks, renderAt, setupSettingsMocks } from "./testing";

function setPhoneWidth(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · plugins (5c)", () => {
  it("toggles the steerable flag from the list and reports what the save broke", async () => {
    const put = vi.spyOn(api, "putRegistry").mockResolvedValue({
      hooks,
      invalid_templates: { hotfix: "hook(s) missing" },
    });
    renderAt("/settings/plugins");
    const toggle = await screen.findByRole("switch", {
      name: "steerable: on.implementation.start",
    });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith({
      ...hooks,
      "on.implementation.start": { ...hooks["on.implementation.start"], interactive: true },
    });
    expect(await screen.findByText(/now unresolvable: hotfix/)).toBeInTheDocument();
  });

  it("selecting a hook shows its binding detail with kind chips including forge", async () => {
    renderAt("/settings/plugins");
    await userEvent.click(await screen.findByText("on.mr.open"));
    expect(screen.getByRole("button", { name: "forge" })).toBeInTheDocument();
  });

  it("steerable is disabled with a reason for a subprocess hook", async () => {
    renderAt("/settings/plugins");
    await userEvent.click(await screen.findByText("on.test.run"));
    const steer = screen.getByRole("switch", { name: "steerable" });
    expect(steer).toBeDisabled();
    expect(steer).toHaveAttribute("title", expect.stringContaining("nothing to steer"));
  });

  it("timeout is hidden for a builtin hook", async () => {
    renderAt("/settings/plugins");
    await userEvent.click((await screen.findAllByText("on.env.prepare"))[0]);
    expect(screen.queryByLabelText("timeout")).toBeNull();
  });

  it("per-repo overrides and timeout are read-only (Kraft-vpyi: not read by the executor)", async () => {
    renderAt("/settings/plugins");
    await userEvent.click(await screen.findByText("on.test.run"));
    expect(await screen.findByRole("switch", { name: /repo-a/i })).toBeDisabled();
  });

  it("Dry run is disabled with a reason", async () => {
    renderAt("/settings/plugins");
    await userEvent.click(await screen.findByText("on.test.run"));
    expect(screen.getByRole("button", { name: /dry run/i })).toBeDisabled();
  });

  it("LAST RUNS lists recent sessions for the selected hook", async () => {
    vi.spyOn(api, "getHookRuns").mockResolvedValue({
      runs: [
        {
          work_item_id: "wi_1",
          node_id: "verify",
          round: 2,
          status: "done",
          wall_ms: 14200,
          created_at: "2026-09-01T00:00:00Z",
        },
      ],
    });
    renderAt("/settings/plugins");
    await userEvent.click(await screen.findByText("on.test.run"));
    expect(await screen.findByText(/wi_1/)).toBeInTheDocument();
  });
});

describe("phone", () => {
  beforeEach(() => setPhoneWidth(true));
  afterEach(() => vi.unstubAllGlobals());

  it("shows the hook list with a filter input and no binding detail", async () => {
    renderAt("/settings/plugins");
    expect(await screen.findByPlaceholderText(/filter hooks/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("timeout")).toBeNull();
  });

  it("opening a hook shows its binding detail with a back link to Plugins", async () => {
    renderAt("/settings/plugins?hook=on.test.run");
    expect(await screen.findByText("Plugins")).toBeInTheDocument(); // the PhoneHeader back label
    expect(screen.getByLabelText(/command/i)).toBeInTheDocument();
  });
});
