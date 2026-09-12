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
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({
        loops: { verify_fix_loop: { attempts: 5, wall_clock_s: 3600 } },
        default: { attempts: 3, wall_clock_s: 3600 },
      }),
    );
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

  it("shows the concurrency section wired to policy.max_concurrent", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByLabelText(/max concurrent/i)).toHaveValue(3);
  });

  it("editing max_concurrent dirties the page and saves", async () => {
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue({ ...policy, max_concurrent: 5 });
    renderAt("/settings/policy");
    const input = await screen.findByLabelText(/max concurrent/i);
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(expect.objectContaining({ max_concurrent: 5 }));
  });

  it("shows findings severity toggles and rate-limit retries", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByRole("button", { name: /critical/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /minor/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText(/rate limit retries/i)).toHaveValue(5);
  });

  it("shows the auto-escalate-on-stuck defaults", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByRole("switch", { name: /auto-escalate on stuck/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByLabelText("auto-escalate stuck cap")).toHaveValue(3);
    expect(screen.getByLabelText("auto-escalate delay")).toHaveValue(0);
  });

  it("toggling auto-escalate-on-stuck dirties the page and saves", async () => {
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue({
      ...policy,
      auto_escalate_stuck: false,
    });
    renderAt("/settings/policy");
    await userEvent.click(await screen.findByRole("switch", { name: /auto-escalate on stuck/i }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(expect.objectContaining({ auto_escalate_stuck: false }));
  });

  it("editing the auto-escalate cap and delay saves both", async () => {
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const cap = await screen.findByLabelText("auto-escalate stuck cap");
    await userEvent.clear(cap);
    await userEvent.type(cap, "5");
    const delay = screen.getByLabelText("auto-escalate delay");
    await userEvent.clear(delay);
    await userEvent.type(delay, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({ auto_escalate_stuck_cap: 5, auto_escalate_delay_s: 30 }),
    );
  });
});
