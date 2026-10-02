import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ItemPage } from "./ItemPage";
import { detail, stubFetch } from "./testkit";
import { usePaneMemory } from "./Workspace";

const Where = () => {
  const l = useLocation();
  return <span data-testid="where">{l.pathname + l.search}</span>;
};
const mount = () =>
  render(
    <MemoryRouter initialEntries={["/work-items/w1"]}>
      <Routes>
        <Route path="/work-items/:id" element={<><ItemPage /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));
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
