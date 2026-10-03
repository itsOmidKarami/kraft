import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import type { KraftEvent } from "../../types";
import { useResizable } from "../graph/useResizable";
import { acceptWrites, detail, stubFetch } from "../item/testkit";
import type { ItemDetail } from "../item/useItem";
import { answer, ov } from "../item/draft/testkit";
import type { MarkedOp } from "../item/draft/types";
import { Peek, type PeekTab } from "./Peek";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /work-items/w1/archive", "POST /work-items/w1/resume");

const Where = () => <span data-testid="where">{useLocation().pathname + useLocation().search}</span>;

function Harness({ start = "overview", budget = false }: { start?: PeekTab; budget?: boolean }) {
  const [tab, setTab] = useState<PeekTab>(start);
  const [b, setB] = useState(budget);
  const size = useResizable("board", 1400);
  return <Peek id="w1" tab={tab} onTab={setTab} budget={b} onBudget={setB} offline={false} size={size} onClose={() => {}} onRepo={() => {}} />;
}

const ev = (seq: number, type: string, node_id: string | null = null): KraftEvent => ({ seq, work_item_id: "w1", type, payload: { node_id }, node_id, created_at: "2026-09-13T09:00:00Z" }) as KraftEvent;

const mount = (over: Partial<ItemDetail>, opts: { start?: PeekTab; budget?: boolean; events?: KraftEvent[]; draft?: MarkedOp[] } = {}) => {
  const calls = stubFetch({ ...WRITES, "GET /work-items/w1": [200, detail(over)], "GET /work-items/w1/events": [200, opts.events ?? []], "GET /policy": [200, {}], "GET /work-items/w1/draft": answer(opts.draft ?? []) });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<><Harness start={opts.start} budget={opts.budget} /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return calls;
};
const stop = (kind: string, more: object = {}) => ({ kind, node: "verification", task: null, attempt: 1, reason: null, resume_at: null, facts: {}, ...more }) as ItemDetail["stop"];

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Peek", () => {
  it("shows the card the item page would: the gate banner, a failure, a question, a pause", async () => {
    mount({ status: "needs_human", display_status: "needs_you", stop: stop("gate"), pending_gate: "plan_approval" });
    expect(await screen.findByText(/Waiting for your approval at/)).toBeInTheDocument();
    fireEvent.click(within(document.querySelector<HTMLElement>(".item-banner")!).getByRole("button", { name: "Review changes" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/w1/review?gate=plan_approval&doc=1");
  });

  // R11b-05: a waiting item's line said Running.
  it.each([
    [{ status: "needs_human", display_status: "failed", stop: stop("failed") }, /^kraft-cb59 · Needs you · failed at/],
    [{ status: "waiting", display_status: "waiting", stop: stop("wait") }, /^kraft-cb59 · Waiting · waiting at/],
  ] as [Partial<ItemDetail>, RegExp][])("is headed and named by the item's title, its id and group in the line under it: %#", async (over, line) => {
    mount(over);
    const pane = await screen.findByRole("complementary", { name: "Design the cache pane" });
    expect(pane.querySelector(".pane-title")).toHaveTextContent(/^Design the cache$/);
    expect(within(pane).getByText(line)).toBeInTheDocument();
  });

  it.each([
    [{ status: "needs_human", display_status: "failed", stop: stop("failed", { reason: "The forge refused." }) }, /Failed/],
    [{ status: "needs_human", display_status: "needs_you", needs_context_question: "Keep the header?", stop: stop("question", { reason: "needs_context: Keep the header?" }) }, /Keep the header\?/],
    [{ status: "paused", display_status: "paused" }, /Paused/],
  ] as [Partial<ItemDetail>, RegExp][])("draws %# of the item page's cards", async (over, text) => {
    mount(over);
    expect((await screen.findAllByText(text)).length).toBeGreaterThan(0);
  });

  // The banner's Raise cap and the footer's main button, which says the same: a budget stop's
  // opens Config's budget editor (R11a-05), a time cap's its own editor, which Config has no row for (R12b-06).
  const budgetStop = { status: "needs_human", display_status: "needs_you", stop: stop("budget", { reason: "Spend cap reached", scope: "work_item" }), budget_cap: { cap_usd: 5, source: "policy", spent_usd: 5 } as ItemDetail["budget_cap"] } as Partial<ItemDetail>;
  const timeCap = { status: "needs_human", display_status: "needs_you", stop: stop("cap", { reason: "running time hit its 1m cap", limit: { path: "", key: "time_cap_minutes", value: 1, maximum: null } }) } as Partial<ItemDetail>;
  const budgetEditor = async () => {
    expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("textbox", { name: "Budget in dollars" })).toBeInTheDocument();
  };
  const capEditor = async () => expect(await screen.findByRole("dialog", { name: "Raise running-time cap" })).toBeInTheDocument();
  it.each([
    ["a budget stop", ".item-banner", budgetStop, budgetEditor],
    ["a budget stop", ".pane-footer", budgetStop, budgetEditor],
    ["a time cap", ".item-banner", timeCap, capEditor],
    ["a time cap", ".pane-footer", timeCap, capEditor],
  ] as const)("opens the editor for %s from its Raise cap in %s", async (_, where, over, opened) => {
    mount(over, { start: "activity" });
    if (where === ".item-banner") fireEvent.click(await screen.findByRole("tab", { name: "Overview" }));
    await screen.findAllByRole("button", { name: "Raise cap" });
    fireEvent.click(within(document.querySelector<HTMLElement>(where)!).getByRole("button", { name: "Raise cap" }));
    await opened();
  });

  // BD-3: a board row's Raise budget asks for the budget editor; the peek opens the one that raises this stop's cap.
  it.each([
    ["the item's own cap: Config's budget editor", budgetStop, async () => {
      expect(await screen.findByRole("textbox", { name: "Budget in dollars" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
    }],
    ["a policy dollar cap: its own editor", { ...budgetStop, stop: stop("budget", { reason: "Spend cap reached", limit: { path: "", key: "budget_usd", value: 5, maximum: null } }) }, async () => expect(await screen.findByRole("dialog", { name: "Raise budget cap" })).toBeInTheDocument()],
    ["the daily cap: the banner saying why it can't", { ...budgetStop, stop: stop("budget", { reason: "Daily cap reached", scope: "daily" }) }, async () => {
      await waitFor(() => expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true"));
      expect(await screen.findByText(/The item can't raise this cap/)).toBeInTheDocument();
    }],
  ] as const)("opens on a row's Raise budget at %s", async (_, over, opened) => {
    mount(over as Partial<ItemDetail>, { start: "config", budget: true });
    await opened();
  });

  it("moves focus into the budget editor's field, and Enter saves the typed cap (R10a-05)", async () => {
    const calls = mount({ status: "needs_human", display_status: "needs_you", stop: stop("budget", { reason: "Spend cap reached", scope: "work_item" }), budget_cap: { cap_usd: 5, source: "policy", spent_usd: 5 } as ItemDetail["budget_cap"] });
    fireEvent.click((await screen.findAllByRole("button", { name: "Raise cap" }))[0]);
    const field = await screen.findByRole("textbox", { name: "Budget in dollars" });
    expect(field).toHaveFocus();
    // The cap in force is selected, so typing replaces it rather than appending to it (R11a-03).
    expect([(field as HTMLInputElement).selectionStart, (field as HTMLInputElement).selectionEnd]).toEqual([0, 1]);
    await userEvent.keyboard("7{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/budget/raise", body: { budget_usd: 7 } }]));
  });

  it("starts a never-started item with /resume", async () => {
    const calls = mount({ status: "paused", display_status: "paused", current_node_id: null });
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    await act(async () => {});
    expect(calls.find((c) => c.method === "POST")).toMatchObject({ path: "/work-items/w1/resume" });
  });

  it("Start with a draft starts nothing here: it goes to the item page, which asks Apply and start or Start without them", async () => {
    const calls = mount({ status: "paused", display_status: "paused", current_node_id: null }, { draft: [ov("implementation", undefined, { budget_usd: 2 })] });
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    await act(async () => {});
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/w1?start=1");
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });

  it("Start with only passed edits in the draft starts as before", async () => {
    const calls = mount({ status: "paused", display_status: "paused", current_node_id: null }, { draft: [ov("plan", undefined, { budget_usd: 2 }, true)] });
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    await act(async () => {});
    expect(calls.filter((c) => c.method !== "GET").map((c) => c.path)).toEqual(["/work-items/w1/resume"]);
  });

  it("puts the item's main action in the footer: Pause asks first, Archive once done, and Open item", async () => {
    const calls = mount({ display_status: "running" });
    fireEvent.click(await screen.findByRole("button", { name: "‖ Pause" }));
    expect(screen.getByRole("dialog", { name: "Pause this item?" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Open item ↗" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/w1");
  });

  it("archives a done item from the footer", async () => {
    const calls = mount({ status: "completed", display_status: "done" });
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));
    await act(async () => {});
    expect(calls.find((c) => c.method === "POST")).toMatchObject({ path: "/work-items/w1/archive" });
  });

  it("lists Activity newest first and pages back with before_seq; a node's line opens the item there", async () => {
    const page = Array.from({ length: 50 }, (_, k) => ev(51 + k, "node_started", k === 49 ? "verification" : null));
    mount({}, { start: "activity", events: page });
    // The list is drawn empty before its events answer: wait for the rows, not the list.
    const list = await screen.findByRole("list");
    expect((await within(list).findAllByRole("listitem"))[0]).toHaveTextContent("verification");
    fireEvent.click(screen.getByRole("button", { name: "Show earlier" }));
    await act(async () => {});
    const urls = vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/events?"));
    expect(urls.at(-1)).toContain("before_seq=51&limit=50");
    fireEvent.click(within(list).getAllByRole("button")[0]);
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/w1?sel=verification");
  });

  it("adds a new event to Activity live, though the item's updated_at stays put", async () => {
    useStore.setState({ eventsByItem: {} });
    mount({}, { start: "activity", events: [ev(1, "node_started", "plan")] });
    expect(within(await screen.findByRole("list")).getAllByRole("listitem")).toHaveLength(1);
    stubFetch({ "GET /work-items/w1": [200, detail({})], "GET /work-items/w1/events": [200, [ev(1, "node_started", "plan"), ev(2, "node_started", "verification")]] });
    act(() => useStore.getState().applyEvent(ev(2, "worker_session_created")));
    expect(await within(screen.getByRole("list")).findByText(/verification/)).toBeInTheDocument();
  });

  it("adds a new event to the Overview's Recent live", async () => {
    useStore.setState({ eventsByItem: {} });
    mount({}, { events: [ev(1, "node_started", "plan")] });
    expect(await screen.findByText("plan started")).toBeInTheDocument();
    stubFetch({ "GET /work-items/w1": [200, detail({})], "GET /work-items/w1/events": [200, [ev(1, "node_started", "plan"), ev(2, "node_started", "verification")]], "GET /policy": [200, {}] });
    act(() => useStore.getState().applyEvent(ev(2, "worker_session_created")));
    expect(await screen.findByText("verification started")).toBeInTheDocument();
  });

  it("says so when the item is gone", async () => {
    stubFetch({ "GET /work-items/w1": [404, { detail: "work item not found" }] });
    render(<MemoryRouter><Harness /></MemoryRouter>);
    expect(await screen.findByText("This item is gone.")).toBeInTheDocument();
  });
});
