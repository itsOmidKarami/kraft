import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi, afterEach } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { ChainNode, DocumentDetail, KraftEvent, WorkItem, WorkerSession, WorkItemDocument } from "../../types";
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

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Forces `usePhone()` to `true` for the life of a test — same stub
 *  `Phone.test.tsx` uses. Needed here for spec §2.1a: node selection is a
 *  page transition only on the phone, so it must push there, not replace. */
function mockPhone() {
  const mql: Partial<MediaQueryList> = {
    matches: true,
    media: "(max-width: 767px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue(mql));
}

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

/** Two history entries — a board stub, then the item page — so a single
 *  Back press has somewhere real to land. `renderDetail`'s one-entry stack
 *  can't tell "replaced the current entry" from "pushed a new one": Back has
 *  nowhere to go either way, so it can't distinguish the two behaviours. */
const renderDetailFromBoard = (hash = "") =>
  render(
    <MemoryRouter
      initialEntries={["/", `/work-items/w1${hash}`]}
      initialIndex={1}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/" element={<span data-testid="board-stub">board</span>} />
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
    // Nothing selected: the right pane streams the node (W13 · D.4); a task run is one row.
    const pane = screen.getByTestId("right-pane-events");
    expect(within(pane).getByText("Task 3 of 6")).toBeInTheDocument();
    expect(pane.querySelector('.stream-row[data-kind="session"]')).not.toBeNull();
    expect(pane.textContent).not.toContain("worker_session_started");
    await user.click(within(pane).getByRole("button", { name: "gates" }));
    expect(within(pane).queryByText("Task 3 of 6")).toBeNull();
  });

  const tev = (seq: number, type: string, node: string): KraftEvent =>
    ({ seq, work_item_id: "w1", type, payload: { node_id: node }, created_at: `2026-01-01T00:0${seq}:00Z` }) as KraftEvent;
  const timelineEvents = [
    tev(1, "node_started", "spec"),
    tev(2, "node_completed", "spec"),
    tev(3, "node_started", "verify"),
    tev(4, "fix_cycle_started", "verify"),
    tev(5, "judge_verdict", "verify"),
  ];

  it("Timeline shows this node as rounds, newest first, the current round open, and folds nodes under all (W13 · C)", async () => {
    const user = userEvent.setup();
    renderDetailWithEvents(timelineEvents);
    await user.click(screen.getByRole("tab", { name: /timeline/i }));
    // The tab still counts the scope's events (as built); the list counts rounds.
    expect(screen.getByRole("tab", { name: /timeline/i }).textContent).toBe("Timeline · 3");
    const list = screen.getByTestId("inspector-timeline");
    expect(list).toHaveTextContent("ROUNDS · 2");
    const current = within(list).getByTestId("timeline-round-verify-1");
    const first = within(list).getByTestId("timeline-round-verify-0");
    expect(current).toHaveTextContent("round 2");
    expect(current).toHaveAttribute("aria-expanded", "true");
    expect(first).toHaveTextContent("round 1");
    expect(first).toHaveAttribute("aria-expanded", "false");
    // node-level rows keep their place; the round events themselves are not rows
    expect(within(list).getByTestId("timeline-event-3")).toHaveTextContent("node_started");
    expect(within(list).queryByTestId("timeline-event-4")).toBeNull();
    const order = [...list.querySelectorAll("button[data-trow]")].map((b) => b.getAttribute("data-testid"));
    expect(order).toEqual(["timeline-round-verify-1", "timeline-round-verify-0", "timeline-event-3"]);
    // Nothing is picked for the person: the right pane streams the node (D.4).
    expect(list.querySelector('[data-selected="true"]')).toBeNull();

    await user.click(within(list).getByRole("button", { name: "all" }));
    expect(list).toHaveTextContent("ROUNDS · 3");
    expect(within(list).getByTestId("timeline-node-verify")).toHaveTextContent(/^▾verify · 2 rounds/);
    expect(within(list).getByTestId("timeline-node-spec")).toHaveTextContent(/^▸spec · 1 round/);
    expect(within(list).getByTestId("timeline-node-verify")).toHaveAttribute("aria-expanded", "true");
  });

  it("Timeline rows are buttons: arrows move, Left/Right fold a round, a click selects it (W13 · C.4, C.5)", async () => {
    const user = userEvent.setup();
    renderDetailWithEvents(timelineEvents);
    await user.click(screen.getByRole("tab", { name: /timeline/i }));
    const list = screen.getByTestId("inspector-timeline");
    const current = within(list).getByTestId("timeline-round-verify-1");
    current.focus();
    await user.keyboard("{ArrowLeft}");
    expect(current).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{ArrowRight}");
    expect(current).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{ArrowDown}");
    expect(within(list).getByTestId("timeline-round-verify-0")).toHaveFocus();
    await user.click(within(list).getByTestId("timeline-round-verify-0"));
    expect(within(list).getByTestId("timeline-round-verify-0")).toHaveAttribute("data-selected", "true");
    await user.click(within(list).getByTestId("timeline-event-3"));
    expect(within(list).getByTestId("timeline-event-3")).toHaveAttribute("data-selected", "true");
  });

  it("Timeline and Tasks share one scope, and picking a pill rescopes to that node (W11 · F.1)", async () => {
    const user = userEvent.setup();
    renderDetailWithEvents(timelineEvents);
    await user.click(screen.getByRole("tab", { name: /timeline/i }));
    await user.click(screen.getByRole("button", { name: /^spec$/ }));
    expect(screen.getByRole("tab", { name: /timeline/i }).textContent).toBe("Timeline · 2");
    await user.click(within(screen.getByTestId("inspector-timeline")).getByRole("button", { name: "all" }));
    await user.click(screen.getByRole("tab", { name: /tasks/i }));
    expect(within(screen.getByTestId("inspector-tasks")).getByRole("button", { name: "all" })).toHaveAttribute("aria-pressed", "true");
  });

  it("disables Review spec with 'not written yet' when the artifact is absent", () => {
    renderDetailAtGate({ gate_artifact: null });
    const btn = screen.getByText(/Review spec/);
    expect(btn).toHaveAttribute("aria-disabled", "true");
    expect(btn.getAttribute("title")).toMatch(/not written yet/);
  });

  it("hydrates on mount and names the current node and its position in the header's run (W11 rule 2)", () => {
    renderDetail();
    expect(useStore.getState().hydrateItem).toHaveBeenCalledWith("w1");
    expect(document.querySelector(".detail-run-node")?.textContent).toMatch(/^verify · 3\/3/);
    expect(document.querySelector(".detail-hero")).toBeNull();
  });

  it.each([
    ["running", {}],
    ["gate", { status: "needs_human", pending_gate: "spec_approval", current_node_id: "spec" }],
    ["paused", { status: "paused" }],
    ["capped", { status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }],
    ["done", { status: "completed", current_node_id: null }],
    ["not started", { status: "paused", current_node_id: null }],
  ] as [string, Partial<WorkItem>][])("%s: one item card, and no action bar (W11 · A)", (_, over) => {
    setup(over);
    renderDetail();
    expect(document.querySelector(".action-bar")).toBeNull();
    expect(document.querySelectorAll(".item-card")).toHaveLength(1);
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

  // spec 2026-09-12 §2: desktop node clicks replace, so switching between
  // nodes never grows the history — one Back press leaves the item page.
  it("desktop: node clicks replace, so one Back press leaves the item page", async () => {
    renderDetailFromBoard();
    await userEvent.click(screen.getByRole("button", { name: /^plan$/ }));
    await userEvent.click(screen.getByRole("button", { name: /^spec$/ }));
    expect(screen.getByRole("button", { name: /^spec$/ })).toHaveAttribute("data-selected", "true");

    await userEvent.click(screen.getByRole("button", { name: "test-go-back" }));
    expect(await screen.findByTestId("board-stub")).toBeInTheDocument();
  });

  // spec §2.1a: on the phone, opening a node is a page transition (m05),
  // not a same-page selection — it must still push, or Back breaks.
  it("phone: selecting a node still pushes, so Back returns to the stage list", async () => {
    mockPhone();
    renderDetailFromBoard();
    expect(await screen.findByTestId("phone-stage-list")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("phone-stage-plan"));
    expect(await screen.findByTestId("phone-node-page")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "test-go-back" }));
    expect(await screen.findByTestId("phone-stage-list")).toBeInTheDocument();
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

  it("shows the not_started bar and the intake card in the split (21, Kraft-pfqdb)", () => {
    setup({ current_node_id: null, status: "paused" });
    renderDetail();
    expect(screen.getAllByRole("button", { name: /^start$/i }).length).toBeGreaterThan(0);
    expect(screen.getByTestId("not-started-card")).toBeInTheDocument();
    expect(screen.getByTestId("not-started-chain")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /tasks/i })).toBeNull();
  });

  it("shows the hero task bar and the Tasks-tab plan list from item.progress", async () => {
    renderDetailWithProgress();
    expect(screen.getByTestId("task-bar")).toBeInTheDocument();
    expect(screen.getByText("wire the thing")).toBeInTheDocument();
    expect(await screen.findByTestId("plan-list")).toBeInTheDocument();
  });

  it("lays the header out as a grid with the state chip leading its run", () => {
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
    await user.click(screen.getByRole("button", { name: "More" }));
    await user.click(screen.getByRole("button", { name: /edit title/i }));
    expect(screen.getByLabelText("title")).toBeTruthy();
  });

  it("edits the title in a textarea: Escape cancels, Enter saves (W0.11)", async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "updateWorkItem").mockResolvedValue({} as never);
    renderDetail();
    await user.click(screen.getByRole("button", { name: "edit title" }));
    const box = screen.getByLabelText("title");
    expect(box.tagName).toBe("TEXTAREA");
    await user.click(box);
    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("title")).toBeNull();
    await user.click(screen.getByRole("button", { name: "edit title" }));
    await user.type(screen.getByLabelText("title"), "X{Enter}");
    expect(spy).toHaveBeenCalledWith("w1", { title: "TX" });
  });

  it("keeps the brief closed until the description link opens it as rendered markdown (W11 rule 3)", async () => {
    const user = userEvent.setup();
    setup({ description: "## Context\n\n- The **verify** node measures." });
    renderDetail();
    expect(screen.queryByTestId("item-description")).toBeNull();
    const link = screen.getByRole("button", { name: "description" });
    expect(link).toHaveAttribute("aria-expanded", "false");
    await user.click(link);
    const desc = screen.getByTestId("item-description");
    expect(link).toHaveAttribute("aria-expanded", "true");
    expect(desc.textContent).not.toMatch(/##|\*\*/);
    expect(within(desc).getByRole("heading", { name: "Context" })).toBeTruthy();
  });

  it("lists repos under Config → Repos, not under the hero, and the submodules chip opens it (W0.7)", async () => {
    const user = userEvent.setup();
    setup({
      root_merge_policy: "bump",
      repos: [
        { repo: "/r", path: "/r", role: "root", merge_rank: 0, state: "clean" },
        { repo: "/code/sub", path: "/r/vendor/sub", role: "submodule", merge_rank: 1, state: "dirty" },
      ],
    } as never);
    renderDetail();
    expect(document.querySelector(".repos-panel")).toBeNull();
    expect(screen.queryByTestId("config-repos")).toBeNull();
    await user.click(screen.getByRole("button", { name: "+1 submodule" }));
    expect(screen.getByRole("tab", { name: /config/i })).toHaveAttribute("aria-selected", "true");
    const repos = screen.getByTestId("config-repos");
    expect(within(repos).getByText("sub")).toBeTruthy();
    expect(within(repos).getByText(/root_merge_policy/)).toBeTruthy();
  });

  it("counts the selected node's sessions on the Tasks tab, the same N as the pane (W0.8)", () => {
    setup({}, [session({ id: "a" }), session({ id: "b", status: "done" }), session({ id: "c", node_id: "spec" })]);
    renderDetail("#node=verify&tab=tasks");
    expect(screen.getByRole("tab", { name: /tasks/i }).textContent).toBe("Tasks · 2");
    expect(screen.getByText(/SESSIONS · 2/)).toBeTruthy();
    expect(screen.queryByText(/newest first/)).toBeNull();
  });

  it("under 1024 shows one pane: the list until a row is picked, then its detail (W2.2)", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "matchMedia",
      vi.fn((q: string) => ({ matches: q.includes("1023"), media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    );
    vi.spyOn(api, "getLogLines").mockResolvedValue({ session_id: "s1", status: "done", lines: [] });
    setup({}, [session({ id: "s1", status: "done" })]);
    renderDetail("#node=verify&tab=changes");
    const split = document.querySelector(".item-split") as HTMLElement;
    expect(split.dataset.view).toBe("list");
    expect(screen.getByRole("button", { name: "List" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("tab", { name: /tasks/i }));
    await user.click(screen.getByTestId("task-row-s1"));
    expect(split.dataset.view).toBe("detail");
    await user.click(screen.getByRole("button", { name: "List" }));
    expect(split.dataset.view).toBe("list");
  });

  it("keeps the split with no List / Detail switch at desktop width (W2.2)", () => {
    renderDetail();
    expect((document.querySelector(".item-split") as HTMLElement).dataset.view).toBeUndefined();
    expect(screen.queryByRole("button", { name: "Detail" })).toBeNull();
  });

  it("a never-started item says where it starts, without a paused chip (W0.5)", () => {
    setup({ current_node_id: null, status: "paused" });
    renderDetail();
    expect(document.querySelector(".detail-run-node")?.textContent).toMatch(/^starts at spec/);
    expect(document.querySelector(".detail-status")?.textContent).toBe("waiting to start");
  });

  it("an escalating item's status chip reads escalating, not needs you (W11 · J.5)", () => {
    setup({ status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }, [
      session({ id: "e1", hook_point: "escalation", status: "running" }),
    ]);
    useStore.setState({
      eventsByItem: {
        w1: [
          { seq: 1, work_item_id: "w1", type: "work_item_needs_human", payload: {}, created_at: "t" },
          { seq: 2, work_item_id: "w1", type: "escalation_message", payload: { session_id: "e1", message: "go" }, created_at: "t" },
        ],
      },
    } as never);
    renderDetail();
    expect(document.querySelector(".detail-status")).toHaveTextContent("escalating");
    expect(document.querySelector(".detail-status")).toHaveClass("tag-escalating");
  });

  it("a completed item names its last node, never — or not started (W0.5)", () => {
    setup({ current_node_id: null, status: "completed" });
    renderDetail();
    expect(document.querySelector(".detail-run-node")?.textContent).toMatch(/^verify · 3\/3 · completed/);
  });

  it("shows how long a gate has waited on its card (W0.4)", () => {
    setup({ status: "needs_human", pending_gate: "spec_approval", gate_artifact: "docs/spec.md", current_node_id: "spec" });
    useStore.setState({
      eventsByItem: {
        w1: [{ seq: 1, work_item_id: "w1", type: "gate_requested", payload: { node_id: "spec", gate: "spec_approval" }, created_at: new Date(Date.now() - (3 * 60 + 12) * 60_000).toISOString() }],
      },
    } as never);
    renderDetail();
    expect(within(screen.getByTestId("gate-card")).getByText(/waiting 3h 12m/)).toBeTruthy();
  });

  it("renders the gate artifact when the index has not ingested it yet", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1",
      path: "docs/spec.md",
      title: "The spec",
      content: "# The plan\n\nTask 1: do the thing.",
      truncated: false,
      artifact_max_bytes: 1_000_000,
    });

    renderDetailAtGate();
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));

    expect(await screen.findByText(/Task 1: do the thing/)).toBeInTheDocument();
    expect(screen.queryByText(/select a document to view it/i)).not.toBeInTheDocument();
  });

  it("says 'not written yet' only when the gate has no artifact", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
    renderDetailAtGate({ gate_artifact: null });
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));
    expect(await screen.findByText("not written yet")).toBeInTheDocument();
  });

  it("says 'not indexed yet' when the artifact exists but the index has not caught up", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1", path: "docs/spec.md", title: "The spec",
      content: "body", truncated: false, artifact_max_bytes: 1_000_000,
    });
    renderDetailAtGate();
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));
    expect(await screen.findByText("not indexed yet")).toBeInTheDocument();
    expect(screen.queryByText("not written yet")).not.toBeInTheDocument();
  });

  it("heads the document pane with a one-line title, a one-line path and the index note (W12.2)", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
    const path = ".engineering/reviews/2026-09-13-design-the-caching-layer-for-document-search.md";
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1", path, title: "Review: Design the caching layer for document search",
      content: "body", truncated: false, artifact_max_bytes: 1_000_000,
    });
    renderDetailAtGate();
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));
    const head = (await screen.findByTestId("right-pane-doc")).querySelector(".doc-modal-title") as HTMLElement;
    const name = head.querySelector(".doc-modal-name")!;
    expect(name).toHaveAttribute("title", "Review: Design the caching layer for document search");
    expect(name).toHaveAttribute("data-allow-ellipsis");
    const pathEl = head.querySelector(".doc-path")!;
    expect(pathEl).toHaveAttribute("title", path);
    expect(pathEl).toHaveAttribute("data-allow-ellipsis");
    expect(pathEl).toHaveTextContent(path);
    // the path is its own line, not a meta chip beside the note
    expect(head.querySelector(".doc-modal-meta")).not.toHaveTextContent(path);
    expect(within(head).getByText("not indexed yet — read from the worktree")).toBeInTheDocument();
  });

  it("approves the gate from the artifact pane", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1", path: "docs/spec.md", title: "The spec",
      content: "body", truncated: false, artifact_max_bytes: 1_000_000,
    });
    const approve = vi.spyOn(api, "approveGate").mockResolvedValue(undefined as never);

    renderDetailAtGate();
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));
    const pane = await screen.findByTestId("right-pane-doc");
    await userEvent.click(within(pane).getByRole("button", { name: /^Approve$/ }));

    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve).toHaveBeenCalledWith("w1", "spec_approval");
  });

  it("upgrades from the artifact to the indexed document once it lands", async () => {
    const docs = vi.spyOn(api, "getWorkItemDocuments")
      .mockResolvedValueOnce({ work_item_id: "w1", documents: [] })
      .mockResolvedValue({
        work_item_id: "w1",
        documents: [{ document_id: "dc_1", path: "docs/spec.md", title: "The spec", kind: "plans" } as WorkItemDocument],
      });
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1", path: "docs/spec.md", title: "The spec",
      content: "body", truncated: false, artifact_max_bytes: 1_000_000,
    });
    vi.spyOn(api, "getDocument").mockResolvedValue({
      id: "dc_1", path: "docs/spec.md", title: "The spec",
      content: "indexed body", kind: "plans", repo: "/repo",
      indexed_at: new Date().toISOString(), source_updated_at: new Date().toISOString(),
    } as DocumentDetail);

    renderDetailAtGate();
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));
    expect(await screen.findByText("not indexed yet")).toBeInTheDocument();

    // `Documents`' fetch effect keys on `eventCount`, so pushing an event into
    // the store is what makes it refetch — the same path a live `gate_approved`
    // takes through `store.applyEvent`.
    act(() => {
      useStore.setState({
        eventsByItem: { w1: [{ seq: 1, work_item_id: "w1", type: "gate_approved", created_at: "t", payload: {} }] },
      } as never);
    });

    expect(await screen.findByText("indexed body")).toBeInTheDocument();
    expect(docs).toHaveBeenCalledTimes(2);
  });

  it("shows an error and no document when the artifact cannot be read", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({ work_item_id: "w1", documents: [] });
    vi.spyOn(api, "getWorkItemArtifact").mockRejectedValue(new Error("404"));

    renderDetailAtGate();
    await userEvent.click(await screen.findByRole("tab", { name: /documents/i }));

    expect(await screen.findByText(/the gate's document could not be read/)).toBeInTheDocument();
  });
});
