import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkerSession } from "../../../types";
import { detail, stubFetch } from "../testkit";
import { usePaneMemory, Workspace } from "../Workspace";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));

const sess = (hook_point: string, attempt: number, over: Partial<WorkerSession> = {}) =>
  ({ id: `${hook_point}-${attempt}`, node_id: hook_point.split(".")[0] === "escalation" ? "verification" : hook_point.split(".")[0], hook_point, status: "done", attempt, round: attempt - 1, thread: 1, created_at: `2026-09-13T09:0${attempt}:00Z`, started_at: null, exited_at: null, wall_ms: 60_000, model: "sonnet", tokens_in: 1, tokens_out: 1, cost_usd: 0.1, head_sha: "abc1234567890", ...over }) as WorkerSession;
const item = detail({ worker_sessions: [sess("verification.review.code_review", 1), sess("verification.review.code_review", 2, { status: "failed", harness: "codex" }), sess("escalation", 1, { node_id: "verification", status: "needs_context" })] });
function Where() { const l = useLocation(); return <output data-testid="where">{l.pathname + l.search}</output>; }
const mount = (path: string, it = item, events: unknown[] = []) => {
  stubFetch({ "GET /work-items/w1/events": [200, events], "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [
    { document_id: "d1", title: "Review notes", path: "a.md", kind: "reviews", worker_session_id: "verification.review.code_review-1", hook_point: "verification.review.code_review", attempt: 1 },
    { document_id: "d2", title: "Other", path: "b.md", kind: "plans", worker_session_id: "x", hook_point: "plan.write.plan", attempt: 1 },
  ] }] });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/work-items/:id/nodes/:node" element={<><Workspace item={it} version="1" reload={() => {}} /><Where /></>} /></Routes>
    </MemoryRouter>,
  );
};
const pane = (name: string) => screen.getByRole("complementary", { name: `${name} pane` });

