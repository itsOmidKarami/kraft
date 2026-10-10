import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { holdFetch, stubFetch } from "../item/testkit";
import { StoragePage } from "./StoragePage";
import { bulkOk, GB, storagePreview, storageUsage } from "./testkit";

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
    expect(screen.getByRole("link", { name: "Edit the quota and limit in Policy › Housekeeping" })).toHaveAttribute("href", "/settings/policy/housekeeping");
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

describe("Settings › Storage worktrees", () => {
  beforeEach(() => {
    vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
  });
  afterEach(() => vi.restoreAllMocks());

  const rows = () => within(screen.getByRole("table", { name: "Worktrees by size" })).getAllByRole("row").slice(1);

  it("lists one row per worktree in the server's order, then the orphans, each item linking to its page", async () => {
    stubFetch({ "GET /storage": [200, storageUsage()] });
    mount();
    await screen.findByRole("table");
    expect(rows().map((r) => within(r).getAllByRole("cell")[1].textContent)).toEqual(["Rate limiter", "Cache embeddings", "Old spike", "7f3a"]);
    const [live, finished] = rows();
    expect(within(live).getAllByRole("cell").map((c) => c.textContent)).toEqual(["", "Rate limiter", "active", "6G", "1d ago"]);
    expect(within(finished).getByText(/completed/)).toHaveTextContent(/completed.*reclaimable/);
    expect(screen.getByRole("link", { name: "Rate limiter" })).toHaveAttribute("href", "/work-items/w3");
  });

  it("lets only reclaimable rows be selected and lists an orphan read-only", async () => {
    stubFetch({ "GET /storage": [200, storageUsage()] });
    mount();
    await screen.findByRole("table");
    expect(screen.getAllByRole("checkbox").map((c) => c.getAttribute("aria-label"))).toEqual(["Select Cache embeddings", "Select Old spike"]);
    const orphan = rows().at(-1) as HTMLElement;
    expect(orphan).toHaveTextContent("7f3a");
    expect(orphan).toHaveTextContent("no work item");
    expect(orphan).toHaveTextContent("512M");
    expect(within(orphan).queryByRole("checkbox")).toBeNull();
    expect(within(orphan).queryByRole("link")).toBeNull();
  });

  it("enables Clean up selected once something is ticked, and counts what Clean up all would take", async () => {
    stubFetch({ "GET /storage": [200, storageUsage()] });
    mount();
    const selected = await screen.findByRole("button", { name: "Clean up selected" });
    expect(selected).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clean up all reclaimable (2, 3G)" })).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Old spike" }));
    expect(selected).toBeEnabled();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("has nothing to clean up when no finished item holds a worktree", async () => {
    const live = storageUsage().items.filter((i) => !i.reclaimable);
    stubFetch({ "GET /storage": [200, storageUsage({ items: live, reclaimable_bytes: 0 })] });
    mount();
    const all = await screen.findByRole("button", { name: "Clean up all reclaimable (0, 0K)" });
    expect(all).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clean up selected" })).toBeDisabled();
    expect(screen.getByText(/Nothing to clean up: no finished item holds a worktree/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("drops a ticked row that a refresh no longer shows as reclaimable", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /storage": [200, storageUsage()] };
    stubFetch(answers);
    mount();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Cache embeddings" }));
    expect(screen.getByRole("button", { name: "Clean up selected" })).toBeEnabled();
    const after = storageUsage();
    after.items[1] = { ...after.items[1], archived: true, reclaimable: false };
    answers["GET /storage"] = [200, after];
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: "Select Cache embeddings" })).toBeNull());
    expect(screen.getByRole("button", { name: "Clean up selected" })).toBeDisabled();
  });

  it("previews the ticked rows, archives them on confirm, reads the page again and keeps focus on the page", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /storage": [200, storageUsage()], "POST /storage/preview": [200, storagePreview()], "POST /work-items/bulk": bulkOk("w1", "w4") };
    const calls = stubFetch(answers);
    mount();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Cache embeddings" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Old spike" }));
    await userEvent.click(screen.getByRole("button", { name: "Clean up selected" }));
    const dialog = await screen.findByRole("dialog");
    expect(calls.find((c) => c.path === "/storage/preview")?.body).toEqual({ ids: ["w1", "w4"] });
    expect(calls.filter((c) => c.path === "/work-items/bulk")).toEqual([]);
    const after = storageUsage();
    answers["GET /storage"] = [200, storageUsage({ items: [after.items[0]], reclaimable_bytes: 0, used_bytes: 9 * GB, state: "over_quota" })];
    await userEvent.click(await within(dialog).findByRole("button", { name: "Archive 2 items and free 3G" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.filter((c) => c.path === "/work-items/bulk").map((c) => c.body)).toEqual([{ action: "archive", ids: ["w1", "w4"] }]);
    expect(calls.filter((c) => c.method === "GET" && c.path === "/storage")).toHaveLength(2);
    // The button that opened the dialog is disabled now: Refresh takes focus rather than the body.
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toHaveFocus());
  });

  it("keeps a clean-up's figures when a slower Refresh answers after its re-read, and measures until that Refresh settles", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /storage": [200, storageUsage()], "POST /storage/preview": [200, storagePreview()], "POST /work-items/bulk": bulkOk("w1", "w4") };
    const held = holdFetch(/refresh=1/, answers);
    mount();
    await screen.findByText(/12G used/);
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Cache embeddings" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Old spike" }));
    await userEvent.click(screen.getByRole("button", { name: "Clean up selected" }));
    const dialog = await screen.findByRole("dialog");
    const after = storageUsage();
    answers["GET /storage"] = [200, storageUsage({ items: [after.items[0]], reclaimable_bytes: 0, used_bytes: 9 * GB, state: "over_quota" })];
    await userEvent.click(await within(dialog).findByRole("button", { name: "Archive 2 items and free 3G" }));
    expect(await screen.findByText(/9G used/)).toBeInTheDocument();
    // The re-read answered first, and the Refresh is still out.
    expect(screen.getByRole("button", { name: "Measuring…" })).toBeDisabled();
    held[0](storageUsage());
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
    expect(screen.getByText(/9G used/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select Old spike" })).toBeNull();
  });

  it("sends every reclaimable id from Clean up all", async () => {
    const calls = stubFetch({ "GET /storage": [200, storageUsage()], "POST /storage/preview": [200, storagePreview()] });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Clean up all reclaimable (2, 3G)" }));
    await screen.findByRole("dialog");
    expect(calls.find((c) => c.path === "/storage/preview")?.body).toEqual({ ids: ["w1", "w4"] });
  });
});
