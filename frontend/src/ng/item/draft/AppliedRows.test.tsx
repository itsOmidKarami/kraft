import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KraftEvent, WorkerSession } from "../../../types";
import { detail, stubFetch, type Call } from "../testkit";
import { usePaneMemory, Workspace } from "../Workspace";

beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));
afterEach(() => vi.unstubAllGlobals());

const ev = (seq: number, source: string | undefined, changes: unknown[]): KraftEvent => ({ seq, work_item_id: "w1", type: "chain_revised", payload: { gate: null, changes, diff: [], ...(source ? { source } : {}) }, node_id: null, created_at: "2026-09-13T09:00:00Z" });
const events = [
  ev(1, "draft", [{ op: "override", path: "merge_request.open.open_draft", task_config: { model: "opus" } }, { op: "override", path: "mr_checks", policy: { time_cap_minutes: 45 } }]),
  ev(2, "gate", [{ op: "override", path: "merge_request", policy: { budget_usd: 99 } }]),
];
const lint = { id: "s1", node_id: "verification", hook_point: "verification.checks.lint", status: "done", attempt: 1, round: 0, thread: 1, created_at: "2026-09-13T09:01:00Z", started_at: null, exited_at: null, wall_ms: 1000, model: null, tokens_in: 1, tokens_out: 1, cost_usd: 0, head_sha: "abc1234567" } as WorkerSession;
let calls: Call[];
const mount = (path: string, evs: KraftEvent[] = events, item = detail()) => {
  calls = stubFetch({ "GET /work-items/w1/events": [200, evs] });
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/work-items/:id" element={<Workspace item={item} version="1" reload={() => {}} />} /></Routes></MemoryRouter>);
};
const eventReads = () => calls.filter((c) => c.path === "/work-items/w1/events").length;

describe("applied drafts in Config", () => {
  it("lists them in the chain pane's Changed for this item, with no reset of their own", async () => {
    mount("/work-items/w1?tab=config");
    const pane = screen.getByRole("complementary", { name: "default pane" });
    expect(await within(pane).findByText("merge_request.open.open_draft")).toBeInTheDocument();
    expect(within(pane).getByText("model opus")).toBeInTheDocument();
    expect(within(pane).getByText("policy.time_cap_minutes 45")).toBeInTheDocument();
    expect(within(pane).queryByText(/budget_usd 99/)).toBeNull();
    expect(within(pane).queryByRole("button", { name: "reset" })).toBeNull();
  });

  it("keeps the frozen item's own override list beside them", async () => {
    mount("/work-items/w1?tab=config", events, detail({ node_overrides: { verification: { attempts: 4 } } }));
    const pane = screen.getByRole("complementary", { name: "default pane" });
    expect(await within(pane).findByText("model opus")).toBeInTheDocument();
    expect(within(pane).getByText("attempts 4")).toBeInTheDocument();
  });

  it("scopes a node's Config to the paths under it", async () => {
    mount("/work-items/w1?sel=merge_request&tab=config");
    const pane = screen.getByRole("complementary", { name: "merge_request pane" });
    expect(await within(pane).findByText("merge_request.open.open_draft")).toBeInTheDocument();
    expect(within(pane).queryByText("mr_checks")).toBeNull();
  });

  it("shows a step's and a run task's own paths in their Config", async () => {
    const e = [ev(1, "draft", [{ op: "override", path: "verification.checks.lint", task_config: { model: "m1" } }, { op: "override", path: "verification.review.code_review", task_config: { model: "m2" } }])];
    mount("/work-items/w1?sel=verification.checks.lint&tab=config", e, detail({ worker_sessions: [lint] }));
    const pane = screen.getByRole("complementary", { name: "lint pane" });
    expect(await within(pane).findByText("model m1")).toBeInTheDocument();
    expect(within(pane).queryByText("model m2")).toBeNull();
  });

  it("shows nothing for a revision that did not come from a draft", async () => {
    mount("/work-items/w1?tab=config", [events[1]]);
    await waitFor(() => expect(eventReads()).toBeGreaterThan(0));
    expect(screen.queryByText("applied by the draft")).toBeNull();
  });

  it("reads the whole log only while a Config tab is open", async () => {
    mount("/work-items/w1");
    await screen.findByRole("complementary", { name: "default pane" });
    expect(calls.filter((c) => c.path === "/work-items/w1/events").length).toBe(1); // W5's own read for Recent
    expect(eventReads()).toBe(1);
  });
});
