import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detail, stubFetch } from "./testkit";
import { usePaneMemory, Workspace } from "./Workspace";

beforeEach(() => {
  stubFetch();
  usePaneMemory.setState({ pane: { open: true, userCollapsed: false } });
});
afterEach(() => vi.unstubAllGlobals());

let nav: ReturnType<typeof useNavigate>;
function Where() {
  const loc = useLocation();
  nav = useNavigate();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}
const mount = (path = "/work-items/w1") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        {["/work-items/:id", "/work-items/:id/nodes/:node"].map((p) => (
          <Route key={p} path={p} element={<><Workspace item={detail()} reload={() => {}} /><Where /></>} />
        ))}
      </Routes>
    </MemoryRouter>,
  );
const where = () => screen.getByTestId("where").textContent;

describe("Workspace", () => {
  it("opens on the chain's pane, and a node click selects it in the URL", async () => {
    mount();
    expect(screen.getByRole("complementary", { name: "default pane" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "plan, node, done" }));
    expect(where()).toBe("/work-items/w1?sel=plan");
    expect(screen.getByRole("complementary", { name: "plan pane" })).toBeInTheDocument();
  });

  it("restores the selection and tab from a shared URL", () => {
    mount("/work-items/w1?tab=config");
    expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps a collapse the person chose while they pick other nodes, and the rail expands it", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Collapse pane" }));
    await userEvent.click(screen.getByRole("button", { name: "verification, node, running" }));
    expect(where()).toBe("/work-items/w1?sel=verification");
    expect(screen.getByRole("complementary", { name: "verification pane, collapsed" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Expand pane" }));
    expect(screen.getByRole("complementary", { name: "verification pane" })).toBeInTheDocument();
  });

  it("keeps the collapse when the page is left for another item and comes back (within the session)", async () => {
    const first = mount();
    await userEvent.click(screen.getByRole("button", { name: "Collapse pane" }));
    first.unmount();
    mount("/work-items/w1?sel=plan");
    expect(screen.getByRole("complementary", { name: "plan pane, collapsed" })).toBeInTheDocument();
  });

  it("opens the pane when Item settings sends it to the chain's Config", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Collapse pane" }));
    const { act } = await import("@testing-library/react");
    act(() => nav("/work-items/w1?tab=config"));
    const pane = screen.getByRole("complementary", { name: "default pane" });
    expect(within(pane).getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
  });

  it("goes back to the chain's pane from a node's crumb", async () => {
    mount("/work-items/w1?sel=plan");
    await userEvent.click(within(screen.getByRole("complementary", { name: "plan pane" })).getByRole("button", { name: "default" }));
    expect(where()).toBe("/work-items/w1");
  });
});
