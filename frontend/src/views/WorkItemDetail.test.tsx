import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkItem, WorkerSession } from "../types";
import { WorkItemDetail } from "./WorkItemDetail";

const NODES = [
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null },
];

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1", title: "T", repo: "/r", status: "active", chain_template: "quick-task",
    chain_definition: { template_id: "quick-task", nodes: NODES },
    current_node_id: "verify", bead_id: "B", created_at: "t", updated_at: "t",
    ...over,
  }) as WorkItem;

const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "running", attempt: 1, created_at: "t", exited_at: null, ...over,
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
});

const renderDetail = () =>
  render(
    <MemoryRouter
      initialEntries={["/work-items/w1"]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/work-items/:id" element={<WorkItemDetail />} />
      </Routes>
    </MemoryRouter>,
  );

describe("WorkItemDetail", () => {
  it("hydrates on mount and leads with the current node, not the chain", () => {
    renderDetail();
    expect(useStore.getState().hydrateItem).toHaveBeenCalledWith("w1");
    expect(screen.getByText("verify", { selector: ".hero-node" })).toBeInTheDocument();
    expect(screen.getByText(/node 2 of 2/)).toBeInTheDocument();
    expect(screen.getByTestId("chain-bar")).toBeInTheDocument();
  });

  it("shows the description under the title", async () => {
    setup({ description: "the brief" });
    renderDetail();
    expect(await screen.findByTestId("item-description")).toHaveTextContent("the brief");
  });

  it("offers to add one when there is no description", () => {
    setup({ description: null });
    renderDetail();
    expect(screen.queryByTestId("item-description")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add a description/i })).toBeInTheDocument();
  });

  it("saves an edited description and rehydrates the item", async () => {
    const update = vi
      .spyOn(api, "updateWorkItem")
      .mockResolvedValue({ id: "w1", description: "the revised brief" });
    setup({ description: "the brief" });
    renderDetail();

    await userEvent.click(
      within(screen.getByTestId("item-description")).getByRole("button", { name: /edit/i }),
    );
    const box = screen.getByLabelText("description");
    await userEvent.clear(box);
    await userEvent.type(box, "the revised brief");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("w1", { description: "the revised brief" }),
    );
  });

  it("edits the title and re-reads the item", async () => {
    const update = vi
      .spyOn(api, "updateWorkItem")
      .mockResolvedValue({ id: "w1", title: "a better label" });
    setup({ title: "typed in a hurry" });
    renderDetail();

    await userEvent.click(
      within(screen.getByTestId("item-title")).getByRole("button", { name: /edit/i }),
    );
    const box = screen.getByLabelText("title");
    await userEvent.clear(box);
    await userEvent.type(box, "a better label");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("w1", { title: "a better label" }));
    expect(useStore.getState().hydrateItem).toHaveBeenCalledWith("w1");
  });

  it("tags the work-item status in the meta line", () => {
    setup({ status: "needs_human" });
    renderDetail();
    expect(screen.getByText("needs you")).toHaveClass("tag-accent");
  });

  it("shows the control row while nothing is waiting on a human", () => {
    renderDetail();
    expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument();
  });

  it("replaces the control row with the gate card once the gate is reached", () => {
    setup(
      { current_node_id: "plan", pending_gate: "plan_approval" },
      [session({ node_id: "plan", hook_point: "on.plan.requested", status: "done" })],
    );
    renderDetail();
    expect(screen.queryByRole("button", { name: /pause/i })).toBeNull();
    expect(screen.getByText(/plan_approval/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("puts tasks, timeline and documents behind tabs, tasks first", async () => {
    setup({}, [session()]);
    renderDetail();
    const tasks = screen.getByRole("tab", { name: /Tasks/ });
    expect(tasks).toHaveAttribute("aria-selected", "true");
    expect(within(tasks).getByText("1")).toBeInTheDocument();
    expect(screen.getByTestId("session-s1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Documents/ }));
    expect(await screen.findByText(/no linked documents/i)).toBeInTheDocument();
    expect(api.getWorkItemDocuments).toHaveBeenCalledWith("w1");
    expect(screen.queryByTestId("session-s1")).toBeNull();
  });

  it("puts the item's captured usage in the control row", () => {
    setup(
      {
        usage: {
          total: { tokens_in: 130_000, tokens_out: 8_000, cost_usd: 2.41, cost_complete: true, wall_ms: 0, sessions: 3, rounds: 1, capped_out: 0 },
          by_node: [
            { node: "verify", tokens_in: 40_000, tokens_out: 1_200, cost_usd: 1.1, cost_complete: true, wall_ms: 0, sessions: 1, rounds: 1, capped_out: 0 },
          ],
        },
      },
      [session()],
    );
    renderDetail();
    expect(screen.getByText(/41.2k tokens this node/)).toBeInTheDocument();
    expect(screen.getByText(/138k total/)).toBeInTheDocument();
    expect(screen.getByText(/\$2\.41/)).toBeInTheDocument();
  });

  it("holds the Pause button in a pending state until the pause actually lands", async () => {
    const spy = vi.spyOn(api, "pauseWorkItem").mockResolvedValue({
      id: "w1",
      paused_sessions: ["s1"],
    });
    setup({}, [session()]);
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(spy).toHaveBeenCalledWith("w1");
    const button = screen.getByRole("button", { name: /pausing/i });
    expect(button).toBeDisabled();
  });

  it("says why a pause failed instead of leaving the button stuck", async () => {
    vi.spyOn(api, "pauseWorkItem").mockRejectedValue(new Error("work item is paused"));
    setup({}, [session()]);
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(await screen.findByText("work item is paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Pause$/ })).toBeEnabled();
  });

  it("offers Cancel at a gate, disabled while active (Kraft-aayj)", () => {
    setup({ status: "active" });
    renderDetail();
    const cancelBtn = screen.getByRole("button", { name: /^Cancel$/ });
    expect(cancelBtn).toBeDisabled();
    expect(screen.getByText(/pause it before abandoning/)).toBeInTheDocument();
  });

  it("abandons on confirm once the item is stopped, and does nothing without one", async () => {
    const spy = vi.spyOn(api, "abandonWorkItem").mockResolvedValue({
      id: "w1",
      status: "abandoned",
      worktree_removed: true,
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    setup({ status: "needs_human" });
    renderDetail();

    const cancelBtn = screen.getByRole("button", { name: /^Cancel$/ });
    expect(cancelBtn).toBeEnabled();

    await userEvent.click(cancelBtn);
    expect(spy).not.toHaveBeenCalled(); // declined the confirm

    await userEvent.click(cancelBtn);
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(spy).toHaveBeenCalledWith("w1");
  });

  it("hides Cancel once the item is completed or abandoned", () => {
    setup({ status: "completed" });
    renderDetail();
    expect(screen.queryByRole("button", { name: /^Cancel$/ })).toBeNull();
  });

  it("swaps the control row for the paused card, which resumes with or without the note", async () => {
    const spy = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({
      id: "w1",
      node_id: "verify",
      steer: null,
    });
    setup({ status: "paused", pending_steer_context: "keep the signature" }, [
      session({ status: "paused", attempt: 1 }),
    ]);
    renderDetail();
    expect(screen.queryByRole("button", { name: /^Pause$/ })).toBeNull();
    expect(screen.getByTestId("paused-card")).toBeInTheDocument();
    // the note the server already holds is prefilled, not lost
    expect(screen.getByLabelText(/Steer/)).toHaveValue("keep the signature");
    expect(screen.getByText(/relaunches on\.test\.run as attempt 2/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /resume with steer/i }));
    expect(spy).toHaveBeenCalledWith("w1", "keep the signature");

    await userEvent.click(screen.getByRole("button", { name: /resume without/i }));
    expect(spy).toHaveBeenLastCalledWith("w1", undefined);
  });

  it("cannot resume with a steer that is not there", () => {
    setup({ status: "paused" }, [session({ status: "paused" })]);
    renderDetail();
    expect(screen.getByRole("button", { name: /resume with steer/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /resume without/i })).toBeEnabled();
  });

  it("shows the repos panel only on a multi-repo item, deepest submodule first", () => {
    setup({
      root_merge_policy: "bump",
      repos: [
        { repo: "b", path: "vendor/deep/b", role: "submodule", merge_rank: 1, state: "pending" },
        { repo: "a", path: "libs/a", role: "submodule", merge_rank: 2, state: "pending" },
        { repo: "r", path: "/r", role: "root", merge_rank: 3, state: "pending" },
      ],
    });
    const { container } = renderDetail();
    const rows = [...container.querySelectorAll("[data-repo]")];
    expect(rows.map((r) => r.getAttribute("data-repo"))).toEqual([
      "vendor/deep/b",
      "libs/a",
      "/r",
    ]);
    expect(screen.getByText("+2 submodules")).toBeInTheDocument();
    expect(screen.getByText(/root_merge_policy/)).toHaveTextContent("bump");
  });

  it("shows a failed repo as failed, not pending", () => {
    setup({
      root_merge_policy: "bump",
      repos: [
        { repo: "b", path: "repos/pkg", role: "submodule", merge_rank: 1, state: "failed" },
        { repo: "r", path: "/r", role: "root", merge_rank: 2, state: "pending" },
      ],
    });
    const { container } = renderDetail();
    const row = container.querySelector('[data-repo="repos/pkg"]');
    expect(row?.textContent).toContain("failed");
    expect(row?.querySelector('[data-status="failed"]')).toBeTruthy();
  });

  it("has no repos panel on a single-repo item", () => {
    setup({ repos: [] });
    const { container } = renderDetail();
    expect(container.querySelector(".repos-panel")).toBeNull();
  });

  it("offers a diff at the human review gate, reachable from a phone", async () => {
    setup({ status: "needs_human", pending_gate: "human_review_approval" });
    renderDetail();
    const button = await screen.findByRole("button", { name: /review changes/i });
    expect(button).toBeInTheDocument();
    // The whole point: unlike "Open worktree", this must survive on a device
    // with no filesystem, so it must not be inside a desktop-only wrapper.
    expect(button.closest(".desktop-only")).toBeNull();
  });

  it("does not double up the review button inside the spec gate's artifact slot", async () => {
    setup({ status: "needs_human", pending_gate: "spec_approval" });
    renderDetail();
    // one button, from the hoisted row below the gate — not a second copy
    // inside Gate's artifact slot, which spec_approval doesn't use.
    const button = await screen.findByRole("button", { name: /review changes/i });
    expect(button.closest(".desktop-only")).toBeNull();
  });

  it("opens the worktree from the control row (Kraft-kmq)", async () => {
    const spy = vi
      .spyOn(api, "openWorktree")
      .mockResolvedValue({ path: "/run/worktrees/w1", editor: "code" });
    renderDetail();
    // jsdom serves the app from localhost, i.e. the server's own machine.
    await userEvent.click(screen.getByRole("button", { name: /open worktree/i }));
    expect(spy).toHaveBeenCalledWith("w1");
  });

  it("says so when the server cannot open the worktree", async () => {
    vi.spyOn(api, "openWorktree").mockRejectedValue(new Error("no worktree yet"));
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /open worktree/i }));
    expect(await screen.findByText("no worktree yet")).toBeInTheDocument();
  });

  it("offers a diff from the control row too, reachable from a phone", () => {
    renderDetail();
    const button = screen.getByRole("button", { name: /review changes/i });
    expect(button.closest(".desktop-only")).toBeNull();
  });

  it("offers a working retry control on a no-progress stop with no capped payload", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({
      id: "w1",
      node_id: "verify",
      loop: "verify_fix_loop",
      steer: null,
    });
    setup({
      status: "needs_human",
      chain_definition: {
        template_id: "quick-task",
        nodes: [
          NODES[0],
          { id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" },
        ],
      },
    });
    renderDetail();
    expect(screen.queryByRole("button", { name: /^Pause$/ })).toBeNull();
    const button = await screen.findByRole("button", { name: /steer and retry/i });
    await userEvent.click(button);
    expect(spy).toHaveBeenCalledWith("w1", undefined);
  });

  it("offers a diff while paused, not just while active", () => {
    setup({ status: "paused" }, [session({ status: "paused" })]);
    renderDetail();
    const button = screen.getByRole("button", { name: /review changes/i });
    expect(button.closest(".desktop-only")).toBeNull();
  });

  it("shows the agent's question and an answer box on a needs_context stop", async () => {
    const spy = vi.spyOn(api, "resumeWorkItem").mockResolvedValue({
      id: "w1",
      node_id: "verify",
      steer: "use postgres",
    });
    setup({
      status: "needs_human",
      needs_context_question: "which database should this target?",
    });
    renderDetail();
    // not the old dead end: no hard-disabled Steer button, no gate card
    expect(screen.queryByRole("button", { name: /^Steer$/ })).toBeNull();
    expect(screen.getByText(/which database should this target\?/)).toBeInTheDocument();

    const answer = screen.getByRole("button", { name: /answer/i });
    expect(answer).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/answer/i), "use postgres");
    expect(answer).toBeEnabled();
    await userEvent.click(answer);
    expect(spy).toHaveBeenCalledWith("w1", "use postgres");
  });

  it("offers escalate on a needs_context stop too", async () => {
    const spy = vi
      .spyOn(api, "escalateWorkItem")
      .mockResolvedValue({ id: "w1", status: "escalating" });
    setup({
      status: "needs_human",
      needs_context_question: "which database should this target?",
    });
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: /^escalate/i }));
    await userEvent.type(screen.getByLabelText("escalate message"), "postgres, obviously");
    await userEvent.click(screen.getByRole("button", { name: /send to agent/i }));
    expect(spy).toHaveBeenCalledWith("w1", "postgres, obviously");
  });

  it("answers a needs_context stop before falling to CappedCard, even on a fix-loop node", () => {
    setup({
      status: "needs_human",
      needs_context_question: "which branch is the target?",
      chain_definition: {
        template_id: "quick-task",
        nodes: [
          NODES[0],
          { id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" },
        ],
      },
    });
    renderDetail();
    expect(screen.getByTestId("needs-context-card")).toBeInTheDocument();
    expect(screen.queryByTestId("capped-card")).toBeNull();
  });

  it("offers retry on a task failure even though the node has no fix loop", () => {
    // Kraft-bzwi: retry is the only door onto a needs_human stop — resume
    // wants `paused`, pause wants `running`, approve/reject want a gate — so
    // hiding it on a loopless node left the item with no control at all.
    setup({ status: "needs_human", current_node_id: "verify" });
    renderDetail();
    expect(screen.getByTestId("capped-card")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows concerns at the review gate", () => {
    setup({
      status: "needs_human",
      pending_gate: "human_review_approval",
      concerns: ["the retry path is untested"],
    });
    renderDetail();
    expect(screen.getByText(/the retry path is untested/)).toBeInTheDocument();
  });

  it("shows concerns at any gate, not only the review gate", () => {
    // Spec §2 puts concerns at the next gate the item reaches. Binding the
    // condition to `human_review_approval` is equivalent only while
    // `on.implementation.start` is the sole `kind: agent` hook — and
    // registry.yaml is operator-editable from Settings.
    setup({
      status: "needs_human",
      pending_gate: "spec_approval",
      concerns: ["the spec contradicts the chain template"],
    });
    renderDetail();
    expect(screen.getByText(/the spec contradicts the chain template/)).toBeInTheDocument();
  });

  it("renders the gate card even while a stale needs_context question is held", () => {
    // human_review_approval is the one gate the Board refuses to approve
    // inline, so a suppressed gate card leaves no approve affordance anywhere.
    setup({
      status: "needs_human",
      pending_gate: "human_review_approval",
      needs_context_question: "which database?",
    });
    renderDetail();
    expect(screen.queryByTestId("needs-context-card")).toBeNull();
    expect(screen.getByText(/Review changes/)).toBeInTheDocument();
  });

  it("links out to the merge request once open_mr has run", () => {
    setup({ mr_ref: { number: 42, url: "https://forge.example/mr/42" } });
    renderDetail();
    const link = screen.getByRole("link", { name: /open mr !42/i });
    expect(link).toHaveAttribute("href", "https://forge.example/mr/42");
  });

  it("offers no MR link before open_mr has run", () => {
    renderDetail();
    expect(screen.queryByRole("link", { name: /open mr/i })).toBeNull();
  });

  it("offers the spec at a spec gate", async () => {
    setup({ pending_gate: "spec_approval", gate_artifact: ".engineering/specs/w1.md" });
    renderDetail();
    expect(await screen.findByText(/Review spec/)).toBeTruthy();
  });

  it("offers nothing when the gate has no artifact", async () => {
    setup({ pending_gate: "spec_approval", gate_artifact: null });
    renderDetail();
    expect(screen.queryByText(/Review spec/)).toBeNull();
  });

  it("offers both the review brief and the diff at the human_review gate", async () => {
    // Kraft-yytk: `human_review_approval` short-circuited to the diff, so the
    // one page the review-brief skill spent a session writing had no button
    // anywhere and the last gate before merge was approved on raw diff alone.
    vi.spyOn(api, "getWorkItemArtifact").mockResolvedValue({
      work_item_id: "w1",
      path: ".engineering/reviews/w1.md",
      title: "Review brief",
      content: "CI is green. One minor finding left in.",
      truncated: false,
    });
    setup({
      status: "needs_human",
      pending_gate: "human_review_approval",
      gate_artifact: ".engineering/reviews/w1.md",
    });
    renderDetail();

    const brief = await screen.findByRole("button", { name: /review brief/i });
    expect(screen.getByRole("button", { name: /review changes/i })).toBeInTheDocument();

    await userEvent.click(brief);
    expect(await screen.findByText(/one minor finding left in/i)).toBeInTheDocument();
    expect(api.getWorkItemArtifact).toHaveBeenCalledWith("w1");
  });

  it("still offers the diff when the review brief is missing", async () => {
    // The agent reported done without honouring the artifact contract. Rare
    // (agent.py's post_resolve fails the node), and the gate stays answerable.
    setup({
      status: "needs_human",
      pending_gate: "human_review_approval",
      gate_artifact: null,
    });
    renderDetail();
    expect(await screen.findByRole("button", { name: /review changes/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /review brief/i })).toBeNull();
    expect(screen.getByRole("button", { name: /^approve$/i })).toBeEnabled();
  });

  it("links to the merge request once an mr_opened event exists", () => {
    const mrEvent = {
      seq: 9,
      work_item_id: "w1",
      type: "mr_opened",
      payload: { number: 76, url: "https://gitlab.example.com/x/y/-/merge_requests/76" },
      created_at: "2026-09-09T10:00:00Z",
    };

    setup({ current_node_id: "human_review" });
    const { unmount } = renderDetail();
    expect(screen.queryByRole("link", { name: "!76" })).toBeNull();
    unmount();

    setup({ current_node_id: "human_review" });
    useStore.setState({ eventsByItem: { w1: [mrEvent] } } as never);
    renderDetail();
    const link = screen.getByRole("link", { name: "!76" });
    expect(link).toHaveAttribute("href", "https://gitlab.example.com/x/y/-/merge_requests/76");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });
});
