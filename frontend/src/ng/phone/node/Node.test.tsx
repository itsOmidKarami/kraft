import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../../store";
import type { DisplayStatus, WorkItemStop, WorkerSession } from "../../../types";
import { chainGraph } from "../../item/graph";
import { detail, holdFetch, stubFetch, type Call } from "../../item/testkit";
import { Toaster } from "../nav/Toaster";
import { nodeBar } from "./model";
import { NodeRoute } from "./NodeRoute";

const stop = (kind: WorkItemStop["kind"], over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "verification", resume_at: null, reason: null, ...over });
const session = (over: Partial<WorkerSession>): WorkerSession => ({ id: "s1", work_item_id: "w1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1, thread: 1, round: 0, created_at: "2026-09-13T09:00:00Z", started_at: "2026-09-13T09:00:00Z", exited_at: null, model: "claude-sonnet-4-5", tokens_in: 100, tokens_out: 50, cost_usd: 0.15, wall_ms: 60000, ...over }) as WorkerSession;
const item = (status: DisplayStatus, s: WorkItemStop | null = null, over = {}) => detail({ display_status: status, stop: s, worker_sessions: [session({})], ...over });

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
function mount(it: ReturnType<typeof detail>, path: string, answers: Record<string, [number, unknown]> = {}) {
  const calls = stubFetch({ "GET /work-items/w1": [200, it], "GET /worker-sessions/s1/log": [200, { lines: [{ n: 1, t: "0:03", src: "agent", text: "loaded review_package", summary: "loaded review_package" }, { n: 2, t: "0:05", src: "tool", text: "grep cache", summary: "grep cache" }] }], ...answers });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/work-items/:id/nodes/:node" element={<><NodeRoute /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
  return calls;
}
const posts = (calls: Call[]) => calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
const where = () => screen.getByLabelText("where").textContent;
afterEach(() => vi.unstubAllGlobals());

describe("nodeBar (D.6): one pair by node state, never Retry while it runs", () => {
  const bar = (it: ReturnType<typeof detail>, id: string) => {
    const api = it.chain_definition.nodes.find((n) => n.id === id)!;
    const g = chainGraph(it, [], Date.parse("2026-09-13T10:00:00Z")).nodes.find((n) => n.id === id)!;
    const b = nodeBar(it, api, g);
    return [b.secondary?.id ?? null, b.primary?.id ?? null];
  };
  it.each([
    ["running node", item("running"), "verification", ["pause", "skip"]],
    ["escalated node", item("escalated"), "verification", ["pause", "skip"]],
    ["paused node", item("paused"), "verification", ["skip", "retry-node"]],
    ["failed node", item("failed", stop("failed")), "verification", ["skip", "retry-node"]],
    ["capped node", item("needs_you", stop("cap")), "verification", ["skip", "retry-node"]],
    ["done node", item("running"), "plan", [null, "retry-from"]],
    ["node not reached", item("running"), "merge_request", [null, null]],
    ["waiting gate", item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval" }), "plan_approval", [null, "review"]],
    ["a gate not reached", item("running"), "plan_approval", [null, null]],
    ["any node of a done item", item("done"), "plan", [null, null]],
    ["any node of a cancelled item", item("cancelled"), "verification", [null, null]],
  ] as const)("%s", (_n, it, id, want) => expect(bar(it, id)).toEqual(want));

  it("never offers Retry on a running node", () => {
    expect(bar(item("running"), "verification")).not.toContain("retry-node");
    expect(bar(item("running"), "verification")).not.toContain("retry-from");
  });
});

describe("the node screen (D)", () => {
  it("shows the strip, the node's facts, its steps and tasks, and opens a task", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification");
    expect(await screen.findByRole("heading", { level: 1, name: "verification" })).toBeInTheDocument();
    const strip = screen.getByRole("group", { name: "Chain" });
    expect(within(strip).getAllByRole("button").map((b) => b.textContent)).toEqual(["plan", "plan_approval", "verification", "merge_request"]);
    expect(within(strip).getByRole("button", { name: "verification" })).toHaveAttribute("aria-current", "true");
    expect(screen.getAllByText("exec node · running", { exact: false })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: /code_review/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
  });

  it("changes node from the strip with replace, not a new history entry", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification");
    const before = window.history.length;
    await userEvent.click(within(await screen.findByRole("group", { name: "Chain" })).getByRole("button", { name: "merge_request" }));
    await waitFor(() => expect(where()).toBe("/work-items/w1/nodes/merge_request"));
    expect(window.history.length).toBe(before);
  });

  it("keeps the tab in the URL", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("tab", { name: "Config" }));
    expect(where()).toBe("/work-items/w1/nodes/verification?tab=config");
    expect(screen.getByText("default · frozen at intake", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("No item override on this node.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(where()).toBe("/work-items/w1/nodes/verification");
  });

  it("shows the node's own overrides as changed for this item", async () => {
    mount(item("running", null, { node_overrides: { verification: { attempts: 3, wall_clock_s: 2700 } } }), "/work-items/w1/nodes/verification?tab=config");
    expect(await screen.findByText("fix attempts 3 · wall clock 45m · changed for this item")).toBeInTheDocument();
  });

  it("filters the node's log by source and says when nothing is left", async () => {
    mount(item("running"), "/work-items/w1/nodes/verification?tab=log");
    expect(await screen.findByText(/loaded review_package/)).toBeInTheDocument();
    expect(screen.getByText(/code_review · loaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "tool" }));
    expect(screen.queryByText(/loaded review_package/)).toBeNull();
    expect(screen.getByText(/grep cache/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "stdout" }));
    expect(screen.getByText("No lines to show. Clear the filter, or the node has not started.")).toBeInTheDocument();
  });

  it("a gate shows its review path with the reject target, never an invented verdict", async () => {
    mount(item("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval", worker_sessions: [] }), "/work-items/w1/nodes/plan_approval");
    expect((await screen.findAllByText("gate · waiting for you", { exact: false })).length).toBeGreaterThan(0);
    expect(screen.getByText("waiting for your decision")).toBeInTheDocument();
    expect(screen.getByText("Goes back to plan with your note.")).toBeInTheDocument();
    expect(screen.queryByText("approve")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Review and decide" }));
    expect(where()).toBe("/work-items/w1/review?gate=plan_approval");
  });

  it("falls back to the item when the node does not exist", async () => {
    mount(item("running"), "/work-items/w1/nodes/nope");
    await waitFor(() => expect(where()).toBe("/work-items/w1"));
  });
});

describe("the strip", () => {
  it("scrolls the current chip into view and unmounts cleanly when the browser's scrollIntoView returns a value", async () => {
    const spy = vi.fn(() => ({ not: "a function" }));
    Element.prototype.scrollIntoView = spy as never;
    mount(item("running"), "/work-items/w1/nodes/verification");
    await screen.findByRole("heading", { level: 1, name: "verification" });
    expect(spy).toHaveBeenCalled();
    document.body.innerHTML = "";
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });
});

describe("the node's actions (D.6)", () => {
  it("Pause asks, then pauses; Skip asks and names the node", async () => {
    const calls = mount(item("running"), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Pause this item?" })).getByRole("button", { name: "Pause now" }));
    await waitFor(() => expect(posts(calls)).toEqual(["POST /work-items/w1/pause"]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Skip node" }));
    expect(screen.getByRole("dialog", { name: "Skip verification?" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Skip node" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").at(-1)).toEqual({ method: "POST", path: "/work-items/w1/skip", body: { path: "verification" } }));
  });

  it("a stopped node retries from its first step straight away", async () => {
    const calls = mount(item("failed", stop("failed")), "/work-items/w1/nodes/verification");
    await userEvent.click(await screen.findByRole("button", { name: "Retry node" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "verification" } }]));
  });

  it("a done node asks before it rewinds", async () => {
    const calls = mount(item("running"), "/work-items/w1/nodes/plan");
    await userEvent.click(await screen.findByRole("button", { name: "Retry from here" }));
    expect(posts(calls)).toEqual([]);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Retry from plan?" })).getByRole("button", { name: "Rewind and retry" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/retry", body: { path: "plan" } }]));
  });
});

