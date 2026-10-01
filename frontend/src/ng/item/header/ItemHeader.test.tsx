import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../../store";
import { item } from "../../../testFixtures";
import { detail, inShell, stubFetch } from "../testkit";
import { ItemHeader } from "./ItemHeader";

/** The shell reads health and drafts itself; only the item's writes matter here. */
const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

beforeEach(() => useStore.setState({ workItems: {} }));
afterEach(() => vi.unstubAllGlobals());

const show = (over: Parameters<typeof detail>[0] = {}, handlers: Partial<{ reload: () => void; onSettings: () => void; onRunLog: () => void }> = {}) =>
  inShell(<ItemHeader item={detail(over)} reload={handlers.reload ?? (() => {})} onSettings={handlers.onSettings ?? (() => {})} onRunLog={handlers.onRunLog ?? (() => {})} />);

describe("ItemHeader", () => {
  it("counts the others that need you, never this item, and hides at zero", () => {
    useStore.setState({ workItems: { w1: item({ id: "w1", display_status: "needs_you" }), a: item({ id: "a", display_status: "needs_you" }), b: item({ id: "b", display_status: "needs_you" }), c: item({ id: "c", display_status: "running" }) } });
    const { unmount } = show({ display_status: "needs_you" });
    // The new UI's own board (the router's root, /ng in the app), not the shipped one.
    expect(screen.getByRole("link", { name: /2 others need you/ })).toHaveAttribute("href", "/ng");
    unmount();
    useStore.setState({ workItems: { w1: item({ id: "w1", display_status: "needs_you" }) } });
    show({ display_status: "needs_you" });
    expect(screen.queryByText(/need you/)).toBeNull();
  });

  it("shows the server's badge and retries a failed item from the stopped task's path", async () => {
    const calls = stubFetch();
    const reload = vi.fn();
    show({ display_status: "failed", stop: { kind: "failed", node: "merge_request", task: "merge_request.open.open_draft", attempt: 3, resume_at: null, reason: "403" } }, { reload });
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "merge_request.open.open_draft" } }]);
  });

  it("asks before pausing, then pauses", async () => {
    const calls = stubFetch();
    show();
    await userEvent.click(screen.getByRole("button", { name: /^Pause$/ }));
    const card = screen.getByRole("dialog", { name: "Pause this item?" });
    expect(writes(calls)).toEqual([]);
    await userEvent.click(within(card).getByRole("button", { name: "Pause now" }));
    await waitFor(() => expect(writes(calls)).toEqual([{ method: "POST", path: "/work-items/w1/pause", body: {} }]));
  });

  it("opens Item settings for a capped item's Resume instead of resuming", async () => {
    const calls = stubFetch();
    const onSettings = vi.fn();
    show({ display_status: "needs_you", stop: { kind: "cap", node: "verification", resume_at: null, reason: null } }, { onSettings });
    await userEvent.click(screen.getByRole("button", { name: /Resume/ }));
    expect(onSettings).toHaveBeenCalled();
    expect(writes(calls)).toEqual([]);
  });

  it("puts every ⋮ item on its route, the copied link on the shipped path, and Cancel… in ⋮ too", async () => {
    const calls = stubFetch({ "GET /work-items/w1/cancel-preview": [200, { running: null, kept: { branch: "b", worktree: "/w", findings: 0, threads: 0 }, mr: null, spend: { spent_usd: 0, cap_usd: null } }] });
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    const onSettings = vi.fn();
    const onRunLog = vi.fn();
    show({}, { onSettings, onRunLog });
    const menu = async (name: RegExp) => {
      await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
      await userEvent.click(screen.getByRole("menuitem", { name }));
    };
    await menu(/Item settings/);
    expect(onSettings).toHaveBeenCalled();
    await menu(/View run log/);
    expect(onRunLog).toHaveBeenCalled();
    await menu(/Copy ID/);
    await menu(/Copy link/);
    expect(writeText.mock.calls).toEqual([["w1"], [`${window.location.origin}/work-items/w1`]]);
    await menu(/Open worktree/);
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/work-items/w1/open-worktree", body: { editor: null } }));
    await menu(/Cancel…/);
    expect(await screen.findByRole("dialog", { name: "Cancel this item?" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    // The review page under /ng (W8): the shell's crumbs end in it.
    await menu(/Review changes/);
    expect(await screen.findByText("Review changes", { selector: '[aria-current="page"]' })).toBeInTheDocument();
  });

  it("offers Duplicate on an ended item and opens the copy", async () => {
    stubFetch({ "POST /work-items/w1/duplicate": [201, { id: "w2", status: "paused" }] });
    show({ display_status: "cancelled" });
    await userEvent.click(screen.getByRole("button", { name: "Item menu" }));
    expect(screen.queryByRole("menuitem", { name: /Cancel…/ })).toBeNull();
    await userEvent.click(screen.getByRole("menuitem", { name: /Duplicate as new item/ }));
    // The route the copy lands on is the item page's: the header (rendered for any path here) stays.
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("w2"));
  });
});
