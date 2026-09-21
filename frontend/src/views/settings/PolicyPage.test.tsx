import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { policy, renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · policy (5d)", () => {
  it.each<[string, string | RegExp, string, object]>([
    ["a loop cap, saving every counter together", "verify.fix_loop attempts", "5", {
      loops: { "verify.fix_loop": { attempts: 5, wall_clock_s: 3600 } },
      default: { attempts: 3, wall_clock_s: 3600 },
    }],
    ["max_concurrent", /max concurrent/i, "5", { max_concurrent: 5 }],
    ["the auto-escalate cap", "auto-escalate stuck cap", "5", { auto_escalate_stuck_cap: 5 }],
    ["the auto-escalate delay", "auto-escalate delay", "30", { auto_escalate_delay_s: 30 }],
  ])("editing %s dirties the page and saves it", async (_, label, typed, saved) => {
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const input = await screen.findByLabelText(label);
    await userEvent.clear(input);
    await userEvent.type(input, typed);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(expect.objectContaining(saved));
  });

  it.each([
    ["a budget", { budget: { work_item_usd: 20, daily_usd: null } }, "work item budget", 20, { budget: { work_item_usd: null, daily_usd: null } }],
    ["the archive", { archive: { after_days: 30 } }, /archive after days/i, 30, { archive: { after_days: null } }],
  ])("clearing %s field saves null, not zero", async (_, loaded, label, shown, saved) => {
    vi.spyOn(api, "getPolicy").mockResolvedValue({ ...policy, ...loaded });
    const put = vi.spyOn(api, "putPolicy").mockResolvedValue(policy);
    renderAt("/settings/policy");
    const field = await screen.findByLabelText(label);
    expect(field).toHaveValue(shown);
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(expect.objectContaining(saved));
  });

  // policy fixture: max_concurrent 3, rate_limit_retries 5, wall_clock_s 3600.
  it.each([
    ["max concurrent", /max concurrent/i, 3],
    ["wall clock in minutes, not raw seconds", "verify.fix_loop wall clock", 60],
    ["rate-limit retries", /rate limit retries/i, 5],
    ["the auto-escalate stuck cap", "auto-escalate stuck cap", 3],
    ["the auto-escalate delay", "auto-escalate delay", 0],
  ])("shows %s from the loaded policy", async (_, label, value) => {
    renderAt("/settings/policy");
    expect(await screen.findByLabelText(label)).toHaveValue(value);
  });

  it("states the one-task overshoot", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByText(/overshoot/i)).toBeInTheDocument();
  });

  it("shows findings severity toggles", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByRole("button", { name: /critical/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /minor/i })).toHaveAttribute("aria-pressed", "false");
  });

  it("shows the auto-escalate-on-stuck switch on", async () => {
    renderAt("/settings/policy");
    expect(await screen.findByRole("switch", { name: /auto-escalate on stuck/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
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

  it("names the template whose node's fix_loop matches this loop key", async () => {
    renderAt("/settings/policy");
    const row = (await screen.findByLabelText("verify.fix_loop attempts")).closest(
      "[data-loop]",
    );
    expect(row).toHaveTextContent("quick-task");
  });

  it("marks the default row as the fallback for any loop not listed", async () => {
    renderAt("/settings/policy");
    const row = (await screen.findByLabelText("default attempts")).closest("[data-loop]");
    expect(row).toHaveTextContent(/any loop not listed/i);
  });

  it("+ Add loop prompts a key and adds it with the default cap", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("new_loop");
    renderAt("/settings/policy");
    await screen.findByLabelText("verify.fix_loop attempts");
    await userEvent.click(screen.getByRole("button", { name: /add loop/i }));
    expect(await screen.findByLabelText("new_loop attempts")).toHaveValue(3);
  });

});
