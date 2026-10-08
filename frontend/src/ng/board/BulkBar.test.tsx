import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { DisplayStatus, WorkItem, WorkItemStatus } from "../../types";
import { detail, stubFetch } from "../item/testkit";
import { Toaster } from "../ui/Toast";
import { CANCEL_WINDOW_MS, useBulk } from "./bulk";
import { BulkBar } from "./BulkBar";

const item = (id: string, display_status: DisplayStatus, status: WorkItemStatus = "active") => detail({ id, title: `Item ${id}`, display_status, status, bead_id: null });
const RUN = item("r1", "running");
const WAIT = item("w2", "waiting", "rate_limited");
const NEED = item("n3", "needs_you", "needs_human");
const DONE = item("d4", "done", "completed");
const RUN2 = item("r5", "running");
const byId = Object.fromEntries([RUN, WAIT, NEED, DONE, RUN2].map((i) => [i.id, i]));
const okFor = (...ids: string[]): [number, unknown] => [200, { results: ids.map((id) => ({ id, ok: true, status: "abandoned" })) }];

const mount = (checked: WorkItem[], answers: Parameters<typeof stubFetch>[0] = {}) => {
  const calls = stubFetch(answers);
  const onChecked = vi.fn();
  render(<><BulkBar checked={checked} byId={byId} offline={false} onChecked={onChecked} /><Toaster /></>);
  return { calls, onChecked, bulk: () => calls.filter((c) => c.path === "/work-items/bulk") };
};

beforeEach(() => {
  useBulk.setState({ last: null });
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("BulkBar", () => {
  it("counts each action from what is checked: Pause what the pause route takes, Cancel what has not ended, Archive what has", () => {
    mount([RUN, WAIT, NEED, DONE]);
    expect(screen.getByText("4 selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "‖ Pause 2" })).toBeInTheDocument(); // running and rate-limited: the pause route takes both
    expect(screen.getByRole("button", { name: "Cancel 3…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive 1" })).toBeEnabled();
  });

  it("greys Archive when nothing checked has ended, and says why", () => {
    mount([NEED]);
    const archive = screen.getByRole("button", { name: "Archive" });
    expect(archive).toBeDisabled();
    expect(archive).toHaveAttribute("title", "Only done items can be archived");
    expect(screen.queryByRole("button", { name: /Pause/ })).toBeNull();
  });

  it("asks for a reason, then holds the cancel for the window: nothing sent before it ends, one bulk cancel after, never /abandon", async () => {
    vi.useFakeTimers();
    const { bulk, calls, onChecked } = mount([RUN, NEED, DONE], { "POST /work-items/bulk": okFor("r1", "n3") });
    fireEvent.click(screen.getByRole("button", { name: "Cancel 2…" }));
    expect(screen.getByText(/Running attempts stop and are lost/)).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Cancel 2" });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value: "superseded by kraft-cb61" } });
    fireEvent.click(go);
    expect(onChecked).toHaveBeenLastCalledWith([]);
    expect(screen.getByText("Cancelling 2 items…")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTime(CANCEL_WINDOW_MS - 1));
    expect(bulk()).toHaveLength(0);
    await act(async () => vi.advanceTimersByTime(1));
    expect(bulk()).toEqual([{ method: "POST", path: "/work-items/bulk", body: { action: "cancel", ids: ["r1", "n3"], reason: "superseded by kraft-cb61" } }]);
    expect(calls.some((c) => /abandon/.test(c.path))).toBe(false);
  });

  it("sends nothing at all when Undo is pressed inside the window, and checks the items again", async () => {
    vi.useFakeTimers();
    const { bulk, onChecked } = mount([RUN, NEED]);
    fireEvent.click(screen.getByRole("button", { name: "Cancel 2…" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value: "wrong repo" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel 2" }));
    await act(async () => vi.advanceTimersByTime(CANCEL_WINDOW_MS - 100));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onChecked).toHaveBeenLastCalledWith(["r1", "n3"]);
    await act(async () => vi.advanceTimersByTime(CANCEL_WINDOW_MS * 2));
    expect(bulk()).toHaveLength(0);
  });

  it("lists each failure of a partial answer in the bar's place, in the server's words", async () => {
    const { bulk } = mount([RUN, RUN2], {
      "POST /work-items/bulk": [200, { results: [{ id: "r1", ok: true, status: "paused" }, { id: "r5", ok: false, status: "paused", error: "work item is paused, not running" }] }],
    });
    fireEvent.click(screen.getByRole("button", { name: "‖ Pause 2" }));
    await act(async () => {});
    expect(bulk()[0].body).toEqual({ action: "pause", ids: ["r1", "r5"] });
    const strip = await screen.findByRole("alert");
    expect(strip).toHaveTextContent("1 of 2 items paused");
    expect(within(strip).getByRole("listitem")).toHaveTextContent("Item r5 r5 · work item is paused, not running");
    fireEvent.click(within(strip).getByRole("button", { name: "Dismiss" }));
    expect(useBulk.getState().last).toBeNull();
  });

  it("archives at once with an Undo that restores", async () => {
    const { bulk } = mount([DONE], { "POST /work-items/bulk": [200, { results: [{ id: "d4", ok: true, status: "completed" }] }] });
    fireEvent.click(screen.getByRole("button", { name: "Archive 1" }));
    await act(async () => {});
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await act(async () => {});
    expect(bulk().map((c) => c.body)).toEqual([{ action: "archive", ids: ["d4"] }, { action: "restore", ids: ["d4"] }]);
  });
});