describe("task pane", () => {
  it("opens on the latest attempt; the switcher moves to an earlier one and the URL keeps it", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(within(pane("code_review")).getByText(/attempt 2 of 2/)).toBeInTheDocument();
    expect(within(pane("code_review")).getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("button", { name: "Earlier attempt" }));
    expect(screen.getByTestId("where").textContent).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review&attempt=1");
    expect(within(pane("code_review")).getByText(/attempt 1 of 2/)).toBeInTheDocument();
  });

  it("drops the pin when › reaches the newest attempt, so the pane follows the next one", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&attempt=1");
    await userEvent.click(screen.getByRole("button", { name: "Later attempt" }));
    expect(screen.getByTestId("where").textContent).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(within(pane("code_review")).getByText(/attempt 2 of 2/)).toBeInTheDocument();
  });

  it("puts the attempt switcher above the tabs, outside every tab's panel", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&tab=input");
    const switcher = within(pane("code_review")).getByText(/attempt 2 of 2/);
    const tablist = within(pane("code_review")).getByRole("tablist");
    expect(switcher.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(screen.getByRole("tabpanel")).queryByText(/attempt/)).toBeNull();
  });

  it("lists only this attempt's documents under the result", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&attempt=1");
    expect(await within(pane("code_review")).findByRole("button", { name: /Review notes/ })).toBeInTheDocument();
    expect(within(pane("code_review")).queryByRole("button", { name: /Other/ })).toBeNull();
  });

  it("puts Thread first on the escalation task and opens on it", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation");
    const tabs = within(pane("escalation")).getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Thread", "Overview", "Input", "Output", "Log", "Config"]);
    expect(within(pane("escalation")).getByRole("tab", { name: "Thread" })).toHaveAttribute("aria-selected", "true");
    // One turn: no switcher, the subtitle says how it went.
    expect(within(pane("escalation")).getByText("escalation · agent task · needs you")).toBeInTheDocument();
    expect(within(pane("escalation")).queryByRole("button", { name: "Earlier attempt" })).toBeNull();
  });

  it("hands focus to the other arrow when one reaches the end", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    await userEvent.click(screen.getByRole("button", { name: "Earlier attempt" }));
    expect(screen.getByRole("button", { name: "Earlier attempt" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Later attempt" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Later attempt" }));
    expect(screen.getByRole("button", { name: "Earlier attempt" })).toHaveFocus();
  });

  it("names the harness the attempt ran on in Overview", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    const facts = within(pane("code_review")).getByText("harness").closest("div")!;
    expect(facts).toHaveTextContent("harnesscodex");
  });

  it("shows the thread through the turn picked above the tabs, and all of it on the latest", async () => {
    const turns = detail({ worker_sessions: [sess("escalation", 1, { id: "e1", node_id: "verification" }), sess("escalation", 2, { id: "e2", node_id: "verification" })] });
    const msg = (seq: number, turn: number, message: string, session_id: string, node_id = "verification") => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread: 1, turn, message, session_id }, node_id, created_at: "2026-09-13T09:00:00Z" });
    // The last message is about another node: the latest turn here still shows it.
    const events = [msg(1, 1, "Why did lint fail?", "e1"), msg(2, 2, "Try the other config.", "e2"), msg(3, 3, "And the merge request?", "m1", "merge_request")];
    const first = mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation&attempt=1", turns, events);
    expect(await screen.findByText("Why did lint fail?")).toBeInTheDocument();
    expect(screen.queryByText("Try the other config.")).toBeNull();
    expect(screen.getByText("2 later messages after this turn.")).toBeInTheDocument();
    first.unmount();
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation", turns, events);
    expect(await screen.findByText("Try the other config.")).toBeInTheDocument();
    expect(screen.getByText("Why did lint fail?")).toBeInTheDocument();
    expect(screen.getByText("And the merge request?")).toBeInTheDocument();
  });

  it("counts the escalation's sessions as turns, the word its Thread tab uses (R10b-06)", () => {
    const turns = detail({ worker_sessions: [sess("escalation", 1, { id: "e1", node_id: "verification" }), sess("escalation", 2, { id: "e2", node_id: "verification" })] });
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation", turns);
    const p = pane("escalation");
    expect(within(p).getByText("turn 2 of 2")).toBeInTheDocument();
    expect(within(p).queryByText(/attempt 2 of 2/)).toBeNull();
    expect(within(p).getByRole("button", { name: "Earlier turn" })).toBeInTheDocument();
  });

  it("cuts the thread at a turn whose message names no session by the turn's place among its node's", async () => {
    const turns = detail({ worker_sessions: [sess("escalation", 1, { id: "e1", node_id: "verification" }), sess("escalation", 2, { id: "e2", node_id: "verification" })] });
    const msg = (seq: number, turn: number, message: string, node_id = "verification") => ({ seq, work_item_id: "w1", type: "escalation_message", payload: { thread: 1, turn, message }, node_id, created_at: "2026-09-13T09:00:00Z" });
    const events = [msg(1, 1, "And the plan?", "plan"), msg(2, 2, "Why did lint fail?"), msg(3, 3, "Try the other config.")];
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation&attempt=1", turns, events);
    expect(await screen.findByText("Why did lint fail?")).toBeInTheDocument();
    expect(screen.queryByText("Try the other config.")).toBeNull();
    expect(screen.getByRole("region", { name: "Thread 1" })).toHaveTextContent("thread 1 · turn 2 of 3");
  });

  it("has no Thread tab on an ordinary task, and offers Retry once it stopped", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review", { ...item, display_status: "failed" });
    expect(within(pane("code_review")).queryByRole("tab", { name: "Thread" })).toBeNull();
    expect(within(pane("code_review")).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it.each([
    ["the node the run stands on: Skip task before it runs", "verification", "verification.review.code_review", "code_review", true],
    ["a later node: nothing to skip yet", "verification", "merge_request.open.open_draft", "open_draft", false],
  ])("keeps a task's tabs before it runs, each saying why it is empty, on %s", async (_, current, sel, name, skip) => {
    const node = sel.split(".")[0];
    mount(`/work-items/w1/nodes/${node}?sel=${sel}&tab=log`, { ...item, current_node_id: current, worker_sessions: [] });
    expect(within(pane(name)).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Input", "Output", "Log", "Config"]);
    expect(within(pane(name)).getByText("No log yet. It starts when the step before this one finishes.")).toBeInTheDocument();
    expect(within(pane(name)).queryByRole("button", { name: "Skip task" }) !== null).toBe(skip);
    expect(within(pane(name)).queryByRole("button", { name: "Pause" })).toBeNull();
  });
});
