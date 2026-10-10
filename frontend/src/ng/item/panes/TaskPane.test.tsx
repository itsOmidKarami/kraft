import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChainNode, SessionStatus, TaskProgress, WorkerSession } from "../../../types";
import { detail, stubFetch, V1 } from "../testkit";
import { usePaneMemory, Workspace } from "../Workspace";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => usePaneMemory.setState({ pane: { open: true, userCollapsed: false } }));

const sess = (hook_point: string, attempt: number, over: Partial<WorkerSession> = {}) =>
  ({ id: `${hook_point}-${attempt}`, node_id: hook_point.split(".")[0] === "escalation" ? "verification" : hook_point.split(".")[0], hook_point, status: "done", attempt, round: attempt - 1, thread: 1, created_at: `2026-09-13T09:0${attempt}:00Z`, started_at: null, exited_at: null, wall_ms: 60_000, model: "sonnet", tokens_in: 1, tokens_out: 1, cost_usd: 0.1, head_sha: "abc1234567890", ...over }) as WorkerSession;
const item = detail({ worker_sessions: [sess("verification.review.code_review", 1), sess("verification.review.code_review", 2, { status: "failed", harness: "codex" }), sess("escalation", 1, { node_id: "verification", status: "needs_context" })] });
function Where() { const l = useLocation(); return <output data-testid="where">{l.pathname + l.search}</output>; }
const mount = (path: string, it = item, events: unknown[] = [], routes: Parameters<typeof stubFetch>[0] = {}) => {
  stubFetch({ ...routes, "GET /work-items/w1/events": [200, events], "GET /work-items/w1/documents": [200, { work_item_id: "w1", documents: [
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
/** Open the subtitle's attempt menu (its pill reads `pill`) and pick the row whose label starts `row`. */
const pick = async (pill: RegExp, row: RegExp) => {
  await userEvent.click(screen.getByRole("button", { name: pill }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: row }));
};

describe("task pane", () => {
  it("opens on the latest attempt; picking an earlier one in the menu switches the tabs and the URL keeps it", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(within(pane("code_review")).getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(within(screen.getByRole("tabpanel")).getByText("harness")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^attempt 2 of 2/ }));
    // Newest first, each with how it went; the shown one checked.
    expect(screen.getAllByRole("menuitemradio").map((r) => [r.textContent, r.getAttribute("aria-checked")])).toEqual([["✓Attempt 2failed · 1m", "true"], ["Attempt 1done · 1m", "false"]]);
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^Attempt 1/ }));
    expect(screen.getByTestId("where").textContent).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review&attempt=1");
    expect(within(pane("code_review")).getByRole("button", { name: /^attempt 1 of 2/ })).toBeInTheDocument();
    // Attempt 1 ran on no named harness: Overview reads that session now.
    expect(within(screen.getByRole("tabpanel")).queryByText("harness")).toBeNull();
  });

  it("drops the pin when the newest attempt is picked, so the pane follows the next one", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&attempt=1");
    await pick(/^attempt 1 of 2/, /^Attempt 2/);
    expect(screen.getByTestId("where").textContent).toBe("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    expect(within(pane("code_review")).getByRole("button", { name: /^attempt 2 of 2/ })).toBeInTheDocument();
  });

  it("puts the attempt menu in the subtitle, between the task's kind and its state, outside every tab's panel", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&tab=input");
    const pill = within(pane("code_review")).getByRole("button", { name: "attempt 2 of 2 · round 2" });
    expect(pill.closest(".pane-sub")).toHaveTextContent(/^agent task · attempt 2 of 2 · round 2▾ · failed$/);
    expect(within(screen.getByRole("tabpanel")).queryByText(/attempt/)).toBeNull();
  });

  it("closes the menu on Escape and leaves the pane open", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review");
    await userEvent.click(screen.getByRole("button", { name: /^attempt 2 of 2/ }));
    expect(screen.getByRole("menu", { name: "Attempts" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(pane("code_review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^attempt 2 of 2/ })).toHaveFocus();
  });

  it("lists only this attempt's documents under the result", async () => {
    mount("/work-items/w1/nodes/verification?sel=verification.review.code_review&attempt=1");
    expect(await within(pane("code_review")).findByRole("button", { name: /Review notes/ })).toBeInTheDocument();
    expect(within(pane("code_review")).queryByRole("button", { name: /Other/ })).toBeNull();
  });

  // The work brief's session note only says where the brief is: Output opens the brief itself.
  describe("a task that produces a document", () => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "work_brief", kind: "exec", steps: [{ id: "main", tasks: [{ id: "author", kind: "agent", produces: "work_brief" }] }] }] } });
    const briefed = detail({ chain_definition: { template_id: "default", nodes: [{ id: "work_brief", kind: "exec", gate_after: null, tasks: ["work_brief.main.author"], steps: [["work_brief.main.author"]] }] }, materialized_chain: frozen, worker_sessions: [sess("work_brief.main.author", 1), sess("work_brief.main.author", 2)] });
    const url = "/work-items/w1/nodes/work_brief?sel=work_brief.main.author&tab=output";
    const brief = { "GET /work-items/w1/artifacts/work_brief": [200, { path: ".engineering/work_briefs/w1.md", title: "Work brief", content: "# Work brief\n\nWhat changed and why." }] as [number, unknown] };

    it("offers the document on Output, and opens it rather than the session note", async () => {
      mount(url, briefed, [], brief);
      await userEvent.click(within(screen.getByRole("tabpanel")).getByRole("button", { name: "work brief" }));
      const viewer = await screen.findByRole("dialog", { name: "Work brief" });
      expect(await within(viewer).findByText("What changed and why.")).toBeInTheDocument();
      expect(within(viewer).getByText("written by work_brief.main.author")).toBeInTheDocument();
    });

    it.each([
      ["an earlier attempt: every attempt rewrites the one file", "&attempt=1", briefed],
      ["a newest attempt that failed: it may have written nothing", "", { ...briefed, worker_sessions: [sess("work_brief.main.author", 1), sess("work_brief.main.author", 2, { status: "failed" })] }],
    ])("does not offer it on %s", (_, extra, it) => {
      mount(`${url}${extra}`, it, [], brief);
      expect(within(screen.getByRole("tabpanel")).queryByText("wrote")).toBeNull();
    });
  });

  it("puts Thread first on the escalation task and opens on it", () => {
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation");
    const tabs = within(pane("escalation")).getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Thread", "Overview", "Input", "Output", "Log", "Config"]);
    expect(within(pane("escalation")).getByRole("tab", { name: "Thread" })).toHaveAttribute("aria-selected", "true");
    // One turn: no menu, the subtitle says how it went.
    expect(within(pane("escalation")).getByText("escalation · agent task · needs you")).toBeInTheDocument();
    expect(within(pane("escalation")).queryByRole("button", { name: /^turn/ })).toBeNull();
  });

  describe("the plan task's sub-tasks", () => {
    // `implementation.main.implement` is the one agent task with no skill: the plan task. The reviewer has a skill.
    const nodes: ChainNode[] = [{ id: "implementation", kind: "exec", gate_after: null, tasks: ["implementation.main.implement"], steps: [["implementation.main.implement"]] }, V1.find((n) => n.id === "verification")!];
    const frozen = JSON.stringify({ chain: { nodes: [
      { id: "implementation", kind: "exec", tasks: [{ id: "implement", kind: "agent", skill: null }] },
      { id: "verification", kind: "exec", steps: [{ id: "checks", tasks: [{ id: "lint", kind: "subprocess" }] }, { id: "review", tasks: [{ id: "code_review", kind: "agent", skill: "kraft:code-review" }] }] },
    ] } });
    const st = (n: number, state: "done" | "current" | "pending", sha: string | null = null) => ({ n, title: ["parse", "serve", "render"][n - 1], state, sha });
    const LIVE: TaskProgress = { current: 2, total: 3, title: "serve", tasks: [st(1, "done", "a1b2c3d"), st(2, "current"), st(3, "pending")] };
    const DONE: TaskProgress = { current: 3, total: 3, title: "render", tasks: [st(1, "done", "a1b2c3d"), st(2, "done", "e4f5a6b"), st(3, "done")] };
    const planned = (progress: TaskProgress | null, newest: SessionStatus = "running") => detail({
      chain_definition: { template_id: "default", nodes }, current_node_id: "implementation", materialized_chain: frozen, progress,
      worker_sessions: [sess("implementation.main.implement", 1, { status: "failed" }), sess("implementation.main.implement", 2, { status: newest }), sess("verification.review.code_review", 1)],
    });
    const progressFact = () => within(screen.getByRole("tabpanel")).queryByText("progress")?.closest("div")?.textContent?.replace(/^progress/, "") ?? null;

    it.each<[string, TaskProgress | null, SessionStatus, string | null, boolean]>([
      ["running: the current sub-task's title", LIVE, "running", "2 of 3 · serve", true],
      ["stopped: the count alone", LIVE, "paused", "2 of 3 sub-tasks", true],
      ["every sub-task done", DONE, "done", "3 of 3 sub-tasks · done", true],
      ["the first report of a run, before its list: the fact alone", { ...LIVE, tasks: [] }, "running", "2 of 3 · serve", false],
      ["no progress: nothing", null, "running", null, false],
    ])("on the plan task's Overview, %s", (_, progress, newest, fact, list) => {
      mount("/work-items/w1/nodes/implementation?sel=implementation.main.implement", planned(progress, newest));
      expect(progressFact()).toBe(fact);
      expect(within(screen.getByRole("tabpanel")).queryByRole("heading", { name: /^Progress/ }) !== null).toBe(list);
    });

    it("lists the plan's sub-tasks whichever attempt is picked: the current one marked, a done one with its commit", () => {
      mount("/work-items/w1/nodes/implementation?sel=implementation.main.implement&attempt=1", planned(LIVE));
      const panel = within(screen.getByRole("tabpanel"));
      // "Running" is the task's newest session, not the picked one.
      expect(progressFact()).toBe("2 of 3 · serve");
      expect(panel.getByRole("heading", { name: "Progress · 3 sub-tasks" })).toBeInTheDocument();
      const rows = within(panel.getByRole("list", { name: "Sub-tasks" })).getAllByRole("listitem");
      expect(rows.map((r) => [within(r).getByRole("img").getAttribute("aria-label"), r.textContent, r.getAttribute("aria-current")])).toEqual([
        ["done", "1parsea1b2c3d", null],
        ["current", "2servecurrent", "step"],
        ["pending", "3render", null],
      ]);
    });

    it("shows nothing on another agent task", () => {
      mount("/work-items/w1/nodes/verification?sel=verification.review.code_review", planned(LIVE));
      expect(progressFact()).toBeNull();
      expect(within(screen.getByRole("tabpanel")).queryByRole("heading", { name: /^Progress/ })).toBeNull();
    });
  });

  describe("a gate's auto_review", () => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan_approval", kind: "gate", auto_review: { id: "auto_review", kind: "agent" } }] } });
    const reviewed = detail({ materialized_chain: frozen, display_status: "failed", worker_sessions: [sess("plan_approval.auto_review", 1, { model: null }), sess("plan_approval.auto_review", 2, { model: null, status: "failed" })] });
    const url = "/work-items/w1/nodes/plan_approval?sel=plan_approval.auto_review";

    it("opens its own pane: attempts, the kind from the frozen chain, and a crumb back to the gate", async () => {
      mount(url, reviewed);
      const p = pane("auto_review");
      expect(within(p).getByText(/attempt 2 of 2/)).toBeInTheDocument();
      expect(within(p).getByText(/^agent task · /)).toBeInTheDocument();
      expect(within(p).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Input", "Output", "Log", "Config"]);
      // "<gate> › auto_review": the gate links back, the reviewer is text (no step of the chain to open).
      expect(within(p).getByRole("button", { name: "plan_approval" })).toBeInTheDocument();
      expect(within(p).queryByRole("button", { name: "auto_review" })).toBeNull();
      await pick(/^attempt 2 of 2/, /^Attempt 1/);
      expect(screen.getByTestId("where").textContent).toBe(`${url}&attempt=1`);
      expect(within(pane("auto_review")).getByText(/attempt 1 of 2/)).toBeInTheDocument();
    });

    it("reads the attempt's log on the Log tab", async () => {
      mount(`${url}&tab=log`, reviewed);
      expect(within(pane("auto_review")).getByRole("tab", { name: "Log" })).toHaveAttribute("aria-selected", "true");
      await waitFor(() => expect(vi.mocked(fetch).mock.calls.map((c) => String(c[0]))).toContain("/api/worker-sessions/plan_approval.auto_review-2/log?format=jsonl"));
    });

    it("offers no Skip or Retry: /skip and /retry refuse a path under a gate", () => {
      mount(url, reviewed);
      for (const name of ["Retry", "Skip task", "Pause", "Resume"]) expect(within(pane("auto_review")).queryByRole("button", { name })).toBeNull();
    });
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

  it("counts the escalation's sessions as turns, the word its Thread tab uses (R10b-06)", async () => {
    const turns = detail({ worker_sessions: [sess("escalation", 1, { id: "e1", node_id: "verification" }), sess("escalation", 2, { id: "e2", node_id: "verification" }), sess("escalation", 3, { id: "e3", node_id: "verification", thread: 2 })] });
    mount("/work-items/w1/nodes/verification?sel=verification.escalation.escalation&attempt=2", turns);
    const p = pane("escalation");
    expect(within(p).queryByText(/attempt/)).toBeNull();
    await userEvent.click(within(p).getByRole("button", { name: "turn 2 of 2 · thread 1" }));
    // Numbered within each thread, newest first; the thread named once there is more than one.
    expect(screen.getAllByRole("menuitemradio").map((r) => r.textContent)).toEqual(["Turn 1 · thread 2done · 1m", "✓Turn 2 · thread 1done · 1m", "Turn 1 · thread 1done · 1m"]);
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

  it("counts a skipped task as settled in the node and step panes, whether the skip cut it short or it never ran", () => {
    const skipped = { ...item, skipped_paths: ["verification.checks", "verification.review"], worker_sessions: [sess("verification.checks.lint", 1, { status: "paused", skipped: true })] };
    const { unmount } = mount("/work-items/w1/nodes/verification", skipped);
    expect(within(pane("verification")).getByText(/2 of 2 tasks/)).toBeInTheDocument();
    expect(within(pane("verification")).getAllByText("✓")).toHaveLength(2);
    unmount();
    mount("/work-items/w1/nodes/verification?sel=verification.review", skipped);
    expect(within(pane("review")).getByText("status").nextSibling).toHaveTextContent("done");
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
