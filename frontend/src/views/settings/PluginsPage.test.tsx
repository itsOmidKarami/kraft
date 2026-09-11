import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { hooks, renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · plugins (5c)", () => {
  it("toggles the steerable flag and reports what the save broke", async () => {
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
});
