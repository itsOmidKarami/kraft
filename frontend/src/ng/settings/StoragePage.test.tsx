import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { holdFetch, stubFetch } from "../item/testkit";
import { StoragePage } from "./StoragePage";
import { GB, storageUsage } from "./testkit";

const mount = () =>
  render(
    <MemoryRouter>
      <StoragePage />
    </MemoryRouter>,
  );
afterEach(() => vi.unstubAllGlobals());

describe("Settings › Storage, usage", () => {
  it.each([
    ["ok", 7 * GB, "is-ok", ""],
    ["over_quota", 9 * GB, "is-over_quota", "Over the quota: Kraft is warning; starts still go ahead."],
    ["held", 12 * GB, "is-held", "Over the limit: starts are held until you make room."],
  ] as const)("%s: the bar takes the state's look and the page says what it means", async (state, used, cls, note) => {
    stubFetch({ "GET /storage": [200, storageUsage({ state, used_bytes: used })] });
    mount();
    expect(await screen.findByRole("img", { name: /^Worktrees use .* of the 10G limit$/ })).toHaveClass(cls);
    expect(screen.getByRole("status")).toHaveTextContent(note);
    if (!note) expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("marks the quota and the limit on the bar", async () => {
    stubFetch({ "GET /storage": [200, storageUsage({ state: "ok", used_bytes: 5 * GB })] });
    mount();
    const bar = await screen.findByRole("img", { name: /Worktrees use 5G of the 10G limit/ });
    expect((bar.querySelector(".set-st-fill") as HTMLElement).style.width).toBe("50%");
    expect([...bar.querySelectorAll<HTMLElement>(".set-st-mark")].map((m) => m.style.left)).toEqual(["80%", "100%"]);
    expect(screen.getByText(/5G used · quota 8G · limit 10G/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Change them/ })).toHaveAttribute("href", "/settings/policy/housekeeping");
  });

  it("shows usage and a link to the Housekeeping rows when no limit is set", async () => {
    stubFetch({ "GET /storage": [200, storageUsage({ state: null, quota_bytes: null, limit_bytes: null })] });
    mount();
    expect(await screen.findByText(/12G used by worktrees · no limit set/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("link", { name: /Set one in Policy › Housekeeping/ })).toHaveAttribute("href", "/settings/policy/housekeeping");
  });

  it("totals each category and names the measurement's age", async () => {
    stubFetch({ "GET /storage": [200, storageUsage()] });
    mount();
    const totals = (await screen.findByText("Sandbox stores and homes")).closest("dl") as HTMLElement;
    expect([...totals.querySelectorAll("dt")].map((d) => d.textContent)).toEqual(["Worktrees", "Sandbox stores and homes", "Logs", "Results", "Databases and backups", "Attachments", "Everything else"]);
    expect(within(totals).getByText("Worktrees").nextElementSibling).toHaveTextContent("11G");
    expect(within(totals).getByText("Logs").nextElementSibling).toHaveTextContent("200M");
    expect(screen.getByText("measured 2m ago")).toBeInTheDocument();
  });

  it("refreshes with ?refresh=1, says it is measuring, and keeps the figures on screen meanwhile", async () => {
    const held = holdFetch(/refresh=1/, { "GET /storage": [200, storageUsage()] });
    mount();
    await screen.findByText(/12G used/);
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByRole("button", { name: "Measuring…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Measuring…");
    expect(screen.getByText(/12G used/)).toBeInTheDocument();
    held[0](storageUsage({ state: "over_quota", used_bytes: 9 * GB }));
    expect(await screen.findByText(/9G used/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect(screen.getByRole("status")).not.toHaveTextContent("Measuring…");
  });

  it("says it is measuring while the first measurement runs", async () => {
    const held = holdFetch(/^\/storage/);
    mount();
    expect(await screen.findByText(/Measuring the run folder/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Measuring…" })).toBeDisabled();
    held[0](storageUsage());
    expect(await screen.findByRole("img", { name: /Worktrees use/ })).toBeInTheDocument();
    expect(screen.queryByText(/Measuring the run folder/)).toBeNull();
  });

  it("keeps the last figures when a refresh fails", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /storage": [200, storageUsage()] };
    stubFetch(answers);
    mount();
    await screen.findByText(/12G used/);
    answers["GET /storage"] = [500, { detail: "the walk failed" }];
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the walk failed");
    expect(screen.getByText(/12G used/)).toBeInTheDocument();
  });
});
