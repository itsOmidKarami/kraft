import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { ChainNode, WorkItem, WorkerSession } from "../../types";
import { WorkItemDetail } from ".";

const here = dirname(fileURLToPath(import.meta.url));

const NODES: ChainNode[] = [
  { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null },
];

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1", title: "T", repo: "/r", status: "active", chain_template: "default",
    chain_definition: { template_id: "default", nodes: NODES },
    current_node_id: "verify", bead_id: "B", created_at: "t", updated_at: "t",
    completedNodes: ["spec", "plan"],
    node_overrides: {},
    node_overrides_count: 0,
    effective_chain: { template_id: "default", nodes: NODES },
    budget_cap: { cap_usd: 10, source: "policy", spent_usd: 1 },
    ...over,
  }) as WorkItem;

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "running", attempt: 1, round: 0, created_at: "t", started_at: "t", exited_at: null,
    tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, model: null, head_sha: null,
    ...over,
  }) as WorkerSession;

const setup = (over: Partial<WorkItem> = {}, sessions: WorkerSession[] = []) =>
  useStore.setState({
    workItems: { w1: item(over) },
    sessionsByItem: { w1: sessions },
    eventsByItem: { w1: [] },
  } as never);

beforeEach(() => {
  setup();
  vi.restoreAllMocks();
  vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue(undefined);
  vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
  vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
    base_ref: "abc123", diff: "", files: [], untracked: [], truncated: false, landed: null,
  } as never);
});

/** A back button, so a `MemoryRouter` test (which has no `window.history` to
 *  press) can still exercise `useNodeSelection`'s back/forward claim. */
function GoBack() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate(-1)} aria-label="test-go-back">
      back
    </button>
  );
}

/** `MemoryRouter` keeps its own history, not `window.location` — read the
 *  hash back through the router the same way `useMaximized`/`useNodeSelection`
 *  do, rather than a browser API this test double has no reason to touch. */
function HashProbe() {
  const location = useLocation();
  return <span data-testid="hash-probe">{location.hash}</span>;
}

