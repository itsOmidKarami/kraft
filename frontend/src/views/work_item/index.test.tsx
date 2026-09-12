import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { ChainNode, KraftEvent, WorkItem, WorkerSession } from "../../types";
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

const renderDetailWithEvents = (events: KraftEvent[]) => {
  setup();
  useStore.setState({ eventsByItem: { w1: events } } as never);
  return renderDetail();
};

const renderDetailAtGate = (over: Partial<WorkItem> = {}) => {
  setup({
    status: "needs_human",
    pending_gate: "spec_approval",
    gate_artifact: "docs/spec.md",
    current_node_id: "spec",
    ...over,
  });
  return renderDetail();
};

describe("WorkItemDetail (item page)", () => {
  it("Review spec selects the gate document and Approve repeats in the pane", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [
        {
          document_id: "d1", repo: "/r", title: "Spec", kind: "specs", source_kind: "file",
          path: "docs/spec.md", node_id: "spec", hook_point: null, worker_session_id: null,
          attachment_kind: null, indexed_at: "t",
        },
      ],
    } as never);
    vi.spyOn(api, "getDocument").mockResolvedValue({
      document_id: "d1", repo: "/r", title: "Spec", kind: "specs", source_kind: "file",
      path: "docs/spec.md", content: "# Spec", origin: "file",
      source_updated_at: "t", indexed_at: "t",
    } as never);
    const user = userEvent.setup();
    renderDetailAtGate();
    await user.click(screen.getByRole("link", { name: /Review spec/ }));
    // A plain `<a href="#...">` isn't a `<Link>` — `MemoryRouter` never sees
    // it, but jsdom still updates the real `window.location.hash`, same as
    // `BrowserRouter` (App.tsx) would in production.
    expect(window.location.hash).toContain("tab=documents");
    expect(window.location.hash).toContain("node=spec");
  });

  it("Approve repeats in the pane when the open document is the gate's own artifact", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [
        {
          document_id: "d1", repo: "/r", title: "Spec", kind: "specs", source_kind: "file",
          path: "docs/spec.md", node_id: "spec", hook_point: null, worker_session_id: null,
          attachment_kind: null, indexed_at: "t",
        },
      ],
    } as never);
    vi.spyOn(api, "getDocument").mockResolvedValue({
      document_id: "d1", repo: "/r", title: "Spec", kind: "specs", source_kind: "file",
      path: "docs/spec.md", content: "# Spec", origin: "file",
      source_updated_at: "t", indexed_at: "t",
    } as never);
    renderDetailAtGate();
    // Documents.tsx auto-selects the gate's artifact by path once the list
    // lands (Kraft-esc) — no click needed beyond opening the tab.
    await userEvent.click(screen.getByRole("tab", { name: /documents/i }));
    const pane = await screen.findByTestId("right-pane-doc");
    expect(within(pane).getByRole("button", { name: /^Approve$/ })).toBeInTheDocument();
  });

  it("renders a task_progress event as a task row and filters to it", async () => {
    const user = userEvent.setup();
    renderDetailWithEvents([
      {
        seq: 2, work_item_id: "w1", type: "task_progress", created_at: "2026-01-01T00:01:00Z",
        payload: { node_id: "verify", task: 3, total: 6, title: "open_mr refuses a dirty worktree" },
      },
      {
        seq: 1, work_item_id: "w1", type: "worker_session_started", created_at: "2026-01-01T00:00:00Z",
        payload: { node_id: "verify", session_id: "s1" },
      },
    ] as never);
    await user.click(screen.getByRole("tab", { name: /timeline/i }));
    expect(screen.getByText("Started task 3 — open_mr refuses a dirty worktree")).toBeInTheDocument();
    expect(screen.getByText("3 of 6")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "tasks" }));
    expect(screen.queryByText("worker_session_started")).toBeNull();
  });

  it("disables Review spec with 'not written yet' when the artifact is absent", () => {
    renderDetailAtGate({ gate_artifact: null });
    const btn = screen.getByText(/Review spec/);
    expect(btn).toHaveAttribute("aria-disabled", "true");
    expect(btn.getAttribute("title")).toMatch(/not written yet/);
  });

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

  it("Changes tab groups files into a folder tree and selecting one names it in the hash", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      base_ref: "abc123",
      diff: "",
      files: [
        { path: "frontend/src/App.tsx", insertions: 2, deletions: 0 },
        { path: "frontend/src/api.ts", insertions: 11, deletions: 0 },
      ],
      untracked: [],
      truncated: false,
      landed: null,
    } as never);
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: /changes/i }));
    const tree = await screen.findByTestId("inspector-changes");
    expect(within(tree).getByText("frontend")).toBeInTheDocument();
    await userEvent.click(within(tree).getByText("api.ts"));
    expect(screen.getByTestId("hash-probe").textContent).toContain("file=frontend%2Fsrc%2Fapi.ts");
  });

  it("renders a second section for what already landed on the branch, when the server sends it", async () => {
    vi.spyOn(api, "getWorkItemDiff").mockResolvedValue({
      base_ref: "abc123",
      diff: "",
      files: [],
      untracked: [],
      truncated: false,
      landed: { commits: ["c1"], files: [{ path: "README.md", insertions: 1, deletions: 0 }], diff: "", truncated: false },
    } as never);
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: /changes/i }));
    const tree = await screen.findByTestId("inspector-changes");
    expect(within(tree).getByText(/On this branch · 1 commit/)).toBeInTheDocument();
  });

  it("fetches the diff once for the tree and the pane", async () => {
    const spy = vi.spyOn(api, "getWorkItemDiff");
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: /changes/i }));
    await screen.findByTestId("inspector-changes");
    expect(spy).toHaveBeenCalledTimes(1);
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
    expect(screen.getByText(/1 override/)).toBeInTheDocument();
    expect(screen.getByTestId("config-pane").textContent).toMatch(/# override/);
  });

  it("splits Config into a node block left and the work item + chain right", async () => {
    renderDetail();
    await userEvent.click(screen.getByRole("tab", { name: /config/i }));
    const insp = document.querySelector(".inspector") as HTMLElement;
    const pane = document.querySelector(".item-right-pane") as HTMLElement;
    expect(within(insp).getByText(/Node ·/)).toBeTruthy();
    expect(within(pane).getByText("Work item")).toBeTruthy();
    expect(within(pane).getByText(/Effective chain/)).toBeTruthy();
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

  it("makes the inspector and the right pane the only scrollers", () => {
    // jsdom has no cascade either, and this suite's vitest config does not
    // process CSS imports -- pin the source, the way the test above does.
    const detail = readFileSync(join(here, "../../styles.css"), "utf-8");
    const workItem = readFileSync(join(here, "work_item.css"), "utf-8");
    expect(detail).toMatch(/\.detail\.item-page\s*\{[^}]*height:\s*100%;\s*min-height:\s*0;\s*overflow:\s*hidden/);
    expect(workItem).toMatch(/\.inspector,\s*\.item-right-pane\s*\{\s*overflow-y:\s*auto;\s*min-height:\s*0;\s*\}/);
  });

  it("collapses the title and description when the top block is scrolled", () => {
    renderDetail();
    const top = document.querySelector(".detail-head") as HTMLElement;
    fireEvent.wheel(top, { deltaY: 60 });
    expect(document.querySelector(".detail[data-head='collapsed']")).toBeTruthy();
    fireEvent.wheel(top, { deltaY: -60 });
    expect(document.querySelector(".detail[data-head='collapsed']")).toBeNull();
  });

  it("maximizing hides the inspector and the graph and lands in the URL", async () => {
    const user = userEvent.setup();
    setup({}, [session({ id: "s1", node_id: "verify" })]);
    renderDetail();
    await screen.findByTestId("right-pane-log");
    await user.click(screen.getByRole("button", { name: /maximi/i }));
    expect(screen.getByTestId("hash-probe").textContent).toContain("max=1");
    expect(document.querySelector(".inspector")).toBeNull();
    expect(document.querySelector(".stage-graph")).toBeNull();
    expect(document.querySelector(".item-max-strip")).toBeTruthy();
  });

  it("maximizes a diff the same way it maximizes a log", async () => {
    const user = userEvent.setup();
    renderDetail("#node=verify&tab=changes&file=src/a.ts");
    await screen.findByTestId("right-pane-diff");
    await user.click(screen.getByRole("button", { name: /maximi/i }));
    expect(screen.getByTestId("hash-probe").textContent).toContain("max=1");
    expect(document.querySelector(".inspector")).toBeNull();
    expect(document.querySelector(".item-max-strip")).toBeTruthy();
  });

  it("Escape restores the split", async () => {
    const user = userEvent.setup();
    setup({}, [session({ id: "s1", node_id: "verify" })]);
    renderDetail("#node=verify&max=1");
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("hash-probe").textContent).not.toContain("max=1");
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
