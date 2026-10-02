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
const mount = (path: string) => {
  stubFetch({ "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [
    { document_id: "d1", title: "Review notes", path: "a.md", kind: "reviews", worker_session_id: "verification.review.code_review-1", hook_point: "verification.review.code_review", attempt: 1 },
    { document_id: "d2", title: "Other", path: "b.md", kind: "plans", worker_session_id: "x", hook_point: "plan.write.plan", attempt: 1 },
  ] }] });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/work-items/:id/nodes/:node" element={<><Workspace item={item} reload={() => {}} /><Where /></>} /></Routes>
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
    expect(within(pane("escalation")).getByText("needs you", { selector: ".ip-attempt-state" })).toBeInTheDocument();
  });

  it("names the harness the attempt ran on in Overview", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    const facts = within(pane("code_review")).getByText("harness").closest("div")!;
    expect(facts).toHaveTextContent("harnesscodex");
  });

  it("has no Thread tab on an ordinary task, and offers Retry once it stopped", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(within(pane("code_review")).queryByRole("tab", { name: "Thread" })).toBeNull();
    expect(within(pane("code_review")).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
