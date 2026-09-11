import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { policy, renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · policy (5d)", () => {
  it("edits a cap and saves every counter together", async () => {
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const attempts = await screen.findByLabelText("verify_fix_loop attempts");
    await userEvent.clear(attempts);
    await userEvent.type(attempts, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith({
      loops: { verify_fix_loop: { attempts: 5, wall_clock_s: 3600 } },
      default: { attempts: 3, wall_clock_s: 3600 },
    });
  });

  it("clearing a budget field saves null, not zero", async () => {
    vi.spyOn(api, "getPolicy").mockResolvedValue({
      ...policy,
      budget: { work_item_usd: 20, daily_usd: null },
    });
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const field = await screen.findByLabelText("work item budget");
    expect(field).toHaveValue(20);
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({ budget: { work_item_usd: null, daily_usd: null } }),
    );
  });

  it("states the one-task overshoot", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByText(/overshoot/i)).toBeInTheDocument();
  });
});
