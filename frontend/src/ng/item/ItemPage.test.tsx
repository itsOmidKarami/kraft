import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import type { WorkerSession } from "../../types";
import { ItemPage } from "./ItemPage";
import { detail, stubFetch } from "./testkit";
import { usePaneMemory } from "./Workspace";

const Where = () => {
  const l = useLocation();
  return <span data-testid="where">{l.pathname + l.search}</span>;
};
const mount = (path = "/work-items/w1") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/work-items/:id" element={<><ItemPage /><Where /></>} />
        <Route path="/work-items/:id/nodes/:node" element={<><ItemPage /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  usePaneMemory.setState({ pane: { open: true, userCollapsed: false } });
  useStore.setState({ eventsByItem: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe("ItemPage", () => {
  it("opens a collapsed pane on the gate from the banner's Open gate", async () => {
    const gated = detail({ status: "needs_human", display_status: "needs_you", current_node_id: "plan_approval", pending_gate: "plan_approval", stop: { kind: "gate", node: "plan_approval", task: null, resume_at: null, reason: null } as never });
    stubFetch({ "GET /work-items/w1": [200, gated] });
    usePaneMemory.setState({ pane: { open: false, userCollapsed: true } });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open gate" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/w1?sel=plan_approval");
    expect(screen.getByRole("button", { name: "Collapse pane" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand pane" })).toBeNull();
  });
});

const sess = (id: string, hook_point: string, attempt: number, over: Partial<WorkerSession> = {}) =>
  ({ id, node_id: "verification", hook_point, status: "failed", attempt, round: 0, thread: 1, created_at: `2026-09-13T09:0${attempt}:00Z`, started_at: null, exited_at: null, wall_ms: 1000, model: "sonnet", ...over }) as WorkerSession;
const msg = (seq: number, thread: number, message: string, session_id: string) =>
  ({ seq, work_item_id: "w1", type: "escalation_message", node_id: "verification", payload: { thread, turn: 1, message, session_id }, created_at: "2026-09-13T09:00:00Z" });
const pane = (name: string) => screen.getByRole("complementary", { name: `${name} pane` });
/** A live frame for the item, as the socket delivers it: the page reads the item again. */
const live = (seq: number) => act(() => useStore.getState().applyEvent({ seq, work_item_id: "w1", type: "worker_session_created", payload: {}, created_at: "t" }));

// A new session or a thread message leaves the item's updated_at where it was:
// what the pane reads beside the item must still follow the new attempt.
describe("ItemPage, live", () => {
  it("shows a reply's new thread on the escalation's Thread tab without a reload", async () => {
    const one = detail({ worker_sessions: [sess("e1", "escalation", 1)] });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, one], "GET /work-items/w1/events": [200, [msg(1, 1, "Why did lint fail?", "e1")]] };
    stubFetch(answers);
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation");
    expect(await within(await screen.findByRole("complementary", { name: "escalation pane" })).findByText("Why did lint fail?")).toBeInTheDocument();
    answers["GET /work-items/w1"] = [200, { ...one, worker_sessions: [...one.worker_sessions, sess("e2", "escalation", 2, { thread: 2, status: "running" })] }];
    answers["GET /work-items/w1/events"] = [200, [msg(1, 1, "Why did lint fail?", "e1"), msg(2, 2, "sdas", "e2")]];
    live(2);
    expect(await within(pane("escalation")).findByText("sdas")).toBeInTheDocument();
    expect(within(pane("escalation")).getByText(/turn 2 of 2/)).toBeInTheDocument();
    expect(within(pane("escalation")).getByRole("region", { name: "Thread 2" })).toHaveTextContent("thread 2 · 1 turn");
  });

  it("shows a new attempt's documents on Overview while the pane follows the newest", async () => {
    const path = "verification.review.code_review";
    const one = detail({ worker_sessions: [sess("r1", path, 1)] });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, one] };
    stubFetch(answers);
    mount(`/work-items/w1/nodes/verification?sel=${path}`);
    expect(await within(await screen.findByRole("complementary", { name: "code_review pane" })).findByText("This attempt wrote no documents.")).toBeInTheDocument();
    answers["GET /work-items/w1"] = [200, { ...one, worker_sessions: [...one.worker_sessions, sess("r2", path, 2, { status: "done" })] }];
    answers["GET /work-items/w1/documents"] = [200, { work_item_id: "w1", documents: [{ document_id: "d2", title: "Review notes", path: "a.md", kind: "reviews", worker_session_id: "r2", hook_point: path, attempt: 2 }] }];
    live(2);
    expect(await within(pane("code_review")).findByRole("button", { name: /Review notes/ })).toBeInTheDocument();
    expect(within(pane("code_review")).getByText(/attempt 2 of 2/)).toBeInTheDocument();
  });

  it("keeps a pinned older attempt when a newer one starts, and the count shows the newer one", async () => {
    const path = "verification.review.code_review";
    const two = detail({ worker_sessions: [sess("r1", path, 1), sess("r2", path, 2)] });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, two] };
    stubFetch(answers);
    mount(`/work-items/w1/nodes/verification?sel=${path}&attempt=1`);
    expect(await within(await screen.findByRole("complementary", { name: "code_review pane" })).findByText(/attempt 1 of 2/)).toBeInTheDocument();
    answers["GET /work-items/w1"] = [200, { ...two, worker_sessions: [...two.worker_sessions, sess("r3", path, 3, { status: "running" })] }];
    live(2);
    expect(await within(pane("code_review")).findByText(/attempt 1 of 3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Later attempt" })).toBeEnabled();
  });

  it("reads the diff line again when a new attempt starts", async () => {
    const path = "verification.review.code_review";
    const one = detail({ worker_sessions: [sess("r1", path, 1)] });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, one], "GET /work-items/w1/diff": [200, { files: [{ path: "a.py", insertions: 1, deletions: 0 }] }] };
    stubFetch(answers);
    mount(`/work-items/w1/nodes/verification?sel=${path}`);
    expect(await screen.findByText(/1 file/)).toBeInTheDocument();
    answers["GET /work-items/w1"] = [200, { ...one, worker_sessions: [...one.worker_sessions, sess("r2", path, 2, { status: "running" })] }];
    answers["GET /work-items/w1/diff"] = [200, { files: [{ path: "a.py", insertions: 1, deletions: 0 }, { path: "b.py", insertions: 2, deletions: 0 }] }];
    live(2);
    expect(await screen.findByText(/2 files/)).toBeInTheDocument();
  });

  it("shows a draft applied while Config is open, without a reload", async () => {
    const applied = { seq: 2, work_item_id: "w1", type: "chain_revised", node_id: null, payload: { gate: null, source: "draft", changes: [{ op: "override", path: "merge_request.open.open_draft", task_config: { model: "opus" } }], diff: [] }, created_at: "2026-09-13T09:00:00Z" };
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, detail()], "GET /work-items/w1/events": [200, []] };
    stubFetch(answers);
    mount("/work-items/w1?tab=config");
    const pane = await screen.findByRole("complementary", { name: "default pane" });
    await waitFor(() => expect(within(pane).queryByText("model opus")).toBeNull());
    answers["GET /work-items/w1/events"] = [200, [applied]];
    live(2);
    expect(await within(pane).findByText("model opus")).toBeInTheDocument();
  });

  it("adds a new event to the chain's Recent without a reload", async () => {
    const started = (seq: number, node: string) => ({ seq, work_item_id: "w1", type: "node_started", node_id: node, payload: { node_id: node }, created_at: "2026-09-13T09:00:00Z" });
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1": [200, detail()], "GET /work-items/w1/events": [200, [started(1, "plan")]] };
    stubFetch(answers);
    mount("/work-items/w1");
    const pane = await screen.findByRole("complementary", { name: "default pane" });
    expect(await within(pane).findByText("plan started")).toBeInTheDocument();
    answers["GET /work-items/w1/events"] = [200, [started(1, "plan"), started(2, "verification")]];
    live(2);
    expect(await within(pane).findByText("verification started")).toBeInTheDocument();
  });
});
