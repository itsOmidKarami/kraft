import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { detail, stubFetch } from "../../item/testkit";
import { Item } from "./Item";

const SESSION = { id: "s1", work_item_id: "w1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1, thread: 1, round: 0, created_at: "2026-09-13T09:00:00Z", started_at: "2026-09-13T09:00:00Z", exited_at: null };
const agent = (description: string, status = "running") => ({ id: description, description, status });

function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}</output>;
}
/** The running item screen, whose session's log answers with `subagents` beside its lines. */
function mount(subagents: unknown[]) {
  const calls = stubFetch({
    "GET /work-items/w1": [200, detail({ display_status: "running", worker_sessions: [SESSION as never] })],
    "GET /work-items/w1/compare": [200, { files: [] }],
    "GET /work-items/w1/draft": [200, { ops: [] }],
    "GET /work-items/w1/sessions/s1/log": [200, { lines: [{ n: 1, t: "0:03", src: "agent", text: "x", summary: "loaded review_package" }], subagents }],
  });
  render(
    <MemoryRouter initialEntries={["/work-items/w1"]}>
      <Routes>
        <Route path="/work-items/:id" element={<><Item /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("the item screen's running sub-agents", () => {
  it("names them in the Log block, and a tap opens the task's Overview, with no second request for the list", async () => {
    const calls = mount([agent("Explore codebase"), agent("Review diff"), agent("Old", "completed")]);
    await userEvent.click(await screen.findByRole("button", { name: "2 sub-agents running: Explore codebase, Review diff" }));
    expect(screen.getByLabelText("where").textContent).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(calls.some((c) => c.path.endsWith("/subagents"))).toBe(false);
  });

  it.each([
    { id: "one running, in the singular", subagents: [agent("Lint")], said: "1 sub-agent running: Lint" },
    { id: "none listed", subagents: [], said: null },
    { id: "only finished ones", subagents: [agent("Old", "completed"), agent("Cut", "cut_off")], said: null },
  ])("has $id: $said", async ({ subagents, said }) => {
    mount(subagents);
    await screen.findByText(/loaded review_package/);
    expect(screen.queryByRole("button", { name: /sub-agents? running/ })?.textContent ?? null).toBe(said);
  });
});
