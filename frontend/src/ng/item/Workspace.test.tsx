import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkerSession } from "../../types";
import { detail, fresh, stubFetch } from "./testkit";
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
const mount = (path = "/work-items/w1", over: Parameters<typeof detail>[0] = {}) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        {["/work-items/:id", "/work-items/:id/nodes/:node"].map((p) => (
          <Route key={p} path={p} element={<><Workspace item={detail(over)} version="1" reload={() => {}} /><Where /></>} />
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

  it("opens a never-started item's chain readable, at no less than 80%, as its draft page does", () => {
    render(<MemoryRouter initialEntries={["/work-items/w1"]}><Workspace item={fresh()} version="1" reload={() => {}} /></MemoryRouter>);
    // jsdom has no layout, so a plain fit would floor at 30%.
    expect((document.querySelector(".canvas-world") as HTMLElement).style.transform).toContain("scale(0.8)");
  });

  it("names the chain of an item filed with no chain by the chain it runs (Kraft-9d8b2.52)", () => {
    mount("/work-items/w1", { chain_template: null } as never);
    expect(screen.getByRole("complementary", { name: "default pane" })).toBeInTheDocument();
  });

  it("restores the selection and tab from a shared URL", () => {
    mount("/work-items/w1?tab=config");
    expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
  });

  it("opens the document a shared ?doc= names, and closing it drops the param", async () => {
    stubFetch({ "GET /documents/d1": [200, { id: "d1", title: "Review notes", path: "/r/n.md", content: "# Review notes\n\nThe cache has **no size bound**." }] });
    mount("/work-items/w1?doc=d1");
    expect(await screen.findByText("no size bound")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(where()).toBe("/work-items/w1");
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

  it("opens the node view by Focus, pushing the URL, and the strip's ← chain comes back", async () => {
    mount("/work-items/w1?sel=verification");
    await userEvent.click(screen.getByRole("button", { name: /Focus/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification");
    expect(screen.getByRole("group", { name: "verification" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "code_review, agent task, not started" }).closest("button")!);
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    await userEvent.click(screen.getByRole("button", { name: "Back to the chain" }));
    expect(where()).toBe("/work-items/w1");
  });

  it("opens the node view when a step or task is picked from a node's pane on the chain, and ← chain comes back", async () => {
    mount("/work-items/w1?sel=verification");
    const pane = () => screen.getByRole("complementary", { name: /pane$/ });
    await userEvent.click(within(pane()).getByRole("button", { name: /review/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.review");
    expect(screen.getByRole("group", { name: "verification" })).toBeInTheDocument();
    await userEvent.click(within(pane()).getByRole("button", { name: /code_review/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    await userEvent.click(screen.getByRole("button", { name: "Back to the chain" }));
    expect(where()).toBe("/work-items/w1");
  });

  it("pushes one entry for a step picked on the chain, and replaces it for picks inside the node view", async () => {
    const { act } = await import("@testing-library/react");
    mount("/work-items/w1?sel=verification");
    const pane = () => screen.getByRole("complementary", { name: /pane$/ });
    await userEvent.click(within(pane()).getByRole("button", { name: /review/ }));
    await userEvent.click(within(pane()).getByRole("button", { name: /code_review/ }));
    expect(where()).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    act(() => nav(-1));
    expect(where()).toBe("/work-items/w1?sel=verification");
  });

  it("goes back to the chain's pane from a node's crumb", async () => {
    mount("/work-items/w1?sel=plan");
    await userEvent.click(within(screen.getByRole("complementary", { name: "plan pane" })).getByRole("button", { name: "default" }));
    expect(where()).toBe("/work-items/w1");
  });
});

// R10a-06: a running node read "running 0s" for up to 30 s, the clock ticking every 30 s.
describe("the item page's clock", () => {
  const running = { id: "s1", node_id: "verification", hook_point: "verification.review.code_review", status: "running", attempt: 1, round: 0, thread: 1, created_at: "2026-09-13T09:59:57Z", started_at: "2026-09-13T09:59:57Z", exited_at: null, model: "m", tokens_in: 1, tokens_out: 1, cost_usd: 0, wall_ms: null } as unknown as WorkerSession;
  afterEach(() => vi.useRealTimers());

  it("counts a running node's time every second", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(Date.parse("2026-09-13T10:00:00Z"));
    render(
      <MemoryRouter initialEntries={["/work-items/w1/nodes/verification"]}>
        <Routes><Route path="/work-items/:id/nodes/:node" element={<Workspace item={detail({ worker_sessions: [running] })} version="1" reload={() => {}} />} /></Routes>
      </MemoryRouter>,
    );
    const pane = screen.getByRole("complementary", { name: "verification pane" });
    expect(within(pane).getByText(/exec node · running 3s/)).toBeInTheDocument();
    await act(async () => void vi.advanceTimersByTime(5_000));
    expect(within(pane).getByText(/exec node · running 8s/)).toBeInTheDocument();
  });
});
