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

  // F4: each item counts only where the server's `actions` admit the door; the old reading is a server's that sends none.
  it.each([
    ["Cancel, the server refusing it on one", [RUN, { ...NEED, actions: ["retry", "escalate"] }], "Cancel 1…"],
    ["Archive, the server refusing it on one", [DONE, { ...item("d6", "done", "completed"), actions: ["abandon"] }], "Archive 1"],
    ["Pause, a queued one the server takes", [RUN, { ...item("q7", "queued", "queued"), actions: ["pause", "cancel"] }], "‖ Pause 2"],
  ] as const)("counts %s", (_name, checked, label) => {
    mount([...checked] as WorkItem[]);
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  });

  it("greys Archive when nothing checked has ended, and says why in text beside it, not in a title", () => {
    mount([NEED]);
    const archive = screen.getByRole("button", { name: "Archive" });
    expect(archive).toBeDisabled();
    expect(archive).not.toHaveAttribute("title");
    expect(archive).toHaveAccessibleDescription("Only done or cancelled items can be archived.");
    expect(screen.getByText("Only done or cancelled items can be archived.")).toBeVisible();
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

  it("keeps Archive's reason off once something checked can be archived", () => {
    mount([DONE]);
    expect(screen.queryByText(/can be archived/)).toBeNull();
  });

  const PAUSED = detail({ id: "p6", title: "Item p6", display_status: "paused", status: "paused", current_node_id: "plan", bead_id: null });
  const NEW = detail({ id: "p7", title: "Item p7", display_status: "paused", status: "paused", current_node_id: null, bead_id: null });
  const FAILED = detail({ id: "f8", title: "Item f8", display_status: "failed", status: "needs_human", bead_id: null });

  it("counts Resume on the paused ones that have begun (a never-started one has Start) and Retry on the stopped ones", () => {
    const GATE = detail({ id: "g9", title: "Item g9", display_status: "needs_you", status: "needs_human", stop: { kind: "gate", reason: "approve spec" }, pending_gate: "spec_approval", bead_id: null });
    mount([PAUSED, NEW, FAILED, RUN, GATE]);
    expect(screen.getByRole("button", { name: "Resume 1" })).toBeInTheDocument(); // the gate has its own Review, so Retry is 1, not 2
    expect(screen.getByRole("button", { name: "Retry 1" })).toBeInTheDocument();
  });

  it("sends Resume and Retry through the item's own routes, one per item, and lists the one that refused", async () => {
    const calls = stubFetch({ "POST /work-items/f8/retry": [409, { detail: "work item is not stopped" }], "POST /work-items/p6/resume": [200, {}] });
    vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
    const onChecked = vi.fn();
    render(<BulkBar checked={[PAUSED, FAILED]} byId={{ p6: PAUSED, f8: FAILED }} offline={false} onChecked={onChecked} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume 1" }));
    await act(async () => {});
    expect(useBulk.getState().last).toBeNull(); // all went: nothing to read
    fireEvent.click(screen.getByRole("button", { name: "Retry 1" }));
    await act(async () => {});
    expect(calls.some((c) => c.path === "/work-items/bulk")).toBe(false);
    expect(calls.map((c) => c.path)).toEqual(expect.arrayContaining(["/work-items/p6/resume", "/work-items/f8/retry"]));
    expect(await screen.findByRole("alert")).toHaveTextContent("0 of 1 item retried");
    expect(screen.getByRole("listitem")).toHaveTextContent("work item is not stopped");
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