describe("the task screen (E)", () => {
  const TASK = "/work-items/w1/nodes/verification?sel=verification.review.code_review";
  it("shows the task, its facts and its attempts, and the attempt is in the URL", async () => {
    mount(item("running", null, { worker_sessions: [session({ id: "s1", attempt: 1, status: "failed", wall_ms: 80000 }), session({ id: "s2", attempt: 2, status: "running" })] }), TASK);
    expect(await screen.findByRole("heading", { level: 1, name: "code_review" })).toBeInTheDocument();
    expect(screen.getByText(/agent task · running now · attempt 2 of 2/)).toBeInTheDocument();
    expect(screen.getByText("kraft-cb59 › verification › review")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /#1/ }));
    expect(where()).toContain("attempt=1");
    expect(await screen.findByText(/attempt 1 of 2/)).toBeInTheDocument();
    expect(screen.getByText("1m")).toBeInTheDocument();
    // Back on the newest, the pin drops: the screen follows the next attempt.
    await userEvent.click(screen.getByRole("button", { name: /#2/ }));
    expect(where()).not.toContain("attempt=");
  });

  it("has no Thread tab on an ordinary task, and Thread first on the escalation", async () => {
    mount(item("running"), TASK);
    await screen.findByRole("heading", { name: "code_review" });
    expect(screen.queryByRole("tab", { name: "Thread" })).toBeNull();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Log", "Config"]);
  });

  it("the escalation task opens on its Thread", async () => {
    mount(item("running", null, { worker_sessions: [session({ id: "e1", hook_point: "verification.escalation.escalation", status: "done" })] }), "/work-items/w1/nodes/verification?sel=verification.escalation.escalation", {
      "GET /work-items/w1/events": [200, [{ seq: 1, work_item_id: "w1", type: "escalation_message", payload: { thread: 1, turn: 1, message: "decide if the race is real" }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" }]],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "escalation" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Thread", "Overview", "Log", "Config"]);
    expect(await screen.findByText("decide if the race is real")).toBeInTheDocument();
  });

  it("names the harness the attempt ran on", async () => {
    mount(item("running", null, { worker_sessions: [session({ harness: "codex" })] }), TASK);
    expect(await screen.findByText("harness")).toBeInTheDocument();
    expect(screen.getByText("codex")).toBeInTheDocument();
  });

  it("shows the escalation thread through the thread picked, and all of it on the latest", async () => {
    const esc = (id: string, thread: number, created_at: string) => session({ id, hook_point: "verification.escalation.escalation", status: "done", thread, created_at });
    const msg = (seq: number, thread: number, message: string, session_id: string) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn: seq, message, session_id }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" });
    mount(item("running", null, { worker_sessions: [esc("e1", 1, "2026-09-13T09:00:00Z"), esc("e1b", 1, "2026-09-13T09:02:00Z"), esc("e2", 2, "2026-09-13T09:05:00Z")] }), "/work-items/w1/nodes/verification?sel=verification.escalation.escalation", {
      "GET /work-items/w1/events": [200, [msg(1, 1, "decide if the race is real", "e1"), msg(2, 1, "check the lock order too", "e1b"), msg(3, 2, "start over on the lock", "e2")]],
    });
    expect(await screen.findByText("start over on the lock")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: /thread 1/ })[0]);
    expect(await screen.findByText("decide if the race is real")).toBeInTheDocument();
    expect(screen.getByText("check the lock order too")).toBeInTheDocument();
    expect(screen.queryByText("start over on the lock")).toBeNull();
    expect(screen.getByText("1 later message after this thread.")).toBeInTheDocument();
  });

  it("shows a new thread on the latest without a reload, though the item's updated_at stays put", async () => {
    const esc = (id: string, thread: number, created_at: string) => session({ id, hook_point: "verification.escalation.escalation", status: "failed", thread, attempt: thread, created_at });
    const msg = (seq: number, thread: number, message: string, session_id: string) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn: 1, message, session_id }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" });
    const one = item("failed", stop("failed"), { worker_sessions: [esc("e1", 1, "2026-09-13T09:00:00Z")] });
    useStore.setState({ eventsByItem: {} });
    mount(one, "/work-items/w1/nodes/verification?sel=verification.escalation.escalation", { "GET /work-items/w1/events": [200, [msg(1, 1, "decide if the race is real", "e1")]] });
    expect(await screen.findByText("decide if the race is real")).toBeInTheDocument();
    stubFetch({
      "GET /work-items/w1": [200, { ...one, worker_sessions: [...one.worker_sessions, esc("e2", 2, "2026-09-13T09:05:00Z")] }],
      "GET /work-items/w1/events": [200, [msg(1, 1, "decide if the race is real", "e1"), msg(2, 2, "start over on the lock", "e2")]],
    });
    act(() => useStore.getState().applyEvent({ seq: 2, work_item_id: "w1", type: "worker_session_created", payload: {}, created_at: "t" }));
    expect(await screen.findByText("start over on the lock")).toBeInTheDocument();
    expect(screen.getByText(/attempt 2 of 2/)).toBeInTheDocument();
  });

  it("keeps the newest read of the thread when an older one answers late", async () => {
    const esc = (id: string, thread: number) => session({ id, hook_point: "verification.escalation.escalation", status: "failed", thread, attempt: thread });
    const msg = (seq: number, thread: number, message: string, session_id: string) => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread, turn: 1, message, session_id }, node_id: "verification", created_at: "2026-09-13T09:00:00Z" });
    const one = item("failed", stop("failed"), { worker_sessions: [esc("e1", 1)] });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, one] };
    useStore.setState({ eventsByItem: {} });
    const reads = holdFetch(/\/work-items\/w1\/events\?after_seq=/, answers);
    render(<MemoryRouter initialEntries={["/work-items/w1/nodes/verification?sel=verification.escalation.escalation"]}><Routes><Route path="/work-items/:id/nodes/:node" element={<NodeRoute />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(reads).toHaveLength(1));
    answers["GET /work-items/w1"] = [200, { ...one, worker_sessions: [esc("e1", 1), esc("e2", 2)] }];
    act(() => useStore.getState().applyEvent({ seq: 2, work_item_id: "w1", type: "worker_session_created", payload: {}, created_at: "t" }));
    await waitFor(() => expect(reads).toHaveLength(2));
    await act(async () => reads[1]([msg(1, 1, "decide if the race is real", "e1"), msg(2, 2, "start over on the lock", "e2")]));
    expect(await screen.findByText("start over on the lock")).toBeInTheDocument();
    await act(async () => reads[0]([msg(1, 1, "decide if the race is real", "e1")]));
    expect(screen.getByText("start over on the lock")).toBeInTheDocument();
  });

  it("says what a task that has not started waits for", async () => {
    mount(item("running", null, { worker_sessions: [] }), "/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(await screen.findByText(/After step checks finishes\./)).toBeInTheDocument();
  });

  it("reads the log of the attempt and filters it", async () => {
    mount(item("running"), `${TASK}&tab=log`);
    expect(await screen.findByText(/loaded review_package/)).toBeInTheDocument();
    expect(screen.getByText(/following/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "sys" }));
    expect(screen.getByText("No lines to show. Clear the filter, or the task has not started.")).toBeInTheDocument();
  });
});
void [Where];