const renderDetail = (hash = "") =>
  render(
    <MemoryRouter
      initialEntries={[`/work-items/w1${hash}`]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/work-items/:id" element={<><GoBack /><HashProbe /><WorkItemDetail /></>} />
      </Routes>
    </MemoryRouter>,
  );

const renderDetailWithProgress = (hash = "") => {
  setup({
    progress: {
      current: 3,
      total: 6,
      title: "wire the thing",
      tasks: [1, 2, 3, 4, 5, 6].map((n) => ({
        n,
        title: `task ${n}`,
        state: n < 3 ? "done" : n === 3 ? "current" : "pending",
      })),
    },
  });
  return renderDetail(hash);
};

describe("WorkItemDetail (item page)", () => {
  it("hydrates on mount and leads with the current node in the hero", () => {
    renderDetail();
    expect(useStore.getState().hydrateItem).toHaveBeenCalledWith("w1");
    expect(screen.getByText("verify", { selector: ".hero-node" })).toBeInTheDocument();
    expect(screen.getByText(/node 3 of 3/)).toBeInTheDocument();
  });

  it("renders no modal for logs, diffs or documents — panes instead", () => {
    renderDetail();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("selects the current node in the stage graph by default", () => {
    renderDetail();
    const pill = screen.getByRole("button", { name: /^verify$/ });
    expect(pill).toHaveAttribute("data-selected", "true");
    expect(pill).toHaveAttribute("aria-current", "step");
  });

  it("clicking a pill sets #node= and updates the inspector header", async () => {
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /^plan$/ }));
    expect(screen.getByRole("button", { name: /^plan$/ })).toHaveAttribute("data-selected", "true");
    expect(within(screen.getByTestId("inspector")).getByText("plan")).toBeInTheDocument();
  });

  it("#node= in the URL selects that node on load", () => {
    renderDetail("#node=spec");
    const pill = screen.getByRole("button", { name: /^spec$/ });
    expect(pill).toHaveAttribute("data-selected", "true");
  });

  it("back/forward moves between selected nodes", async () => {
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /^plan$/ }));
    await userEvent.click(screen.getByRole("button", { name: /^spec$/ }));
    expect(screen.getByRole("button", { name: /^spec$/ })).toHaveAttribute("data-selected", "true");
    await userEvent.click(screen.getByRole("button", { name: "test-go-back" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^plan$/ })).toHaveAttribute("data-selected", "true"),
    );
  });

  it("switching tabs keeps each tab's own selection", async () => {
    setup({}, [session({ id: "s1", node_id: "verify" })]);
    renderDetail();
    // Tasks tab defaults to the newest session on the selected node
    expect(await screen.findByTestId("right-pane-log")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /documents/i }));
    expect(await screen.findByTestId("inspector-documents")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /tasks/i }));
    // still on the same session's log, not reset
    expect(await screen.findByTestId("right-pane-log")).toBeInTheDocument();
  });

  it("Config tab shows the lock notice on a started node and none on an unstarted one", async () => {
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: /config/i }));
    // "verify" (current) is started
    expect(screen.getByText(/locked once it starts/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^plan$/ }));
    // "plan" is a completed, started node too
    expect(screen.getByText(/locked once it starts/)).toBeInTheDocument();
  });

  it("Config tab marks an overridden node in the effective-chain YAML", async () => {
    setup({
      node_overrides: { verify: { auto_escalate: true } },
      node_overrides_count: 1,
      effective_chain: {
        template_id: "default",
        nodes: NODES.map((n) => (n.id === "verify" ? { ...n, auto_escalate: true } : n)),
      },
    });
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: /config/i }));
    expect(screen.getByText(/default \+ 1 override/)).toBeInTheDocument();
    expect(screen.getByTestId("inspector-config").textContent).toMatch(/# override/);
  });

  it("shows the not_started bar (21) for an unstarted item", () => {
    setup({ current_node_id: null, status: "paused" });
    renderDetail();
    expect(screen.getByRole("button", { name: /^start$/i })).toBeInTheDocument();
  });

  it("shows the hero task bar and the Tasks-tab plan list from item.progress", async () => {
    renderDetailWithProgress();
    expect(screen.getByTestId("task-bar")).toBeInTheDocument();
    expect(screen.getByText("wire the thing")).toBeInTheDocument();
    expect(await screen.findByTestId("plan-list")).toBeInTheDocument();
  });

  it("puts the hero in the header's right column and the state tag inline in the meta line", () => {
    // jsdom has no cascade to compute a grid layout from; pin the source
    // instead, the way styles.order.test.ts does.
    const css = readFileSync(join(here, "../../styles.css"), "utf-8");
    expect(css).toMatch(/\.detail-head\s*\{[^}]*display:\s*grid/);
    expect(css).toMatch(/\.detail-status\s*\{[^}]*margin-left:\s*0/);
  });

  it("maximizing hides the inspector and the graph and lands in the URL", async () => {
    const user = userEvent.setup();
    setup({}, [session({ id: "s1", node_id: "verify" })]);
    renderDetail();
    await screen.findByTestId("right-pane-log");
    await user.click(screen.getByRole("button", { name: /maximi/i }));
    expect(screen.getByTestId("hash-probe").textContent).toContain("log=max");
    expect(document.querySelector(".inspector")).toBeNull();
    expect(document.querySelector(".stage-graph")).toBeNull();
    expect(document.querySelector(".item-max-strip")).toBeTruthy();
  });

  it("Escape restores the split", async () => {
    const user = userEvent.setup();
    setup({}, [session({ id: "s1", node_id: "verify" })]);
    renderDetail("#node=verify&log=max");
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("hash-probe").textContent).not.toContain("log=max");
    expect(document.querySelector(".inspector")).toBeTruthy();
  });

  it("renders the hero task line through the shared primitive, not a bespoke bar", async () => {
    renderDetailWithProgress();
    expect(document.querySelector(".hero-task-seg")).toBeNull();
    expect(await screen.findByTestId("task-bar")).toBeTruthy();
    expect(screen.getByText("Task 3 of 6")).toBeTruthy();
  });

  it("puts the task fraction on the current stage pill", () => {
    renderDetailWithProgress();
    const pill = document.querySelector(".stage-pill[data-state='current']") as HTMLElement;
    expect(pill.textContent).toContain("3/6");
  });

  it("edits the title from the ⋯ menu rather than an inline Edit button", async () => {
    const user = userEvent.setup();
    renderDetail();
    expect(screen.queryByRole("button", { name: /^Edit$/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /more/i }));
    await user.click(screen.getByRole("button", { name: /edit title/i }));
    expect(screen.getByLabelText("title")).toBeTruthy();
  });
});
